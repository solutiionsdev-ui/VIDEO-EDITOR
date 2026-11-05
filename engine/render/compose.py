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


ENCODE_VERSION = 2   # bump when the encoding below changes, so cached beats are redone


def encode_frames(frames_dir: Path, out: Path, fps: int = FPS, crf: int = 18) -> Path:
    """JPEG frames → H.264. JPEGs are full-range; convert to the limited range players expect."""
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-framerate", str(fps), "-i", str(frames_dir / "%05d.jpg"),
                "-vf", "scale=in_range=pc:out_range=tv,format=yuv420p",
                "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
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


def loudness(path: Path) -> dict:
    """Integrated loudness, range and true peak of a file's audio (EBU R128)."""
    import re
    import subprocess

    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostdin", "-nostats", "-i", str(path), "-vn",
                           "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"], capture_output=True, text=True)
    text = proc.stderr
    def grab(pattern):
        m = re.findall(pattern, text)
        return float(m[-1]) if m else None
    return {"lufs": grab(r"I:\s+(-?\d+(?:\.\d+)?) LUFS"), "lra": grab(r"LRA:\s+(-?\d+(?:\.\d+)?) LU"),
            "true_peak": grab(r"Peak:\s+(-?\d+(?:\.\d+)?) dBFS")}


def black_frozen(path: Path, duration: float | None, min_run: float = 0.2,
                 crop: tuple[int, int, int, int] | None = None) -> dict:
    """Runs of black or frozen frames longer than min_run seconds, within the first `duration` seconds.

    `crop` (w, h, x, y) limits the freeze test to the moving region — in the white-card layout most
    of the frame is a still background by design, so only the footage card is tested there.
    """
    import re
    import subprocess

    args = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-nostats", "-i", str(path)]
    if duration:
        args += ["-t", f"{duration:.3f}"]
    cropf = f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}," if crop else ""
    # -60 dB catches duplicated/stuck frames (their difference is encoder noise only) but not a person holding still.
    args += ["-an", "-vf", f"blackdetect=d={min_run}:pic_th=0.98,{cropf}freezedetect=n=-60dB:d={min_run}", "-f", "null", "-"]
    proc = subprocess.run(args, capture_output=True, text=True)
    black = [(float(a), float(b)) for a, b in re.findall(r"black_start:(\d+(?:\.\d+)?) black_end:(\d+(?:\.\d+)?)", proc.stderr)]
    starts = [float(x) for x in re.findall(r"freeze_start: (\d+(?:\.\d+)?)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"freeze_end: (\d+(?:\.\d+)?)", proc.stderr)]
    frozen = list(zip(starts, ends))
    if len(starts) > len(ends):
        frozen.append((starts[-1], duration or starts[-1]))
    return {"black": black, "frozen": frozen}


def remix_audio(video_with_audio: Path, base_audio: Path, out: Path, *, total: float, music: Path | None,
                ducking_db: float, measured: dict | None = None, target_lufs: float = -14.0) -> Path:
    """Second loudness pass, exact: measure the mix before any normalising, then set its level with plain
    gain. When that gain would push the peaks over the limit (a voice with sharp peaks), a limiter holds
    them and the gain is corrected once more from what came out, so the result lands on target."""
    import re
    import subprocess

    inputs = ["-i", str(video_with_audio), "-i", str(base_audio)]
    chain = f"[1:a]aresample=48000,apad=whole_dur={total:.3f},atrim=0:{total:.3f}[v]"
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        chain += (f";[2:a]aresample=48000,atrim=0:{total:.3f},afade=t=out:st={max(0.0, total - 1.5):.3f}:d=1.5,"
                  f"volume={ducking_db}dB[m];[m][v]sidechaincompress=threshold=0.02:ratio=6:attack=40:release=600[md];"
                  f"[v][md]amix=inputs=2:normalize=0:duration=first[mix]")
        last = "[mix]"
    else:
        last = "[v]"

    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostdin", "-nostats"] + inputs +
                          ["-filter_complex", chain + f";{last}ebur128=peak=true:framelog=quiet[a]", "-map", "[a]", "-t", f"{total:.3f}",
                           "-f", "null", "-"], capture_output=True, text=True)
    def grab(pattern):
        m = re.findall(pattern, proc.stderr)
        return float(m[-1]) if m else None
    mix_lufs, mix_peak = grab(r"I:\s+(-?\d+(?:\.\d+)?) LUFS"), grab(r"Peak:\s+(-?\d+(?:\.\d+)?) dBFS")
    if mix_lufs is None or mix_lufs < -60:
        raise JobError("could not measure the loudness of the mix")
    gain = target_lufs - mix_lufs
    limit = (mix_peak if mix_peak is not None else 0.0) + gain > -1.5      # plain gain would leave too little headroom
    for _ in range(3):
        level = f"volume={gain:.2f}dB" + (",alimiter=limit=0.75:attack=5:release=80:level=0" if limit else "")
        run_ffmpeg(inputs + ["-filter_complex", chain + f";{last}{level}[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(out)])
        got = loudness(out).get("lufs")
        if not limit or got is None or abs(got - target_lufs) <= 0.4:
            break
        gain += target_lufs - got       # the limiter took a little off: give it back
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
