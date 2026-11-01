"""Stage 5 — cut silences.

Turns the kept takes into an edit list: which pieces of the raw footage stay,
in order. Dead air, long pauses and filler words are removed and a natural
breath is kept at every cut.

Pauses are found in the audio itself (energy below the room level for longer
than `max_pause`), not from the transcript: Whisper stretches each word's end
time over the silence that follows it, so word gaps hide most pauses. A region
that is quiet for half a second cannot contain a spoken word, which is how the
"never cut mid-word" rule is kept. Word timestamps are only used to find
fillers and rejected takes, and those cut points are snapped to the quietest
moment nearby.

Writes cuts.json and a low-res preview video (previews/cut-preview.mp4).
"""

from __future__ import annotations

import numpy as np
from rich.table import Table

from .. import audio_analysis as aa
from ..job import Job, console, fmt_clock, fmt_time
from ..media import decode_audio, run_ffmpeg
from ..profile import get
from ..text import ends_sentence, is_filler, preview

OUTPUT = "cuts.json"
PREVIEW = "previews/cut-preview.mp4"
MIN_SEGMENT = 0.25          # kept pieces shorter than this are not worth a cut
WORDLESS_KEEP = 1.0         # a stretch with no words is kept only if it is at least this long


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
    silence_db_over_floor = float(get(profile, "editing.silence_db_over_floor", 8))

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
        samples = decode_audio(job.path / src["wav"], 16000)
        env = aa.envelope(samples, 16000)
        stats = aa.analyse(samples, 16000, env)
        threshold = stats.noise_floor_dbfs + max(silence_db_over_floor, 0.3 * stats.snr_db)
        units = sorted(units_by_source.get(si, []), key=lambda u: u["start"])
        kept_words = [w for u in units if u["kept"] for w in u["words"]]
        kept_words.sort(key=lambda w: w["start"])

        cuts: list[dict] = []   # intervals to remove: start, end, kind, text

        # 1. Whole lines that lost to a better take (or were hallucinated).
        for u in units:
            if u["kept"]:
                continue
            kind = "suspect" if u["suspect"] else "rejected_take"
            s = env.quietest(u["start"] - 0.3, u["start"] + 0.05) + breath / 2
            t = env.quietest(u["end"] - 0.05, u["end"] + 0.3) - breath / 2
            cuts.append(_cut(min(s, u["start"]), max(t, u["end"]), kind, u["text"]))

        # 2. Filler words inside kept lines.
        if remove_fillers:
            for u in units:
                if not u["kept"] or len(u["words"]) < 2:
                    continue
                for w in u["words"]:
                    if is_filler(w["w"]):
                        s = env.quietest(w["start"] - 0.15, w["start"] + 0.03)
                        t = env.quietest(w["end"] - 0.03, w["end"] + 0.15)
                        cuts.append(_cut(min(s, w["start"]), max(t, w["end"]), "filler", w["w"]))

        # 3. Pauses: stretches of near-silence longer than max_pause.
        for a, b in _quiet_runs(env, threshold, max_pause):
            if a <= 0.05:                                   # dead air before the first word
                cuts.append(_cut(0.0, max(0.0, b - pre_pad), "lead", ""))
            elif b >= duration - 0.05:                       # dead air after the last word
                cuts.append(_cut(min(duration, a + post_pad), duration, "tail", ""))
            else:
                pad = sentence_breath if _ends_sentence_before(kept_words, a) else breath
                if (b - a) - 2 * pad >= min_cut:
                    cuts.append(_cut(a + pad, b - pad, "pause", ""))

        merged = _merge(cuts, duration)

        # 4. What stays is everything between the cuts — unless it is a short stretch with
        #    no words in it (a breath or a noise between two pauses), which goes too.
        src_segments: list[dict] = []
        cursor = 0.0
        for c in merged + [None]:
            end = duration if c is None else c["start"]
            has_words = any(cursor <= (w["start"] + w["end"]) / 2 <= end for w in kept_words)
            if end - cursor >= MIN_SEGMENT and (has_words or end - cursor >= WORDLESS_KEEP):
                src_segments.append({"source": si, "start": round(cursor, 3), "end": round(end, 3)})
            if c is not None:
                cursor = c["end"]
        # Re-derive the removed list from the gaps between kept segments (so folded pieces are counted).
        prev_end = 0.0
        src_removed: list[dict] = []
        for seg in src_segments + [None]:
            gap_start = prev_end
            gap_end = duration if seg is None else seg["start"]
            if gap_end - gap_start > 0.01:
                inside = [c for c in merged if c["start"] < gap_end and c["end"] > gap_start]
                kinds = {c["kind"] for c in inside}
                kind = next((k for k in ("rejected_take", "suspect", "filler", "lead", "tail", "pause") if k in kinds), "pause")
                src_removed.append({"source": si, "start": round(gap_start, 3), "end": round(gap_end, 3),
                                    "duration": round(gap_end - gap_start, 3), "kind": kind,
                                    "text": preview(" ".join(c["text"] for c in inside if c["text"]), 80)})
            if seg is not None:
                prev_end = seg["end"]

        for seg in src_segments:
            seg["words"] = [w for w in kept_words if seg["start"] <= (w["start"] + w["end"]) / 2 <= seg["end"]
                            and not (remove_fillers and is_filler(w["w"]))]
            seg["units"] = sorted({u["id"] for u in units if u["kept"]
                                   for w in u["words"] if seg["start"] <= (w["start"] + w["end"]) / 2 <= seg["end"]})
            seg["quiet_threshold_dbfs"] = round(threshold, 1)
        segments.extend(src_segments)
        removed.extend(src_removed)

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
                     "min_cut": min_cut, "pre_pad": pre_pad, "post_pad": post_pad, "remove_fillers": remove_fillers,
                     "silence_db_over_floor": silence_db_over_floor},
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


def _cut(start: float, end: float, kind: str, text: str) -> dict:
    return {"start": max(0.0, start), "end": end, "kind": kind, "text": text}


def _quiet_runs(env: aa.Envelope, threshold: float, min_len: float) -> list[tuple[float, float]]:
    """Stretches where the level stays under `threshold` for at least `min_len` seconds."""
    quiet = env.db < threshold
    runs: list[tuple[float, float]] = []
    n = len(quiet)
    i = 0
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < n and quiet[j]:
            j += 1
        a, b = float(env.times[i]), float(env.times[j - 1])
        if b - a >= min_len:
            runs.append((a, b))
        i = j
    return runs


def _ends_sentence_before(words: list[dict], t: float) -> bool:
    """Does the last word spoken before time t close a sentence?"""
    last = None
    for w in words:
        if w["start"] < t:
            last = w
        else:
            break
    return bool(last) and ends_sentence(last["w"])


def _merge(cuts: list[dict], duration: float) -> list[dict]:
    """Sort, clamp and merge overlapping or touching cuts; stronger kinds win the label."""
    rank = {"rejected_take": 0, "suspect": 1, "filler": 2, "lead": 3, "tail": 3, "pause": 4}
    cleaned = []
    for c in sorted(cuts, key=lambda c: c["start"]):
        s, e = max(0.0, c["start"]), min(duration, c["end"])
        if e - s <= 0.01:
            continue
        if cleaned and s <= cleaned[-1]["end"] + 0.05:
            last = cleaned[-1]
            last["end"] = max(last["end"], e)
            if rank[c["kind"]] < rank[last["kind"]]:
                last["kind"] = c["kind"]
            if c["text"]:
                last["text"] = (last["text"] + " " + c["text"]).strip()
        else:
            cleaned.append({"start": s, "end": e, "kind": c["kind"], "text": c["text"]})
    return cleaned


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
              "suspect": "hallucinated lines", "lead": "lead-in", "tail": "tail"}
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
