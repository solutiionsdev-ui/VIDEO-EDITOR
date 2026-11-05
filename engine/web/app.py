"""`reel platform`: Jobs, Queue, the Job page (Beat Board + review), New job, Clients.

Everything is read from the job folders on every request; the only things the pages
write are the queue, the approvals, the Direction thread and a new job folder. Listens on
127.0.0.1 only.
"""

from __future__ import annotations

import re
import shutil
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from .. import direction, ops
from ..job import RAW_EXTENSIONS, Job, JobError, console, fmt_clock, fmt_time
from ..profile import CLIENTS_DIR, ROOT
from ..review import _CSS, _JS, sections
from ..profile import load_profile
from ..stages.beats import ASSET_EXT, asset_files, load_style
from ..stages.revise import OPS, _BEAT_RE, _TIME_RE

HERE = Path(__file__).parent

ICONS = {
    "film": '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7.5 4v16M16.5 4v16M3 9h4.5M3 15h4.5M16.5 9H21M16.5 15H21"/></svg>',
    "queue": '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h9"/></svg>',
    "plus": '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
    "users": '<svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><circle cx="17" cy="9" r="2.5"/><path d="M15.5 14.5a5 5 0 0 1 6 5.5"/></svg>',
    "cpu": '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/></svg>',
    "grid": '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="6" height="6" rx="1.2"/><rect x="14" y="4" width="6" height="6" rx="1.2"/><rect x="4" y="14" width="6" height="6" rx="1.2"/><rect x="14" y="14" width="6" height="6" rx="1.2"/></svg>',
    "check": '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
    "clock": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>',
    "pause": '<svg viewBox="0 0 24 24"><path d="M8 5v14M16 5v14"/></svg>',
    "palette": '<svg viewBox="0 0 24 24"><path d="M12 3a9 9 0 1 0 0 18c1.2 0 2-.8 2-1.8 0-.5-.2-.9-.5-1.2-.3-.4-.5-.8-.5-1.2 0-1 .8-1.8 1.8-1.8H17a4 4 0 0 0 4-4c0-4.4-4-8-9-8z"/><circle cx="7.5" cy="11.5" r="1"/><circle cx="10.5" cy="7.5" r="1"/><circle cx="15" cy="7.5" r="1"/></svg>',
}

templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.filters["ago"] = ops.ago
templates.env.filters["clock"] = fmt_clock
templates.env.filters["ftime"] = fmt_time
templates.env.globals["root"] = str(ROOT)
templates.env.globals["icon"] = lambda name: Markup(ICONS.get(name, ""))
templates.env.globals["queue_open"] = ops.open_count
templates.env.globals["kinds"] = ops.KINDS

app = FastAPI(title="Studiio Reel Editor", docs_url=None, redoc_url=None)

def css_text() -> str:
    """The Beat Board's stylesheet plus the platform theme, read on every request so a style edit shows on reload."""
    return _CSS.replace("ACCENT", "#66023C").replace("FONT", "Inter") + (HERE / "platform.css").read_text(encoding="utf-8")

STATUS_FILTERS = [("", "Every status"), ("to_review", "To review"), ("needs", "Needs you"), ("editing", "In edit"),
                  ("changes", "Changes requested"), ("approved", "Approved"), ("delivered", "Delivered")]
FILTER_STATES = {"to_review": {"to_review"}, "needs": {"waiting", "blocked", "failed", "check_failed"},
                 "editing": {"queued", "editing", "in_progress", "planned", "new"}, "changes": {"changes"},
                 "approved": {"approved", "delivered"}, "delivered": {"delivered"}}


def serve(port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn

    url = f"http://localhost:{port}/"
    console.print(f"Studiio Reel Editor platform: {url}  (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def page(request: Request, template: str, **ctx):
    ctx.setdefault("title", "Studiio")
    ctx.setdefault("accent", None)
    ctx.setdefault("font", None)
    ctx.setdefault("refresh", None)
    ctx.setdefault("nav", None)
    ctx.setdefault("crumb", None)
    return templates.TemplateResponse(request=request, name=template, context=ctx)


def _job(name: str) -> Job:
    if not re.fullmatch(r"[\w.\-]+", name) or name.startswith((".", "_")):
        raise HTTPException(404)
    try:
        return Job(ops.JOBS_DIR / name)
    except JobError:
        raise HTTPException(404, f"no job named {name}")


def _rows() -> list[dict]:
    rows = [ops.job_summary(j) for j in ops.list_jobs()]
    rows.sort(key=lambda r: r["created"] or "", reverse=True)
    for r in rows:
        r["filters"] = " ".join(k for k, states in FILTER_STATES.items() if r["status"]["state"] in states)
    return rows


# -- pages -----------------------------------------------------------------------------

@app.get("/static/platform.css")
def css() -> Response:
    return Response(css_text(), media_type="text/css")


@app.get("/", response_class=HTMLResponse)
def jobs_page(request: Request):
    rows = _rows()
    busy = any(r["status"]["state"] in ("editing", "queued") for r in rows)
    return page(request, "jobs.html", title="Media Tracker", crumb="content / media tracker", nav="jobs", rows=rows,
                stats=ops.stats(rows), filters=STATUS_FILTERS, refresh=10 if busy else None)


@app.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request):
    items = ops.read_queue()
    today = datetime.now(timezone.utc).date().isoformat()
    titles: dict[str, str] = {}

    def enrich(it: dict) -> dict:
        it = dict(it)
        path = ops.JOBS_DIR / it["job"]
        if it["job"] not in titles:
            try:
                titles[it["job"]] = ops.meta(Job(path)).get("title") or it["job"]
            except JobError:
                titles[it["job"]] = it["job"]
        it["title"] = titles[it["job"]]
        it["ws"] = ops.read_json(path / ops.WORKER_STATE, {})
        try:
            job = Job(path)
            it["client"] = ops.client_name(job.state.get("client"))
            it["stages"] = ops.stage_rows(job, it["ws"].get("stage"))
            it["log"] = ops.log_tail(job, 4)
        except JobError:
            it["client"], it["stages"], it["log"] = "", [], ""
        done = [s for s in it["stages"] if s["kind"] == "done"]
        it["done_n"], it["total_n"] = len(done), len(it["stages"])
        it["progress"] = (len(done) / len(it["stages"])) if it["stages"] else 0
        it["last_done"] = done[-1] if done else None
        it["request"] = ops.REQUEST_FILES.get(it.get("waiting_on") or "")
        it["plan"] = ops.WAIT_FILES.get(it.get("waiting_on") or "")
        return it

    running = [enrich(i) for i in items if i.get("claimed_by") and not i.get("finished_at")]
    waiting = [enrich(i) for i in items if not i.get("finished_at") and not i.get("claimed_by") and i.get("result") == "waiting"]
    up_next = [enrich(i) for i in items if not i.get("finished_at") and not i.get("claimed_by") and i.get("result") != "waiting"]
    finished = sorted((enrich(i) for i in items if (i.get("finished_at") or "")[:10] == today),
                      key=lambda i: i["finished_at"], reverse=True)
    rows = _rows()
    return page(request, "queue.html", title="Queue", crumb="content / queue", nav="queue", editors=ops.workers(),
                running=running, waiting=waiting, up_next=up_next, finished=finished, stats=ops.stats(rows), refresh=5)


@app.get("/jobs/new", response_class=HTMLResponse)
def new_job_form(request: Request, client: str = ""):
    clients = ops.list_clients()
    return page(request, "new.html", title="New job", crumb="content / new job", nav="new", clients=clients, error=None,
                formats=_formats(clients),
                values={"client": client or (clients[0]["slug"] if clients else ""), "title": "", "footage_url": "",
                        "notes": "", "due": ""})


def _formats(clients: list[dict]) -> dict:
    out = {}
    for c in clients:
        p = c["profile"]
        f = p.get("format") or {}
        out[c["slug"]] = (f"{f.get('aspect', '9:16')} · max {f.get('max_length_s', 60)} s · {p.get('style', {}).get('name', 'ios')} style"
                          f" · captions {p.get('captions', {}).get('font', '')} {p.get('captions', {}).get('size_px', '')} px")
    return out


@app.post("/jobs/new")
async def create_job(request: Request, client: str = Form(""), title: str = Form(""), footage_url: str = Form(""),
                     notes: str = Form(""), due: str = Form(""), files: list[UploadFile] = File(default=[])):
    clients = ops.list_clients()
    values = {"client": client, "title": title, "footage_url": footage_url.strip(), "notes": notes, "due": due}
    uploads = [f for f in files if f.filename]

    def fail(msg: str):
        return page(request, "new.html", title="New job", crumb="content / new job", nav="new", clients=clients,
                    error=msg, values=values, formats=_formats(clients))

    if not any(c["slug"] == client for c in clients):
        return fail("Pick a client.")
    if not title.strip():
        return fail("Give the reel a title.")
    if not uploads and not values["footage_url"]:
        return fail("Add the footage: a link or a file.")
    if values["footage_url"] and not re.match(r"https?://", values["footage_url"]):
        return fail("The footage link has to start with http:// or https://")
    for f in uploads:
        if Path(f.filename).suffix.lower() not in RAW_EXTENSIONS:
            return fail(f"{f.filename} is not a video file the engine accepts ({', '.join(sorted(RAW_EXTENSIONS))}).")

    base = f"{client}-{ops.slug(title)}"
    name, n = base, 2
    while (ops.JOBS_DIR / name).exists():
        name, n = f"{base}-{n}", n + 1
    path = ops.JOBS_DIR / name
    (path / "raw").mkdir(parents=True)
    for f in uploads:
        safe = re.sub(r"[^\w.\-]+", "_", Path(f.filename).name)
        with (path / "raw" / safe).open("wb") as out:
            shutil.copyfileobj(f.file, out, 1 << 20)
    if notes.strip():
        (path / "note.md").write_text(notes.strip() + "\n", encoding="utf-8")
    job = Job(path)
    job.state["client"] = client
    footage = ({"type": "link", "url": values["footage_url"], "status": "pending"} if values["footage_url"]
               else {"type": "upload", "status": "fetched", "files": [f.filename for f in uploads]})
    job.state["meta"] = {"title": title.strip(), "notes": notes.strip(), "footage": footage, "due": due.strip(),
                         "created_by": "platform"}
    job.save_state()
    ops.enqueue(job.name, "edit", note=title.strip())
    return RedirectResponse(f"/jobs/{job.name}", status_code=303)


@app.get("/jobs/{name}", response_class=HTMLResponse)
def job_page(request: Request, name: str, direction_error: str = ""):
    job = _job(name)
    fold_error = _fold(job)   # Claude Code answered through the file: fold it in, close the queue item
    s = sections(job, base=f"/jobs/{name}/files", board=_board(job))
    st = ops.job_status(job)
    vers = ops.versions(job)
    all_items = ops.items_for(job.name)
    busy = st["state"] in ("editing", "queued", "waiting") or s["running"]
    request_file = ops.REQUEST_FILES.get((st.get("waiting") or {}).get("waiting_on") or "")
    editors = [w for w in ops.workers() if w.get("online")]
    return page(request, "job.html", title=s["title"], crumb=f"content / {job.name}", nav="jobs", accent=s["accent"],
                font=s["font"], name=job.name, job=job, meta=ops.meta(job), blocks=s["blocks"], js=_JS, st=st,
                versions=vers, approvals=ops.read_approvals(job), items=all_items[:12], log=ops.log_tail(job, 12),
                stages=ops.stage_rows(job, (st.get("worker") or {}).get("stage")), request_file=request_file,
                client_name=s["client_name"], refresh=5 if busy else None,
                delivered=ops.delivered_file(job, st["version"]), editors=editors,
                direction_error=direction_error[:300], fold_error=fold_error, assets=_assets(job),
                **_thread_ctx(job, all_items, vers))


def _assets(job: Job) -> list[dict]:
    """The client's visuals this job can use, with a URL the page can show."""
    client = job.state.get("client")
    out = []
    for name, path in asset_files(job, load_profile(client)).items():
        if path.parent == job.path / "assets":
            out.append({"name": name, "url": f"/jobs/{job.name}/files/assets/{name}", "where": "this reel"})
        else:
            out.append({"name": name, "url": f"/clients/{client}/files/assets/{name}", "where": "client"})
    return out


@app.post("/jobs/{name}/assets")
async def add_assets(name: str, files: list[UploadFile] = File(default=[])):
    job = _job(name)
    uploads = [f for f in files if f.filename]
    bad = [f.filename for f in uploads if Path(f.filename).suffix.lower() not in ASSET_EXT]
    if bad:
        return RedirectResponse(f"/jobs/{name}?direction_error={urllib.parse.quote('not an image: ' + ', '.join(bad))}#assets", status_code=303)
    folder = job.path / "assets"
    folder.mkdir(exist_ok=True)
    for f in uploads:
        safe = re.sub(r"[^\w.\-]+", "_", Path(f.filename).name).strip("._") or "asset.png"
        with (folder / safe).open("wb") as out:
            shutil.copyfileobj(f.file, out, 1 << 20)
    return RedirectResponse(f"/jobs/{name}#assets", status_code=303)


def _board(job: Job) -> dict | None:
    """The per-beat direction boxes on the Beat Board: where they post and which graphics can be picked."""
    if not job.has("beats.json"):
        return None
    style = load_style(str(ops.read_json(job.path / "beats.json", {}).get("style") or "ios"))
    comps = [c for c in (style.get("components") or {}) if c not in ("asset", "logo-end-card")]
    return {"action": f"/jobs/{job.name}/board", "components": comps}


def _request_changes(job: Job, lines: list[str]) -> None:
    """One round of changes: the Studiio verdict goes to 'changes' and a revise item carries the lines."""
    st = ops.job_status(job)
    if st["version"]:
        ops.set_review(job, "changes", st["version"], note="\n".join(lines))
    ops.enqueue(job.name, "revise", comments=lines)


@app.post("/jobs/{name}/board")
async def board_post(request: Request, name: str):
    """The Beat Board's per-beat boxes, turned into comment lines the revise stage understands."""
    job = _job(name)
    form = await request.form()
    beats = ops.read_json(job.path / "beats.json", {}).get("beats", []) if job.has("beats.json") else []
    lines: list[str] = []
    for b in beats:
        bid = b["id"]
        note = str(form.get(f"note_{bid}") or "").strip()
        comp = str(form.get(f"comp_{bid}") or "").strip()
        lock = bool(form.get(f"lock_{bid}"))
        if note:
            lines.append(f"beat {bid}: {note}")
        if comp == "none":
            lines.append(f"beat {bid}: no graphic")
        elif comp:
            lines.append(f"beat {bid}: make the graphic a {comp}")
        if lock and not b.get("locked"):
            lines.append(f"beat {bid}: lock")
        elif not lock and b.get("locked"):
            lines.append(f"beat {bid}: unlock")
    if not lines:
        return RedirectResponse(f"/jobs/{name}?direction_error={urllib.parse.quote('Nothing to apply: write under a beat, pick a graphic, or tick keep as is.')}#shots", status_code=303)
    _request_changes(job, lines)
    return RedirectResponse(f"/jobs/{name}#shots", status_code=303)


def _fold(job: Job) -> str | None:
    """Fold a reply file from Claude Code into the thread and close its queue item; the error text if it was unusable."""
    try:
        if direction.fold_reply(job):
            for i in ops.items_for(job.name):
                if i["kind"] == "direction" and not i.get("finished_at"):
                    ops.finish_item(i["id"], "done")
    except JobError as e:
        return str(e)
    return None


def _thread_ctx(job: Job, all_items: list[dict], vers: list[dict]) -> dict:
    thread = direction.thread(job)
    for m in thread:
        for c in m.get("changes") or []:
            c["desc"] = _change_desc(c)
        if m.get("applied"):
            m["apply_state"] = _apply_state(m["applied"], all_items, vers)
    dir_items = [i for i in all_items if i["kind"] == "direction"]
    return {"thread": thread, "pending": direction.pending(job), "live": direction.live_state(job),
            "dir_item": next((i for i in dir_items if not i.get("finished_at")), None),
            "dir_last": dir_items[0] if dir_items else None}


@app.get("/jobs/{name}/direction/thread", response_class=HTMLResponse)
def direction_thread(request: Request, name: str):
    """The thread alone, polled by the job page while Claude works."""
    job = _job(name)
    fold_error = _fold(job)
    ctx = _thread_ctx(job, ops.items_for(job.name), ops.versions(job))
    live = bool(ctx["live"] or (ctx["dir_item"] and not ctx["dir_item"].get("finished_at")))
    resp = templates.TemplateResponse(request=request, name="_thread.html", context={
        **ctx, "name": job.name, "fold_error": fold_error, "editors": [w for w in ops.workers() if w.get("online")]})
    resp.headers["X-Live"] = "1" if live else "0"
    return resp


def _change_desc(c: dict) -> str:
    args = c.get("args") or {}
    try:
        return OPS[c["op"]].format(**{**args, "factor": args.get("factor"), "words": ", ".join(args.get("words", []))})
    except (KeyError, IndexError, TypeError):
        return str(c.get("op"))


def _apply_state(applied: dict, items: list[dict], vers: list[dict]) -> dict:
    """What happened to a reply the editor applied, from the queue item it made."""
    it = next((i for i in items if i["id"] == applied.get("item")), None)
    if it is None:
        return {"label": "applied", "kind": "none"}
    if not it.get("finished_at"):
        if it.get("claimed_by"):
            return {"label": f"applying on {it['claimed_by']}…", "kind": "warn"}
        if it.get("result") == "waiting":
            return {"label": f"waiting for Claude · {it.get('waiting_on')}", "kind": "warn"}
        return {"label": "queued", "kind": "none"}
    if it.get("result") in ("done", "check_failed"):
        v = next((v for v in vers if v["version"] > int(applied.get("version_before") or 0)), None)
        if v:
            return {"label": f"applied → v{v['version']}", "kind": "ok", "version": v["version"]}
        return {"label": "applied", "kind": "ok"}
    return {"label": f"failed · {it.get('error') or 'see the log'}", "kind": "bad"}


@app.post("/jobs/{name}/direction")
async def direction_post(name: str, text: str = Form(""), ref_url: str = Form(""), at: str = Form(""),
                         pin: str = Form(""), files: list[UploadFile] = File(default=[])):
    job = _job(name)
    uploads = [f for f in files if f.filename]
    t = None
    if pin and at:
        try:
            t = float(at)
        except ValueError:
            t = None
    ref = ref_url.strip()
    if ref and not re.match(r"https?://", ref):
        ref = "https://" + ref
    try:
        direction.post(job, text, ref_url=ref, at=t, files=[(f.filename, f.file) for f in uploads])
    except JobError as e:
        return RedirectResponse(f"/jobs/{name}?direction_error={urllib.parse.quote(str(e))}#direction", status_code=303)
    if not any(i["kind"] == "direction" and not i.get("finished_at") for i in ops.items_for(job.name)):
        ops.enqueue(job.name, "direction", note=(text.strip() or "reference attached")[:80])
    return RedirectResponse(f"/jobs/{name}#direction", status_code=303)


@app.post("/jobs/{name}/direction/ask")
def direction_ask_again(name: str):
    """Put the unanswered direction back on the queue (after a failed attempt)."""
    job = _job(name)
    asked = direction.pending(job)
    if asked and not any(i["kind"] == "direction" and not i.get("finished_at") for i in ops.items_for(job.name)):
        ops.enqueue(job.name, "direction", note=(asked.get("text") or "reference attached")[:80])
    return RedirectResponse(f"/jobs/{name}#direction", status_code=303)


@app.post("/jobs/{name}/direction/{msg_id}/apply")
def direction_apply(name: str, msg_id: str):
    job = _job(name)
    try:
        msg = direction.apply(job, msg_id)
    except JobError as e:
        return RedirectResponse(f"/jobs/{name}?direction_error={urllib.parse.quote(str(e))}#direction", status_code=303)
    st = ops.job_status(job)
    if st["version"]:
        asked = next((m for m in direction.thread(job) if m["id"] == msg.get("reply_to")), None)
        words = ((asked or {}).get("text") or msg["text"]).strip()
        if len(words) > 120:
            words = words[:120].rsplit(" ", 1)[0] + "…"
        ops.set_review(job, "changes", st["version"], note="Direction: " + words)
    return RedirectResponse(f"/jobs/{name}#direction", status_code=303)


@app.post("/jobs/{name}/review")
def review(name: str, action: str = Form(...), comments: str = Form(""), at: str = Form(""), pin: str = Form(""),
           note: str = Form("")):
    job = _job(name)
    st = ops.job_status(job)
    if not st["version"]:
        raise HTTPException(400, "nothing rendered yet")
    if action == "approve":
        ops.set_review(job, "approved", st["version"], note=note)
        ops.deliver(job, st["version"])
    elif action == "changes":
        lines = [ln.strip() for ln in comments.splitlines() if ln.strip()]
        if not lines:
            raise HTTPException(400, "write what should change")
        if pin and at:
            try:
                t = float(at)
            except ValueError:
                t = None
            if t is not None and not (_BEAT_RE.match(lines[0]) or _TIME_RE.match(lines[0])):
                m, sec = divmod(int(round(t)), 60)
                lines[0] = f"{m}:{sec:02d} {lines[0]}"
        _request_changes(job, lines)
    else:
        raise HTTPException(400, "unknown action")
    return RedirectResponse(f"/jobs/{name}", status_code=303)


@app.post("/jobs/{name}/queue")
def queue_job(name: str):
    job = _job(name)
    ops.enqueue(job.name, "edit", note=ops.meta(job).get("title") or job.name)
    return RedirectResponse(f"/jobs/{name}", status_code=303)


@app.get("/jobs/{name}/files/{path:path}")
def job_file(name: str, path: str):
    job = _job(name)
    target = (job.path / path).resolve()
    if not str(target).startswith(str(job.path.resolve()) + "/") or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)


@app.get("/styles", response_class=HTMLResponse)
def styles_page(request: Request):
    styles = ops.list_styles()
    for st in styles:
        ex = ops.style_examples(st["name"])
        st["cover"] = next(iter(ex["layouts"].values()), None) or next(iter(ex["components"].values()), None)
        st["with_frame"] = sum(1 for c in st["components"] if c in ex["components"])
    return page(request, "styles.html", title="Styles", crumb="library / styles", nav="styles", styles=styles)


@app.get("/styles/{name}", response_class=HTMLResponse)
def style_page(request: Request, name: str):
    st = next((s for s in ops.list_styles() if s["name"] == name), None)
    if not st:
        raise HTTPException(404)
    ex = ops.style_examples(name)
    comps = []
    for cname, c in st["components"].items():
        props = c.get("props") or {}
        sample = ", ".join(f"{k}: {v}" for k, v in list(props.items())[:3])
        comps.append({"name": cname, "description": c.get("description", ""), "props": props,
                      "ask": f"beat 7: {cname} — {sample}" if sample else f"beat 7: {cname}",
                      "frame": ex["components"].get(cname)})
    layouts = [{"name": lname, "description": (l.get("description") or "").strip(), "anchors": l.get("graphic_anchors") or [],
                "frame": ex["layouts"].get(lname)} for lname, l in st["layouts"].items()]
    return page(request, "style.html", title=st["title"], crumb=f"library / styles / {name}", nav="styles", st=st,
                comps=comps, layouts=layouts, with_frame=sum(1 for c in comps if c["frame"]))


@app.get("/clients", response_class=HTMLResponse)
def clients_page(request: Request):
    return page(request, "clients.html", title="Clients", crumb="clients", nav="clients", clients=ops.list_clients())


@app.get("/clients/{slug}/files/{path:path}")
def client_file(slug: str, path: str):
    if not re.fullmatch(r"[\w.\-]+", slug) or slug.startswith((".", "_")):
        raise HTTPException(404)
    base = (CLIENTS_DIR / slug).resolve()
    target = (base / path).resolve()
    if not str(target).startswith(str(base) + "/") or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)


@app.get("/clients/{slug}", response_class=HTMLResponse)
def client_page(request: Request, slug: str):
    c = next((c for c in ops.list_clients() if c["slug"] == slug), None)
    if not c:
        raise HTTPException(404)
    jobs = [ops.job_summary(j) for j in ops.list_jobs() if j.state.get("client") == slug]
    jobs.sort(key=lambda r: r["created"] or "", reverse=True)
    accent = str(((c["profile"].get("brand") or {}).get("colours") or {}).get("primary") or "#66023C")
    return page(request, "client.html", title=c["name"], crumb=f"clients / {slug}", nav="clients", c=c,
                p=c["profile"], jobs=jobs, accent=accent)
