"""ffmpeg and media probing helpers shared by every stage.

ffmpeg is bundled with the imageio-ffmpeg package, so nothing has to be
installed on the machine.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np

from .job import JobError


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args: list[str], *, quiet: bool = True) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-y"]
    if quiet:
        cmd += ["-loglevel", "error"]
    cmd += args
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise JobError(f"ffmpeg failed ({' '.join(args[:6])} ...):\n{tail}")
    return proc


# -- probing -----------------------------------------------------------------

@dataclass
class VideoInfo:
    codec: str
    width: int           # stored (encoded) size
    height: int
    rotation: int        # degrees from the display matrix, 0/90/180/270
    fps: float
    frames: int | None
    bit_rate: int | None

    @property
    def display_width(self) -> int:
        return self.height if self.rotation in (90, 270) else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation in (90, 270) else self.height

    @property
    def orientation(self) -> str:
        if self.display_height > self.display_width:
            return "portrait"
        if self.display_height < self.display_width:
            return "landscape"
        return "square"


@dataclass
class AudioInfo:
    codec: str
    sample_rate: int
    channels: int
    bit_rate: int | None


@dataclass
class MediaInfo:
    path: Path
    format: str
    duration: float
    size_bytes: int
    video: VideoInfo | None = None
    audio: AudioInfo | None = None
    metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = {
            "file": self.path.name,
            "format": self.format,
            "duration": round(self.duration, 3),
            "size_mb": round(self.size_bytes / 1e6, 1),
        }
        if self.video:
            v = self.video
            d["video"] = {
                "codec": v.codec,
                "width": v.display_width,
                "height": v.display_height,
                "rotation": v.rotation,
                "orientation": v.orientation,
                "fps": round(v.fps, 3),
                "frames": v.frames,
            }
        if self.audio:
            a = self.audio
            d["audio"] = {"codec": a.codec, "sample_rate": a.sample_rate, "channels": a.channels}
        if self.metadata.get("com.apple.quicktime.model"):
            d["device"] = self.metadata["com.apple.quicktime.model"]
        if self.metadata.get("creation_time"):
            d["recorded"] = self.metadata["creation_time"]
        return d


_RE = {
    "input": re.compile(r"^Input #0, ([^,\s]+)", re.M),
    "duration": re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)"),
    "video": re.compile(r"Stream #0:(\d+).*?: Video: (\w+)(.*)$", re.M),
    "audio": re.compile(r"Stream #0:(\d+).*?: Audio: (\w+)[^,]*, (\d+) Hz, ([^,]+)", re.M),
    "size": re.compile(r"[, ](\d{2,5})x(\d{2,5})(?:[, \[]|$)"),
    "fps": re.compile(r"(\d+(?:\.\d+)?) fps"),
    "vbitrate": re.compile(r"(\d+) kb/s"),
    "rotation": re.compile(r"rotation of (-?\d+(?:\.\d+)?) degrees"),
    "rotate_tag": re.compile(r"^\s*rotate\s*:\s*(-?\d+)", re.M),
    "meta": re.compile(r"^\s{4}([\w.\-]+)\s*:\s*(.+?)\s*$", re.M),
    "bitrate": re.compile(r"bitrate: (\d+) kb/s"),
}
_CHANNELS = {"mono": 1, "stereo": 2, "2.1": 3, "quad": 4, "5.0": 5, "5.1": 6, "7.1": 8}


def probe(path: Path) -> MediaInfo:
    """Read a media file's streams from ffmpeg's own description of it."""
    path = Path(path)
    if not path.is_file():
        raise JobError(f"File not found: {path}")
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path)],
                          capture_output=True, text=True)
    text = proc.stderr
    m_in = _RE["input"].search(text)
    m_dur = _RE["duration"].search(text)
    if not m_in or not m_dur:
        tail = "\n".join(text.strip().splitlines()[-3:])
        raise JobError(f"Cannot open {path.name}: {tail or 'not a media file'}")
    duration = int(m_dur.group(1)) * 3600 + int(m_dur.group(2)) * 60 + float(m_dur.group(3))
    metadata = {}
    for k, v in _RE["meta"].findall(text.split("Duration:", 1)[0]):
        metadata.setdefault(k, v)
    info = MediaInfo(path=path, format=m_in.group(1), duration=duration,
                     size_bytes=path.stat().st_size, metadata=metadata)

    m_v = _RE["video"].search(text)
    if m_v:
        rest = m_v.group(3)
        m_size = _RE["size"].search(rest)
        m_fps = _RE["fps"].search(rest)
        m_br = _RE["vbitrate"].search(rest)
        rotation = 0
        m_rot = _RE["rotation"].search(text)
        m_tag = _RE["rotate_tag"].search(text)
        if m_rot:
            rotation = (-int(round(float(m_rot.group(1))))) % 360   # matrix angle → display rotation
        elif m_tag:
            rotation = int(m_tag.group(1)) % 360
        info.video = VideoInfo(
            codec=m_v.group(2),
            width=int(m_size.group(1)) if m_size else 0,
            height=int(m_size.group(2)) if m_size else 0,
            rotation=rotation,
            fps=float(m_fps.group(1)) if m_fps else 0.0,
            frames=None,
            bit_rate=int(m_br.group(1)) * 1000 if m_br else None,
        )
    m_a = _RE["audio"].search(text)
    if m_a:
        layout = m_a.group(4).strip()
        channels = _CHANNELS.get(layout.split("(")[0])
        if channels is None:
            m_ch = re.match(r"(\d+) channels", layout)
            channels = int(m_ch.group(1)) if m_ch else 2
        info.audio = AudioInfo(codec=m_a.group(2), sample_rate=int(m_a.group(3)), channels=channels, bit_rate=None)
    return info


# -- audio -------------------------------------------------------------------

def decode_audio(path: Path, sample_rate: int = 16000, start: float | None = None,
                 duration: float | None = None) -> np.ndarray:
    """Decode (part of) a file's audio to mono float32 samples in [-1, 1]."""
    args: list[str] = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path)]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-"]
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error"] + args
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise JobError(f"Could not decode audio from {Path(path).name}: {proc.stderr.decode(errors='ignore')[-300:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def extract_wav(src: Path, dst: Path, sample_rate: int = 16000) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)])
    return dst


_LUFS_RE = re.compile(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS")


def integrated_loudness(path: Path) -> float | None:
    """Integrated loudness in LUFS (EBU R128), or None if it can't be measured."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-nostats", "-i", str(path),
           "-vn", "-af", "ebur128=framelog=quiet", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    matches = _LUFS_RE.findall(proc.stderr)
    if not matches:
        return None
    return float(matches[-1])


# -- video frames ------------------------------------------------------------

def preview_size(src_display_size: tuple[int, int], width: int) -> tuple[int, int]:
    dw, dh = src_display_size
    height = int(round(dh * width / dw / 2)) * 2
    return width, height


def iter_frames(path: Path, start: float, end: float, width: int,
                src_display_size: tuple[int, int], fps: float) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (timestamp, BGR frame) for every frame between start and end.

    Frames are shrunk to `width` pixels wide (keeping aspect). ffmpeg applies the
    phone's rotation automatically, so frames come out the way the viewer sees them.
    """
    width, height = preview_size(src_display_size, width)
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error",
           "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(path),
           "-vf", f"scale={width}:{height}", "-an", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    frame_bytes = width * height * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    index = 0
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
            yield start + index / fps, frame
            index += 1
    finally:
        proc.stdout.close()
        proc.wait()


class FrameWriter:
    """Pipe BGR frames into ffmpeg to make a small H.264 preview video."""

    def __init__(self, dst: Path, width: int, height: int, fps: float):
        dst.parent.mkdir(parents=True, exist_ok=True)
        self.cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                    "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps:.3f}",
                    "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)]
        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        self.dst = dst
        self.width = width
        self.height = height

    def write(self, frame: np.ndarray) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(np.ascontiguousarray(frame).tobytes())

    def close(self) -> None:
        _, err = self.proc.communicate()   # closes stdin, waits for the encoder to finish
        if self.proc.returncode != 0:
            raise JobError(f"Preview encode failed: {err.decode(errors='ignore')[-300:]}")
