"""Build a small synthetic job to smoke-test stages 4–6 without real footage.

    .venv/bin/python tests/make_synthetic_job.py jobs/_synthetic
    ./reel takes jobs/_synthetic --show
    ./reel cuts jobs/_synthetic --show
    ./reel face jobs/_synthetic --show      # no face in the test pattern: coverage 0% is expected

The transcript contains a false start, a messy take followed by a clean retake,
a long pause inside a sentence, a filler word inside a kept line and one
hallucinated "Thank you for watching." line. Expected: the clean retake wins,
the long pause and the filler are cut, the hallucination is dropped.
"""

from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

LINES = [  # text, start, end, suspect
    ("So today I want to", 1.0, 2.2, False),
    ("So today I want to talk about contracts.", 3.0, 5.6, False),
    ("Most people", 6.2, 6.9, False),
    ("never read them.", 7.9, 8.8, False),
    ("And um that is that is a big mistake.", 9.5, 12.0, False),
    ("And that is a big mistake.", 12.8, 14.4, False),
    ("Here is uh why.", 15.2, 16.6, False),
    ("Thank you for watching.", 19.0, 20.0, True),
    ("Let's go.", 21.0, 21.8, False),
]


def main(out: Path) -> None:
    import imageio_ffmpeg

    sr, duration = 16000, 24.0
    (out / "raw").mkdir(parents=True, exist_ok=True)
    (out / "audio").mkdir(exist_ok=True)
    rng = np.random.default_rng(1)
    audio = rng.normal(0, 0.003, int(sr * duration)).astype(np.float32)   # room noise
    segments = []
    for i, (text, s, e, suspect) in enumerate(LINES):
        words = text.split()
        step = (e - s) / len(words)
        ws = []
        for k, w in enumerate(words):
            a, b = round(s + k * step, 3), round(s + (k + 0.8) * step, 3)
            ws.append({"w": w, "start": a, "end": b, "p": 0.95})
            ia, ib = int(a * sr), int(b * sr)
            audio[ia:ib] += (rng.normal(0, 0.2, ib - ia) * np.hanning(ib - ia)).astype(np.float32)
        segments.append({"id": i, "start": s, "end": e, "text": text, "avg_logprob": -0.3,
                         "no_speech_prob": 0.05, "suspect": suspect, "words": ws})

    wav = out / "audio" / "synthetic.wav"
    with wave.open(str(wav), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc=size=540x960:rate=30:duration={duration}",
                    "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                    str(out / "raw" / "synthetic.mp4")], check=True)
    source = {"index": 0, "file": "synthetic.mp4", "wav": "audio/synthetic.wav", "duration": duration, "sample_rate": sr}
    (out / "audio" / "index.json").write_text(json.dumps({"sources": [source]}, indent=1))
    (out / "transcript.json").write_text(json.dumps({
        "model": "synthetic", "device": "none", "multilingual": False,
        "stats": {"words": sum(len(s["words"]) for s in segments), "segments": len(segments)},
        "sources": [{**source, "language": "en", "language_probability": 1.0, "segments": segments}],
    }, indent=1))
    print(f"synthetic job ready at {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "jobs/_synthetic"))
