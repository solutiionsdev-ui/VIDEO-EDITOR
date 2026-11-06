"""Music beds, made by the engine itself.

A bed is composed and synthesised by the code below from sine waves and noise: a chord
progression, a pad, a plucked pattern, a bass and, when the energy asks for it, drums. Nothing is
sampled or borrowed, so nothing needs a licence. It is made to the length of the reel: it opens
thin, fills out, and comes to rest when the end card arrives.

It is a bed, not a song: it is there so the voice does not sit in silence. A licensed track in
the job's music/ folder (or the client's) always takes its place.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

import numpy as np

from .sound import SR, _band, _noise, write_wav

VERSION = 4      # bump when a bed would sound different, so cached files are made again


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


PRESETS = {
    # chords as (bass note, [chord notes]) in MIDI numbers; one chord a bar
    "minimal": {"title": "Minimal electronic", "bpm": (96, 112), "words": ("minimal", "electronic", "tech", "modern", "clean", "corporate"),
                "chords": [(45, [57, 60, 64, 67]), (41, [57, 60, 65, 69]), (48, [55, 60, 64, 67]), (43, [55, 59, 62, 67])],
                "drums": "four", "pluck": 8},
    "ambient": {"title": "Warm ambient", "bpm": (76, 92), "words": ("ambient", "calm", "warm", "soft", "gentle", "emotional", "reflective"),
                "chords": [(50, [57, 62, 66, 69]), (47, [54, 59, 62, 66]), (43, [55, 59, 62, 67]), (45, [57, 61, 64, 69])],
                "drums": None, "pluck": 2},
    "upbeat": {"title": "Upbeat", "bpm": (110, 124), "words": ("upbeat", "energetic", "pop", "fun", "bright", "happy", "driving"),
               "chords": [(48, [55, 60, 64, 67]), (43, [55, 59, 62, 67]), (45, [57, 60, 64, 69]), (41, [57, 60, 65, 69])],
               "drums": "four+clap", "pluck": 16},
    "cinematic": {"title": "Cinematic pulse", "bpm": (88, 100), "words": ("cinematic", "dramatic", "epic", "serious", "tension", "bold"),
                  "chords": [(38, [57, 62, 65, 69]), (46, [58, 62, 65, 70]), (41, [57, 60, 65, 69]), (48, [55, 60, 64, 67])],
                  "drums": "pulse", "pluck": 8},
}


def choose(mood: str = "", bpm: str | float | None = None, energy: str = "medium") -> dict:
    """The bed for a beat plan's music line ({mood, bpm, energy}): which preset, at what tempo."""
    text = str(mood or "").lower()
    name = next((n for n, p in PRESETS.items() if any(w in text for w in p["words"])), "minimal")
    lo, hi = PRESETS[name]["bpm"]
    numbers = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(bpm or ""))]
    tempo = sum(numbers) / len(numbers) if numbers else (lo + hi) / 2
    while tempo > hi * 1.25:
        tempo /= 2
    while tempo < lo * 0.8:
        tempo *= 2
    energy = str(energy or "medium").lower()
    return {"preset": name, "title": PRESETS[name]["title"], "bpm": round(min(max(tempo, lo), hi), 1),
            "energy": energy if energy in ("low", "medium", "high") else "medium"}


# -- voices ---------------------------------------------------------------------------------

def _pluck(freq: float, seconds: float, bright: float = 1.0) -> np.ndarray:
    """A plucked note: the high partials die first, like a string or a soft synth pluck."""
    t = np.arange(int(seconds * SR)) / SR
    out = np.zeros(len(t))
    k = 1
    while k <= 14 and freq * k < 9000:
        out += np.sin(2 * np.pi * freq * k * t) * (1 / k ** 1.15) * np.exp(-t * (2.6 + 2.4 * k / bright))
        k += 1
    a = int(0.004 * SR)
    out[:a] *= np.linspace(0, 1, a)
    return out


def _pad(notes: list[int], seconds: float, detune: float) -> np.ndarray:
    """A soft chord that swells in and lets go; detune (cents) differs left and right for width."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for m in notes:
        for cents in (-detune, detune * 0.6):
            f = _hz(m) * 2 ** (cents / 1200)
            for k in range(1, 9):
                if f * k < 5200:
                    out += np.sin(2 * np.pi * f * k * t + k * 0.7 + m) * (1 / k ** 1.75)
    return out / max(1, len(notes))


def _kick(seconds: float = 0.36) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    body = np.sin(2 * np.pi * np.cumsum(46 + 118 * np.exp(-t * 36)) / SR) * np.exp(-t * 8.5)
    click = _band(_noise(len(t), 5), 1200, 6000) * np.exp(-t * 700) * 0.25
    return np.tanh((body + click) * 1.4)


def _hat(seconds: float, seed: int) -> np.ndarray:
    t = np.arange(int(0.12 * SR)) / SR
    return _band(_noise(len(t), seed), 7000, 16000) * np.exp(-t / seconds)


def _clap() -> np.ndarray:
    t = np.arange(int(0.22 * SR)) / SR
    body = _band(_noise(len(t), 9), 900, 3800)
    env = np.exp(-t * 26) + sum(np.exp(-np.abs(t - d) * 900) for d in (0.0, 0.011, 0.023)) * 0.8
    return body * env


def _put(buf: np.ndarray, sound: np.ndarray, at: float, gain: float = 1.0) -> None:
    i = int(round(at * SR))
    if i >= len(buf) or i < 0:
        return
    b = min(len(buf), i + len(sound))
    buf[i:b] += sound[:b - i] * gain


def _reverb(x: np.ndarray, seconds: float = 1.7, seed: int = 3) -> np.ndarray:
    """A room: the sound convolved with a burst of noise that dies away."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    ir = _band(_noise(n, seed), 250, 5200) * np.exp(-t * 3.4)
    ir[: int(0.012 * SR)] = 0
    size = 1 << int(math.ceil(math.log2(len(x) + n)))
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    return wet / (np.sqrt(np.sum(ir ** 2)) + 1e-9)


# -- the bed ---------------------------------------------------------------------------------

def bed(preset: str, bpm: float, energy: str, seconds: float, end_at: float | None = None) -> np.ndarray:
    """Compose and synthesise a stereo bed of this length. end_at: where the end card starts
    (the bed comes to rest there); None lets it run to the end and fade."""
    p = PRESETS.get(preset) or PRESETS["minimal"]
    beat = 60.0 / bpm
    bar = 4 * beat
    n = int(math.ceil(seconds * SR))
    bars = int(math.ceil(seconds / bar)) + 1
    rest = end_at if end_at is not None and end_at > bar else seconds - 0.2
    intro = 2 if seconds > 6 * bar else 1                        # the bars before everything is in
    level = {"low": 0.0, "medium": 0.7, "high": 1.0}[energy]

    pad_l, pad_r, pl_l, pl_r = (np.zeros(n) for _ in range(4))
    bass, drums = np.zeros(n), np.zeros(n)
    kick, clap = _kick(), _clap()

    # one bar of each voice for each chord, made once and laid down bar after bar
    bars_made: dict = {}
    for ci, (root, notes) in enumerate(p["chords"]):
        seg = int((bar + 0.9) * SR)
        env = np.ones(seg)
        a, r = int(0.55 * SR), int(0.9 * SR)
        env[:a] = np.linspace(0, 1, a) ** 1.5
        env[-r:] = np.linspace(1, 0, r) ** 1.2
        low = [root] + notes                                 # the root an octave under the chord fills the low middle
        bars_made[ci] = (_pad(low, bar + 0.9, 7) * env, _pad(low, bar + 0.9, -9) * env)

    step = p["pluck"]
    for b in range(bars):
        t0 = b * bar
        if t0 >= rest:
            break
        ci = b % len(p["chords"])
        root, notes = p["chords"][ci]
        full = b >= intro
        l, r = bars_made[ci]
        _put(pad_l, l, t0, 1.0)
        _put(pad_r, r, t0, 1.0)

        # the plucked pattern: chord notes up and back, an octave above the pad
        order = notes + notes[-2:0:-1]
        count = step if full else max(2, step // 2)
        for k in range(count):
            at = t0 + k * bar / count
            if at >= rest - 0.05:
                break
            m = order[(k + b) % len(order)] + (12 if (k % 4 != 3) else 24)
            accent = 1.0 if k % (count // 2 or 1) == 0 else 0.62
            note = _pluck(_hz(m), min(1.1, 2.4 * bar / count), bright=1.0 if full else 0.7) * accent
            _put(pl_l if k % 2 == 0 else pl_r, note, at, 0.9)
            _put(pl_r if k % 2 == 0 else pl_l, note, at, 0.45)

        # bass: the root, firm on the bar and pushing on the off-beats when the energy is up
        hits = [0, 2.5] if level < 0.6 or not full else ([0, 1.5, 2.5, 3.5] if p["drums"] != "pulse" else [i * 0.5 for i in range(8)])
        for h in hits:
            at = t0 + h * beat
            if at >= rest - 0.05:
                break
            f = _hz(root - 12)
            tt = np.arange(int(min(1.6, 1.5 * beat) * SR)) / SR
            note = (np.sin(2 * np.pi * f * tt) + 0.55 * np.sin(2 * np.pi * 2 * f * tt) + 0.28 * np.sin(2 * np.pi * 3 * f * tt)) * np.exp(-tt * (2.2 if p["drums"] != "pulse" else 7)) * np.minimum(1, tt / 0.008)
            _put(bass, np.tanh(note * 1.3), at, 0.85 if h == 0 else 0.6)

        # drums
        if p["drums"] and full and level > 0:
            last = t0 + bar >= rest - 0.01
            for q in range(4):
                at = t0 + q * beat
                if at >= rest - 0.05:
                    break
                if p["drums"] in ("four", "four+clap") or q in (0, 2):
                    _put(drums, kick, at, 0.9 * (0.75 + 0.25 * level))
                if p["drums"] == "four+clap" and q in (1, 3):
                    _put(drums, clap, at, 0.42 * level)
                if p["drums"] != "pulse" and not last:
                    _put(drums, _hat(0.022, 60 + q), at + beat / 2, 0.22 * level)                 # the off-beat hat
                    if level >= 1.0:
                        _put(drums, _hat(0.012, 70 + q), at + beat / 4, 0.1)
                        _put(drums, _hat(0.012, 80 + q), at + 3 * beat / 4, 0.1)

    # the bed breathes with the kick: the pad and the bass duck on every beat
    t = np.arange(n) / SR
    if p["drums"] and level > 0:
        on = (t >= intro * bar) & (t < rest)
        pump = 1 - 0.42 * level * np.exp(-((t % beat) / beat) * 6.5) * on
    else:
        pump = np.ones(n)

    # the arrival: the last chord rings, with one soft bell note on top
    if end_at is not None and end_at < seconds:
        root, notes = p["chords"][0]
        ring = max(0.6, seconds - end_at)
        seg = _pad(notes, ring + 0.2, 6)
        env = np.minimum(1, np.arange(len(seg)) / (0.05 * SR)) * np.exp(-np.arange(len(seg)) / SR * 1.6)
        _put(pad_l, seg * env, end_at, 1.2)
        _put(pad_r, _pad(notes, ring + 0.2, -8) * env, end_at, 1.2)
        top = _pluck(_hz(notes[0] + 24), min(2.0, ring + 0.2), 1.4)
        _put(pl_l, top, end_at, 0.9)
        _put(pl_r, top, end_at + 0.012, 0.9)
        tt = np.arange(int(1.2 * SR)) / SR
        _put(bass, np.sin(2 * np.pi * _hz(root - 12) * tt) * np.exp(-tt * 2.4) * np.minimum(1, tt / 0.01), end_at, 0.9)

    # echoes on the plucks, three quarters of a beat apart, crossing sides
    d = int(0.75 * beat * SR)
    for src, dst in ((pl_l, pl_r), (pl_r, pl_l)):
        echo = np.zeros(n)
        echo[d:] = src[:-d] * 0.36
        dst += echo
        echo2 = np.zeros(n)
        if 2 * d < n:
            echo2[2 * d:] = src[:-2 * d] * 0.14
        src += echo2

    pad_gain, pluck_gain = (0.5, 0.36) if p["drums"] else (0.62, 0.42)
    dry_l = _band(pad_l, 90, 4200) * pad_gain * pump + pl_l * pluck_gain
    dry_r = _band(pad_r, 90, 4200) * pad_gain * pump + pl_r * pluck_gain
    wet_l, wet_r = _reverb(dry_l, seed=3), _reverb(dry_r, seed=4)
    low = bass * 0.5 * pump + drums * 0.62
    left = dry_l + wet_l * 0.34 + low
    right = dry_r + wet_r * 0.34 + low
    mix = np.stack([left, right], axis=1)

    # it opens over the first half second and is gone by the very end
    fade_in, fade_out = int(0.5 * SR), int(0.45 * SR)
    mix[:fade_in] *= (np.linspace(0, 1, fade_in) ** 1.5)[:, None]
    mix[-fade_out:] *= (np.linspace(1, 0, fade_out) ** 1.3)[:, None]
    mix = np.tanh(mix * 1.1)
    rms = float(np.sqrt(np.mean(mix ** 2))) or 1.0
    mix *= min(10 ** (-17 / 20) / rms, 0.89 / (float(np.max(np.abs(mix))) or 1.0))      # about −17 dBFS RMS, never clipping
    return mix.astype(np.float32)


def bed_file(folder: Path, choice: dict, seconds: float, end_at: float | None) -> Path:
    """The bed as a wav file in `folder`, made once for a given choice and length."""
    key = hashlib.sha1(f"{VERSION}|{choice['preset']}|{choice['bpm']}|{choice['energy']}|{seconds:.2f}|{end_at}".encode()).hexdigest()[:10]
    out = folder / f"music-{choice['preset']}-{key}.wav"
    if not out.is_file():
        write_wav(out, bed(choice["preset"], float(choice["bpm"]), choice["energy"], seconds, end_at))
    return out
