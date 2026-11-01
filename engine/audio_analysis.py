"""Simple audio measurements: levels, clipping, noise floor, silence finding.

Everything works on mono float32 samples from media.decode_audio().
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPS = 1e-9


def to_db(x: np.ndarray | float) -> np.ndarray | float:
    return 20.0 * np.log10(np.maximum(x, EPS))


@dataclass
class Envelope:
    """RMS level in dBFS over short windows."""

    times: np.ndarray   # window centre, seconds
    db: np.ndarray      # dBFS per window
    hop: float

    def level_at(self, t: float) -> float:
        i = int(np.clip(round((t - self.times[0]) / self.hop), 0, len(self.db) - 1))
        return float(self.db[i])

    def slice(self, t0: float, t1: float) -> np.ndarray:
        i0 = int(np.clip(np.searchsorted(self.times, t0), 0, len(self.db)))
        i1 = int(np.clip(np.searchsorted(self.times, t1), 0, len(self.db)))
        if i1 <= i0:
            i1 = min(i0 + 1, len(self.db))
        return self.db[i0:i1]

    def quietest(self, t0: float, t1: float) -> float:
        """Time of the lowest level between t0 and t1 (falls back to the midpoint)."""
        i0 = int(np.clip(np.searchsorted(self.times, t0), 0, len(self.db) - 1))
        i1 = int(np.clip(np.searchsorted(self.times, t1), 0, len(self.db) - 1))
        if i1 <= i0:
            return (t0 + t1) / 2
        j = i0 + int(np.argmin(self.db[i0:i1 + 1]))
        return float(self.times[j])


def envelope(samples: np.ndarray, sr: int, win: float = 0.02, hop: float = 0.01) -> Envelope:
    n = len(samples)
    w = max(1, int(win * sr))
    h = max(1, int(hop * sr))
    if n < w:
        samples = np.pad(samples, (0, w - n))
        n = len(samples)
    count = 1 + (n - w) // h
    idx = np.arange(w)[None, :] + h * np.arange(count)[:, None]
    frames = samples[idx]
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    times = (np.arange(count) * h + w / 2) / sr
    return Envelope(times=times, db=np.asarray(to_db(rms)), hop=hop)


@dataclass
class AudioStats:
    peak_dbfs: float
    rms_dbfs: float
    speech_dbfs: float      # 90th percentile of window levels: how loud the voice is
    noise_floor_dbfs: float # 10th percentile: the room
    snr_db: float
    clipped_fraction: float # share of samples sitting on a flat top at the peak level
    clipped_runs: int       # flat-top bursts: audible distortion
    clipped_per_minute: float
    silent_fraction: float  # share of windows near the noise floor

    def as_dict(self) -> dict:
        out = {}
        for k, v in self.__dict__.items():
            if isinstance(v, float):
                out[k] = round(v, 5) if k == "clipped_fraction" else round(v, 2)
            else:
                out[k] = v
        return out


def analyse(samples: np.ndarray, sr: int, env: Envelope | None = None) -> AudioStats:
    if len(samples) == 0:
        return AudioStats(-120, -120, -120, -120, 0, 0, 0, 0, 1.0)
    env = env or envelope(samples, sr)
    absx = np.abs(samples)
    peak_lin = float(absx.max())
    peak = float(to_db(peak_lin))
    rms = float(to_db(np.sqrt(np.mean(samples.astype(np.float64) ** 2))))
    speech = float(np.percentile(env.db, 90))
    floor = float(np.percentile(env.db, 10))
    clipped_fraction, runs = _flat_tops(samples, absx, peak_lin)
    minutes = max(1e-6, len(samples) / sr / 60)
    silent = float(np.mean(env.db < floor + 6))
    return AudioStats(peak, rms, speech, floor, speech - floor, clipped_fraction, runs, runs / minutes, silent)


def _flat_tops(samples: np.ndarray, absx: np.ndarray, peak_lin: float) -> tuple[float, int]:
    """Real clipping shows as flat runs at the ceiling, not just loud samples.

    Decoded audio can legitimately exceed 1.0 (hot recordings), so we look for
    consecutive near-identical samples at the file's own peak level.
    """
    if peak_lin < 0.5 or len(samples) < 4:
        return 0.0, 0
    near_peak = absx >= 0.97 * peak_lin
    flat = np.r_[False, np.abs(np.diff(samples)) < 0.002 * peak_lin]
    clipped = near_peak & flat
    if not clipped.any():
        return 0.0, 0
    edges = np.diff(clipped.astype(np.int8))
    starts = np.where(edges == 1)[0] + 1
    ends = np.where(edges == -1)[0] + 1
    if clipped[0]:
        starts = np.r_[0, starts]
    if clipped[-1]:
        ends = np.r_[ends, len(clipped)]
    lengths = ends - starts
    runs = int(np.sum(lengths >= 3))
    return float(np.sum(lengths[lengths >= 3]) / len(samples)), runs


def span_quality(env: Envelope, t0: float, t1: float, file_stats: AudioStats) -> dict:
    """How a take sounds relative to the whole file: 0..1 quality, plus details."""
    seg = env.slice(t0, t1)
    if len(seg) == 0:
        return {"quality": 0.5, "level_dbfs": file_stats.speech_dbfs, "level_vs_file": 0.0}
    level = float(np.percentile(seg, 90))
    delta = level - file_stats.speech_dbfs          # negative = quieter than usual
    quality = 1.0 - min(1.0, abs(delta) / 12.0) * 0.6
    return {"quality": round(quality, 3), "level_dbfs": round(level, 1), "level_vs_file": round(delta, 1)}
