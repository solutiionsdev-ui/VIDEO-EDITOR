"""Stage 0 for infographic videos: a script instead of footage.

A job of kind "infographic" has no camera footage. It has the words (script.md) and,
optionally, a recorded voice-over (voiceover/<file>). This stage turns either into the same
files the footage stages write (intake.json … cuts.json, an empty face.json), so the beat
plan, the stills, the render and the quality check run unchanged. The picture under the
graphics is blank, and the planner may only use layouts that show no footage.

    script only      the words are timed at a reading pace; the video is silent unless
                     music is supplied
    with voice-over  the recording is transcribed for timing, pauses and retakes are cut as
                     usual, and where the transcriber and the script agree on a word the
                     script's spelling wins
"""

from __future__ import annotations

import difflib
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..job import Job, JobError, console, fmt_clock
from ..media import probe, run_ffmpeg
from ..profile import ROOT, get
from ..text import normalize

OUTPUT = "script.json"
SCRIPT_FILES = ("script.md", "script.txt")
VOICE_DIR = "voiceover"
VOICE_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".mp4", ".mov", ".m4v", ".webm"}
WIDTH, HEIGHT, FPS = 1080, 1920, 30
FOOTAGE_STAGES = ("intake", "audio", "transcribe", "takes", "cuts", "face")   # what this stage stands in for
LEAD_IN, TAIL = 0.3, 0.6


def run(job: Job, profile: dict, show: bool = False) -> dict:
    text = script_text(job)
    if not text:
        raise JobError("An infographic video needs its words: put them in script.md in the job folder.")
    voice = voice_file(job)
    job.raw_dir.mkdir(exist_ok=True)
    if voice:
        result = _from_voiceover(job, profile, voice, text)
    else:
        result = _from_script(job, profile, text)
    _merge_state(job)      # the stages run in other processes rewrote job.json
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for stage in FOOTAGE_STAGES:
        entry = job.state.setdefault("stages", {}).get(stage) or {}
        if entry.get("status") != "done":
            job.state["stages"][stage] = {"status": "done", "started": now, "finished": now, "seconds": 0, "by": "script"}
    job.write_json(OUTPUT, result)
    job.log(f"  {result['words']} words · {fmt_clock(result['seconds'])} · "
            + ("timed by the voice-over" if voice else f"timed at {result['words_per_second']} words a second, no voice"))
    if show:
        report(job, result)
    return result


def script_text(job: Job) -> str:
    """The words of the video, without markdown marks (headings, bullets, quotes)."""
    for name in SCRIPT_FILES:
        p = job.path / name
        if p.is_file():
            lines = [re.sub(r"^\s*(?:[#>*\-]+|\d+[.)])\s+", "", ln).strip() for ln in p.read_text(encoding="utf-8").splitlines()]
            return "\n".join(lines).strip()
    return ""


def voice_file(job: Job) -> Path | None:
    d = job.path / VOICE_DIR
    if not d.is_dir():
        return None
    files = sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in VOICE_EXT and not p.name.startswith("."))
    return files[0] if files else None


# -- the two sources --------------------------------------------------------------------

def _from_script(job: Job, profile: dict, text: str) -> dict:
    """No voice: every word gets its time at a steady reading pace, with a breath at punctuation."""
    wps = float(get(profile, "infographic.words_per_second", 2.6))
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    all_tokens = [w for p in paragraphs for w in p.split()]
    if not all_tokens:
        raise JobError("script.md has no words.")
    mean_len = sum(len(normalize(w)) or 1 for w in all_tokens) / len(all_tokens)
    t = LEAD_IN
    segments, units, words = [], [], []
    for pi, para in enumerate(paragraphs):
        seg_words = []
        for sentence in [s for s in re.split(r"(?<=[.!?…؟:])\s+", " ".join(para.split())) if s.strip()]:
            unit_words = []
            for w in sentence.split():
                dur = (1.0 / wps) * (0.62 + 0.38 * (len(normalize(w)) or 1) / mean_len)
                word = {"w": w, "start": round(t, 3), "end": round(t + dur, 3), "p": 1.0}
                t += dur
                if w[-1:] in ",;،":
                    t += 0.18
                unit_words.append(word)
            t += 0.42      # a breath after the sentence
            units.append({"id": len(units), "source": 0, "segment": pi, "start": unit_words[0]["start"], "end": unit_words[-1]["end"],
                          "text": sentence, "words": unit_words, "suspect": False, "kept": True, "reason": None, "group": None})
            seg_words += unit_words
        t += 0.35          # a longer one after the paragraph
        segments.append({"id": pi, "start": seg_words[0]["start"], "end": seg_words[-1]["end"], "text": " ".join(para.split()),
                         "avg_logprob": 0.0, "no_speech_prob": 0.0, "suspect": False, "words": seg_words})
        words += seg_words
    total = round(words[-1]["end"] + TAIL, 3)
    blank = job.raw_dir / "script.mp4"
    _blank_video(blank, total, None)
    language = str((get(profile, "client.languages", ["en"]) or ["en"])[0])
    _write_all(job, profile, blank, total, language, segments, units, words, source="script")
    return {"source": "script", "voice": None, "words": len(words), "seconds": total, "words_per_second": wps,
            "paragraphs": len(paragraphs), "sentences": len(units)}


def _from_voiceover(job: Job, profile: dict, voice: Path, text: str) -> dict:
    """A recorded voice: the usual audio, transcript, takes and cut stages run on it over a blank picture."""
    duration = probe(voice).duration
    if not duration or duration < 1.0:
        raise JobError(f"{voice.name} has no usable audio.")
    blank = job.raw_dir / "voiceover.mp4"
    _blank_video(blank, duration, voice)
    job.write_json("intake.json", _intake(job, blank, duration, source="voice-over", note=f"voice-over {voice.name} under a blank picture"))
    client = job.state.get("client")
    for stage in ("audio", "transcribe"):
        _run(job, stage, client)
    changed = _use_script_spelling(job, text)
    for stage in ("takes", "cuts"):
        _run(job, stage, client, extra=["--no-preview"] if stage == "cuts" else [])
    job.write_json("face.json", _no_face(blank.name))
    cuts = job.read_json("cuts.json")
    n = sum(len(s["words"]) for s in cuts["segments"])
    if not n:
        raise JobError(f"No words were heard in {voice.name}. Check the recording, or remove it to make the video from the script alone.")
    return {"source": "voiceover", "voice": voice.name, "words": n, "seconds": round(float(cuts["summary"]["seconds_out"]), 3),
            "words_per_second": round(n / max(1.0, float(cuts["summary"]["seconds_out"])), 2), "spelling_from_script": changed,
            "recorded_seconds": round(duration, 2)}


# -- pieces -----------------------------------------------------------------------------

def _blank_video(out: Path, seconds: float, audio: Path | None) -> None:
    """A black 9:16 picture of the given length, with the voice under it or with silence."""
    picture = ["-f", "lavfi", "-i", f"color=c=black:s={WIDTH}x{HEIGHT}:r={FPS}:d={seconds:.3f}"]
    sound = ["-i", str(audio)] if audio else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    run_ffmpeg(picture + sound + ["-map", "0:v", "-map", "1:a", "-t", f"{seconds:.3f}",
                                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
                                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out)])


def _intake(job: Job, blank: Path, seconds: float, source: str, note: str) -> dict:
    # the blank picture stands where footage would, so the later stages find it under the role they look for
    return {"status": "pass", "client": job.state.get("client"), "total_raw_seconds": round(seconds, 2),
            "files": [{"file": blank.name, "role": "footage", "source": source, "ok": True, "format": "mp4", "duration": round(seconds, 2),
                       "size_mb": round(blank.stat().st_size / 1e6, 1),
                       "video": {"codec": "h264", "width": WIDTH, "height": HEIGHT, "rotation": 0, "orientation": "portrait", "fps": float(FPS)},
                       "audio": {"codec": "aac"}}],
            "issues": [{"level": "info", "file": blank.name, "code": "infographic", "message": f"Infographic video: no footage, {note}."}]}


def _no_face(file: str) -> dict:
    return {"sources": [{"index": 0, "file": file, "width": WIDTH, "height": HEIGHT, "fps": float(FPS)}], "settings": {},
            "segments": [], "summary": {"segments": 0, "frames": 0, "detected_fraction": 0.0,
                                         "segments_without_face": [], "segments_low_coverage": []}}


def _write_all(job: Job, profile: dict, blank: Path, total: float, language: str, segments: list[dict], units: list[dict],
               words: list[dict], source: str) -> None:
    text = " ".join(w["w"] for w in words)
    job.write_json("intake.json", _intake(job, blank, total, source="script", note="the words are timed at a reading pace"))
    job.write_json("audio/index.json", {"sources": [{"index": 0, "file": blank.name, "wav": None, "duration": total, "sample_rate": 16000}]})
    job.write_json("transcript.json", {"model": "script", "device": "none", "multilingual": False,
                                       "sources": [{"index": 0, "file": blank.name, "wav": None, "duration": total, "language": language,
                                                    "language_probability": 1.0, "segments": segments}],
                                       "stats": {"words": len(words), "segments": len(segments)}})
    job.write_json("takes.json", {"units": units, "groups": [], "kept": [u["id"] for u in units], "dropped_suspect": [],
                                  "summary": {"lines": len(units), "retake_groups": 0, "takes_removed": 0, "suspect_removed": 0, "seconds_removed": 0}})
    job.write_json("cuts.json", {
        "settings": {"source": source},
        "segments": [{"source": 0, "start": 0.0, "end": total, "words": words, "units": [u["id"] for u in units],
                      "quiet_threshold_dbfs": None, "id": 0, "duration": total, "text": text, "n_words": len(words), "out_start": 0}],
        "removed": [], "script": text,
        "summary": {"segments": 1, "seconds_in": total, "seconds_out": total, "removed": {}}})
    job.write_json("face.json", _no_face(blank.name))


def _run(job: Job, stage: str, client: str | None, extra: list[str] | None = None) -> None:
    cmd = [sys.executable, "-m", "engine.cli", stage, str(job.path)] + (["--client", client] if client else []) + list(extra or [])
    code = subprocess.call(cmd, cwd=str(ROOT))
    if code != 0:
        raise JobError(f"the {stage} stage failed on the voice-over (exit {code})")


def _use_script_spelling(job: Job, text: str) -> int:
    """Where the transcriber and the script agree on a word, or disagree one for one, write the script's word."""
    transcript = job.read_json("transcript.json")
    script_words = text.split()
    key = [normalize(w) for w in script_words]
    changed = 0
    for src in transcript["sources"]:
        heard = [w for seg in src["segments"] for w in seg.get("words", [])]
        sm = difflib.SequenceMatcher(a=[normalize(w["w"]) for w in heard], b=key, autojunk=False)
        for tag, a0, a1, b0, b1 in sm.get_opcodes():
            if tag == "equal" or (tag == "replace" and a1 - a0 == b1 - b0):
                for k in range(a1 - a0):
                    w, s = heard[a0 + k], script_words[b0 + k]
                    w["from_script"] = True
                    if w["w"].strip() != s:
                        w["heard"] = w["w"]
                        w["w"] = s
                        changed += 1
        for seg in src["segments"]:
            # the transcriber writes "%" and "," as words of their own; the script's word already carries its
            # mark ("40%"), so the loose mark is folded into the word before it
            kept: list[dict] = []
            for w in seg.get("words", []):
                if kept and not normalize(w["w"]) and kept[-1].get("from_script"):
                    kept[-1]["end"] = w["end"]
                else:
                    kept.append(w)
            for w in kept:
                w.pop("from_script", None)
            if seg.get("words"):
                seg["words"] = kept
                seg["text"] = " ".join(w["w"].strip() for w in seg["words"])
    job.write_json("transcript.json", transcript)
    return changed


def _merge_state(job: Job) -> None:
    """Take over what other processes wrote into job.json, keeping this stage's own running entry."""
    fresh = job._load_state()
    mine = job.state.setdefault("stages", {}).get("script")
    for k, v in fresh.items():
        if k != "stages":
            job.state[k] = v
    for k, v in fresh.get("stages", {}).items():
        if k != "script":
            job.state["stages"][k] = v
    if mine is not None:
        job.state["stages"]["script"] = mine


def report(job: Job, data: dict) -> None:
    console.print(f"\nScript: {data['words']} words · {fmt_clock(data['seconds'])} · "
                  + (f"voice-over {data['voice']}" if data.get("voice") else f"no voice, {data['words_per_second']} words a second"))
