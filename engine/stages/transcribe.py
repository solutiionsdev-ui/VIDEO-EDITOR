"""Stage 3 — transcription with word-level timestamps.

Uses faster-whisper on the CPU. Arabic, English and mixed clips are handled by
auto language detection (per segment when the client speaks more than one language).
Writes transcript.json.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from ..job import Job, console, fmt_time
from ..media import decode_audio
from ..profile import get
from ..text import preview

OUTPUT = "transcript.json"

# Lines Whisper tends to invent in silence or over music.
HALLUCINATION_PATTERNS = re.compile(
    r"(thank you for watching|thanks for watching|subscribe|like and subscribe|"
    r"ترجمة|اشترك في القناة|شكرا للمشاهدة|نانسي قنقر)", re.IGNORECASE)


def run(job: Job, profile: dict, show: bool = False) -> dict:
    index = job.read_json("audio/index.json")
    model_name = str(get(profile, "transcription.model", "large-v3-turbo"))
    language = str(get(profile, "transcription.language", "auto") or "auto").lower()
    vocabulary = [str(v) for v in (get(profile, "transcription.vocabulary", []) or [])]
    spoken = [str(x).lower() for x in (get(profile, "client.languages", []) or [])]
    multilingual = language == "auto" and len(spoken) > 1

    from faster_whisper import WhisperModel

    model_path = _local_model(model_name)
    if model_path:
        job.log(f"  loading whisper model '{model_name}' from {model_path}")
    else:
        job.log(f"  loading whisper model '{model_name}' (first time: downloads about 1.6 GB)")
    model = WhisperModel(model_path or model_name, device="cpu", compute_type="int8")

    kwargs = dict(
        language=None if language == "auto" else language,
        word_timestamps=True,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200},
        condition_on_previous_text=False,   # retakes repeat lines; don't let Whisper "correct" them away
        initial_prompt=_prompt(vocabulary),
        hotwords=_prompt(vocabulary),       # re-applied to every 30 s window (initial_prompt is not)
    )
    if multilingual and "multilingual" in inspect.signature(model.transcribe).parameters:
        kwargs["multilingual"] = True

    sources = []
    total_words = 0
    for src in index["sources"]:
        wav = job.path / src["wav"]
        duration = float(src["duration"])
        segments_out = []
        # Decode with our own ffmpeg and pass samples: avoids faster-whisper's PyAV decoder.
        samples = decode_audio(wav, 16000)
        segments, info = model.transcribe(samples, **kwargs)
        with Progress(TextColumn("  transcribing {task.fields[name]}"), BarColumn(), TextColumn("{task.percentage:>3.0f}%"),
                      TimeElapsedColumn(), console=console, transient=True) as progress:
            task = progress.add_task("t", total=duration, name=src["file"])
            for seg in segments:
                words = [{
                    "w": w.word.strip(),
                    "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3),
                    "p": round(float(w.probability), 3),
                } for w in (seg.words or []) if w.word.strip()]
                text = seg.text.strip()
                suspect = bool(
                    (seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.8)
                    or HALLUCINATION_PATTERNS.search(text)
                    or (words and (words[-1]["end"] - words[0]["start"]) < 0.1 * len(words) and len(words) > 6)
                )
                segments_out.append({
                    "id": len(segments_out),
                    "start": round(float(seg.start), 3),
                    "end": round(float(seg.end), 3),
                    "text": text,
                    "avg_logprob": round(float(seg.avg_logprob), 3),
                    "no_speech_prob": round(float(seg.no_speech_prob), 3),
                    "suspect": suspect,
                    "words": words,
                })
                total_words += len(words)
                progress.update(task, completed=min(duration, float(seg.end)))
        sources.append({
            "index": src["index"],
            "file": src["file"],
            "wav": src["wav"],
            "duration": duration,
            "language": info.language,
            "language_probability": round(float(info.language_probability), 3),
            "segments": segments_out,
        })
        job.log(f"  {src['file']}: {len(segments_out)} lines, {sum(len(s['words']) for s in segments_out)} words, "
                f"language {info.language} ({info.language_probability:.0%})")

    result = {
        "model": model_name,
        "device": "cpu/int8",
        "multilingual": multilingual,
        "sources": sources,
        "stats": {"words": total_words, "segments": sum(len(s["segments"]) for s in sources)},
    }
    job.write_json(OUTPUT, result)
    if show:
        report(job, result)
    return result


MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "models"


def _prompt(vocabulary: list[str]) -> str:
    """A punctuated prompt keeps Whisper punctuating (it tends to stop mid-file otherwise)
    and introduces the client's names so they are spelled right."""
    names = ", ".join(vocabulary)
    base = "Okay, let's start. This is the first sentence, and this is the second one."
    return f"{base} We also mention {names}." if names else base


def _local_model(name: str) -> str | None:
    """A model folder under models/ (e.g. models/faster-whisper-large-v3-turbo) wins over a download."""
    for candidate in (MODELS_DIR / name, MODELS_DIR / f"faster-whisper-{name}"):
        if (candidate / "model.bin").is_file() and (candidate / "config.json").is_file():
            return str(candidate)
    return None


def report(job: Job, data: dict) -> None:
    for src in data["sources"]:
        console.print(f"\n[bold]{src['file']}[/] · language {src['language']} · {len(src['segments'])} lines")
        table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("#", justify="right")
        table.add_column("time")
        table.add_column("line")
        for s in src["segments"]:
            flag = " [yellow](suspect)[/]" if s["suspect"] else ""
            table.add_row(str(s["id"]), f"{fmt_time(s['start'])}–{fmt_time(s['end'])}", preview(s["text"], 90) + flag)
        console.print(table)
    console.print(f"\n{data['stats']['words']} words in {data['stats']['segments']} lines "
                  f"(model {data['model']})")
