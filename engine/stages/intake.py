"""Stage 1 — intake check.

Reads every file in the job folder and flags problems before any work starts.
Writes intake.json with a status of pass / warnings / blocked.
"""

from __future__ import annotations

from pathlib import Path

from rich.table import Table

from .. import audio_analysis as aa
from ..job import Job, console, fmt_clock
from ..media import decode_audio, integrated_loudness, probe
from ..profile import get

OUTPUT = "intake.json"


def run(job: Job, profile: dict) -> dict:
    issues: list[dict] = []
    files: list[dict] = []
    accepted = {str(x).lower() for x in get(profile, "intake.accepted_formats", ["mp4", "mov", "m4v"])}

    def issue(level: str, file: str | None, code: str, message: str) -> None:
        issues.append({"level": level, "file": file, "code": code, "message": message})

    raws = job.raw_files()
    if not raws:
        issue("blocker", None, "no_footage", f"No footage found in {job.raw_dir}. Put the raw clips in raw/.")

    total_duration = 0.0
    for p in raws:
        ext = p.suffix.lower().lstrip(".")
        entry: dict = {"file": p.name, "role": "footage", "ok": True}
        if ext not in accepted:
            issue("blocker", p.name, "unsupported_format",
                  f"{p.name}: .{ext} is not supported. Accepted: {', '.join(sorted(accepted)).upper()}.")
            entry["ok"] = False
            files.append(entry)
            continue
        try:
            info = probe(p)
        except Exception as e:  # noqa: BLE001
            issue("blocker", p.name, "unreadable", f"{p.name}: cannot be read ({e}).")
            entry["ok"] = False
            files.append(entry)
            continue

        entry.update(info.as_dict())
        total_duration += info.duration
        job.log(f"  {p.name}: {fmt_clock(info.duration)}, "
                f"{entry.get('video', {}).get('width', '?')}x{entry.get('video', {}).get('height', '?')} "
                f"{entry.get('video', {}).get('orientation', '')}, "
                f"audio {'yes' if info.audio else 'MISSING'}")

        if info.video is None:
            issue("blocker", p.name, "no_video", f"{p.name}: has no video track.")
            entry["ok"] = False
        else:
            _check_video(entry, p.name, info, profile, issue)

        if info.audio is None:
            issue("blocker", p.name, "no_audio", f"{p.name}: has no audio track. Talking-head footage needs sound.")
            entry["ok"] = False
        else:
            _check_audio(entry, p, info, profile, issue)

        files.append(entry)

    max_minutes = float(get(profile, "format.max_raw_minutes", 10))
    if total_duration > max_minutes * 60:
        issue("blocker", None, "too_long",
              f"Raw footage is {fmt_clock(total_duration)}; the plan allows {max_minutes:g} minutes per video.")

    # Optional inputs: B-roll, music, reference. Problems here only warn.
    for folder, role in (("broll", "b-roll"), ("music", "music"), ("reference", "reference")):
        for p in job.optional_files(folder):
            entry = {"file": f"{folder}/{p.name}", "role": role, "ok": True}
            try:
                info = probe(p)
                entry.update(info.as_dict())
                if role == "music" and info.audio is None:
                    issue("warning", entry["file"], "no_audio", f"{entry['file']}: music file has no audio.")
                    entry["ok"] = False
            except Exception as e:  # noqa: BLE001
                issue("warning", entry["file"], "unreadable", f"{entry['file']}: cannot be read ({e}).")
                entry["ok"] = False
            files.append(entry)

    note = job.note()
    if note:
        issue("info", None, "note", f"Client note: {note[:200]}")

    levels = {i["level"] for i in issues}
    status = "blocked" if "blocker" in levels else ("warnings" if "warning" in levels else "pass")
    result = {
        "status": status,
        "client": profile.get("client", {}).get("slug"),
        "total_raw_seconds": round(total_duration, 2),
        "files": files,
        "issues": issues,
    }
    job.write_json(OUTPUT, result)
    report(job, result)
    return result


def _check_video(entry: dict, name: str, info, profile: dict, issue) -> None:
    v = info.video
    min_side = int(get(profile, "intake.min_short_side", 720))
    if v.orientation == "landscape":
        issue("warning", name, "landscape",
              f"{name}: landscape footage. The 9:16 crop will follow the face; expect a tighter frame.")
    elif v.orientation == "square":
        issue("warning", name, "square", f"{name}: square footage; the 9:16 crop will follow the face.")
    if min(v.display_width, v.display_height) < min_side:
        issue("warning", name, "low_resolution",
              f"{name}: {v.display_width}x{v.display_height} is below {min_side}p; the reel may look soft.")
    if v.fps and v.fps < 23:
        issue("warning", name, "low_fps", f"{name}: {v.fps:.1f} fps is unusually low.")
    if info.duration < 3:
        issue("warning", name, "very_short", f"{name}: only {info.duration:.1f}s long.")


def _check_audio(entry: dict, path: Path, info, profile: dict, issue) -> None:
    name = path.name
    a = info.audio
    samples = decode_audio(path, 16000)
    stats = aa.analyse(samples, 16000)
    lufs = integrated_loudness(path)
    entry["audio"].update(stats.as_dict())
    entry["audio"]["lufs"] = round(lufs, 1) if lufs is not None else None

    clip_warn = float(get(profile, "intake.clipping_warn_per_minute", 20))
    clip_block = float(get(profile, "intake.clipping_block_per_minute", 300))
    quiet = float(get(profile, "intake.quiet_dbfs", -35))
    min_snr = float(get(profile, "intake.min_snr_db", 12))

    if stats.clipped_per_minute >= clip_block or stats.clipped_fraction >= 0.01:
        issue("blocker", name, "clipping",
              f"{name}: audio is heavily clipped/distorted ({stats.clipped_runs} bursts, "
              f"{stats.clipped_per_minute:.0f} per minute). Re-record or send another take.")
        entry["ok"] = False
    elif stats.clipped_per_minute >= clip_warn:
        issue("warning", name, "clipping",
              f"{name}: some audio clipping ({stats.clipped_runs} bursts, {stats.clipped_per_minute:.0f} per minute). "
              f"Loud moments may sound distorted.")
    if stats.peak_dbfs > 0:
        issue("info", name, "hot_audio",
              f"{name}: recording is hot (peaks {stats.peak_dbfs:+.1f} dB over full scale); it will be normalised.")
    if stats.speech_dbfs < quiet:
        issue("warning", name, "quiet_audio",
              f"{name}: voice is very quiet ({stats.speech_dbfs:.0f} dBFS). It will be boosted, which also raises noise.")
    if stats.snr_db < min_snr:
        issue("warning", name, "noisy_audio",
              f"{name}: noisy audio (voice only {stats.snr_db:.0f} dB above the room). Captions may have errors.")
    if stats.silent_fraction > 0.9:
        issue("warning", name, "mostly_silent", f"{name}: almost no speech detected in the audio.")
    if a.sample_rate and a.sample_rate < 16000:
        issue("warning", name, "low_sample_rate", f"{name}: audio sample rate is only {a.sample_rate} Hz.")


def report(job: Job, data: dict) -> None:
    status = data["status"]
    colour = {"pass": "green", "warnings": "yellow", "blocked": "red"}[status]
    console.print(f"\nIntake: [{colour} bold]{status.upper()}[/] · raw footage {fmt_clock(data['total_raw_seconds'])}")
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("file")
    table.add_column("length")
    table.add_column("video")
    table.add_column("audio")
    for f in data["files"]:
        v = f.get("video") or {}
        a = f.get("audio") or {}
        video = f"{v.get('width', '?')}x{v.get('height', '?')} {v.get('orientation', '')} {v.get('fps', '')}fps {v.get('codec', '')}".strip() if v else "—"
        audio = (f"{a.get('speech_dbfs', '?')} dBFS voice, floor {a.get('noise_floor_dbfs', '?')}, "
                 f"{a.get('lufs', '?')} LUFS") if a.get("speech_dbfs") is not None else ("—" if not a else a.get("codec", ""))
        table.add_row(f["file"], fmt_clock(f.get("duration", 0)), video, audio)
    console.print(table)
    for i in data["issues"]:
        mark = {"blocker": "[red]✖[/]", "warning": "[yellow]![/]", "info": "[blue]i[/]"}[i["level"]]
        console.print(f"  {mark} {i['message']}")
    if not data["issues"]:
        console.print("  [green]✔[/] no problems found")
