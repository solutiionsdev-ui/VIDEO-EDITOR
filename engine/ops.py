"""The platform's stores and the honest status of a job.

Everything here is a small JSON file next to the job folders:

    jobs/queue.json            the work queue (edit and revise items), oldest first
    jobs/workers/<name>.json   one heartbeat file per editor machine
    jobs/<job>/approvals.json  the review verdicts (Studiio for now)
    jobs/<job>/worker.json     what the editor is doing on this job right now
    jobs/<job>/worker.log      the engine's output while it ran
    jobs/<job>/delivery/       the approved file
    jobs/<job>/direction/      the editor's thread with Claude (see direction.py)

A job's status is never stored. It is derived from these files and from what the
engine itself wrote (renders/vN.mp4, vN-check.json, beats-request.md ...), so the
pages can only show a state the folder is actually in.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .job import STAGES, Job, JobError
from .profile import CLIENTS_DIR, ROOT

JOBS_DIR = ROOT / "jobs"
QUEUE_FILE = JOBS_DIR / "queue.json"
WORKERS_DIR = JOBS_DIR / "workers"
APPROVALS = "approvals.json"
WORKER_STATE = "worker.json"
WORKER_LOG = "worker.log"
DELIVERY_DIR = "delivery"
KEEP_FINISHED = 300
ONLINE_AFTER_S = 30

STAGE_OUTPUT = {
    "intake": "intake.json", "audio": "audio/index.json", "transcribe": "transcript.json", "takes": "takes.json",
    "cuts": "cuts.json", "face": "face.json", "beats": "beats.json", "stills": "stills/index.json",
}
EDIT_STAGES = STAGES[:STAGES.index("stills") + 1]
WAIT_FILES = {"beat plan": "beats-plan.json", "revision plan": "revision-plan.json", "direction reply": "direction-reply.json"}
REQUEST_FILES = {"beat plan": "beats-request.md", "revision plan": "revision-request.md", "direction reply": "direction-request.md"}
KINDS = {"edit": "Edit", "revise": "Revise", "direction": "Direction"}


# -- small helpers -----------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path, default: Any) -> Any:
    try:
        with Path(path).open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:48] or "job"


def ago(iso: str | None, now: datetime | None = None) -> str:
    """'11 s ago', '3 min ago', '2 h ago', '3 days ago'; '' when unknown."""
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(str(iso))
    except ValueError:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    s = max(0.0, ((now or datetime.now(timezone.utc)) - t).total_seconds())
    if s < 60:
        return f"{int(s)} s ago"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{s / 3600:.0f} h ago"
    days = int(s // 86400)
    return "1 day ago" if days == 1 else f"{days} days ago"


def seconds_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - t).total_seconds())


# -- the queue ---------------------------------------------------------------------

def read_queue() -> list[dict]:
    return list(read_json(QUEUE_FILE, {"items": []}).get("items", []))


def write_queue(items: list[dict]) -> None:
    finished = [i for i in items if i.get("finished_at")]
    if len(finished) > KEEP_FINISHED:
        drop = {i["id"] for i in sorted(finished, key=lambda i: i["finished_at"])[: len(finished) - KEEP_FINISHED]}
        items = [i for i in items if i["id"] not in drop]
    write_json(QUEUE_FILE, {"items": items})


def enqueue(job_name: str, kind: str = "edit", comments: list[str] | None = None, note: str = "") -> dict:
    """Add work for a job. An open, unclaimed item of the same kind is reused: comments pile up
    on one revise item until the editor picks it up."""
    items = read_queue()
    for it in items:
        if it["job"] == job_name and it["kind"] == kind and not it.get("finished_at") and not it.get("claimed_by") \
                and it.get("result") != "waiting":
            if comments:
                it["comments"] = list(dict.fromkeys(it.get("comments", []) + list(comments)))
            if note:
                it["note"] = note
            write_queue(items)
            return it
    item = {
        "id": f"{kind}-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}",
        "job": job_name, "kind": kind, "comments": list(comments or []), "note": note,
        "created": now_iso(), "claimed_by": None, "claimed_at": None, "finished_at": None,
        "result": None, "waiting_on": None, "error": None,
    }
    items.append(item)
    write_queue(items)
    return item


def wait_cleared(item: dict) -> bool:
    name = WAIT_FILES.get(item.get("waiting_on") or "")
    return True if not name else (JOBS_DIR / item["job"] / name).is_file()


def claim_next(worker: str) -> dict | None:
    """The oldest item nobody holds, skipping items still waiting for a plan file."""
    items = read_queue()
    for it in items:
        if it.get("finished_at") or it.get("claimed_by"):
            continue
        if it.get("result") == "waiting" and not wait_cleared(it):
            continue
        if not (JOBS_DIR / it["job"]).is_dir():
            it.update(finished_at=now_iso(), result="failed", error="job folder is gone")
            continue
        it.update(claimed_by=worker, claimed_at=now_iso(), result=None, waiting_on=None, error=None)
        write_queue(items)
        return it
    write_queue(items)
    return None


def update_item(item_id: str, **fields: Any) -> dict | None:
    items = read_queue()
    for it in items:
        if it["id"] == item_id:
            it.update(fields)
            write_queue(items)
            return it
    return None


def finish_item(item_id: str, result: str, error: str | None = None) -> dict | None:
    return update_item(item_id, finished_at=now_iso(), result=result, error=error)


def release_waiting(item_id: str, waiting_on: str) -> dict | None:
    return update_item(item_id, claimed_by=None, claimed_at=None, result="waiting", waiting_on=waiting_on)


def items_for(job_name: str) -> list[dict]:
    """Newest first."""
    return sorted((i for i in read_queue() if i["job"] == job_name), key=lambda i: i["created"], reverse=True)


# -- editors (workers) --------------------------------------------------------------

def heartbeat(name: str, busy_with: str | None = None, stage: str | None = None, item: str | None = None) -> None:
    write_json(WORKERS_DIR / f"{slug(name)}.json",
               {"name": name, "last_seen": now_iso(), "busy_with": busy_with, "stage": stage, "item": item})


def workers() -> list[dict]:
    out = []
    if WORKERS_DIR.is_dir():
        for p in sorted(WORKERS_DIR.glob("*.json")):
            w = read_json(p, None)
            if not w:
                continue
            since = seconds_since(w.get("last_seen"))
            w["online"] = since is not None and since < ONLINE_AFTER_S
            out.append(w)
    return out


# -- approvals and delivery --------------------------------------------------------

def read_approvals(job: Job) -> dict:
    return read_json(job.path / APPROVALS, {"studiio": None, "history": []})


def set_review(job: Job, status: str, version: int, note: str = "", by: str = "Studiio") -> dict:
    data = read_approvals(job)
    entry = {"status": status, "version": version, "note": note, "by": by, "at": now_iso()}
    data["studiio"] = entry
    data.setdefault("history", []).append(entry)
    write_json(job.path / APPROVALS, data)
    return entry


def delivery_name(job: Job, version: int) -> str:
    return f"{job.state.get('client') or 'studiio'}-{job.name}-v{version}.mp4"


def delivered_file(job: Job, version: int | None) -> str | None:
    if not version:
        return None
    p = job.path / DELIVERY_DIR / delivery_name(job, version)
    return f"{DELIVERY_DIR}/{p.name}" if p.is_file() else None


def deliver(job: Job, version: int) -> str:
    src = job.path / "renders" / f"v{version}.mp4"
    if not src.is_file():
        raise JobError(f"renders/v{version}.mp4 does not exist")
    out = job.path / DELIVERY_DIR / delivery_name(job, version)
    out.parent.mkdir(exist_ok=True)
    if not out.is_file():
        shutil.copy2(src, out)
    return f"{DELIVERY_DIR}/{out.name}"


# -- job metadata (title, notes, footage, due) ---------------------------------------

def meta(job: Job) -> dict:
    return dict(job.state.get("meta") or {})


def set_meta(job: Job, **fields: Any) -> None:
    job.state.setdefault("meta", {}).update(fields)
    job.save_state()


# -- jobs and their status ------------------------------------------------------------

def list_jobs() -> list[Job]:
    out = []
    if not JOBS_DIR.is_dir():
        return out
    for p in sorted(JOBS_DIR.iterdir()):
        if not p.is_dir() or p.name.startswith(("_", ".")) or p.name == "workers":
            continue
        if not ((p / "job.json").is_file() or (p / "raw").is_dir()):
            continue
        try:
            out.append(Job(p))
        except JobError:
            continue
    return out


def versions(job: Job) -> list[dict]:
    renders = job.path / "renders"
    if not renders.is_dir():
        return []
    out = []
    for p in sorted(renders.glob("v*.json"), key=lambda p: int(p.stem[1:]) if p.stem[1:].isdigit() else 0):
        if not p.stem[1:].isdigit() or p.stem.endswith("-check"):
            continue
        n = int(p.stem[1:])
        m = read_json(p, {})
        mp4 = renders / f"v{n}.mp4"
        if not mp4.is_file():
            continue
        chk = read_json(renders / f"v{n}-check.json", None)
        out.append({
            "version": n, "file": f"renders/v{n}.mp4",
            "rendered": m.get("created") or datetime.fromtimestamp(mp4.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "beats_version": m.get("beats_version"), "changes": m.get("changes") or [],
            "check": (chk or {}).get("status") or m.get("check"),
            "seconds": m.get("render_seconds"), "duration": m.get("duration"), "size_mb": round(mp4.stat().st_size / 1e6, 1),
        })
    for v in out:
        v["superseded"] = v["version"] != out[-1]["version"]
    return out


def stage_rows(job: Job, current: str | None = None) -> list[dict]:
    stages = job.state.get("stages", {})
    rows = []
    for s in STAGES:
        st = stages.get(s, {}).get("status")
        kind = "done" if st == "done" else "failed" if st == "failed" else "now" if (s == current or st == "running") else "todo"
        rows.append({"name": s, "kind": kind, "seconds": stages.get(s, {}).get("seconds")})
    return rows


def log_tail(job: Job, lines: int = 6) -> str:
    p = job.path / WORKER_LOG
    if not p.is_file():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    keep = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(keep[-lines:])


def job_status(job: Job) -> dict:
    items = items_for(job.name)
    open_items = [i for i in items if not i.get("finished_at")]
    running = next((i for i in open_items if i.get("claimed_by")), None)
    waiting = next((i for i in open_items if i.get("result") == "waiting"), None)
    queued = next((i for i in open_items if not i.get("claimed_by") and i.get("result") != "waiting"), None)
    last = items[0] if items else None
    wstate = read_json(job.path / WORKER_STATE, {})
    vers = versions(job)
    latest = vers[-1] if vers else None
    review = read_approvals(job).get("studiio") or {}
    stages = job.state.get("stages", {})
    failed_stage = next((s for s in reversed(STAGES + ["revise"]) if stages.get(s, {}).get("status") == "failed"), None)

    def out(state: str, label: str, kind: str, **extra: Any) -> dict:
        return {"state": state, "label": label, "kind": kind,
                "version": latest["version"] if latest else None, "check": latest["check"] if latest else None,
                "review": review, "running": running, "waiting": waiting, "queued": queued, "worker": wstate, **extra}

    if running:
        if running.get("kind") == "direction":
            stage = wstate.get("stage")
            label = f"Claude is working · {stage}" if stage and stage != "direction" else "Claude is reading the direction"
            return out("editing", label, "warn")
        return out("editing", f"Editing · {wstate.get('stage') or 'starting'}", "warn")
    if waiting:
        return out("waiting", f"Needs Claude · {waiting.get('waiting_on')}", "warn")
    if last and last.get("finished_at") and last.get("result") in ("failed", "blocked"):
        if last["result"] == "blocked":
            return out("blocked", "Blocked at intake", "bad")
        return out("failed", f"Failed · {last.get('error') or 'see the log'}", "bad")
    if queued:
        return out("queued", "Queued", "none")
    if latest:
        v = latest["version"]
        if review.get("version") == v and review.get("status") == "approved":
            if delivered_file(job, v):
                return out("delivered", f"Delivered · v{v}", "ok")
            return out("approved", f"Approved · v{v}", "ok")
        if review.get("version") == v and review.get("status") == "changes":
            return out("changes", f"Changes requested · v{v}", "warn")
        if latest["check"] == "fail":
            return out("check_failed", f"Quality check failed · v{v}", "bad")
        return out("to_review", f"To review · v{v}", "info")
    if failed_stage:
        return out("failed", f"Failed at {failed_stage}", "bad")
    if job.has("beats.json"):
        return out("planned", "Planned, not rendered", "none")
    if job.has("beats-request.md") and not job.has("beats-plan.json"):
        return out("waiting", "Needs Claude · beat plan", "warn")
    done = [s for s in STAGES if stages.get(s, {}).get("status") == "done"]
    if done:
        return out("in_progress", f"{done[-1].capitalize()} done", "none")
    if (meta(job).get("footage") or {}).get("status") == "pending":
        return out("new", "Footage to fetch", "none")
    return out("new", "New", "none")


_client_names: dict[str, str] = {}


def client_name(slug_: str | None) -> str:
    if not slug_:
        return "no client"
    if slug_ not in _client_names:
        data = {}
        p = CLIENTS_DIR / slug_ / "profile.yaml"
        if p.is_file():
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                data = {}
        _client_names[slug_] = str((data.get("client") or {}).get("name") or slug_)
    return _client_names[slug_]


def job_summary(job: Job) -> dict:
    st = job_status(job)
    m = meta(job)
    beats = job.read_json("beats.json") if job.has("beats.json") else None
    cuts = job.read_json("cuts.json") if job.has("cuts.json") else None
    thumb = next((t for t in ("stills/beat-01.jpg", "previews/beats/beat-01.jpg") if (job.path / t).is_file()), None)
    length = beats.get("duration") if beats else (cuts["summary"]["seconds_out"] if cuts else None)
    return {
        "name": job.name, "title": m.get("title") or (beats or {}).get("title") or job.name,
        "client": job.state.get("client"), "client_name": client_name(job.state.get("client")),
        "version": st["version"], "status": st, "review": st["review"] or None,
        "thumb": thumb, "length": length, "final": delivered_file(job, st["version"]),
        "created": job.state.get("created"), "due": m.get("due") or "",
    }


def counters(rows: list[dict]) -> list[dict]:
    states = [r["status"]["state"] for r in rows]
    month = now_iso()[:7]

    def n(*s: str) -> int:
        return sum(1 for x in states if x in s)

    return [
        {"label": "In edit", "n": n("queued", "editing", "in_progress", "planned", "new")},
        {"label": "Needs you", "n": n("waiting", "blocked", "failed", "check_failed")},
        {"label": "To review", "n": n("to_review")},
        {"label": "Changes requested", "n": n("changes")},
        {"label": "Approved", "n": n("approved", "delivered")},
        {"label": "This month", "n": sum(1 for r in rows if (r.get("created") or "")[:7] == month)},
    ]


# -- clients --------------------------------------------------------------------------

def list_clients() -> list[dict]:
    from .profile import load_profile

    out = []
    if not CLIENTS_DIR.is_dir():
        return out
    jobs_by_client: dict[str, int] = {}
    for j in list_jobs():
        c = j.state.get("client")
        if c:
            jobs_by_client[c] = jobs_by_client.get(c, 0) + 1
    for d in sorted(CLIENTS_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")) or not (d / "profile.yaml").is_file():
            continue
        try:
            p = load_profile(d.name)
        except Exception:  # noqa: BLE001 - a broken profile is shown, not fatal
            continue
        raw = (d / "profile.yaml").read_text(encoding="utf-8")
        out.append({"slug": d.name, "name": client_name(d.name), "profile": p, "yaml": raw,
                    "jobs": jobs_by_client.get(d.name, 0),
                    "rules": len((p.get("rules") or {}).get("do") or []) + len((p.get("rules") or {}).get("dont") or []),
                    "feedback": len(p.get("feedback_log") or [])})
    return out


def stats(rows: list[dict]) -> dict:
    """The numbers on the queue bar, from the job summaries."""
    states = [r["status"]["state"] for r in rows]
    month = now_iso()[:7]

    def n(*s: str) -> int:
        return sum(1 for x in states if x in s)

    this_month = [r for r in rows if (r.get("created") or "")[:7] == month]
    approved_month = sum(1 for r in this_month if r["status"]["state"] in ("approved", "delivered"))
    return {
        "in_edit": n("queued", "editing", "in_progress", "planned", "new", "waiting"), "live": n("editing"),
        "needs_you": n("waiting", "blocked", "failed", "check_failed"),
        "to_review": n("to_review"), "revisions": n("changes"), "approved": n("approved", "delivered"),
        "delivered": n("delivered"), "month": len(this_month), "month_approved": approved_month,
        "share": (approved_month / len(this_month)) if this_month else 0.0,
    }


def open_count() -> int:
    return sum(1 for i in read_queue() if not i.get("finished_at"))
