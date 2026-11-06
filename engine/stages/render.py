"""Stage 10 — render.

Every beat is rendered on its own (frame by frame, same renderer as the stills)
and cached by a fingerprint of everything that went into it, so a revision only
re-renders the beats that changed. The beats are joined and the end card added.
Then the sound: the voice, music under it (a licensed track when one is supplied,
otherwise a bed the engine makes for this reel) and the effects the style asked
for at its moves, mixed to −14 LUFS. The result is renders/vN.mp4, 1080×1920 H.264.
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

from .. import music as music_engine
from .. import sound
from ..job import Job, JobError, console, fmt_clock
from ..profile import ROOT, get
from ..render import compose
from ..render.driver import Renderer, beat_spec, end_card_seconds, end_card_spec, layout_kind, neighbours
from .beats import load_style

FPS = compose.FPS
END_CARD_SECONDS = 1.5
RENDERER_FILES = [ROOT / "renderer" / "page.html", ROOT / "renderer" / "render.js"]


def run(job: Job, profile: dict, show: bool = False, workers: int | None = None,
        version: int | None = None, run_check: bool = True) -> dict:
    """Render the reel. `version` re-renders an existing version in place (quality-check fixes);
    `run_check` runs the automatic quality check on the result."""
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
        prev, nxt = neighbours(beats, i, bool(beats_doc.get("end_card")))
        spec = beat_spec(beat, profile, style, is_first=(i == 0), is_last=(i == len(beats) - 1), job_dir=job.path,
                         prev_layout=prev, next_layout=nxt)
        key = _key(spec, f0, f1, base_fp, renderer_fp)
        items.append({"id": beat["id"], "name": beat["name"], "f0": f0, "f1": f1, "spec": spec, "key": key,
                      "out": str(cache / f"{key}.mp4"), "base": str(base),
                      "footage": layout_kind(style, beat["layout"]) != "stage"})   # a stage beat shows no footage
    end_item = None
    END_CARD_SECONDS = end_card_seconds(style)
    if beats_doc.get("end_card"):
        spec = end_card_spec(profile, style, END_CARD_SECONDS, prev_layout=beats[-1]["layout"])
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
    if version is None:
        existing = [int(p.stem[1:]) for p in (job.path / "renders").glob("v*.json") if p.stem[1:].isdigit()]
        version = (max(existing) + 1) if existing else 1
    out = job.path / "renders" / f"v{version}.mp4"
    joined = job.path / "renders" / f"v{version}-video.mp4"
    parts = [Path(it["out"]) for it in items] + ([Path(end_item["out"])] if end_item else [])
    job.log("  joining beats…")
    compose.concat_videos(parts, joined)
    total = beats_doc["duration"] + (END_CARD_SECONDS if end_item else 0.0)
    audio = plan_audio(job, profile, beats_doc, items, end_item, total, version)
    mixed = compose.mix_and_mux(joined, base, out, total=total, music=audio["music_path"], sfx=audio["sfx_path"],
                                voice=audio["voice"], ducking_db=audio["ducking_db"], music_db=audio["music_db"],
                                sfx_db=audio["sfx_db"], music_is_bed=audio["music_source"] == "engine")
    job.log("  audio: " + audio_line(audio))
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
        "music": audio["music_title"],
        "audio": {"voice": audio["voice"], "music": audio["music"], "music_source": audio["music_source"],
                  "music_title": audio["music_title"], "sfx": audio["sfx"], "effects": audio["effects"],
                  "ducking_db": audio["ducking_db"], "music_db": audio["music_db"], "sfx_db": audio["sfx_db"],
                  "gain_db": mixed.get("gain_db"), "limited": mixed.get("limited")},
        "end_card": bool(end_item),
        "render_seconds": seconds,
        "beats_version": beats_doc.get("version", 1),
        "changes": [c for c in beats_doc.get("changes", []) if c.get("version") == beats_doc.get("version", 1)],
        "beats": [{"id": it["id"], "name": it["name"], "frames": [it["f0"], it["f1"]], "key": it["key"],
                   "rendered": str(it["id"]) in timings, "seconds": timings.get(str(it["id"]))} for it in items],
    }
    job.write_json(f"renders/v{version}.json", manifest)
    u = job.state.setdefault("usage", {})
    u["render_seconds"] = round(u.get("render_seconds", 0) + seconds, 1)
    job.save_state()
    _prune_cache(job, keep_versions=2)
    job.log(f"  → renders/{out.name} ({fmt_clock(total)}, {seconds}s)")

    if run_check:
        from . import check as check_stage

        job.log("  quality check…")
        result = check_stage.run(job, profile, show=show, version=version, fix=True)
        manifest = job.read_json(f"renders/v{version}.json")
        manifest["check"] = result["status"]
        job.write_json(f"renders/v{version}.json", manifest)
    if show:
        report(job, manifest)
    return manifest


def _prune_cache(job: Job, keep_versions: int = 2) -> None:
    """Drop cached beats that no recent version uses."""
    renders = job.path / "renders"
    manifests = sorted((p for p in renders.glob("v*.json") if p.stem[1:].isdigit()), key=lambda p: int(p.stem[1:]))
    keep: set[str] = set()
    for p in manifests[-keep_versions:]:
        try:
            keep.update(b["key"] for b in json.loads(p.read_text())["beats"])
        except (ValueError, KeyError):
            pass
    for p in (renders / "cache").glob("*.mp4"):
        if p.stem not in keep and len(keep) > 0:
            # the end card's key is not in beats[]; keep anything younger than a day to be safe
            if time.time() - p.stat().st_mtime > 86400:
                p.unlink(missing_ok=True)
                p.with_suffix(".json").unlink(missing_ok=True)      # its sound cues
    # the effects track and the engine's music of versions that are gone
    live = {p.stem for p in manifests[-keep_versions:]}
    used = set()
    for p in manifests[-keep_versions:]:
        try:
            used.add(Path(str((json.loads(p.read_text()).get("audio") or {}).get("music") or "")).name)
        except ValueError:
            pass
    for p in renders.glob("v*-sfx.wav"):
        if p.stem.split("-")[0] not in live:
            p.unlink(missing_ok=True)
    for p in renders.glob("music-*.wav"):
        if p.name not in used and time.time() - p.stat().st_mtime > 3600:
            p.unlink(missing_ok=True)


def plan_audio(job: Job, profile: dict, beats_doc: dict, items: list[dict], end_item: dict | None, total: float, version: int) -> dict:
    """What this version sounds like besides the voice.

    Music: a licensed track in the job's music/ folder or the client's wins; otherwise the engine
    makes a bed in the mood of the beat plan, to the length of the reel; "none" (on the job, or as the
    client's music.source) means no music. Effects: the sounds every beat asked for when it was
    rendered, laid on one track."""
    wish = (job.state.get("meta") or {}).get("sound") or {}
    script = job.read_json("script.json") if job.has("script.json") else None
    voice = not (script and not script.get("voice"))            # an infographic made from the script alone has no voice
    source = str(wish.get("music") or get(profile, "music.source", "engine"))
    out = {"voice": voice, "music": None, "music_path": None, "music_source": None, "music_title": None,
           "sfx": None, "sfx_path": None, "effects": 0,
           "ducking_db": float(get(profile, "music.ducking_db", -18)), "music_db": float(get(profile, "music.alone_db", -4)),
           "sfx_db": float(get(profile, "sound.effects_db", -10))}

    def rel(p: Path) -> str:
        try:
            return str(p.resolve().relative_to(job.path.resolve()))
        except ValueError:
            return str(p.resolve())

    track = compose.find_music(job, profile) if source != "none" else None
    end_at = beats_doc["duration"] if end_item else None
    if track:
        out.update(music=rel(track), music_path=track, music_source="file", music_title=track.name)
    elif source == "engine":
        m = beats_doc.get("music") or {}
        choice = music_engine.choose(str(m.get("mood") or ""), m.get("bpm"), str(m.get("energy") or "medium"))
        bed = music_engine.bed_file(job.path / "renders", choice, total, end_at)
        out.update(music=rel(bed), music_path=bed, music_source="engine",
                   music_title=f"{choice['title']}, {choice['bpm']:g} bpm, made by the engine")

    mode = wish.get("effects", get(profile, "sound.effects", "off"))
    mode = {True: "library", False: "off"}.get(mode, str(mode))        # yaml reads a bare off/on as false/true
    if mode in ("library", "engine"):
        folders = [ROOT / "assets" / "sfx"] + ([Path(profile["_path"]).parent / "assets" / "sfx"] if profile.get("_path") else [])
        library = sound.load_library(folders)
        cues = []
        for it in items + ([end_item] if end_item else []):
            start = beats_doc["duration"] if it is end_item else it["f0"] / FPS
            length = (it["f1"] - it["f0"]) / FPS
            f = Path(it["out"]).with_suffix(".json")
            if not f.is_file():
                continue
            try:
                beat_cues = json.loads(f.read_text())
            except ValueError:
                continue
            for c in beat_cues:
                cues.append({"t": start + min(float(c.get("t", 0)), length), "name": c.get("name"), "gain": float(c.get("gain", 1))})
        track_audio = sound.build_track(cues, total, library=library, synth=(mode == "engine"))
        if track_audio is not None:
            f = sound.write_wav(job.path / "renders" / f"v{version}-sfx.wav", track_audio)
            played = sum(1 for c in cues if c["name"] in library or (mode == "engine" and c["name"] in sound.EFFECTS))
            out.update(sfx=rel(f), sfx_path=f, effects=played)
    return out


def audio_line(a: dict) -> str:
    """One honest line about what is in the mix."""
    parts = ["voice" if a["voice"] else "no voice"]
    if a["music_source"] == "engine":
        parts.append(f"music {a['music_title']}" + (f", {a['ducking_db']:g} dB under the voice" if a["voice"] else ""))
    elif a["music_source"] == "file":
        parts.append(f"music {a['music_title']}" + (f", {a['ducking_db']:g} dB under the voice" if a["voice"] else ""))
    else:
        parts.append("no music")
    parts.append(f"{a['effects']} sound effects" if a["effects"] else "no sound effects")
    if not a["voice"] and not a["music"]:
        return ", ".join(parts) + " (nothing to set a level to)"
    return ", ".join(parts) + ", mixed to -14 LUFS"


def _key(spec: dict, f0: int, f1: int, base_fp: str, renderer_fp: str) -> str:
    payload = json.dumps({"spec": spec, "frames": [f0, f1], "base": base_fp, "renderer": renderer_fp,
                          "encode": compose.ENCODE_VERSION}, sort_keys=True)
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def _renderer_fingerprint(style: dict) -> str:
    h = hashlib.sha1()
    for p in RENDERER_FILES + [ROOT / "styles" / style["name"] / "components.css", ROOT / "styles" / style["name"] / "components.js"]:
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
    cues = r.cues()                 # the sounds this beat asks for; kept beside the cached clip
    for k in range(count):
        t = k / FPS
        src = frames_in / f"{k:05d}.jpg" if item["footage"] else None
        if src is not None and not src.is_file():       # ffmpeg can come up a frame short at the very end
            src = frames_in / f"{max(0, k - 1):05d}.jpg"
        r.frame(t, src, frames_out / f"{k:05d}.jpg", quality=93)
    tmp_out = tmp / "beat.mp4"
    compose.encode_frames(frames_out, tmp_out)
    Path(item["out"]).with_suffix(".json").write_text(json.dumps(cues))
    shutil.move(str(tmp_out), item["out"])
    shutil.rmtree(tmp, ignore_errors=True)
    return round(time.time() - t0, 1)


def report(job: Job, data: dict) -> None:
    console.print(f"\nRender v{data['version']}: {data['file']} · {fmt_clock(data['duration'])} · "
                  f"{data['size'][0]}×{data['size'][1]} @ {data['fps']} fps · {data['render_seconds']}s"
                  + (f" · music {data['music']}" if data.get("music") else " · no music")
                  + (f" · quality check {data['check'].upper()}" if data.get("check") else ""))
    rendered = [b for b in data["beats"] if b["rendered"]]
    console.print(f"  {len(rendered)} beats rendered, {len(data['beats']) - len(rendered)} reused from cache")
    for c in data.get("changes", []):
        console.print(f"  changed: beat {c.get('beat', '—')} — {c.get('change')}  ({c.get('comment')})")
