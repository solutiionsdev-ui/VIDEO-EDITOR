"""Local review page: `reel view <job>` serves the Beat Board on localhost.

Dark "shot panel" layout: the full sequence with a beat scrubber, the beats
table, every shot as a card with Replay, the on-screen copy to approve, and
the engine's own results (intake, transcript, takes, cut list, face tracking)
folded underneath. It only reads the job folder and only listens on 127.0.0.1.
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

def sections(job: Job, base: str = "", board: dict | None = None) -> dict:
    """The Beat Board's building blocks, so the platform's job page and `reel view` share them.

    `base` is the URL prefix the job's files are served under ("" for `reel view`).
    """
    client = job.state.get("client")
    try:
        profile = load_profile(client)
    except Exception:  # noqa: BLE001 - a missing profile must not break the page
        profile = load_profile(None)
    colours = profile.get("brand", {}).get("colours", {}) or {}
    accent = str(colours.get("primary") or "#66023C")
    client_name = profile.get("client", {}).get("name") or client or "no client profile"
    font = str(profile.get("captions", {}).get("font") or "Poppins")
    running = any(s.get("status") == "running" for s in job.state.get("stages", {}).values())

    beats = job.read_json("beats.json") if job.has("beats.json") else None
    cuts = job.read_json("cuts.json") if job.has("cuts.json") else None
    video = _sequence_video(job)
    blocks = [
        _hero(job, client_name, beats, cuts, profile),
        _sequence(job, beats, cuts, video, base),
        _changes(beats),
        _quality(job),
        _beats_table(beats),
        _shots(job, beats, base, board),
        _copy(beats, client_name),
        _engine(job, base),
    ]
    return {"title": beats.get("title") if beats else job.name, "accent": accent, "font": font,
            "client_name": client_name, "running": running, "blocks": blocks, "video": video, "beats": beats}


def render(job: Job) -> str:
    s = sections(job)
    refresh = '<meta http-equiv="refresh" content="10">' if s["running"] else ""
    font_q = s["font"].replace(" ", "+")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(s['title'])} · Studiio</title>{refresh}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family={font_q}:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{_CSS.replace('ACCENT', esc(s['accent'])).replace('FONT', esc(s['font']))}</style></head>
<body><main>
{''.join(s['blocks'])}
<footer class="dim">./reel view · {esc(job.path)}{' · a stage is running, refreshing every 10 s' if s['running'] else ''}</footer>
</main>
<script>{_JS}</script>
</body></html>"""


def _sequence_video(job: Job) -> str | None:
    renders = []
    if (job.path / "renders").is_dir():
        renders = sorted((p for p in (job.path / "renders").glob("v*.mp4") if re.fullmatch(r"v\d+\.mp4", p.name)),
                         key=lambda p: int(p.stem[1:]))
    if renders:
        return f"renders/{renders[-1].name}"
    if (job.path / "previews" / "cut-preview.mp4").is_file():
        return "previews/cut-preview.mp4"
    return None


def _pill(text: str) -> str:
    return f'<span class="pill">{esc(text)}</span>'


def _badge(text: str, kind: str) -> str:
    return f'<span class="badge {kind}">{esc(text)}</span>'


def _hero(job: Job, client_name: str, beats: dict | None, cuts: dict | None, profile: dict) -> str:
    version = beats.get("version", 1) if beats else 1
    title = beats.get("title") if beats else job.name
    lede = ""
    if beats:
        m = beats.get("music", {})
        lede = (f"{esc(beats.get('hook', ''))} Music: {esc(m.get('mood', '—'))}, {esc(m.get('bpm', '—'))} bpm, "
                f"{esc(m.get('energy', '—'))} energy, {esc(m.get('ducking_db'))} dB under the voice."
                + (" A logo end card closes the reel." if beats.get("end_card") else ""))
    elif cuts:
        lede = "The cut is ready; the beat plan has not been written yet."
    else:
        lede = "No cut yet. Run the engine stages first."
    pills = []
    if cuts:
        pills.append(_pill(f"{fmt_clock(cuts['summary']['seconds_out'])} edited"))
        pills.append(_pill(f"raw {fmt_clock(cuts['summary']['seconds_in'])}"))
    if beats:
        pills.append(_pill(f"{len(beats['beats'])} beats"))
        pills.append(_pill(f"{beats.get('style', 'ios')} style"))
        pills.append(_pill(f"{sum(1 for b in beats['beats'] if b.get('graphic'))} graphics"))
    aspect = str(profile.get("format", {}).get("aspect") or "9:16")
    pills.append(_pill("1080×1920" if aspect == "9:16" else aspect))
    return (f'<header class="hero"><div class="eyebrow">Studiio · {esc(client_name)} · beat board · v{version}</div>'
            f'<h1>{esc(title)}</h1><p class="lede">{lede}</p><div class="pills">{"".join(pills)}</div></header>')


def _sequence(job: Job, beats: dict | None, cuts: dict | None, video: str | None, base: str = "") -> str:
    if not video:
        return '<section><h2>Full sequence</h2><p class="dim">No preview video yet — run <code>./reel cuts jobs/…</code>.</p></section>'
    duration = beats["duration"] if beats else (cuts["summary"]["seconds_out"] if cuts else 0)
    marks = ""
    data = "[]"
    if beats:
        marks = "".join(f'<i style="left:{(b["start"] / duration * 100):.3f}%"></i>' for b in beats["beats"][1:])
        data = json.dumps([{"id": b["id"], "name": b["name"], "start": b["start"], "end": b["end"]} for b in beats["beats"]])
    what = ("This is the render." if video.startswith("renders/") else
            "This is the cut preview (the edit before graphics, captions and music). The render replaces it here once it exists.")
    return f"""<section id="sequence"><h2>Full sequence</h2>
<div class="player" id="player" data-beats='{esc(data)}' data-duration="{duration}">
<video id="main" src="{base}/{esc(video)}#t=0.1" preload="auto" playsinline></video>
<div class="controls">
<button id="play" class="btn">Play</button>
<div class="scrub" id="scrub"><div class="track"><div class="fill" id="fill"></div>{marks}</div></div>
<div class="time mono" id="time">0:00.0 / {fmt_time(duration)}</div>
<div class="beatlabel" id="beatlabel">{esc('Beat 1 · ' + beats['beats'][0]['name']) if beats else ''}</div>
</div></div>
<p class="dim">{what}</p></section>"""


def _changes(beats: dict | None) -> str:
    if not beats or not beats.get("changes"):
        return ""
    v = beats.get("version", 1)
    latest = [c for c in beats["changes"] if c.get("version") == v]
    if not latest:
        return ""
    items = "".join(f'<li><strong>Beat {c.get("beat", "—")}</strong> — {esc(c.get("change"))} '
                    f'<span class="dim">← “{esc(c.get("comment"))}”</span></li>' for c in latest)
    return f'<section id="changes"><h2>Changes since last version <span class="dim">v{v}</span></h2><ul class="changes">{items}</ul></section>'


def _quality(job: Job) -> str:
    from .stages.check import latest_version

    version = latest_version(job)
    if version is None or not job.has(f"renders/v{version}-check.json"):
        return ""
    d = job.read_json(f"renders/v{version}-check.json")
    labels = {"format": "Format", "audio_level": "Audio level", "caption_sync": "Caption sync", "face_clear": "Face clear",
              "clean_cuts": "Clean cuts", "black_frozen": "Black / frozen", "visual_review": "Visual review"}
    kinds = {"pass": "ok", "fail": "bad", "review": "warn", "warn": "none"}
    rows = []
    for key, c in d["checks"].items():
        items = "".join(f"<li>{esc(i)}</li>" for i in c.get("items", [])[:6])
        rows.append(f'<tr><td>{esc(labels.get(key, key))}</td><td>{_badge(c["status"], kinds.get(c["status"], "none"))}</td>'
                    f'<td>{esc(c["detail"])}{f"<ul class=issues>{items}</ul>" if items else ""}</td></tr>')
    fixes = "".join(f'<li>{esc(f["what"])}</li>' for f in d.get("fixes", []))
    overall = _badge(d["status"], kinds.get(d["status"], "none"))
    return (f'<section id="quality"><h2>Quality check <span class="dim">v{version}</span> {overall}</h2>'
            f'<table class="compact wide"><tr><th>check</th><th>result</th><th>detail</th></tr>{"".join(rows)}</table>'
            f'{f"<p class=dim>Fixed automatically:</p><ul class=issues>{fixes}</ul>" if fixes else ""}</section>')


def _beats_table(beats: dict | None) -> str:
    if not beats:
        return '<section id="beats"><h2>Beats</h2><p class="dim">Not planned yet — run <code>./reel beats jobs/…</code>.</p></section>'
    from .stages.beats import graphic_line

    rows = []
    for b in beats["beats"]:
        g = b.get("graphic")
        sees = f"{esc(b['layout'])}"
        if g:
            sees += f" · {esc(graphic_line(g))}"
        cap = b["caption"]["style"]
        if b["caption"]["highlight"]:
            cap += " · ★ " + ", ".join(b["caption"]["highlight"])
        changed = b.get("changed") or b.get("note") or ""
        rows.append(
            f'<tr data-start="{b["start"]}" data-end="{b["end"]}"><td class="mono">{b["id"]}</td><td class="mono">{fmt_time(b["start"])}</td>'
            f'<td class="mono">{b["duration"]:.2f} s</td><td class="name">{esc(b["name"])}</td>'
            f'<td dir="auto"><div>{esc(b["text"])}</div><div class="dim">{sees} · captions {esc(cap)}'
            f'{" · take " + esc(b["take_used"]) if b["take_used"] != "1 of 1" else ""}</div></td>'
            f'<td class="dim">{esc(changed)}</td></tr>')
    warn = "".join(f'<li class="warn-text">{esc(w)}</li>' for w in beats.get("warnings", []))
    usage = ""
    if beats.get("usage"):
        u = beats["usage"]
        usage = (f'<p class="dim">Planned by {esc(u.get("model"))}: {u["input_tokens"]} tokens in '
                 f'({u["cache_read_input_tokens"]} cached), {u["output_tokens"]} out, {u.get("seconds")} s.</p>')
    return (f'<section id="beats"><h2>Beats <span class="dim">{esc(beats["summary"])}</span></h2>{usage}'
            f'<ul class="issues">{warn}</ul>'
            f'<table class="beats"><thead><tr><th>#</th><th>start</th><th>dur</th><th>beat</th><th>what the viewer sees</th><th>note</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></section>')


def _shots(job: Job, beats: dict | None, base: str = "", board: dict | None = None) -> str:
    """One card per beat. With `board` ({"action": url, "threads": {beat id: html}}) every card carries its own
    conversation with Claude: the comments made on that beat, the answers, and a box to write the next one."""
    if not beats:
        return ""
    from .stages.beats import graphic_line

    cards = []
    for b in beats["beats"]:
        still = f"stills/beat-{b['id']:02d}.jpg"
        thumb = f"previews/beats/beat-{b['id']:02d}.jpg"
        img = still if (job.path / still).is_file() else (thumb if (job.path / thumb).is_file() else None)
        pic = f'<img src="{base}/{esc(img)}" alt="" loading="lazy">' if img else '<div class="noimg">no frame</div>'
        g = b.get("graphic")
        sub = f"{b['duration']:.2f} s · {esc(b['layout'])}" + (f" · {esc(graphic_line(g))}" if g else "")
        locked = ' <span class="badge warn">kept as is</span>' if b.get("locked") else ""
        talk = ""
        if board:
            bid = b["id"]
            tkey, thtml = board.get("threads", {}).get(bid, ("", ""))
            talk = (f'<div class="bcomment"><div class="bthread" data-beat="{bid}" data-key="{esc(tkey)}">{thtml}</div>'
                    f'<form class="cform" data-at="{b["start"] + 0.05:.2f}">'
                    f'<textarea rows="2" placeholder="Comment on beat {bid}. Claude reads it and fixes it."></textarea>'
                    f'<div class="crow"><label class="attach">+ reference<input type="file" multiple accept="image/*,video/*" hidden></label>'
                    f'<span class="files"></span><button class="btn small">Send</button></div></form></div>')
        cards.append(
            f'<figure class="shot" data-id="{b["id"]}" data-start="{b["start"]}" data-end="{b["end"]}">'
            f'<div class="frame">{pic}</div><div class="bar"><i></i></div>'
            f'<figcaption><strong>Beat {b["id"]} · {esc(b["name"])}</strong>{locked} <span class="dim">{sub}</span>'
            f'<div class="line" dir="auto">{esc(b["text"])}</div></figcaption>'
            f'<button type="button" class="btn small replay" data-start="{b["start"]}" data-end="{b["end"]}">Replay</button>{talk}</figure>')
    kind = "Rendered stills, one per beat." if (job.path / "stills").is_dir() else \
        "Frames from the cut preview for now; the renderer will replace them with the real stills, graphics in place."
    if board:
        kind += " Comment under any beat: Claude reads it, fixes that beat, and answers there."
    return f'<section id="shots"><h2>Every shot</h2><p class="dim">{kind}</p><div class="grid">{"".join(cards)}</div></section>'


def _copy(beats: dict | None, client_name: str) -> str:
    if not beats:
        return ""
    from .stages.beats import graphic_copy

    cards = []
    for b in beats["beats"]:
        text = graphic_copy(b.get("graphic"))
        if not text:
            continue
        cards.append(f'<div class="copycard"><div class="copytext" dir="auto">{esc(text)}</div>'
                     f'<div class="dim">Beat {b["id"]} · {esc(b["name"])} · {esc(b["graphic"]["component"])}</div></div>')
    if not cards:
        return ""
    return (f'<section id="copy"><h2>Copy for {esc(client_name)} to approve</h2>'
            f'<p class="dim">Every word a graphic puts on screen. Captions are the spoken words and are not listed.</p>'
            f'<div class="copygrid">{"".join(cards)}</div></section>')


# -- engine details ----------------------------------------------------------

def _engine(job: Job, base: str = "") -> str:
    inner = _stages(job) + _intake(job) + _transcript(job) + _takes(job) + _cuts(job) + _face(job, base)
    return (f'<details class="engine"><summary>Engine details — stages, intake, transcript, takes, cut list, face tracking</summary>'
            f'{inner}</details>')


def _stages(job: Job) -> str:
    rows = []
    for stage in STAGES:
        e = job.state.get("stages", {}).get(stage, {})
        status = e.get("status", "not run")
        kind = {"done": "ok", "failed": "bad", "running": "warn"}.get(status, "none")
        secs = f"{e['seconds']}s" if e.get("seconds") is not None else ""
        err = f'<div class="err">{esc(e.get("error"))}</div>' if e.get("error") else ""
        rows.append(f"<tr><td>{esc(stage)}</td><td>{_badge(status, kind)}{err}</td><td class=mono>{secs}</td></tr>")
    return f"<section><h3>Stages</h3><table class=compact><tr><th>stage</th><th>status</th><th>time</th></tr>{''.join(rows)}</table></section>"


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
    return (f"<section><h3>1 · Intake {_badge(d['status'], kind)} <span class=dim>raw footage {fmt_clock(d['total_raw_seconds'])}</span></h3>"
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
        parts.append(f"<h4>{esc(src['file'])} <span class=dim>· language {esc(src['language'])} · {len(src['segments'])} lines</span></h4>"
                     f"<table><tr><th>#</th><th>time</th><th>line</th></tr>{''.join(rows)}</table>")
    stats = d.get("stats", {})
    return (f"<section><h3>3 · Transcript <span class=dim>{stats.get('words', '?')} words · model {esc(d.get('model'))}</span></h3>"
            f"<details><summary>Show every line</summary>{''.join(parts)}</details></section>")


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
        dropped = f"<h4>Dropped as probably-not-speech</h4><ul>{items}</ul>"
    return f"<section><h3>4 · Takes <span class=dim>{esc(head)}</span></h3>{''.join(groups)}{dropped}</section>"


def _cuts(job: Job) -> str:
    if not job.has("cuts.json"):
        return _empty("Cut list", "./reel cuts jobs/… --show")
    d = job.read_json("cuts.json")
    s = d["summary"]
    labels = {"pause": "pauses", "filler": "filler words", "rejected_take": "rejected takes", "suspect": "hallucinated lines",
              "lead": "lead-in", "tail": "tail"}
    removed = ", ".join(f"{v['count']} {labels.get(k, k)} ({v['seconds']:.1f}s)" for k, v in s["removed"].items())
    rows = "".join(
        f"<tr><td class=mono>{seg['id'] + 1}</td><td class=mono>{fmt_time(seg['start'])} → {fmt_time(seg['end'])}</td>"
        f"<td class=mono>{seg['duration']:.1f}s</td><td class=mono>{fmt_time(seg['out_start'])}</td><td dir=auto>{esc(seg['text'])}</td></tr>"
        for seg in d["segments"])
    return (f"<section><h3>5 · Cut list <span class=dim>{s['segments']} segments · {fmt_clock(s['seconds_in'])} raw → "
            f"<strong>{fmt_clock(s['seconds_out'])}</strong> edited</span></h3>"
            f"<p class=dim>Removed: {esc(removed) or '—'}</p>"
            f"<table><tr><th>#</th><th>raw in → out</th><th>dur</th><th>edited at</th><th>text</th></tr>{rows}</table>"
            f"<details><summary>Final script</summary><p dir=auto class=script>{esc(d['script'])}</p></details></section>")


def _face(job: Job, base: str = "") -> str:
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
    video = ""
    if (job.path / "previews" / "face-preview.mp4").is_file():
        video = f'<video controls preload="metadata" class="small" src="{base}/previews/face-preview.mp4#t=0.5"></video>'
    return (f"<section><h3>6 · Face tracking <span class=dim>{s['segments']} segments · {s['frames']} frames · "
            f"face found in <strong>{s['detected_fraction']:.0%}</strong> of frames</span></h3>"
            f"<p class=dim>Preview colours: green = raw detection, orange = smoothed track, red = no-go zone for graphics and captions.</p>"
            f"<div class=facegrid>{video}<div><table><tr><th>#</th><th>time</th><th>found</th><th>face centre</th><th>no-go zone</th><th>9:16 crop</th></tr>{''.join(rows)}</table>"
            f"<ul class=issues>{notes}</ul></div></div></section>")


def _empty(title: str, command: str) -> str:
    return f"<section><h3>{esc(title)} {_badge('not run yet', 'none')}</h3><p class=dim>Run <code>{esc(command)}</code></p></section>"


_CSS = """
:root{color-scheme:dark;--bg:#111218;--panel:#181922;--card:#1c1d27;--line:#262835;--fg:#ececf1;--muted:#9a9fb0;
--accent:ACCENT;--btn:#b85c8a;--btn-fg:#fff;--ok-bg:#14532d;--ok:#bbf7d0;--bad-bg:#7f1d1d;--bad:#fecaca;--warn-bg:#713f12;--warn:#fde68a;--none-bg:#262835;--none:#aab}
*{box-sizing:border-box}html{background:var(--bg)}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 "FONT",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1160px;margin:0 auto;padding:28px 20px 60px}
.eyebrow{color:var(--muted);font-weight:600;letter-spacing:.14em;text-transform:uppercase;font-size:11px}
h1{font-size:30px;font-weight:600;margin:6px 0 10px;letter-spacing:-.01em}h2{font-size:20px;font-weight:600;margin:44px 0 14px}
h2 .dim{font-weight:400;font-size:14px;margin-left:8px}h3{font-size:16px;margin:28px 0 10px}h4{font-size:14px;margin:16px 0 6px}
.lede{color:var(--muted);max-width:820px;margin:0 0 14px}
.pills{display:flex;flex-wrap:wrap;gap:8px}.pill{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:5px 12px;font-size:13px;font-weight:500}
.player{background:var(--panel);border-radius:16px;padding:10px}
.player video{width:100%;max-height:72vh;background:#000;border-radius:12px;display:block;margin:0 auto}
.controls{display:flex;align-items:center;gap:16px;padding:14px 6px 6px;flex-wrap:wrap}
.btn{background:var(--btn);color:var(--btn-fg);border:0;border-radius:999px;padding:10px 24px;font:inherit;font-weight:600;cursor:pointer}
.btn.small{padding:6px 16px;font-size:13px}.btn:hover{filter:brightness(1.1)}
.scrub{flex:1;min-width:180px;cursor:pointer;padding:10px 0}.track{position:relative;height:4px;background:#3a3c4a;border-radius:2px}
.fill{position:absolute;left:0;top:0;bottom:0;background:var(--btn);border-radius:2px;width:0}
.track i{position:absolute;top:-3px;width:1px;height:10px;background:#6b6e80}
.time{color:var(--muted);font-size:13px}.beatlabel{font-weight:600;font-size:14px;min-width:150px;text-align:right}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.08em}table.compact{max-width:480px}table.compact.wide{max-width:none}
ul.changes{padding-left:20px}ul.changes li{margin:4px 0}
table.beats td.name{font-weight:600;white-space:nowrap}table.beats tr.current td{background:#1f2030}table.beats tbody tr{cursor:pointer}
.mono{font-variant-numeric:tabular-nums;white-space:nowrap}.dim{color:var(--muted)}
.badge{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:600;vertical-align:middle}
.ok{background:var(--ok-bg);color:var(--ok)}.bad{background:var(--bad-bg);color:var(--bad)}.warn{background:var(--warn-bg);color:var(--warn)}.none{background:var(--none-bg);color:var(--none)}
.ok-text{color:var(--ok)}.bad-text{color:var(--bad)}.warn-text{color:var(--warn)}ul.issues{padding-left:20px;margin:6px 0}ul.issues:empty{display:none}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:22px 24px}
.shot{margin:0;background:var(--panel);border-radius:14px;padding:10px;display:flex;flex-direction:column;gap:8px}
.shot.current{outline:2px solid var(--btn)}
.frame{background:#000;border-radius:10px;overflow:hidden;aspect-ratio:9/16;max-height:420px}.frame img{width:100%;height:100%;object-fit:cover;display:block}
.noimg{color:var(--muted);display:flex;align-items:center;justify-content:center;height:100%}
.bar{height:3px;background:#3a3c4a;border-radius:2px;overflow:hidden}.bar i{display:block;height:100%;width:0;background:var(--btn)}
figcaption{font-size:14px}figcaption .line{color:var(--muted);font-size:13px;margin-top:4px}
.shot .btn{align-self:center}
.copygrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.copycard{background:var(--panel);border-radius:14px;padding:16px 18px}.copytext{font-size:17px;font-weight:600;margin-bottom:6px}
details.engine{margin-top:56px;border-top:1px solid var(--line);padding-top:16px}details.engine>summary{cursor:pointer;color:var(--muted);font-weight:600}
details summary{cursor:pointer;color:var(--muted)}.card{background:var(--card);border-radius:12px;padding:12px 16px;margin:12px 0}
.script{white-space:pre-wrap;background:var(--card);padding:12px 16px;border-radius:12px}.err{color:var(--bad);font-size:13px}
code{background:var(--card);padding:1px 6px;border-radius:6px}video.small{width:100%;max-width:320px;border-radius:12px;background:#000}
.facegrid{display:grid;grid-template-columns:minmax(200px,320px) 1fr;gap:24px;align-items:start}
footer{margin-top:56px;font-size:12px}
@media (max-width:760px){.facegrid{grid-template-columns:1fr}h1{font-size:24px}.beatlabel{text-align:left}}
"""

_JS = """
(function(){
  const player = document.getElementById('player'); if (!player) return;
  const video = document.getElementById('main'), play = document.getElementById('play'), fill = document.getElementById('fill');
  const timeEl = document.getElementById('time'), label = document.getElementById('beatlabel'), scrub = document.getElementById('scrub');
  const beats = JSON.parse(player.dataset.beats || '[]'); const duration = parseFloat(player.dataset.duration) || 0;
  let stopAt = null;
  const fmt = s => { s = Math.max(0, s); const m = Math.floor(s / 60); return m + ':' + (s - m * 60).toFixed(1).padStart(4, '0'); };
  const beatAt = t => beats.find(b => t >= b.start && t < b.end) || (beats.length && t >= beats[beats.length-1].end ? beats[beats.length-1] : null);
  function toggle(){ if (video.paused) { stopAt = null; video.play(); } else video.pause(); }
  play.addEventListener('click', toggle);
  video.addEventListener('click', toggle);
  video.addEventListener('play', () => play.textContent = 'Pause');
  video.addEventListener('pause', () => play.textContent = 'Play');
  video.addEventListener('timeupdate', () => {
    const t = video.currentTime, d = video.duration || duration;
    fill.style.width = (d ? (t / d * 100) : 0) + '%';
    timeEl.textContent = fmt(t) + ' / ' + fmt(d);
    const b = beatAt(t);
    if (b) label.textContent = 'Beat ' + b.id + ' · ' + b.name;
    document.querySelectorAll('.shot').forEach(el => {
      const s = parseFloat(el.dataset.start), e = parseFloat(el.dataset.end);
      const on = t >= s && t < e; el.classList.toggle('current', on);
      el.querySelector('.bar i').style.width = on ? ((t - s) / (e - s) * 100) + '%' : (t >= e ? '100%' : '0');
    });
    document.querySelectorAll('table.beats tbody tr').forEach(tr => {
      tr.classList.toggle('current', t >= parseFloat(tr.dataset.start) && t < parseFloat(tr.dataset.end));
    });
    if (stopAt !== null && t >= stopAt) { video.pause(); stopAt = null; }
  });
  scrub.addEventListener('click', ev => {
    const r = scrub.getBoundingClientRect(); const f = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
    video.currentTime = f * (video.duration || duration); stopAt = null;
  });
  function playRange(start, end, scroll){
    stopAt = end - 0.05; video.currentTime = start + 0.01; video.play();
    if (scroll) player.scrollIntoView({behavior: 'smooth', block: 'center'});
  }
  document.querySelectorAll('.replay').forEach(btn => btn.addEventListener('click', () => playRange(parseFloat(btn.dataset.start), parseFloat(btn.dataset.end), true)));
  document.querySelectorAll('table.beats tbody tr').forEach(tr => tr.addEventListener('click', () => playRange(parseFloat(tr.dataset.start), parseFloat(tr.dataset.end), true)));
})();
"""
