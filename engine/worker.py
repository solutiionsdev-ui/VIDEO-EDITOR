"""`reel worker`: the editor machine.

It polls jobs/queue.json, takes the oldest item, runs the engine on that job (each
stage in its own process, exactly like `reel run`), and writes what it is doing to
jobs/<job>/worker.json and jobs/workers/<name>.json so the Queue page can show it.

    edit       footage in → every stage up to the stills → render → quality check
    revise     the comments → changed beats re-rendered → quality check
    direction  the editor's note (and reference frames) → Claude's answer in the thread

When a stage needs Claude (the beat plan, or a comment the rules cannot read) the item
goes back on the queue as "waiting" and is picked up again once the plan file exists.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from . import ops
from .job import RAW_EXTENSIONS, STAGES, Job, JobError, console
from .profile import ROOT
from .stages import beats

POLL_EVERY_S = 2.0


def run_worker(name: str | None = None, once: bool = False, interval: float = 5.0) -> int:
    name = name or socket.gethostname().split(".")[0] or "editor"
    _NAME[:] = [name]
    console.print(f"editor [bold]{name}[/] watching {ops.QUEUE_FILE}  (Ctrl-C to stop)")
    try:
        while True:
            ops.heartbeat(name)
            item = ops.claim_next(name)
            if item is None:
                if once:
                    console.print("[dim]queue empty[/]")
                    return 0
                time.sleep(interval)
                continue
            process_item(name, item)
            if once:
                return 0
    except KeyboardInterrupt:
        console.print("\n[yellow]stopped[/]")
        return 130
    finally:
        ops.heartbeat(name)


def process_item(name: str, item: dict) -> None:
    job_path = ops.JOBS_DIR / item["job"]
    try:
        job = Job(job_path)
    except JobError as e:
        ops.finish_item(item["id"], "failed", str(e))
        return
    client = job.state.get("client")
    log = job.path / ops.WORKER_LOG
    console.print(f"[bold]{item['kind']}[/] {job.name} ({item['id']})")
    with log.open("a", encoding="utf-8") as f:
        f.write(f"\n==== {item['kind']} {item['id']} picked up by {name} at {datetime.now():%Y-%m-%d %H:%M:%S}\n")

    def report(stage: str | None, note: str = "") -> None:
        ops.write_json(job.path / ops.WORKER_STATE, {
            "worker": name, "item": item["id"], "kind": item["kind"], "stage": stage, "note": note,
            "stage_started": ops.now_iso(), "heartbeat": ops.now_iso()})
        ops.heartbeat(name, busy_with=job.name, stage=stage, item=item["id"])

    try:
        if item["kind"] == "revise":
            result, note = _revise(job, client, item, report, log)
        elif item["kind"] == "direction":
            result, note = _direction(job, client, report, log)
        else:
            result, note = _edit(job, client, report, log)
    except Exception as e:  # noqa: BLE001 - the queue must keep moving
        result, note = "failed", str(e)
    if result == "waiting":
        ops.release_waiting(item["id"], note)
        console.print(f"[yellow]waiting for Claude:[/] {note}")
    else:
        ops.finish_item(item["id"], result, note or None)
        colour = {"done": "green", "check_failed": "yellow"}.get(result, "red")
        console.print(f"[{colour}]{result}[/] {job.name} {note}")
    ops.write_json(job.path / ops.WORKER_STATE, {"worker": name, "item": item["id"], "kind": item["kind"], "stage": None,
                                                 "note": note, "result": result, "heartbeat": ops.now_iso()})
    ops.heartbeat(name)
    with log.open("a", encoding="utf-8") as f:
        f.write(f"==== {result} {note}\n")


# -- the two kinds of work -------------------------------------------------------------

def _edit(job: Job, client: str | None, report, log: Path) -> tuple[str, str]:
    footage = ops.meta(job).get("footage") or {}
    if footage.get("type") == "link" and footage.get("status") != "fetched":
        report("fetch footage", footage.get("url", ""))
        try:
            saved = fetch_link(footage["url"], job.raw_dir)
        except Exception as e:  # noqa: BLE001
            ops.set_meta(job, footage={**footage, "status": "failed", "error": str(e)})
            return "failed", f"could not fetch the footage: {e}"
        ops.set_meta(job, footage={**footage, "status": "fetched", "file": saved.name, "bytes": saved.stat().st_size})
    if not job.raw_files():
        return "failed", "no footage in raw/"
    for stage in ops.EDIT_STAGES:
        if _done(job, stage):
            continue
        code = _stage(job, client, stage, report, log)
        if stage == "intake" and code == 3:
            return "blocked", "intake found blockers (see the job page)"
        if stage == "beats" and code == beats.NEEDS_OPERATOR:
            return "waiting", "beat plan"
        if code != 0:
            return "failed", f"{stage} failed (see worker.log)"
    code = _stage(job, client, "render", report, log)
    if code == 5:
        return "check_failed", "rendered, but the quality check failed"
    if code != 0:
        return "failed", "render failed (see worker.log)"
    return "done", ""


def _revise(job: Job, client: str | None, item: dict, report, log: Path) -> tuple[str, str]:
    comments = [c for c in item.get("comments", []) if c.strip()]
    if not comments and not job.has("revision-plan.json"):
        return "failed", "no comments to apply"
    code = _stage(job, client, "revise", report, log, extra=comments)
    if code == beats.NEEDS_OPERATOR:
        return "waiting", "revision plan"
    if code == 5:
        return "check_failed", "re-rendered, but the quality check failed"
    if code != 0:
        return "failed", "revise failed (see worker.log)"
    return "done", ""


def _direction(job: Job, client: str | None, report, log: Path) -> tuple[str, str]:
    """Fold Claude Code's reply if it is there; else run the live editor on the API; else leave the
    request for Claude Code. The live editor's tools run engine stages through _stage, so the job
    page shows "revise" or "render" while Claude works."""
    from . import direction

    if direction.fold_reply(job):
        return "done", "reply from Claude Code folded into the thread"
    if not direction.pending(job):
        return "done", "already answered"
    report("direction", "asking Claude")
    with log.open("a", encoding="utf-8") as f:
        f.write(f"\n== direction {datetime.now():%H:%M:%S}\n")
    outcome: dict = {}

    def run_stage(stage: str, extra=()) -> int:
        return _stage(job, client, stage, report, log, extra=list(extra))

    def ask() -> None:
        try:
            outcome["msg"] = direction.answer_with_api(job, run_stage=run_stage)
        except Exception as e:  # noqa: BLE001 - reported below
            outcome["error"] = e

    t = threading.Thread(target=ask, daemon=True)
    t.start()
    started = ops.now_iso()
    while t.is_alive():
        cur = ops.read_json(job.path / ops.WORKER_STATE, {}) or {}
        fresh = ops.seconds_since(cur.get("heartbeat"))
        if cur.get("stage") in (None, "direction") or fresh is None or fresh > 2 * POLL_EVERY_S:
            ops.write_json(job.path / ops.WORKER_STATE, {
                "worker": _worker_name(), "stage": "direction", "stage_started": started, "heartbeat": ops.now_iso(),
                "kind": "direction"})
            ops.heartbeat(_worker_name(), busy_with=job.name, stage="direction")
        else:
            ops.heartbeat(_worker_name(), busy_with=job.name, stage=cur.get("stage"))
        t.join(POLL_EVERY_S)
    if "error" in outcome:
        direction._set_live(job, None)
        raise outcome["error"]
    if outcome.get("msg"):
        return "done", f"answered via {outcome['msg']['via']}"
    direction.write_request(job)
    return "waiting", "direction reply"


def _done(job: Job, stage: str) -> bool:
    fresh = Job(job.path)   # the stages run in other processes and rewrite job.json
    out = ops.STAGE_OUTPUT.get(stage)
    return fresh.stage_status(stage) == "done" and (out is None or fresh.has(out))


def _stage(job: Job, client: str | None, stage: str, report, log: Path, extra: list[str] | None = None) -> int:
    cmd = [sys.executable, "-m", "engine.cli", stage, str(job.path)]
    if client:
        cmd += ["--client", client]
    cmd += list(extra or [])
    report(stage)
    with log.open("a", encoding="utf-8") as f:
        f.write(f"\n== {stage} {datetime.now():%H:%M:%S}\n")
        f.flush()
        proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=str(ROOT))
        started = ops.now_iso()
        while proc.poll() is None:
            ops.write_json(job.path / ops.WORKER_STATE, {
                "worker": _worker_name(), "stage": stage, "stage_started": started, "heartbeat": ops.now_iso(),
                "kind": "revise" if stage == "revise" else "edit"})
            ops.heartbeat(_worker_name(), busy_with=job.name, stage=stage)
            time.sleep(POLL_EVERY_S)
    return proc.returncode


_NAME: list[str] = []


def _worker_name() -> str:
    return _NAME[0] if _NAME else socket.gethostname().split(".")[0]


# -- footage by link -------------------------------------------------------------------

_DRIVE = re.compile(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?(?:.*&)?id=)([\w-]+)")


def fetch_link(url: str, raw_dir: Path) -> Path:
    """Download a footage link into raw/. Google Drive share links are turned into direct downloads."""
    m = _DRIVE.search(url)
    if m:
        url = f"https://drive.google.com/uc?export=download&confirm=t&id={m.group(1)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Studiio Reel Editor)"})
    raw_dir.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=120) as r:
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype.startswith("text/html"):
            raise JobError("the link returned a web page, not a video. For Google Drive, share the file with "
                           "'anyone with the link' and paste the file link.")
        name = None
        cd = r.headers.get("Content-Disposition") or ""
        mm = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
        if mm:
            name = urllib.parse.unquote(mm.group(1)).strip()
        if not name:
            name = Path(urllib.parse.urlsplit(url).path).name or "footage.mp4"
        name = re.sub(r"[^\w.\-]+", "_", name)
        if Path(name).suffix.lower() not in RAW_EXTENSIONS:
            name += ".mp4"
        out = raw_dir / name
        tmp = out.with_suffix(out.suffix + ".part")
        with tmp.open("wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        tmp.replace(out)
    if out.stat().st_size < 100_000:
        out.unlink()
        raise JobError("the download is too small to be footage; check the link")
    return out
