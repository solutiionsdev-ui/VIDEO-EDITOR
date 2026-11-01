"""Stage 5 — cut silences.

Turns the kept takes into an edit list: which pieces of the raw footage stay,
in order. Dead air, long pauses and filler words are removed, a natural breath
is kept at every cut, and a cut never lands inside a word.
Writes cuts.json and a low-res preview video (previews/cut-preview.mp4).
"""

from __future__ import annotations

from rich.table import Table

from .. import audio_analysis as aa
from ..job import Job, console, fmt_clock, fmt_time
from ..media import decode_audio, run_ffmpeg
from ..profile import get
from ..text import ends_sentence, is_filler, preview

OUTPUT = "cuts.json"
PREVIEW = "previews/cut-preview.mp4"


def run(job: Job, profile: dict, show: bool = False, preview_video: bool = True) -> dict:
    takes = job.read_json("takes.json")
    transcript = job.read_json("transcript.json")
    e = profile.get("editing", {})
    breath = float(e.get("breath", 0.12))
    sentence_breath = float(e.get("sentence_breath", 0.18))
    max_pause = float(e.get("max_pause", 0.45))
    min_cut = float(e.get("min_cut", 0.12))
    pre_pad = float(e.get("pre_pad", 0.15))
    post_pad = float(e.get("post_pad", 0.20))
    remove_fillers = bool(e.get("remove_fillers", True))

    units_by_source: dict[int, list[dict]] = {}
    for u in takes["units"]:
        units_by_source.setdefault(u["source"], []).append(u)

    segments: list[dict] = []
    removed: list[dict] = []
    total_in = 0.0
    for src in transcript["sources"]:
        si = src["index"]
        duration = float(src["duration"])
        total_in += duration
        env = aa.envelope(decode_audio(job.path / src["wav"], 16000), 16000)
        units = sorted(units_by_source.get(si, []), key=lambda u: u["start"])
        all_words = sorted((w for u in units for w in u["words"]), key=lambda w: w["start"])

        # 1. The words we keep, in order, grouped into runs that play without a cut.
        runs: list[dict] = []
        run_words: list[dict] = []
        run_units: list[int] = []
        removed_words: list[dict] = []   # words dropped inside kept lines (fillers)

        def close_run() -> None:
            if run_words:
                runs.append({"words": list(run_words), "units": list(run_units)})
            run_words.clear()
            run_units.clear()

        last_kept_word: dict | None = None
        for u in units:
            if not u["kept"]:
                close_run()
                for w in u["words"]:
                    removed_words.append({**w, "kind": "rejected_take", "unit": u["id"]})
                continue
            for w in u["words"]:
                if remove_fillers and is_filler(w["w"]) and len(u["words"]) > 1:
                    close_run()
                    removed_words.append({**w, "kind": "filler", "unit": u["id"]})
                    continue
                if last_kept_word is not None and run_words and (w["start"] - last_kept_word["end"]) > max_pause:
                    close_run()
                run_words.append(w)
                if u["id"] not in run_units:
                    run_units.append(u["id"])
                last_kept_word = w
        close_run()

        # 2. Turn runs into keep-segments with safe, quiet cut points.
        prev_end = 0.0
        for ri, r in enumerate(runs):
            first, last = r["words"][0], r["words"][-1]
            prev_word = _word_before(all_words, first["start"])
            next_word = _word_after(all_words, last["end"])

            if ri == 0:
                start = max(0.0, first["start"] - pre_pad)
                if prev_word is not None:
                    start = max(start, prev_word["end"] + 0.02)
            else:
                lo = first["start"] - pre_pad - 0.15
                hi = first["start"] - breath
                if prev_word is not None:
                    lo = max(lo, prev_word["end"] + 0.02)
                start = env.quietest(lo, hi) if hi > lo else max(lo, min(hi, first["start"] - 0.03))
                start = max(start, prev_end + 0.01)

            pause = sentence_breath if ends_sentence(last["w"]) else breath
            if ri == len(runs) - 1:
                end = min(duration, last["end"] + post_pad)
                if next_word is not None:
                    end = min(end, next_word["start"] - 0.02)
            else:
                lo = last["end"] + pause
                hi = last["end"] + post_pad + 0.15
                if next_word is not None:
                    hi = min(hi, next_word["start"] - 0.02)
                end = env.quietest(lo, hi) if hi > lo else max(last["end"] + 0.02, min(lo, hi))
            end = max(end, last["end"] + 0.02)
            start = _not_inside_word(all_words, start, push="earlier")
            end = _not_inside_word(all_words, end, push="later")
            end = min(end, duration)
            if end <= start:
                continue

            seg = {"source": si, "start": round(start, 3), "end": round(end, 3), "words": r["words"], "units": r["units"]}

            # Tiny pauses aren't worth a cut: merge with the previous segment unless words were removed between.
            if segments and segments[-1]["source"] == si:
                prev = segments[-1]
                gap_words = [w for w in removed_words if prev["end"] <= w["start"] and w["end"] <= seg["start"] + 0.05]
                if not gap_words and (seg["start"] - prev["end"]) < min_cut:
                    prev["end"] = seg["end"]
                    prev["words"] += seg["words"]
                    prev["units"] += [x for x in seg["units"] if x not in prev["units"]]
                    prev_end = prev["end"]
                    continue
                kinds = {w["kind"] for w in gap_words}
                kind = "rejected_take" if "rejected_take" in kinds else ("filler" if "filler" in kinds else "pause")
                removed.append({"source": si, "start": prev["end"], "end": seg["start"],
                                "duration": round(seg["start"] - prev["end"], 3), "kind": kind,
                                "text": preview(" ".join(w["w"] for w in gap_words), 80)})
            elif start > 0:
                lead_words = [w for w in removed_words if w["end"] <= start + 0.05]
                removed.append({"source": si, "start": 0.0, "end": seg["start"], "duration": round(seg["start"], 3),
                                "kind": "rejected_take" if lead_words else "lead",
                                "text": preview(" ".join(w["w"] for w in lead_words), 80)})
            segments.append(seg)
            prev_end = seg["end"]

        if segments and segments[-1]["source"] == si and segments[-1]["end"] < duration - 0.05:
            tail_start = segments[-1]["end"]
            tail_words = [w for w in removed_words if w["start"] >= tail_start - 0.05]
            removed.append({"source": si, "start": tail_start, "end": round(duration, 3),
                            "duration": round(duration - tail_start, 3),
                            "kind": "rejected_take" if tail_words else "tail",
                            "text": preview(" ".join(w["w"] for w in tail_words), 80)})

    for i, s in enumerate(segments):
        s["id"] = i
        s["duration"] = round(s["end"] - s["start"], 3)
        s["text"] = " ".join(w["w"] for w in s["words"])
        s["n_words"] = len(s["words"])
        s["out_start"] = round(sum(x["duration"] for x in segments[:i]), 3)

    total_out = sum(s["duration"] for s in segments)
    by_kind: dict[str, dict] = {}
    for r in removed:
        k = by_kind.setdefault(r["kind"], {"count": 0, "seconds": 0.0})
        k["count"] += 1
        k["seconds"] = round(k["seconds"] + r["duration"], 2)

    result = {
        "settings": {"breath": breath, "sentence_breath": sentence_breath, "max_pause": max_pause,
                     "min_cut": min_cut, "pre_pad": pre_pad, "post_pad": post_pad, "remove_fillers": remove_fillers},
        "segments": segments,
        "removed": removed,
        "script": " ".join(s["text"] for s in segments),
        "summary": {
            "segments": len(segments),
            "seconds_in": round(total_in, 2),
            "seconds_out": round(total_out, 2),
            "removed": by_kind,
        },
    }
    job.write_json(OUTPUT, result)
    job.log(f"  {len(segments)} segments · {fmt_clock(total_in)} → {fmt_clock(total_out)}")

    if preview_video and segments:
        job.log("  rendering low-res preview…")
        _render_preview(job, transcript, segments)
        job.log(f"  preview: {PREVIEW}")
    if show:
        report(job, result)
    return result


def _word_before(words: list[dict], t: float) -> dict | None:
    best = None
    for w in words:
        if w["end"] <= t + 1e-6:
            best = w
        elif w["start"] >= t:
            break
    return best


def _word_after(words: list[dict], t: float) -> dict | None:
    for w in words:
        if w["start"] >= t - 1e-6:
            return w
    return None


def _not_inside_word(words: list[dict], t: float, push: str) -> float:
    """Never cut mid-word: move the cut point just outside any word it falls into."""
    for w in words:
        if w["start"] + 0.02 < t < w["end"] - 0.02:
            return w["start"] - 0.02 if push == "earlier" else w["end"] + 0.02
        if w["start"] > t + 1:
            break
    return t


def _render_preview(job: Job, transcript: dict, segments: list[dict]) -> None:
    sources = {s["index"]: s["file"] for s in transcript["sources"]}
    inputs: list[str] = []
    order = sorted(sources)
    for si in order:
        inputs += ["-i", str(job.raw_dir / sources[si])]
    parts = []
    concat = ""
    for i, s in enumerate(segments):
        idx = order.index(s["source"])
        parts.append(f"[{idx}:v]trim=start={s['start']:.3f}:end={s['end']:.3f},setpts=PTS-STARTPTS,scale=-2:640[v{i}]")
        parts.append(f"[{idx}:a]atrim=start={s['start']:.3f}:end={s['end']:.3f},asetpts=PTS-STARTPTS[a{i}]")
        concat += f"[v{i}][a{i}]"
    graph = ";".join(parts) + f";{concat}concat=n={len(segments)}:v=1:a=1[v][a]"
    out = job.path / PREVIEW
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(inputs + ["-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
                         "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)])


def report(job: Job, data: dict) -> None:
    s = data["summary"]
    console.print(f"\nCut list: {s['segments']} segments · {fmt_clock(s['seconds_in'])} raw → "
                  f"[bold]{fmt_clock(s['seconds_out'])}[/] edited")
    bits = []
    labels = {"pause": "pauses", "filler": "filler words", "rejected_take": "rejected takes",
              "lead": "lead-in", "tail": "tail"}
    for kind, v in s["removed"].items():
        bits.append(f"{v['count']} {labels.get(kind, kind)} ({v['seconds']:.1f}s)")
    if bits:
        console.print("  removed: " + ", ".join(bits))
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("#", justify="right")
    table.add_column("raw in → out")
    table.add_column("dur", justify="right")
    table.add_column("edited at")
    table.add_column("text")
    for seg in data["segments"]:
        table.add_row(str(seg["id"] + 1), f"{fmt_time(seg['start'])} → {fmt_time(seg['end'])}",
                      f"{seg['duration']:.1f}s", fmt_time(seg["out_start"]), preview(seg["text"], 70))
    console.print(table)
    console.print(f"\n  preview video: {job.path / PREVIEW}")
