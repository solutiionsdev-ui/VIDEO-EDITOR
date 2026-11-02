"""ffmpeg compositing: the edited base video, frame extraction, encoding, joining, audio."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..job import Job, JobError
from ..media import ffmpeg_exe, probe, run_ffmpeg

FPS = 30
WIDTH, HEIGHT = 1080, 1920


def base_video(job: Job, cuts: dict, face: dict | None, fps: int = FPS) -> Path:
    """The cut applied to the raw footage at full quality, cropped to 9:16, constant fps.

    Cached in renders/base.mp4 next to a fingerprint of the cut; rebuilt only when the cut changes.
    """
    renders = job.path / "renders"
    renders.mkdir(exist_ok=True)
    out = renders / "base.mp4"
    stamp = renders / "base.json"
    crops = {s["id"]: s["crop"] for s in (face or {}).get("segments", [])}
    fingerprint = hashlib.sha1(json.dumps({
        "segments": [(s["source"], s["start"], s["end"]) for s in cuts["segments"]],
        "crops": {k: (v["x"], v["y"], v["w"], v["h"]) for k, v in crops.items()},
        "fps": fps, "size": [WIDTH, HEIGHT],
    }, sort_keys=True).encode()).hexdigest()
    if out.is_file() and stamp.is_file():
        try:
            if json.loads(stamp.read_text())["fingerprint"] == fingerprint:
                return out
        except (ValueError, KeyError):
            pass

    sources = sorted({s["source"] for s in cuts["segments"]})
    files = {}
    for si in sources:
        name = next(seg["file"] for seg in job.read_json("transcript.json")["sources"] if seg["index"] == si)
        files[si] = job.raw_dir / name
    inputs: list[str] = []
    for si in sources:
        inputs += ["-i", str(files[si])]
    parts, concat = [], ""
    for i, seg in enumerate(cuts["segments"]):
        idx = sources.index(seg["source"])
        crop = crops.get(seg["id"])
        cropf = f"crop={crop['w']}:{crop['h']}:{crop['x']}:{crop['y']}," if crop else ""
        parts.append(f"[{idx}:v]trim=start={seg['start']:.3f}:end={seg['end']:.3f},setpts=PTS-STARTPTS,"
                     f"{cropf}scale={WIDTH}:{HEIGHT}:flags=lanczos,setsar=1,fps={fps}[v{i}]")
        parts.append(f"[{idx}:a]atrim=start={seg['start']:.3f}:end={seg['end']:.3f},asetpts=PTS-STARTPTS,aresample=48000[a{i}]")
        concat += f"[v{i}][a{i}]"
    graph = ";".join(parts) + f";{concat}concat=n={len(cuts['segments'])}:v=1:a=1[v][a]"
    run_ffmpeg(inputs + ["-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                         "-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(fps),
                         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
    stamp.write_text(json.dumps({"fingerprint": fingerprint, "duration": probe(out).duration}))
    return out


def extract_frame(video: Path, t: float, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-ss", f"{t:.4f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)])
    return out


def extract_frames(video: Path, start_frame: int, count: int, out_dir: Path, fps: int = FPS) -> None:
    """Frames [start_frame, start_frame+count) of a constant-fps video as 00000.jpg, 00001.jpg, ..."""
    out_dir.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-ss", f"{start_frame / fps:.5f}", "-i", str(video), "-frames:v", str(count), "-q:v", "2",
                "-start_number", "0", str(out_dir / "%05d.jpg")])


def encode_frames(frames_dir: Path, out: Path, fps: int = FPS, crf: int = 18) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-framerate", str(fps), "-i", str(frames_dir / "%05d.jpg"),
                "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p", "-r", str(fps), str(out)])
    return out


def concat_videos(parts: list[Path], out: Path) -> Path:
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)])
    return out


def mix_and_mux(video: Path, base_audio: Path, out: Path, *, total: float, music: Path | None,
                ducking_db: float = -18, target_lufs: float = -14) -> Path:
    """Voice (+ music ducked under it), loudness-normalised, muxed with the joined video."""
    inputs = ["-i", str(video), "-i", str(base_audio)]
    chain = f"[1:a]aresample=48000,apad=whole_dur={total:.3f},atrim=0:{total:.3f}[v]"
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        chain += (f";[2:a]aresample=48000,atrim=0:{total:.3f},afade=t=out:st={max(0.0, total - 1.5):.3f}:d=1.5,"
                  f"volume={ducking_db}dB[m];[m][v]sidechaincompress=threshold=0.02:ratio=6:attack=40:release=600[md];"
                  f"[v][md]amix=inputs=2:normalize=0:duration=first[mix]")
        last = "[mix]"
    else:
        last = "[v]"
    chain += f";{last}loudnorm=I={target_lufs}:TP=-1.5:LRA=11[a]"
    run_ffmpeg(inputs + ["-filter_complex", chain, "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(out)])
    return out


def find_music(job: Job, profile: dict) -> Path | None:
    for p in job.optional_files("music"):
        if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac", ".aif", ".aiff"):
            return p
    client_dir = Path(profile["_path"]).parent if profile.get("_path") else None
    for rel in (profile.get("music", {}).get("tracks") or []):
        if client_dir and (client_dir / str(rel)).is_file():
            return client_dir / str(rel)
    return None
