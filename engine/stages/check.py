"""Stage 11 — automatic quality check.

Runs on every render before anyone sees it:

  format         1080×1920, 30 fps, right length, audio present
  audio_level    −15 to −13 LUFS, true peak under −1 dBTP
  caption_sync   every caption word within 0.1 s of its transcript time
  face_clear     no widget or caption box ever overlaps the face box
  clean_cuts     no cut lands on speech, no click at a cut
  black_frozen   no black or frozen run over 0.2 s
  visual_review  each rendered frame matches the approved still

What fails and can be fixed by the engine (loudness, a widget on the face,
a frozen beat) is fixed and re-rendered, up to two rounds. What cannot is
reported and the job goes to Failed. Writes renders/vN-check.json.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
from rich.table import Table

from .. import audio_analysis as aa
from ..job import Job, JobError, console, fmt_time
from ..media import decode_audio, probe, run_ffmpeg
from ..profile import get
from ..render import compose
from ..render.driver import FPS, HEIGHT, Renderer, WIDTH, beat_spec, layout_kind, neighbours, widget_in_time
from ..text import normalize
from .beats import load_style

MAX_ROUNDS = 2
VISUAL_THRESHOLD = 12.0      # mean absolute pixel difference (0-255) between still and render frame


def run(job: Job, profile: dict, show: bool = False, version: int | None = None, fix: bool = True) -> dict:
    version = version or latest_version(job)
    if version is None:
        raise JobError("No render to check. Run `reel render <job>` first.")
    result = None
    done: list[dict] = []          # what the rounds before this one repaired; kept in the final result
    for round_no in range(MAX_ROUNDS + 1):
        result = check_version(job, profile, version)
        result["round"] = round_no
        result["fixes"] = list(done)
        job.write_json(f"renders/v{version}-check.json", result)
        failed = [k for k, c in result["checks"].items() if c["status"] == "fail"]
        if not failed or not fix or round_no == MAX_ROUNDS:
            break
        fixes = apply_fixes(job, profile, version, result)
        done += fixes
        if not fixes:
            break
        job.log(f"  round {round_no + 1}: " + "; ".join(f["what"] for f in fixes))
    job.log(f"  quality check v{version}: {result['status'].upper()}")
    if show:
        report(job, result)
    return result


def latest_version(job: Job) -> int | None:
    versions = [int(p.stem[1:]) for p in (job.path / "renders").glob("v*.json")
                if p.stem[1:].isdigit()] if (job.path / "renders").is_dir() else []
    return max(versions) if versions else None


# -- the checks ------------------------------------------------------------------

def check_version(job: Job, profile: dict, version: int) -> dict:
    manifest = job.read_json(f"renders/v{version}.json")
    video = job.path / manifest["file"]
    beats = job.read_json("beats.json")
    cuts = job.read_json("cuts.json")
    face = job.read_json("face.json") if job.has("face.json") else None
    stills = job.read_json("stills/index.json") if job.has("stills/index.json") else None
    style = load_style(str(beats.get("style") or "ios"))

    checks = {
        "format": check_format(video, manifest),
        "audio_level": check_audio_level(video, silent_by_design=_silent(job, manifest)),
        "caption_sync": check_caption_sync(beats, cuts, manifest),
        "face_clear": check_face_clear(job, profile, style, beats, cuts, face),
        "clean_cuts": check_clean_cuts(job, cuts),
        "black_frozen": check_black_frozen(job, video, beats, manifest, style),
        "visual_review": check_visual(job, video, beats, stills, version),
    }
    statuses = {c["status"] for c in checks.values()}
    status = "fail" if "fail" in statuses else ("review" if "review" in statuses else "pass")
    return {"version": version, "file": manifest["file"], "status": status, "checks": checks, "fixes": []}


def check_format(video: Path, manifest: dict) -> dict:
    info = probe(video)
    problems = []
    v = info.video
    if not v:
        problems.append("no video stream")
    else:
        if (v.display_width, v.display_height) != (WIDTH, HEIGHT):
            problems.append(f"{v.display_width}×{v.display_height}, expected {WIDTH}×{HEIGHT}")
        if abs(v.fps - FPS) > 0.05:
            problems.append(f"{v.fps} fps, expected {FPS}")
    if not info.audio:
        problems.append("no audio stream")
    elif info.audio.sample_rate != 48000:
        problems.append(f"audio {info.audio.sample_rate} Hz, expected 48000")
    expected = float(manifest["duration"])
    if abs(info.duration - expected) > 0.25:
        problems.append(f"length {info.duration:.2f}s, expected {expected:.2f}s")
    detail = f"{v.display_width}×{v.display_height} · {v.fps:g} fps · {info.duration:.2f}s · audio {'yes' if info.audio else 'no'}" if v else "unreadable"
    return {"status": "fail" if problems else "pass", "detail": detail, "items": problems}


def _silent(job: Job, manifest: dict) -> bool:
    """An infographic video made from a script alone, with no music supplied, has nothing to hear."""
    if manifest.get("music") or not job.has("script.json"):
        return False
    return job.read_json("script.json").get("source") == "script"


def check_audio_level(video: Path, silent_by_design: bool = False) -> dict:
    if silent_by_design:
        return {"status": "review", "detail": "silent: no voice-over and no music supplied",
                "items": ["add a voice-over or a music track, or accept a silent video"], "measured": None}
    m = compose.loudness(video)
    problems = []
    if m["lufs"] is None:
        return {"status": "fail", "detail": "could not measure loudness", "items": ["no measurement"], "measured": m}
    if not (-15.0 <= m["lufs"] <= -13.0):
        problems.append(f"{m['lufs']:.1f} LUFS is outside −15 to −13")
    if m["true_peak"] is not None and m["true_peak"] > -1.0:
        problems.append(f"true peak {m['true_peak']:.1f} dBTP is above −1 (clipping risk)")
    detail = f"{m['lufs']:.1f} LUFS · peak {m['true_peak']:.1f} dBTP · LRA {m['lra']:.1f} LU"
    return {"status": "fail" if problems else "pass", "detail": detail, "items": problems, "measured": m}


def check_caption_sync(beats: dict, cuts: dict, manifest: dict) -> dict:
    """Caption words are drawn from beats.json; compare them with the cut's own word times.

    A word whose transcript start falls inside a removed pause is shown from the segment's first
    frame (the speech onset), so the expected time is clamped to the segment the same way.
    """
    expected = []
    for seg in cuts["segments"]:
        for w in seg["words"]:
            t = seg["out_start"] + (w["start"] - seg["start"])
            expected.append((normalize(w["w"]), min(max(t, seg["out_start"]), seg["out_start"] + seg["duration"])))
    got = []
    for b in beats["beats"]:
        for w in b["words"]:
            got.append((normalize(w["w"]), w["start"]))
    problems = []
    if len(got) != len(expected):
        problems.append(f"{len(got)} caption words vs {len(expected)} transcript words")
    worst = 0.0
    for (gw, gt), (ew, et) in zip(got, expected):
        if gw != ew:
            problems.append(f"word mismatch at {fmt_time(gt)}: caption '{gw}' vs transcript '{ew}'")
            break
        worst = max(worst, abs(gt - et))
    if worst > 0.1:
        problems.append(f"a caption word is {worst:.2f}s off its transcript time")
    grid = 0.0
    for b, m in zip(beats["beats"], manifest["beats"]):
        grid = max(grid, abs(m["frames"][0] / FPS - b["start"]))
    if grid > 0.05:
        problems.append(f"beat start off the frame grid by {grid:.3f}s")
    detail = f"{len(got)} words · worst offset {worst * 1000:.0f} ms · frame grid {grid * 1000:.0f} ms"
    return {"status": "fail" if problems else "pass", "detail": detail, "items": problems, "worst_offset": round(worst, 3)}


def check_face_clear(job: Job, profile: dict, style: dict, beats: dict, cuts: dict, face: dict | None) -> dict:
    if not face:
        return {"status": "warn", "detail": "no face.json", "items": ["face tracking not run"]}
    segs = {s["id"]: s for s in face["segments"]}
    sources = {s["index"]: s for s in face["sources"]}
    cut_segs = cuts["segments"]
    layouts = style.get("layouts", {})

    def face_box_at(t_out: float) -> list[float] | None:
        seg = next((s for s in cut_segs if s["out_start"] <= t_out < s["out_start"] + s["duration"]), cut_segs[-1])
        fseg = segs.get(seg["id"])
        if not fseg or not fseg.get("track"):
            return None
        t_src = seg["start"] + (t_out - seg["out_start"])
        track = fseg["track"]
        row = min(track, key=lambda r: abs(r[0] - t_src))
        src = sources[seg["source"]]
        crop = fseg["crop"]
        x = row[1] * src["width"] - crop["x"]
        y = row[2] * src["height"] - crop["y"]
        w = row[3] * src["width"]
        h = row[4] * src["height"]
        sx, sy = WIDTH / crop["w"], HEIGHT / crop["h"]
        return [x * sx, y * sy, w * sx, h * sy]

    def in_card(box: list[float], card: dict) -> list[float]:
        cx, cy, cw, ch = card["x"] * WIDTH, card["y"] * HEIGHT, card["w"] * WIDTH, card["h"] * HEIGHT
        scale = max(cw / WIDTH, ch / HEIGHT)
        dx, dy = cx + (cw - WIDTH * scale) / 2, cy + (ch - HEIGHT * scale) / 2
        return [dx + box[0] * scale, dy + box[1] * scale, box[2] * scale, box[3] * scale]

    def overlap(a, b) -> float:
        if not a or not b:
            return 0.0
        ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
        iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
        return ix * iy

    items = []
    worst = 0.0
    samples = 0
    n = len(beats["beats"])
    with Renderer() as r:
        for i, b in enumerate(beats["beats"]):
            kind = layout_kind(style, b["layout"])
            if kind == "stage":      # no footage on screen, so no face to cover
                continue
            prev, nxt = neighbours(beats["beats"], i, bool(beats.get("end_card")))
            spec = beat_spec(b, profile, style, is_first=(i == 0), is_last=(i == n - 1), job_dir=job.path,
                             prev_layout=prev, next_layout=nxt)
            r.setup(spec)
            times = sorted({round(x, 2) for x in np.arange(0.0, b["duration"], 0.2)} | {round(min(b["duration"] - 0.01, widget_in_time(spec) + 0.4), 2)})
            beat_worst = 0.0
            for t in times:
                boxes = r.boxes(t)
                fb = face_box_at(b["start"] + t)
                if fb is None:
                    continue
                if kind == "card":
                    fb = in_card(fb, layouts[b["layout"]]["footage_card"])
                samples += 1
                for name in ("widget", "captions"):
                    ov = overlap(fb, boxes.get(name))
                    if ov > 4:  # a few px² is rounding
                        beat_worst = max(beat_worst, ov)
                        items.append({"beat": b["id"], "t": round(b["start"] + t, 2), "element": name, "overlap_px2": int(ov)})
            worst = max(worst, beat_worst)
    failing = sorted({it["beat"] for it in items})
    detail = f"{samples} frames sampled · {len(failing)} beats overlap the face" if failing else f"{samples} frames sampled · nothing covers the face"
    return {"status": "fail" if failing else "pass", "detail": detail, "items": [f"beat {it['beat']} at {fmt_time(it['t'])}: {it['element']} covers the face ({it['overlap_px2']} px²)" for it in items[:12]],
            "beats": failing}


def check_clean_cuts(job: Job, cuts: dict) -> dict:
    base = job.path / "renders" / "base.mp4"
    if not base.is_file():
        return {"status": "warn", "detail": "no base video", "items": []}
    sr = 48000
    x = decode_audio(base, sr)
    env = aa.envelope(x, sr)
    floor = float(np.percentile(env.db, 10))
    diffs = np.abs(np.diff(x))
    p99 = float(np.percentile(diffs, 99.9)) if len(diffs) else 0.0
    problems = []
    boundaries = [s["out_start"] for s in cuts["segments"][1:]]
    for bnd in boundaries:
        i = int(bnd * sr)
        before = x[max(0, i - int(0.03 * sr)):i]
        after = x[i:i + int(0.03 * sr)]
        if len(before) == 0 or len(after) == 0:
            continue
        db_before = float(aa.to_db(np.sqrt(np.mean(before ** 2))))
        db_after = float(aa.to_db(np.sqrt(np.mean(after ** 2))))
        if db_before > floor + 18 and db_after > floor + 18:
            problems.append(f"cut at {fmt_time(bnd)} lands on speech ({db_before:.0f} / {db_after:.0f} dBFS)")
        window = diffs[max(0, i - int(0.002 * sr)):i + int(0.002 * sr)]
        if len(window) and window.max() > max(0.25, 8 * p99):
            problems.append(f"click at the cut at {fmt_time(bnd)}")
    detail = f"{len(boundaries)} cuts checked · room {floor:.0f} dBFS"
    return {"status": "fail" if problems else "pass", "detail": detail, "items": problems}


def check_black_frozen(job: Job, video: Path, beats: dict, manifest: dict, style: dict) -> dict:
    """Black runs on the whole render; frozen runs per beat, on the footage region only."""
    res = compose.black_frozen(video, duration=float(beats["duration"]))
    stage_spans = [(b["start"], b["end"]) for b in beats["beats"] if layout_kind(style, b["layout"]) == "stage"]
    # a dark stage is the look of a stage beat, not a fault: only black outside those beats counts
    black = [(a, e) for a, e in res["black"] if not any(s - 0.05 <= a and e <= t + 0.05 for s, t in stage_spans)]
    problems = [f"black from {fmt_time(a)} to {fmt_time(b)}" for a, b in black]
    beats_hit = sorted({b["id"] for b in beats["beats"] for a, e in black if a < b["end"] and e > b["start"]})
    layouts = style.get("layouts", {})
    cache = job.path / "renders" / "cache"
    by_id = {b["id"]: b for b in beats["beats"]}
    for m in manifest["beats"]:
        b = by_id.get(m["id"])
        clip = cache / f"{m['key']}.mp4"
        if not b or not clip.is_file() or b["duration"] < 0.25:
            continue
        crop = None
        kind = layout_kind(style, b["layout"])
        if kind == "stage":      # nothing filmed is on screen; the graphics are checked by the visual comparison
            continue
        card = layouts.get(b["layout"], {}).get("footage_card")
        if kind == "card" and card:
            crop = (int(card["w"] * WIDTH) // 2 * 2, int(card["h"] * HEIGHT) // 2 * 2, int(card["x"] * WIDTH), int(card["y"] * HEIGHT))
        r = compose.black_frozen(clip, duration=None, crop=crop)
        for a, e in r["frozen"]:
            problems.append(f"frozen from {fmt_time(b['start'] + a)} to {fmt_time(b['start'] + e)} (beat {b['id']})")
            beats_hit.append(b["id"])
    beats_hit = sorted(set(beats_hit))
    return {"status": "fail" if problems else "pass", "detail": "no black or frozen runs" if not problems else f"{len(problems)} runs",
            "items": problems, "beats": beats_hit}


def check_visual(job: Job, video: Path, beats: dict, stills: dict | None, version: int) -> dict:
    if not stills:
        return {"status": "warn", "detail": "no stills to compare with", "items": []}
    from PIL import Image

    out_dir = job.path / "renders" / f"v{version}-check"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "frames").mkdir(parents=True)
    (out_dir / "pairs").mkdir()
    flagged = []
    scores = []
    tiles = []
    for s in stills["stills"]:
        still_path = job.path / s["file"]
        if not still_path.is_file():
            continue
        t = round(round(s["time"] * FPS) / FPS, 4)
        frame_path = out_dir / "frames" / f"{s['id']}.jpg"
        compose.extract_frame(video, t, frame_path)
        a = np.asarray(Image.open(still_path).convert("L").resize((180, 320)), dtype=np.float32)
        b = np.asarray(Image.open(frame_path).convert("L").resize((180, 320)), dtype=np.float32)
        mad = float(np.mean(np.abs(a - b)))
        scores.append({"id": s["id"], "mad": round(mad, 2)})
        tiles.append(frame_path)
        if mad > VISUAL_THRESHOLD:
            flagged.append({"id": s["id"], "mad": round(mad, 2), "pair": f"renders/v{version}-check/pairs/{s['id']}.jpg"})
            pair = Image.new("RGB", (WIDTH * 2 // 4, HEIGHT // 4))
            pair.paste(Image.open(still_path).convert("RGB").resize((WIDTH // 4, HEIGHT // 4)), (0, 0))
            pair.paste(Image.open(frame_path).convert("RGB").resize((WIDTH // 4, HEIGHT // 4)), (WIDTH // 4, 0))
            pair.save(out_dir / "pairs" / f"{s['id']}.jpg", quality=88)
    if tiles:
        cols = 8
        tw, th = 180, 320
        rows = (len(tiles) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tw, rows * th), (16, 16, 20))
        for k, p in enumerate(tiles):
            sheet.paste(Image.open(p).convert("RGB").resize((tw, th)), ((k % cols) * tw, (k // cols) * th))
        sheet.save(out_dir / "sheet.jpg", quality=85)
    worst = max((s["mad"] for s in scores), default=0.0)
    detail = f"{len(scores)} frames compared with the approved stills · worst difference {worst:.1f}/255"
    if flagged:
        detail += f" · {len(flagged)} to look at"
    return {"status": "review" if flagged else "pass", "detail": detail,
            "items": [f"beat {f['id']}: differs from its still ({f['mad']:.1f}) — see {f['pair']}" for f in flagged],
            "flagged": flagged, "sheet": f"renders/v{version}-check/sheet.jpg" if tiles else None}


# -- fixes -------------------------------------------------------------------------

def apply_fixes(job: Job, profile: dict, version: int, result: dict) -> list[dict]:
    """Fix what the engine can, re-rendering the same version in place. Returns what was done."""
    from . import render as render_stage

    fixes: list[dict] = []
    checks = result["checks"]
    beats = job.read_json("beats.json")
    changed_beats: set[int] = set()

    if checks["face_clear"]["status"] == "fail":
        for b in beats["beats"]:
            if b["id"] in checks["face_clear"].get("beats", []):
                r = b.setdefault("render", {})
                prev_shrink = float(r.get("shrink", 1.0))
                if b["layout"] == "face-full" and not r.get("anchor_flipped"):
                    current = r.get("anchor") or (b.get("graphic") or {}).get("anchor", "top")
                    r["anchor"] = "bottom" if current == "top" else "top"
                    r["anchor_flipped"] = True
                r["shrink"] = round(prev_shrink * 0.8, 2)
                changed_beats.add(b["id"])
        if changed_beats:
            fixes.append({"check": "face_clear", "what": f"moved/shrunk the widget on beats {sorted(changed_beats)}"})

    if checks["black_frozen"]["status"] == "fail":
        for b in beats["beats"]:
            if b["id"] in checks["black_frozen"].get("beats", []):
                r = b.setdefault("render", {})
                r["nonce"] = int(r.get("nonce", 0)) + 1
                changed_beats.add(b["id"])
        if checks["black_frozen"].get("beats"):
            fixes.append({"check": "black_frozen", "what": f"re-rendered beats {checks['black_frozen']['beats']}"})

    need_render = bool(changed_beats)
    if need_render:
        job.write_json("beats.json", beats)
        render_stage.run(job, profile, show=False, version=version, run_check=False)

    if checks["audio_level"]["status"] == "fail" or (checks["format"]["status"] == "fail" and not need_render):
        manifest = job.read_json(f"renders/v{version}.json")
        video = job.path / manifest["file"]
        measured = checks["audio_level"].get("measured") or compose.loudness(video)
        if measured.get("lufs") is not None:
            tmp = video.with_name(f"v{version}-remix.mp4")
            compose.remix_audio(video, job.path / "renders" / "base.mp4", tmp, total=float(manifest["duration"]),
                                music=compose.find_music(job, profile),
                                ducking_db=float(get(profile, "music.ducking_db", -18)), measured=measured)
            tmp.replace(video)
            fixes.append({"check": "audio_level", "what": f"re-mixed the audio from {measured['lufs']:.1f} LUFS with a measured second pass"})
    return fixes


# -- report ------------------------------------------------------------------------

def report(job: Job, data: dict) -> None:
    colour = {"pass": "green", "fail": "red", "review": "yellow"}[data["status"]]
    console.print(f"\nQuality check v{data['version']}: [{colour} bold]{data['status'].upper()}[/]")
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("check")
    table.add_column("result")
    table.add_column("detail")
    labels = {"format": "Format", "audio_level": "Audio level", "caption_sync": "Caption sync", "face_clear": "Face clear",
              "clean_cuts": "Clean cuts", "black_frozen": "Black / frozen", "visual_review": "Visual review"}
    for key, c in data["checks"].items():
        mark = {"pass": "[green]✔ pass[/]", "fail": "[red]✖ fail[/]", "review": "[yellow]! review[/]", "warn": "[yellow]! skipped[/]"}[c["status"]]
        table.add_row(labels.get(key, key), mark, c["detail"])
    console.print(table)
    for key, c in data["checks"].items():
        for it in c.get("items", []):
            console.print(f"    {labels.get(key, key)}: {it}")
    for f in data.get("fixes", []):
        console.print(f"  [cyan]fixed:[/] {f['what']}")
