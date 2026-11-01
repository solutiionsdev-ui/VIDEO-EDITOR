"""Stage 4 — pick the best takes.

Splits the transcript into lines, finds lines the speaker said more than once
(retakes and false starts), scores every take and keeps the best one. The
rejected takes are recorded with the reason, so the client can swap later.
Writes takes.json.
"""

from __future__ import annotations

from rich.table import Table

from .. import audio_analysis as aa
from ..job import Job, console, fmt_time
from ..media import decode_audio
from ..profile import get
from ..text import (FILLERS, ends_sentence, is_filler, looks_cut_off, prefix_overlap, preview,
                    similarity, stumbles, tokens)

OUTPUT = "takes.json"


def run(job: Job, profile: dict, show: bool = False) -> dict:
    transcript = job.read_json("transcript.json")
    threshold = float(get(profile, "editing.retake_similarity", 0.6))
    window = float(get(profile, "editing.retake_window_s", 90))
    prefer_last = str(get(profile, "editing.take_preference", "last")) == "last"

    units: list[dict] = []
    for src in transcript["sources"]:
        units.extend(_split_units(src, start_id=len(units)))

    # Audio envelope per source, to judge how each take sounds.
    envelopes = {}
    for src in transcript["sources"]:
        samples = decode_audio(job.path / src["wav"], 16000)
        env = aa.envelope(samples, 16000)
        envelopes[src["index"]] = (env, aa.analyse(samples, 16000, env))

    # Drop hallucinated lines before looking for retakes.
    dropped = []
    for u in units:
        if u["suspect"]:
            u["kept"] = False
            u["reason"] = "probably not speech (transcriber hallucination)"
            dropped.append(u["id"])

    groups = _find_retakes(units, threshold, window)
    for gi, members in enumerate(groups):
        takes = []
        max_tokens = max(len(_content_tokens(units[m]["tokens"])) for m in members)
        for take_no, m in enumerate(members, start=1):
            u = units[m]
            u["group"] = gi
            env, stats = envelopes[u["source"]]
            takes.append(_score_take(u, take_no, max_tokens, env, stats, prefer_last))
        best = max(takes, key=lambda t: t["score"])
        for t in takes:
            u = units[t["unit"]]
            if t is best:
                u["kept"] = True
                u["reason"] = None
                t["kept"] = True
            else:
                u["kept"] = False
                t["kept"] = False
                t["reasons"] = t["reasons"] or ["speaker redid this line" if prefer_last else "a better take exists"]
                u["reason"] = "; ".join(t["reasons"])
        groups[gi] = {
            "id": gi,
            "line": preview(units[best["unit"]]["text"], 80),
            "takes": takes,
            "chosen": best["unit"],
            "take_used": f"{best['take_no']} of {len(takes)}",
        }

    kept = [u["id"] for u in units if u["kept"]]
    removed_seconds = sum(u["end"] - u["start"] for u in units if not u["kept"])
    result = {
        "units": [{k: v for k, v in u.items() if k != "tokens"} for u in units],
        "groups": groups,
        "kept": kept,
        "dropped_suspect": dropped,
        "summary": {
            "lines": len(units),
            "retake_groups": len(groups),
            "takes_removed": sum(len(g["takes"]) - 1 for g in groups),
            "suspect_removed": len(dropped),
            "seconds_removed": round(removed_seconds, 2),
        },
    }
    job.write_json(OUTPUT, result)
    job.log(f"  {len(units)} lines, {len(groups)} retake groups, "
            f"{result['summary']['takes_removed']} takes removed ({removed_seconds:.1f}s)")
    if show:
        report(job, result)
    return result


def _split_units(src: dict, start_id: int) -> list[dict]:
    """One unit per sentence (or per transcript segment when there is no punctuation)."""
    units = []
    for seg in src["segments"]:
        words = seg["words"]
        if not words:
            continue
        current: list[dict] = []
        for w in words:
            current.append(w)
            if ends_sentence(w["w"]):
                units.append(_make_unit(src, seg, current, start_id + len(units)))
                current = []
        if current:
            units.append(_make_unit(src, seg, current, start_id + len(units)))
    return units


def _make_unit(src: dict, seg: dict, words: list[dict], uid: int) -> dict:
    text = " ".join(w["w"] for w in words)
    return {
        "id": uid,
        "source": src["index"],
        "segment": seg["id"],
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "text": text,
        "words": words,
        "tokens": tokens(text),
        "suspect": bool(seg.get("suspect")),
        "kept": True,
        "reason": None,
        "group": None,
    }


def _find_retakes(units: list[dict], threshold: float, window: float) -> list[list[int]]:
    parent = list(range(len(units)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    for i, a in enumerate(units):
        if a["suspect"] or len(a["tokens"]) < 3:
            continue
        for j in range(i + 1, len(units)):
            b = units[j]
            if b["source"] != a["source"]:
                continue
            if b["start"] - a["end"] > window:
                break
            if b["suspect"] or len(b["tokens"]) < 3:
                continue
            ta, tb = a["tokens"], b["tokens"]
            sim = similarity(ta, tb)
            short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
            pre = prefix_overlap(short, long_) if len(short) <= 0.75 * len(long_) else 0.0
            if sim >= threshold or pre >= 0.8:
                union(i, j)

    buckets: dict[int, list[int]] = {}
    for i in range(len(units)):
        buckets.setdefault(find(i), []).append(i)
    groups = [sorted(m) for m in buckets.values() if len(m) > 1]
    groups.sort(key=lambda m: units[m[0]]["start"])
    return groups


def _content_tokens(toks: list[str]) -> list[str]:
    """The words that carry meaning: no fillers, immediate repeats collapsed."""
    out: list[str] = []
    for t in toks:
        if t in FILLERS:
            continue
        if out and out[-1] == t:
            continue
        out.append(t)
    return out


def _score_take(u: dict, take_no: int, max_tokens: int, env: aa.Envelope, stats: aa.AudioStats,
                prefer_last: bool) -> dict:
    words = u["words"]
    n = len(_content_tokens(u["tokens"]))
    reasons: list[str] = []

    completeness = n / max_tokens if max_tokens else 1.0
    if looks_cut_off(u["text"]) and n < max_tokens:
        completeness -= 0.3
        reasons.append(f"cut off ({n} of {max_tokens} words)")
    elif n < max_tokens and not ends_sentence(u["text"]):
        reasons.append(f"stopped early ({n} of {max_tokens} words)")
    elif n < 0.7 * max_tokens:
        reasons.append(f"shorter ({n} of {max_tokens} words)")
    if ends_sentence(u["text"]):
        completeness = min(1.0, completeness + 0.1)
    completeness = max(0.0, completeness)

    n_stumbles = stumbles([w["w"] for w in words])
    n_fillers = sum(1 for w in words if is_filler(w["w"]))
    low_conf = sum(1 for w in words if w["p"] < 0.4)
    mean_p = sum(w["p"] for w in words) / max(1, len(words))
    audio = aa.span_quality(env, u["start"], u["end"], stats)

    if n_stumbles:
        reasons.append(f"{n_stumbles} stumble{'s' if n_stumbles > 1 else ''}")
    if n_fillers:
        reasons.append(f"{n_fillers} filler word{'s' if n_fillers > 1 else ''}")
    if low_conf >= 2:
        reasons.append(f"{low_conf} unclear words")
    if audio["level_vs_file"] <= -6:
        reasons.append(f"quieter audio ({audio['level_vs_file']:+.0f} dB)")

    score = (40 * completeness
             + 15 * (1 - min(1.0, n_stumbles / 3))
             + 15 * (1 - min(1.0, n_fillers / 3))
             + 15 * audio["quality"]
             + 15 * mean_p)
    if prefer_last:
        score += 1.5 * take_no   # later takes win ties: people redo a line until they're happy
    return {
        "unit": u["id"],
        "take_no": take_no,
        "start": u["start"],
        "end": u["end"],
        "text": u["text"],
        "score": round(score, 1),
        "breakdown": {
            "completeness": round(completeness, 2),
            "stumbles": n_stumbles,
            "fillers": n_fillers,
            "unclear_words": low_conf,
            "confidence": round(mean_p, 2),
            "audio": audio,
        },
        "kept": False,
        "reasons": reasons,
    }


def report(job: Job, data: dict) -> None:
    s = data["summary"]
    console.print(f"\nTakes: {s['lines']} lines · {s['retake_groups']} retake groups · "
                  f"{s['takes_removed']} takes removed ({s['seconds_removed']}s)"
                  + (f" · {s['suspect_removed']} suspect lines dropped" if s["suspect_removed"] else ""))
    if not data["groups"]:
        console.print("  no repeated lines found — every line is kept")
    for g in data["groups"]:
        console.print(f"\n[bold]Group {g['id'] + 1}[/]: “{g['line']}” → take {g['take_used']} kept")
        table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("take", justify="right")
        table.add_column("time")
        table.add_column("score", justify="right")
        table.add_column("text")
        table.add_column("verdict")
        for t in g["takes"]:
            verdict = "[green]KEPT[/]" if t["kept"] else "[red]out[/] " + ", ".join(t["reasons"])
            table.add_row(str(t["take_no"]), f"{fmt_time(t['start'])}–{fmt_time(t['end'])}", str(t["score"]),
                          preview(t["text"], 70), verdict)
        console.print(table)
    if data["dropped_suspect"]:
        units = {u["id"]: u for u in data["units"]}
        console.print("\n[yellow]Dropped as probably-not-speech:[/]")
        for uid in data["dropped_suspect"]:
            u = units[uid]
            console.print(f"  {fmt_time(u['start'])} “{preview(u['text'], 70)}”")
