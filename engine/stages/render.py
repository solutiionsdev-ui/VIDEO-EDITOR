"""Stage 10 — render.

Every beat is rendered on its own (frame by frame, same renderer as the stills)
and cached by a fingerprint of everything that went into it, so a revision only
re-renders the beats that changed. The beats are joined, the end card added,
the voice loudness-normalised (−14 LUFS) with music ducked under it, and the
result exported as renders/vN.mp4, 1080×1920 H.264.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from ..job import Job, JobError, console, fmt_clock
from ..profile import ROOT, get
from ..render import compose
from ..render.driver import Renderer, beat_spec, end_card_spec
from .beats import load_style

FPS = compose.FPS
END_CARD_SECONDS = 1.5
RENDERER_FILES = [ROOT / "renderer" / "page.html", ROOT / "renderer" / "render.js"]


def run(job: Job, profile: dict, show: bool = False, workers: int | None = None) -> dict:
    beats_doc = job.read_json("beats.json")
    cuts = job.read_json("cuts.json")
    face = job.read_json("face.json") if job.has("face.json") else None
    style = load_style(str(beats_doc.get("style") or "ios"))
    beats = beats_doc["beats"]
    if not beats:
        raise JobError("beats.json has no beats")

    started = time.time()
    job.log("  base video…")
    base = compose.base_video(job, cuts, face)
    base_fp = json.loads((job.path / "renders" / "base.json").read_text())["fingerprint"]
    renderer_fp = _renderer_fingerprint(style)
    cache = job.path / "renders" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    total_frames = round(beats_doc["duration"] * FPS)

    items = []
    for i, beat in enumerate(beats):
        f0 = round(beat["start"] * FPS)
        f1 = total_frames if i == len(beats) - 1 else round(beats[i + 1]["start"] * FPS)
        spec = beat_spec(beat, profile, style, is_first=(i == 0), is_last=(i == len(beats) - 1))
        key = _key(spec, f0, f1, base_fp, renderer_fp)
        items.append({"id": beat["id"], "name": beat["name"], "f0": f0, "f1": f1, "spec": spec, "key": key,
                      "out": str(cache / f"{key}.mp4"), "base": str(base), "footage": True})
    end_item = None
    if beats_doc.get("end_card"):
        spec = end_card_spec(profile, style, END_CARD_SECONDS)
        key = _key(spec, 0, round(END_CARD_SECONDS * FPS), "none", renderer_fp)
        end_item = {"id": "end", "name": "End card", "f0": 0, "f1": round(END_CARD_SECONDS * FPS), "spec": spec, "key": key,
                    "out": str(cache / f"{key}.mp4"), "base": None, "footage": False}

    todo = [it for it in items + ([end_item] if end_item else []) if not Path(it["out"]).is_file()]
    reused = len(items) + (1 if end_item else 0) - len(todo)
    job.log(f"  {len(items)} beats · {len(todo)} to render · {reused} reused from cache")

    timings: dict = {}
    if todo:
        workers = workers or max(1, min(4, (os.cpu_count() or 4) // 3))
        tmp_root = job.path / "renders" / "tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        with Progress(TextColumn("  rendering beats"), BarColumn(), TextColumn("{task.completed}/{task.total}"),
                      TimeElapsedColumn(), console=console, transient=True) as progress:
            task = progress.add_task("r", total=len(todo))
            with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as pool:
                futures = {pool.submit(_render_item, it, str(tmp_root)): it for it in todo}
                for fut in as_completed(futures):
                    it = futures[fut]
                    try:
                        timings[str(it["id"])] = fut.result()
                    except Exception as e:  # noqa: BLE001
                        raise JobError(f"Rendering beat {it['id']} failed: {e}") from e
                    progress.advance(task)
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Join, then audio.
    version = 1 + len(list((job.path / "renders").glob("v*.mp4")))
    out = job.path / "renders" / f"v{version}.mp4"
    joined = job.path / "renders" / f"v{version}-video.mp4"
    parts = [Path(it["out"]) for it in items] + ([Path(end_item["out"])] if end_item else [])
    job.log("  joining beats…")
    compose.concat_videos(parts, joined)
    total = beats_doc["duration"] + (END_CARD_SECONDS if end_item else 0.0)
    music = compose.find_music(job, profile)
    job.log(f"  audio: voice normalised to -14 LUFS" + (f", music {music.name} ducked {get(profile, 'music.ducking_db', -18)} dB" if music else ", no music track supplied"))
    compose.mix_and_mux(joined, base, out, total=total, music=music,
                        ducking_db=float(get(profile, "music.ducking_db", -18)))
    joined.unlink(missing_ok=True)
    joined.with_suffix(".txt").unlink(missing_ok=True)

    seconds = round(time.time() - started, 1)
    manifest = {
        "version": version,
        "file": f"renders/{out.name}",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration": round(total, 3),
        "fps": FPS,
        "size": [compose.WIDTH, compose.HEIGHT],
        "music": music.name if music else None,
        "end_card": bool(end_item),
        "render_seconds": seconds,
        "beats": [{"id": it["id"], "name": it["name"], "frames": [it["f0"], it["f1"]], "key": it["key"],
                   "rendered": str(it["id"]) in timings, "seconds": timings.get(str(it["id"]))} for it in items],
    }
    job.write_json(f"renders/v{version}.json", manifest)
    u = job.state.setdefault("usage", {})
    u["render_seconds"] = round(u.get("render_seconds", 0) + seconds, 1)
    job.save_state()
    job.log(f"  → renders/{out.name} ({fmt_clock(total)}, {seconds}s)")
    if show:
        report(job, manifest)
    return manifest


def _key(spec: dict, f0: int, f1: int, base_fp: str, renderer_fp: str) -> str:
    payload = json.dumps({"spec": spec, "frames": [f0, f1], "base": base_fp, "renderer": renderer_fp,
                          "encode": compose.ENCODE_VERSION}, sort_keys=True)
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def _renderer_fingerprint(style: dict) -> str:
    h = hashlib.sha1()
    for p in RENDERER_FILES + [ROOT / "styles" / style["name"] / "components.css"]:
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


# -- worker process ------------------------------------------------------------

_RENDERER: Renderer | None = None


def _init_worker() -> None:
    global _RENDERER
    os.environ.setdefault("GLOG_minloglevel", "2")
    _RENDERER = Renderer()


def _render_item(item: dict, tmp_root: str) -> float:
    """Render one beat's frames, encode them, write the cached mp4. Runs in a worker process."""
    global _RENDERER
    r = _RENDERER or Renderer()
    t0 = time.time()
    f0, f1 = item["f0"], item["f1"]
    count = f1 - f0
    tmp = Path(tmp_root) / f"beat-{item['id']}-{os.getpid()}"
    frames_in = tmp / "in"
    frames_out = tmp / "out"
    shutil.rmtree(tmp, ignore_errors=True)
    frames_out.mkdir(parents=True)
    if item["footage"]:
        compose.extract_frames(Path(item["base"]), f0, count, frames_in)
    r.setup(item["spec"])
    for k in range(count):
        t = k / FPS
        src = frames_in / f"{k:05d}.jpg" if item["footage"] else None
        if src is not None and not src.is_file():       # ffmpeg can come up a frame short at the very end
            src = frames_in / f"{max(0, k - 1):05d}.jpg"
        r.frame(t, src, frames_out / f"{k:05d}.jpg", quality=93)
    tmp_out = tmp / "beat.mp4"
    compose.encode_frames(frames_out, tmp_out)
    shutil.move(str(tmp_out), item["out"])
    shutil.rmtree(tmp, ignore_errors=True)
    return round(time.time() - t0, 1)


def report(job: Job, data: dict) -> None:
    console.print(f"\nRender v{data['version']}: {data['file']} · {fmt_clock(data['duration'])} · "
                  f"{data['size'][0]}×{data['size'][1]} @ {data['fps']} fps · {data['render_seconds']}s"
                  + (f" · music {data['music']}" if data.get("music") else " · no music"))
    rendered = [b for b in data["beats"] if b["rendered"]]
    console.print(f"  {len(rendered)} beats rendered, {len(data['beats']) - len(rendered)} reused from cache")
