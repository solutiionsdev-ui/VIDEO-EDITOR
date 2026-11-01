"""Stage 6 — face tracking.

Finds the face box on every frame of the kept segments (MediaPipe, on the CPU).
Used for the 9:16 crop and as a no-go zone that graphics and captions must
never cover. Writes face.json and previews/face-preview.mp4.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import numpy as np
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from ..job import Job, JobError, console, fmt_time
from ..media import FrameWriter, iter_frames, preview_size, probe
from ..profile import get

OUTPUT = "face.json"
PREVIEW = "previews/face-preview.mp4"
MODEL = Path(__file__).resolve().parent.parent / "models" / "blaze_face_short_range.tflite"


def run(job: Job, profile: dict, show: bool = False, preview_video: bool = True) -> dict:
    cuts = job.read_json("cuts.json")
    transcript = job.read_json("transcript.json")
    detect_width = int(get(profile, "face.detect_width", 384))
    margin = float(get(profile, "face.no_go_margin", 0.06))
    max_gap = float(get(profile, "face.max_gap_s", 1.0))
    if not MODEL.is_file():
        raise JobError(f"Face model missing: {MODEL}")

    sources = {}
    for s in transcript["sources"]:
        info = probe(job.raw_dir / s["file"])
        v = info.video
        sources[s["index"]] = {"index": s["index"], "file": s["file"], "width": v.display_width,
                               "height": v.display_height, "fps": round(v.fps, 3)}

    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    with _quiet_stderr():
        detector = vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(MODEL)),
            running_mode=vision.RunningMode.VIDEO,
            min_detection_confidence=0.5))

    writer: FrameWriter | None = None
    ts_ms = 0
    out_segments = []
    total_frames = 0
    detected_frames = 0
    total_seconds = sum(s["duration"] for s in cuts["segments"])
    with Progress(TextColumn("  tracking faces"), BarColumn(), TextColumn("{task.percentage:>3.0f}%"),
                  TimeElapsedColumn(), console=console, transient=True) as progress:
        task = progress.add_task("f", total=max(0.01, total_seconds))
        done_seconds = 0.0
        for seg in cuts["segments"]:
            src = sources[seg["source"]]
            size = (src["width"], src["height"])
            fps = src["fps"] or 30.0
            pw, ph = preview_size(size, detect_width)
            if preview_video and writer is None:
                writer = FrameWriter(job.path / PREVIEW, pw, ph, fps)
            raw_boxes: list[list[float] | None] = []
            times: list[float] = []
            for t, frame in iter_frames(job.raw_dir / src["file"], seg["start"], seg["end"], detect_width, size, fps):
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                ts_ms += max(1, int(round(1000 / fps)))
                result = detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts_ms)
                raw_boxes.append(_best_box(result, pw, ph))
                times.append(round(t, 3))
            total_frames += len(raw_boxes)
            detected_frames += sum(1 for b in raw_boxes if b is not None)
            filled, coverage = _fill_gaps(raw_boxes, fps, max_gap)
            track = _smooth(filled, window=int(fps / 2) | 1) if coverage > 0 else []
            no_go = _no_go(track, margin)
            crop = _crop_9x16(track, src["width"], src["height"])
            out_segments.append({
                "id": seg["id"],
                "source": seg["source"],
                "start": seg["start"],
                "end": seg["end"],
                "fps": fps,
                "coverage": round(coverage, 3),
                "no_go": no_go,
                "crop": crop,
                "face_center": _center(track),
                "face_size": round(float(np.mean([b[2] for b in track])), 3) if track else None,
                "frames": [[times[i]] + ([round(x, 4) for x in raw_boxes[i]] if raw_boxes[i] else [None] * 5)
                           for i in range(len(times))],
                "track": [[times[i]] + [round(x, 4) for x in track[i][:4]] for i in range(len(track))],
            })
            if preview_video and writer is not None:
                # Second, cheap decode pass: the smoothed track needs the whole segment first.
                for i, (_, frame) in enumerate(iter_frames(job.raw_dir / src["file"], seg["start"], seg["end"],
                                                           detect_width, size, fps)):
                    if i >= len(raw_boxes):
                        break
                    frame = frame.copy()   # decoded frames are read-only buffers
                    _draw(frame, raw_boxes[i], track[i] if i < len(track) else None, no_go, crop, src, pw, ph, seg["id"])
                    if (frame.shape[1], frame.shape[0]) != (writer.width, writer.height):
                        frame = cv2.resize(frame, (writer.width, writer.height))
                    writer.write(frame)
            done_seconds += seg["duration"]
            progress.update(task, completed=done_seconds)
    detector.close()
    if writer is not None:
        writer.close()

    low = [s for s in out_segments if 0 < s["coverage"] < 0.5]
    result = {
        "sources": list(sources.values()),
        "settings": {"detect_width": detect_width, "no_go_margin": margin, "max_gap_s": max_gap},
        "segments": out_segments,
        "summary": {
            "segments": len(out_segments),
            "frames": total_frames,
            "detected_fraction": round(detected_frames / total_frames, 3) if total_frames else 0.0,
            "segments_without_face": [s["id"] for s in out_segments if s["coverage"] == 0],
            "segments_low_coverage": [s["id"] for s in low],
        },
    }
    job.write_json(OUTPUT, result)
    job.log(f"  {total_frames} frames, face found in {result['summary']['detected_fraction']:.0%}")
    if preview_video:
        job.log(f"  preview: {PREVIEW}")
    if show:
        report(job, result)
    return result


@contextlib.contextmanager
def _quiet_stderr():
    """MediaPipe prints hardware chatter to stderr on start-up; hide it."""
    fd = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 2)
    try:
        yield
    finally:
        os.dup2(fd, 2)
        os.close(devnull)
        os.close(fd)


def _best_box(result, w: int, h: int) -> list[float] | None:
    best = None
    best_key = -1.0
    for d in result.detections:
        b = d.bounding_box
        score = d.categories[0].score if d.categories else 0.0
        key = score * (b.width * b.height)
        if key > best_key:
            best_key = key
            best = [b.origin_x / w, b.origin_y / h, b.width / w, b.height / h, float(score)]
    if best is None:
        return None
    return [min(max(best[0], 0.0), 1.0), min(max(best[1], 0.0), 1.0), best[2], best[3], best[4]]


def _fill_gaps(boxes: list[list[float] | None], fps: float, max_gap: float) -> tuple[list[list[float]], float]:
    n = len(boxes)
    if n == 0:
        return [], 0.0
    known = [i for i, b in enumerate(boxes) if b is not None]
    if not known:
        return [[0.5, 0.35, 0.0, 0.0, 0.0]] * n, 0.0
    out: list[list[float]] = [None] * n  # type: ignore[list-item]
    max_frames = int(max_gap * fps)
    for i in range(n):
        if boxes[i] is not None:
            out[i] = list(boxes[i])
            continue
        prev = max((k for k in known if k < i), default=None)
        nxt = min((k for k in known if k > i), default=None)
        if prev is not None and nxt is not None and (nxt - prev) <= max_frames:
            a, b = boxes[prev], boxes[nxt]
            f = (i - prev) / (nxt - prev)
            out[i] = [a[k] + (b[k] - a[k]) * f for k in range(4)] + [0.0]
        else:
            src = boxes[prev] if prev is not None else boxes[nxt]
            out[i] = list(src[:4]) + [0.0]
    return out, len(known) / n


def _smooth(boxes: list[list[float]], window: int) -> list[list[float]]:
    if not boxes:
        return []
    arr = np.array([b[:4] for b in boxes], dtype=np.float64)
    half = max(1, window // 2)
    out = np.empty_like(arr)
    for i in range(len(arr)):
        lo, hi = max(0, i - half), min(len(arr), i + half + 1)
        out[i] = np.median(arr[lo:hi], axis=0)
    return [list(map(float, row)) for row in out]


def _no_go(track: list[list[float]], margin: float) -> list[float] | None:
    if not track:
        return None
    arr = np.array(track)
    x1 = float(np.percentile(arr[:, 0], 2)) - margin
    y1 = float(np.percentile(arr[:, 1], 2)) - margin * 1.5
    x2 = float(np.percentile(arr[:, 0] + arr[:, 2], 98)) + margin
    y2 = float(np.percentile(arr[:, 1] + arr[:, 3], 98)) + margin * 1.5
    return [round(max(0.0, x1), 4), round(max(0.0, y1), 4), round(min(1.0, x2), 4), round(min(1.0, y2), 4)]


def _center(track: list[list[float]]) -> list[float] | None:
    if not track:
        return None
    arr = np.array(track)
    return [round(float(np.median(arr[:, 0] + arr[:, 2] / 2)), 4), round(float(np.median(arr[:, 1] + arr[:, 3] / 2)), 4)]


def _crop_9x16(track: list[list[float]], width: int, height: int) -> dict:
    """A fixed 9:16 crop for this segment, centred on the face (in source pixels)."""
    target = 9 / 16
    if abs(width / height - target) < 0.01:
        return {"x": 0, "y": 0, "w": width, "h": height, "note": "already 9:16"}
    c = _center(track) or [0.5, 0.4]
    if width / height > target:          # wider than 9:16 (landscape or square): full height, crop the sides
        cw, ch = int(round(height * target)), height
        x = int(round(c[0] * width - cw / 2))
        x = min(max(0, x), width - cw)
        return {"x": x, "y": 0, "w": cw, "h": ch, "note": "centred on face"}
    cw, ch = width, int(round(width / target))   # taller than 9:16: full width, crop top/bottom
    y = int(round(c[1] * height - ch * 0.4))
    y = min(max(0, y), height - ch)
    return {"x": 0, "y": y, "w": cw, "h": ch, "note": "centred on face"}


def _draw(frame: np.ndarray, raw, smooth, no_go, crop, src, pw: int, ph: int, seg_id: int) -> None:
    import cv2

    if no_go:
        overlay = frame.copy()
        cv2.rectangle(overlay, (int(no_go[0] * pw), int(no_go[1] * ph)), (int(no_go[2] * pw), int(no_go[3] * ph)),
                      (0, 0, 255), -1)
        cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)
        cv2.rectangle(frame, (int(no_go[0] * pw), int(no_go[1] * ph)), (int(no_go[2] * pw), int(no_go[3] * ph)),
                      (0, 0, 255), 1)
    if crop and crop.get("note") != "already 9:16":
        sx, sy = pw / src["width"], ph / src["height"]
        cv2.rectangle(frame, (int(crop["x"] * sx), int(crop["y"] * sy)),
                      (int((crop["x"] + crop["w"]) * sx), int((crop["y"] + crop["h"]) * sy)), (255, 200, 0), 1)
    if smooth:
        cv2.rectangle(frame, (int(smooth[0] * pw), int(smooth[1] * ph)),
                      (int((smooth[0] + smooth[2]) * pw), int((smooth[1] + smooth[3]) * ph)), (255, 128, 0), 2)
    if raw:
        cv2.rectangle(frame, (int(raw[0] * pw), int(raw[1] * ph)),
                      (int((raw[0] + raw[2]) * pw), int((raw[1] + raw[3]) * ph)), (0, 255, 0), 1)
    else:
        cv2.putText(frame, "no face", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, f"seg {seg_id + 1}", (8, ph - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)


def report(job: Job, data: dict) -> None:
    s = data["summary"]
    console.print(f"\nFace tracking: {s['segments']} segments · {s['frames']} frames · "
                  f"face found in [bold]{s['detected_fraction']:.0%}[/] of frames")
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("#", justify="right")
    table.add_column("time")
    table.add_column("found", justify="right")
    table.add_column("face centre")
    table.add_column("no-go zone (x1,y1 → x2,y2)")
    table.add_column("9:16 crop")
    for seg in data["segments"]:
        c = seg["face_center"]
        ng = seg["no_go"]
        crop = seg["crop"]
        table.add_row(str(seg["id"] + 1), f"{fmt_time(seg['start'])}–{fmt_time(seg['end'])}",
                      f"{seg['coverage']:.0%}",
                      f"{c[0]:.2f}, {c[1]:.2f}" if c else "—",
                      f"{ng[0]:.2f},{ng[1]:.2f} → {ng[2]:.2f},{ng[3]:.2f}" if ng else "—",
                      f"{crop['w']}x{crop['h']} @ {crop['x']},{crop['y']} ({crop['note']})")
    console.print(table)
    if s["segments_without_face"]:
        console.print(f"  [red]no face at all in segments:[/] {', '.join(str(i + 1) for i in s['segments_without_face'])}")
    if s["segments_low_coverage"]:
        console.print(f"  [yellow]face found in under half the frames of segments:[/] "
                      f"{', '.join(str(i + 1) for i in s['segments_low_coverage'])}")
    console.print(f"\n  preview video: {job.path / PREVIEW}")
