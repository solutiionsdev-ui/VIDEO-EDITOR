"""A job folder: where every stage reads its inputs and writes its result.

Each stage writes one file (intake.json, transcript.json, ...) and records its
status and timing in job.json, so any stage can be re-run on its own and the
real cost per reel can be measured later.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console

console = Console(highlight=False)

STAGES = ["intake", "audio", "transcribe", "takes", "cuts", "face", "beats", "stills", "render"]
RUN_DEFAULT_TO = "stills"   # `reel run` stops at the beat board; `reel render` is the client's approval

RAW_EXTENSIONS = {".mp4", ".mov", ".m4v", ".hevc", ".avi", ".mkv", ".webm", ".mts", ".3gp"}


class JobError(Exception):
    """A stage could not finish. The message is meant to be read by a person."""


@dataclass
class Job:
    path: Path
    state: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = Path(self.path).resolve()
        if not self.path.is_dir():
            raise JobError(f"Job folder not found: {self.path}")
        self.state = self._load_state()

    # -- folders -----------------------------------------------------------
    @property
    def raw_dir(self) -> Path:
        return self.path / "raw"

    @property
    def audio_dir(self) -> Path:
        return self.path / "audio"

    @property
    def previews_dir(self) -> Path:
        return self.path / "previews"

    @property
    def name(self) -> str:
        return self.path.name

    def raw_files(self) -> list[Path]:
        """Footage files in raw/, in a stable order (by name)."""
        if not self.raw_dir.is_dir():
            return []
        files = [p for p in self.raw_dir.iterdir() if p.is_file() and not p.name.startswith(".")]
        return sorted(files, key=lambda p: p.name.lower())

    def optional_files(self, folder: str) -> list[Path]:
        d = self.path / folder
        if not d.is_dir():
            return []
        return sorted(p for p in d.iterdir() if p.is_file() and not p.name.startswith("."))

    def note(self) -> str:
        for name in ("note.md", "note.txt", "brief.md", "brief.txt"):
            p = self.path / name
            if p.is_file():
                return p.read_text(encoding="utf-8").strip()
        return ""

    # -- json files --------------------------------------------------------
    def file(self, name: str) -> Path:
        return self.path / name

    def has(self, name: str) -> bool:
        return self.file(name).is_file()

    def read_json(self, name: str) -> Any:
        p = self.file(name)
        if not p.is_file():
            raise JobError(f"{name} not found in {self.path}. Run the stage that creates it first.")
        with p.open(encoding="utf-8") as f:
            return json.load(f)

    def write_json(self, name: str, data: Any) -> Path:
        p = self.file(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(p)
        return p

    # -- state and log -----------------------------------------------------
    def _load_state(self) -> dict[str, Any]:
        p = self.path / "job.json"
        if p.is_file():
            with p.open(encoding="utf-8") as f:
                return json.load(f)
        return {
            "job": self.path.name,
            "created": _now(),
            "client": None,
            "stages": {},
            "usage": {"claude_tokens_in": 0, "claude_tokens_out": 0, "claude_tokens_cached": 0},
        }

    def save_state(self) -> None:
        self.write_json("job.json", self.state)

    def set_client(self, slug: str) -> None:
        self.state["client"] = slug
        self.save_state()

    def log(self, message: str, style: str | None = None) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with (self.path / "log.txt").open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {message}\n")
        console.print(message, style=style)

    def stage_status(self, stage: str) -> str | None:
        return self.state.get("stages", {}).get(stage, {}).get("status")

    def run_stage(self, stage: str, fn, *args, **kwargs):
        """Run a stage function, timing it and recording the outcome in job.json."""
        console.rule(f"[bold]{stage}")
        started = time.time()
        entry = {"status": "running", "started": _now()}
        self.state.setdefault("stages", {})[stage] = entry
        self.save_state()
        try:
            result = fn(self, *args, **kwargs)
        except Exception as e:  # noqa: BLE001 - we want every failure recorded
            entry.update(status="failed", error=str(e), seconds=round(time.time() - started, 1))
            self.save_state()
            self.log(f"[red]{stage} failed:[/red] {e}")
            raise
        seconds = round(time.time() - started, 1)
        entry.update(status="done", finished=_now(), seconds=seconds)
        self.save_state()
        self.log(f"[green]{stage} done[/green] in {seconds}s")
        return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fmt_time(seconds: float) -> str:
    """0:01:23.4 style for people; keeps tenths."""
    seconds = max(0.0, float(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    if h:
        return f"{h}:{m:02d}:{s:04.1f}"
    return f"{m}:{s:04.1f}"


def fmt_clock(seconds: float) -> str:
    """m:ss without decimals, for summaries."""
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"
