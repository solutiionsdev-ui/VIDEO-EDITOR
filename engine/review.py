"""Local review page: `reel view <job>` serves a read-only page on localhost.

It shows every stage's result (intake, transcript, takes, cut list, face
tracking) and plays the preview videos inline. It only reads the job folder
and only listens on 127.0.0.1. Not the Phase 3 web app — a quick way to check
results without opening JSON files.
"""

from __future__ import annotations

import html
import json
import mimetypes
import re
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .job import STAGES, Job, console, fmt_clock, fmt_time
from .profile import load_profile

PREVIEW_HEIGHT = 560


def esc(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def serve(job: Job, port: int = 8765, open_browser: bool = True) -> None:
    handler = partial(_Handler, job_path=job.path)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://localhost:{port}/"
    console.print(f"Review page for job [bold]{job.name}[/]: {url}  (Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, job_path: Path, **kwargs):
        self.job_path = job_path
        super().__init__(*args, directory=str(job_path), **kwargs)

    def log_message(self, *args):  # keep the terminal quiet
        pass

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path in ("/", "/index.html"):
            body = render(Job(self.job_path)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_file(path)

    def _send_file(self, path: str) -> None:
        """Serve a file from the job folder, with byte ranges so videos can seek (Safari needs this)."""
        target = (self.job_path / path.lstrip("/")).resolve()
        if not str(target).startswith(str(self.job_path.resolve())) or not target.is_file():
            self.send_error(404)
            return
        size = target.stat().st_size
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        start, end = 0, size - 1
        status = 200
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and size:
            status = 206
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_error(416)
                return
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with target.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)


# -- page --------------------------------------------------------------------

def render(job: Job) -> str:
    client = job.state.get("client")
    try:
        profile = load_profile(client)
    except Exception:  # noqa: BLE001 - a missing profile must not break the page
        profile = load_profile(None)
    accent = str(profile.get("brand", {}).get("colours", {}).get("primary") or "#66023C")
    client_name = profile.get("client", {}).get("name") or client or "no client profile"
    running = any(s.get("status") == "running" for s in job.state.get("stages", {}).values())

    sections = [
        _stages(job),
        _intake(job),
        _transcript(job),
        _takes(job),
        _cuts(job),
        _face(job),
    ]
    refresh = '<meta http-equiv="refresh" content="10">' if running else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(job.name)} · Studiio review</title>{refresh}
<style>{_CSS.replace('ACCENT', esc(accent))}</style></head>
<body><main>
<header><div class="eyebrow">Studiio Reel Editor · review</div>
<h1>{esc(job.name)} <span class="dim">· {esc(client_name)}</span></h1>
<p class="dim">Read-only view of the job folder. Re-run a stage in the terminal, then reload this page.
{'<strong>A stage is running — this page refreshes every 10 s.</strong>' if running else ''}</p></header>
{''.join(sections)}
<footer class="dim">./reel view · {esc(job.path)}</footer>
</main></body></html>"""


def _badge(text: str, kind: str) -> str:
    return f'<span class="badge {kind}">{esc(text)}</span>'


def _stages(job: Job) -> str:
    rows = []
    for stage in STAGES:
        e = job.state.get("stages", {}).get(stage, {})
        status = e.get("status", "not run")
        kind = {"done": "ok", "failed": "bad", "running": "warn"}.get(status, "none")
        secs = f"{e['seconds']}s" if e.get("seconds") is not None else ""
        err = f'<div class="err">{esc(e.get("error"))}</div>' if e.get("error") else ""
        rows.append(f"<tr><td>{esc(stage)}</td><td>{_badge(status, kind)}{err}</td><td class=mono>{secs}</td></tr>")
    return f"<section><h2>Stages</h2><table class=compact><tr><th>stage</th><th>status</th><th>time</th></tr>{''.join(rows)}</table></section>"


def _intake(job: Job) -> str:
    if not job.has("intake.json"):
        return _empty("Intake", "./reel intake jobs/… --client …")
    d = job.read_json("intake.json")
    kind = {"pass": "ok", "warnings": "warn", "blocked": "bad"}[d["status"]]
    rows = []
    for f in d["files"]:
        v = f.get("video") or {}
        a = f.get("audio") or {}
        video = f"{v.get('width', '?')}×{v.get('height', '?')} {v.get('orientation', '')}, {v.get('fps', '')} fps, {v.get('codec', '')}" if v else "—"
        audio = (f"voice {a.get('speech_dbfs')} dBFS · room {a.get('noise_floor_dbfs')} dBFS · {a.get('lufs')} LUFS"
                 if a.get("speech_dbfs") is not None else (a.get("codec") or "—"))
        rows.append(f"<tr><td>{esc(f['file'])}<div class=dim>{esc(f.get('role', ''))}</div></td>"
                    f"<td class=mono>{fmt_clock(f.get('duration', 0))}</td><td>{esc(video)}</td><td>{esc(audio)}</td></tr>")
    issues = "".join(
        f"<li class={ {'blocker': 'bad-text', 'warning': 'warn-text', 'info': 'dim'}[i['level']] }>{esc(i['message'])}</li>"
        for i in d["issues"]) or "<li class=ok-text>No problems found.</li>"
    return (f"<section><h2>1 · Intake {_badge(d['status'], kind)} <span class=dim>raw footage {fmt_clock(d['total_raw_seconds'])}</span></h2>"
            f"<table><tr><th>file</th><th>length</th><th>video</th><th>audio</th></tr>{''.join(rows)}</table>"
            f"<ul class=issues>{issues}</ul></section>")


def _transcript(job: Job) -> str:
    if not job.has("transcript.json"):
        return _empty("Transcript", "./reel transcribe jobs/… --show")
    d = job.read_json("transcript.json")
    parts = []
    for src in d["sources"]:
        rows = []
        for s in src["segments"]:
            flag = _badge("suspect", "warn") if s.get("suspect") else ""
            rows.append(f"<tr><td class=mono>{esc(s['id'])}</td><td class=mono>{fmt_time(s['start'])}–{fmt_time(s['end'])}</td>"
                        f"<td dir=auto>{esc(s['text'])} {flag}</td></tr>")
        parts.append(f"<h3>{esc(src['file'])} <span class=dim>· language {esc(src['language'])} · {len(src['segments'])} lines</span></h3>"
                     f"<table><tr><th>#</th><th>time</th><th>line</th></tr>{''.join(rows)}</table>")
    stats = d.get("stats", {})
    open_attr = " open" if stats.get("segments", 0) <= 40 else ""
    return (f"<section><h2>3 · Transcript <span class=dim>{stats.get('words', '?')} words · model {esc(d.get('model'))}</span></h2>"
            f"<details{open_attr}><summary>Show every line</summary>{''.join(parts)}</details></section>")


def _takes(job: Job) -> str:
    if not job.has("takes.json"):
        return _empty("Takes", "./reel takes jobs/… --show")
    d = job.read_json("takes.json")
    s = d["summary"]
    head = (f"{s['lines']} lines · {s['retake_groups']} retake groups · {s['takes_removed']} takes removed "
            f"({s['seconds_removed']}s)" + (f" · {s['suspect_removed']} suspect lines dropped" if s["suspect_removed"] else ""))
    groups = []
    for g in d["groups"]:
        rows = []
        for t in g["takes"]:
            verdict = _badge("KEPT", "ok") if t["kept"] else _badge("out", "bad") + " " + esc(", ".join(t["reasons"]))
            b = t["breakdown"]
            detail = (f"complete {b['completeness']:.2f} · stumbles {b['stumbles']} · fillers {b['fillers']} · "
                      f"confidence {b['confidence']:.2f} · audio {b['audio']['level_vs_file']:+.1f} dB")
            rows.append(f"<tr><td class=mono>{t['take_no']}</td><td class=mono>{fmt_time(t['start'])}–{fmt_time(t['end'])}</td>"
                        f"<td class=mono>{t['score']}</td><td dir=auto>{esc(t['text'])}<div class=dim>{esc(detail)}</div></td><td>{verdict}</td></tr>")
        groups.append(f"<div class=card><div><strong>Group {g['id'] + 1}</strong> · take {esc(g['take_used'])} kept · <span dir=auto>“{esc(g['line'])}”</span></div>"
                      f"<table><tr><th>take</th><th>time</th><th>score</th><th>text</th><th>verdict</th></tr>{''.join(rows)}</table></div>")
    if not groups:
        groups.append("<p class=dim>No repeated lines found — every line is kept.</p>")
    dropped = ""
    if d["dropped_suspect"]:
        units = {u["id"]: u for u in d["units"]}
        items = "".join(f"<li><span class=mono>{fmt_time(units[i]['start'])}</span> <span dir=auto>“{esc(units[i]['text'])}”</span></li>"
                        for i in d["dropped_suspect"])
        dropped = f"<h3>Dropped as probably-not-speech</h3><ul>{items}</ul>"
    return f"<section><h2>4 · Takes <span class=dim>{esc(head)}</span></h2>{''.join(groups)}{dropped}</section>"


def _cuts(job: Job) -> str:
    if not job.has("cuts.json"):
        return _empty("Cut list", "./reel cuts jobs/… --show")
    d = job.read_json("cuts.json")
    s = d["summary"]
    labels = {"pause": "pauses", "filler": "filler words", "rejected_take": "rejected takes", "lead": "lead-in", "tail": "tail"}
    removed = ", ".join(f"{v['count']} {labels.get(k, k)} ({v['seconds']:.1f}s)" for k, v in s["removed"].items())
    rows = "".join(
        f"<tr><td class=mono>{seg['id'] + 1}</td><td class=mono>{fmt_time(seg['start'])} → {fmt_time(seg['end'])}</td>"
        f"<td class=mono>{seg['duration']:.1f}s</td><td class=mono>{fmt_time(seg['out_start'])}</td><td dir=auto>{esc(seg['text'])}</td></tr>"
        for seg in d["segments"])
    video = _video(job, "previews/cut-preview.mp4")
    return (f"<section><h2>5 · Cut list <span class=dim>{s['segments']} segments · {fmt_clock(s['seconds_in'])} raw → "
            f"<strong>{fmt_clock(s['seconds_out'])}</strong> edited</span></h2>"
            f"<p class=dim>Removed: {esc(removed) or '—'}</p>"
            f"<div class=grid><div>{video}</div><div><table><tr><th>#</th><th>raw in → out</th><th>dur</th><th>edited at</th><th>text</th></tr>{rows}</table></div></div>"
            f"<details><summary>Final script</summary><p dir=auto class=script>{esc(d['script'])}</p></details></section>")


def _face(job: Job) -> str:
    if not job.has("face.json"):
        return _empty("Face tracking", "./reel face jobs/… --show")
    d = job.read_json("face.json")
    s = d["summary"]
    rows = []
    for seg in d["segments"]:
        c, ng, crop = seg["face_center"], seg["no_go"], seg["crop"]
        rows.append(f"<tr><td class=mono>{seg['id'] + 1}</td><td class=mono>{fmt_time(seg['start'])}–{fmt_time(seg['end'])}</td>"
                    f"<td class=mono>{seg['coverage']:.0%}</td>"
                    f"<td class=mono>{f'{c[0]:.2f}, {c[1]:.2f}' if c else '—'}</td>"
                    f"<td class=mono>{f'{ng[0]:.2f},{ng[1]:.2f} → {ng[2]:.2f},{ng[3]:.2f}' if ng else '—'}</td>"
                    f"<td class=mono>{crop['w']}×{crop['h']} @ {crop['x']},{crop['y']} <span class=dim>{esc(crop['note'])}</span></td></tr>")
    notes = ""
    if s["segments_without_face"]:
        notes += f"<li class=bad-text>No face at all in segments {esc(', '.join(str(i + 1) for i in s['segments_without_face']))}.</li>"
    if s["segments_low_coverage"]:
        notes += f"<li class=warn-text>Face found in under half the frames of segments {esc(', '.join(str(i + 1) for i in s['segments_low_coverage']))}.</li>"
    video = _video(job, "previews/face-preview.mp4")
    return (f"<section><h2>6 · Face tracking <span class=dim>{s['segments']} segments · {s['frames']} frames · "
            f"face found in <strong>{s['detected_fraction']:.0%}</strong> of frames</span></h2>"
            f"<p class=dim>Preview colours: green = raw detection, orange = smoothed track, red = no-go zone for graphics and captions, yellow = 9:16 crop.</p>"
            f"<div class=grid><div>{video}</div><div><table><tr><th>#</th><th>time</th><th>found</th><th>face centre</th><th>no-go zone</th><th>9:16 crop</th></tr>{''.join(rows)}</table>"
            f"<ul class=issues>{notes}</ul></div></div></section>")


def _video(job: Job, rel: str) -> str:
    if not (job.path / rel).is_file():
        return f"<p class=dim>No preview video ({esc(rel)}). Re-run the stage without --no-preview.</p>"
    return f'<video controls preload="auto" src="/{esc(rel)}#t=0.5"></video><div class=dim>{esc(rel)}</div>'


def _empty(title: str, command: str) -> str:
    return f"<section><h2>{esc(title)} {_badge('not run yet', 'none')}</h2><p class=dim>Run <code>{esc(command)}</code></p></section>"


_CSS = """
:root{--bg:#fff;--fg:#141414;--muted:#6b6b6b;--line:#e6e6e6;--card:#f6f6f7;--accent:ACCENT;
--ok-bg:#dcfce7;--ok:#15803d;--bad-bg:#fee2e2;--bad:#b91c1c;--warn-bg:#fef3c7;--warn:#a16207;--none-bg:#ececec;--none:#666}
@media (prefers-color-scheme: dark){:root{--bg:#121214;--fg:#ececec;--muted:#9a9a9a;--line:#2a2a2e;--card:#1b1b1f;
--ok-bg:#14532d;--ok:#bbf7d0;--bad-bg:#7f1d1d;--bad:#fecaca;--warn-bg:#713f12;--warn:#fde68a;--none-bg:#2a2a2e;--none:#aaa}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1120px;margin:0 auto;padding:24px 16px 48px}
.eyebrow{color:var(--accent);font-weight:700;letter-spacing:.04em;text-transform:uppercase;font-size:12px}
h1{font-size:24px;margin:4px 0 8px}h2{font-size:17px;margin:40px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--line)}
h2 .dim{font-weight:400;font-size:14px}h3{font-size:14px;margin:18px 0 6px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}table.compact{max-width:480px}
.badge{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:700;vertical-align:middle}
.ok{background:var(--ok-bg);color:var(--ok)}.bad{background:var(--bad-bg);color:var(--bad)}.warn{background:var(--warn-bg);color:var(--warn)}.none{background:var(--none-bg);color:var(--none)}
.ok-text{color:var(--ok)}.bad-text{color:var(--bad)}.warn-text{color:var(--warn)}.dim{color:var(--muted)}.mono{font-variant-numeric:tabular-nums;white-space:nowrap}
.card{background:var(--card);border-radius:12px;padding:12px 16px;margin:12px 0}.card table{margin-top:8px}
.grid{display:grid;grid-template-columns:minmax(220px,340px) 1fr;gap:24px;align-items:start}
video{width:100%;max-height:""" + str(PREVIEW_HEIGHT) + """px;border-radius:12px;background:#000}
ul.issues{padding-left:20px}.err{color:var(--bad);font-size:13px}code{background:var(--card);padding:1px 6px;border-radius:6px}
.script{white-space:pre-wrap;background:var(--card);padding:12px 16px;border-radius:12px}details summary{cursor:pointer;color:var(--muted)}
footer{margin-top:48px;font-size:12px}
@media (max-width:760px){.grid{grid-template-columns:1fr}h1{font-size:20px}}
"""
