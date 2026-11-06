"""Sound effects, made by the engine itself.

Every effect here is synthesised from sine waves and noise by the code below, so nothing is
borrowed and nothing needs a licence. A style asks for a sound by name at a moment of a beat
(STUDIIO.cue in the renderer); the render collects those cues and build_track() lays the effects
on one track that is mixed under the voice.

An effect has an anchor: the moment inside the sound that should fall on the cue (the peak of a
whoosh is at the cut, not its start).
"""

from __future__ import annotations

import math
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

SR = 48000


# -- building blocks ----------------------------------------------------------------------

def _t(seconds: float) -> np.ndarray:
    return np.arange(int(round(seconds * SR))) / SR


def _noise(n: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(n)


def _band(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Keep the band lo..hi (Hz), with soft edges, by shaping the spectrum."""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    gain = np.ones_like(f)
    if lo > 0:
        gain *= 1 / (1 + (lo / np.maximum(f, 1e-6)) ** 4)
    if hi < SR / 2:
        gain *= 1 / (1 + (f / hi) ** 4)
    return np.fft.irfft(spec * gain, len(x))


def _svf_band(x: np.ndarray, fc: np.ndarray, q: float = 1.2) -> np.ndarray:
    """A band-pass whose centre moves (fc in Hz per sample): the body of a whoosh."""
    low = band = 0.0
    damp = 1.0 / q
    out = np.empty_like(x)
    f = 2 * np.sin(np.pi * np.clip(fc, 20, SR / 6.5) / SR)
    for i in range(len(x)):
        low += f[i] * band
        high = x[i] - low - damp * band
        band += f[i] * high
        out[i] = band
    return out


def _sine(freq: np.ndarray | float, n: int) -> np.ndarray:
    """A sine whose pitch may move: freq in Hz, per sample or constant."""
    f = np.full(n, float(freq)) if np.isscalar(freq) else np.asarray(freq, dtype=float)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def _fade(x: np.ndarray, attack: float = 0.002, release: float = 0.01) -> np.ndarray:
    a, r = max(1, int(attack * SR)), max(1, int(release * SR))
    env = np.ones(len(x))
    env[:a] = np.linspace(0, 1, a)
    env[-r:] *= np.linspace(1, 0, r)
    return x * env


def _stereo(x: np.ndarray, pan: np.ndarray | float = 0.0) -> np.ndarray:
    """Mono to stereo; pan −1 (left) … +1 (right), constant or moving."""
    p = (np.asarray(pan) + 1) * np.pi / 4
    return np.stack([x * np.cos(p), x * np.sin(p)], axis=1)


def _peak(x: np.ndarray, level: float) -> np.ndarray:
    m = float(np.max(np.abs(x))) or 1.0
    return (x * (level / m)).astype(np.float32)


def _bell(freq: float, seconds: float, decay: float, partials=((1, 1.0), (2.76, 0.42), (5.4, 0.2), (8.93, 0.08))) -> np.ndarray:
    t = _t(seconds)
    out = np.zeros(len(t))
    for ratio, amp in partials:
        if freq * ratio < SR / 2.2:
            out += amp * np.sin(2 * np.pi * freq * ratio * t) * np.exp(-t * decay * (0.6 + 0.4 * ratio))
    return _fade(out, 0.001, 0.02)


# -- the effects: name -> (stereo sound, anchor seconds) -------------------------------------

def _whoosh():
    d, anchor = 0.6, 0.34
    t = _t(d)
    up = np.clip(t / anchor, 0, 1)
    fc = np.where(t < anchor, 240 * (3400 / 240) ** up, 3400 * (650 / 3400) ** np.clip((t - anchor) / (d - anchor), 0, 1))
    body = _svf_band(_noise(len(t), 11), fc, 1.3)
    env = np.where(t < anchor, up ** 2.4, np.exp(-(t - anchor) * 11))
    air = _band(_noise(len(t), 12), 5000, 14000) * env * 0.25
    sub = _sine(70 - 28 * np.clip((t - anchor + 0.05) / 0.3, 0, 1), len(t)) * np.exp(-np.abs(t - anchor) * 14) * 0.5
    pan = np.clip((t - anchor) / 0.3, -1, 1) * 0.55
    return _peak(_stereo(_fade((body + air) * env + sub * 0.0, 0.004, 0.05), pan) + _stereo(sub)[:, :] * 0.35, 0.8), anchor


def _swell():
    d, anchor = 1.3, 0.85
    t = _t(d)
    up = np.clip(t / anchor, 0, 1)
    fc = 300 * (5200 / 300) ** (up ** 1.6)
    body = _svf_band(_noise(len(t), 21), fc, 0.9)
    env = np.where(t < anchor, up ** 3.0, np.exp(-(t - anchor) * 7.5))
    tone = (_sine(110 * (2 ** up), len(t)) + 0.5 * _sine(165 * (2 ** up), len(t))) * env * 0.22
    shimmer = _band(_noise(len(t), 22), 6000, 15000) * env * 0.3
    left = body * env + tone + shimmer
    right = _svf_band(_noise(len(t), 23), fc * 1.07, 0.9) * env + tone + _band(_noise(len(t), 24), 6000, 15000) * env * 0.3
    return _peak(np.stack([_fade(left, 0.01, 0.08), _fade(right, 0.01, 0.08)], axis=1), 0.8), anchor


def _pop():
    t = _t(0.13)
    x = _sine(190 + 520 * np.exp(-t * 34), len(t)) * np.exp(-t * 36)
    x += _band(_noise(len(t), 31), 1500, 9000) * np.exp(-t * 600) * 0.35
    return _peak(_stereo(_fade(x, 0.001, 0.02)), 0.7), 0.004


def _tick():
    t = _t(0.06)
    x = np.sin(2 * np.pi * 2300 * t) * np.exp(-t * 130) + _band(_noise(len(t), 41), 3500, 12000) * np.exp(-t * 480) * 0.6
    return _peak(_stereo(_fade(x, 0.0005, 0.01)), 0.5), 0.002


def _count():
    d = 0.56
    out = np.zeros(int(d * SR))
    steps = 10
    for k in range(steps):
        at = (1 - (1 - k / (steps - 1)) ** 2.2) * 0.44            # quick at first, then slowing: a counter coming to rest
        f = 1500 + 1300 * k / (steps - 1)
        t = _t(0.05 if k < steps - 1 else 0.11)
        hit = np.sin(2 * np.pi * f * t) * np.exp(-t * (150 if k < steps - 1 else 42)) * (0.55 if k < steps - 1 else 1.0)
        i = int(at * SR)
        out[i:i + len(hit)] += hit[:len(out) - i]
    return _peak(_stereo(_fade(out, 0.0005, 0.02)), 0.5), 0.0


def _key():
    d = 0.2
    out = np.zeros(int(d * SR))
    for at, level, seed in ((0.0, 1.0, 51), (0.072, 0.7, 52), (0.128, 0.85, 53)):
        t = _t(0.04)
        hit = (_band(_noise(len(t), seed), 300, 2600) * np.exp(-t * 210) + np.sin(2 * np.pi * 165 * t) * np.exp(-t * 90) * 0.5) * level
        i = int(at * SR)
        out[i:i + len(hit)] += hit
    return _peak(_stereo(_fade(out, 0.0005, 0.02)), 0.36), 0.0


def _sweep():
    d, anchor = 0.24, 0.1
    t = _t(d)
    fc = 1400 * (6500 / 1400) ** np.clip(t / d, 0, 1)
    env = np.exp(-((t - anchor) / 0.06) ** 2)
    return _peak(_stereo(_fade(_svf_band(_noise(len(t), 61), fc, 1.6) * env, 0.003, 0.03), np.linspace(-0.4, 0.4, len(t))), 0.42), anchor


def _click():
    d = 0.14
    out = np.zeros(int(d * SR))
    for at, level, seed in ((0.0, 1.0, 71), (0.068, 0.6, 72)):
        t = _t(0.03)
        hit = (_band(_noise(len(t), seed), 900, 8000) * np.exp(-t * 520) + np.sin(2 * np.pi * 1150 * t) * np.exp(-t * 170) * 0.6) * level
        i = int(at * SR)
        out[i:i + len(hit)] += hit
    return _peak(_stereo(_fade(out, 0.0005, 0.02)), 0.6), 0.0


def _thud():
    t = _t(0.5)
    x = _sine(46 + 62 * np.exp(-t * 26), len(t)) * np.exp(-t * 9)
    x += _band(_noise(len(t), 81), 150, 1800) * np.exp(-t * 90) * 0.4
    return _peak(_stereo(_fade(np.tanh(x * 1.6), 0.001, 0.06)), 0.85), 0.008


def _impact():
    t = _t(1.1)
    x = _sine(36 + 52 * np.exp(-t * 16), len(t)) * np.exp(-t * 4.6)
    x += _band(_noise(len(t), 91), 80, 900) * np.exp(-t * 26) * 0.6
    tail_l = _band(_noise(len(t), 92), 400, 5000) * np.exp(-t * 5.5) * 0.12
    tail_r = _band(_noise(len(t), 93), 400, 5000) * np.exp(-t * 5.5) * 0.12
    body = np.tanh(x * 1.5)
    return _peak(np.stack([_fade(body + tail_l, 0.001, 0.12), _fade(body + tail_r, 0.001, 0.12)], axis=1), 0.9), 0.015


def _ding():
    x = _bell(1318.5, 1.0, 5.5)
    return _peak(_stereo(x), 0.5), 0.004


def _chime():
    d = 1.9
    out = np.zeros((int(d * SR), 2))
    for at, freq, pan in ((0.0, 659.25, -0.3), (0.1, 987.77, 0.3), (0.2, 1318.5, 0.0)):
        note = _stereo(_bell(freq, d - at - 0.01, 2.6), pan)
        i = int(at * SR)
        out[i:i + len(note)] += note
    return _peak(out, 0.6), 0.01


def _shimmer():
    d = 1.1
    rng = np.random.default_rng(101)
    out = np.zeros((int(d * SR), 2))
    for _ in range(14):
        at, f = rng.uniform(0, 0.6), rng.uniform(2800, 7200)
        t = _t(0.35)
        ping = np.sin(2 * np.pi * f * t) * np.exp(-t * 16) * rng.uniform(0.3, 1.0)
        i = int(at * SR)
        out[i:i + len(ping)] += _stereo(ping, rng.uniform(-0.8, 0.8))
    return _peak(out, 0.4), 0.05


def _bloop():
    t = _t(0.2)
    x = _sine(430 + 300 * np.clip(t / 0.09, 0, 1), len(t)) * np.exp(-t * 17)
    return _peak(_stereo(_fade(x, 0.002, 0.03)), 0.55), 0.004


EFFECTS = {
    "whoosh": _whoosh,     # air at a change of scene
    "swell": _swell,       # a rise into a bloom of light
    "pop": _pop,           # something lands
    "tick": _tick,         # a small part arrives
    "count": _count,       # a number counting up to rest
    "key": _key,           # typing
    "sweep": _sweep,       # a highlight sweeping in
    "click": _click,       # a cursor click
    "thud": _thud,         # a big number lands
    "impact": _impact,     # the opening of the reel
    "ding": _ding,         # a notification
    "chime": _chime,       # the logo
    "shimmer": _shimmer,   # glass arriving
    "bloop": _bloop,       # a message bubble
}
MIN_GAP = {"key": 0.13, "tick": 0.05, "sweep": 0.12, "pop": 0.07, "count": 0.3}   # two of the same closer than this: keep the first


@lru_cache(maxsize=None)
def effect(name: str) -> tuple[np.ndarray, float]:
    return EFFECTS[name]()


AUDIO_EXT = (".wav", ".mp3", ".m4a", ".aac", ".aif", ".aiff", ".ogg", ".flac")
PEAK_ANCHORED = ("whoosh", "swell", "sweep")       # sounds whose loudest moment, not their start, belongs on the cue


def load_library(folders: list[Path]) -> dict[str, tuple[np.ndarray, float]]:
    """Sound files supplied by the studio or a client: a file named after a cue (whoosh.wav, pop.mp3,
    count.wav …) is the sound of that cue. Later folders win over earlier ones."""
    import subprocess

    from .media import ffmpeg_exe

    out: dict[str, tuple[np.ndarray, float]] = {}
    for folder in folders:
        if not folder or not Path(folder).is_dir():
            continue
        for f in sorted(Path(folder).iterdir()):
            name = f.stem.lower()
            if f.suffix.lower() not in AUDIO_EXT or name not in EFFECTS:
                continue
            raw = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", str(f), "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                                 capture_output=True).stdout
            audio = np.frombuffer(raw, dtype="<f4").reshape(-1, 2).copy()
            if not len(audio):
                continue
            anchor = 0.0
            if name in PEAK_ANCHORED:
                env = np.abs(audio).max(axis=1)
                anchor = float(np.argmax(np.convolve(env, np.ones(480) / 480, mode="same"))) / SR
            out[name] = (audio, anchor)
    return out


def build_track(cues: list[dict], seconds: float, library: dict | None = None, synth: bool = True) -> np.ndarray | None:
    """Lay the effects on one stereo track. cues: [{t (seconds into the reel), name, gain}].
    A sound from the library is used when there is one for the cue; the engine's own synthesised
    sound only when synth is on. Returns None when there is nothing to play."""
    n = int(math.ceil(seconds * SR))
    track = np.zeros((n, 2), dtype=np.float32)
    last: dict[str, float] = {}
    used = 0
    library = library or {}
    for c in sorted(cues, key=lambda c: c["t"]):
        name = str(c.get("name"))
        if name not in library and not (synth and name in EFFECTS):
            continue
        t = float(c["t"])
        if t - last.get(name, -9) < MIN_GAP.get(name, 0.0):
            continue
        last[name] = t
        sound, anchor = library[name] if name in library else effect(name)
        i = int(round((t - anchor) * SR))
        a, b = max(0, i), min(n, i + len(sound))
        if b <= a:
            continue
        track[a:b] += sound[a - i:b - i] * float(c.get("gain", 1.0))
        used += 1
    if not used:
        return None
    return np.clip(track, -1, 1)


def write_wav(path: Path, audio: np.ndarray) -> Path:
    """Stereo float (−1…1) to a 16-bit wav file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    return path
