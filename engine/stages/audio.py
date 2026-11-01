"""Stage 2 — audio first.

Extracts the voice track of every footage file to audio/<name>.wav (16 kHz mono,
what the transcriber wants). Everything up to the beat board runs on these.
"""

from __future__ import annotations

from ..job import Job, fmt_clock
from ..media import extract_wav, probe

OUTPUT = "audio/index.json"


def run(job: Job, profile: dict) -> dict:
    intake = job.read_json("intake.json") if job.has("intake.json") else None
    usable = None
    if intake:
        usable = {f["file"] for f in intake["files"] if f.get("role") == "footage" and f.get("ok")}

    sources = []
    for i, raw in enumerate(job.raw_files()):
        if usable is not None and raw.name not in usable:
            job.log(f"  skipping {raw.name} (failed intake)")
            continue
        wav = job.audio_dir / f"{raw.stem}.wav"
        extract_wav(raw, wav, 16000)
        duration = probe(wav).duration
        sources.append({
            "index": len(sources),
            "file": raw.name,
            "wav": f"audio/{wav.name}",
            "duration": round(duration, 3),
            "sample_rate": 16000,
        })
        job.log(f"  {raw.name} → audio/{wav.name} ({fmt_clock(duration)}, {wav.stat().st_size/1e6:.1f} MB)")

    result = {"sources": sources}
    job.write_json(OUTPUT, result)
    return result
