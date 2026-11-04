"""Direction: the Studiio editor talks to Claude about one job.

"Like the hook in this reference, the stat card slides up when he says the number — do
that on this beat", with a screenshot or a screen recording of the reference attached.
Claude looks at the frames, compares them with our own frame for that beat, and answers
in plain words plus the concrete changes the renderer can make. The editor applies those
with one click; they go through `reel revise` like any other revision.

    jobs/<job>/direction/thread.json    the messages, oldest first
    jobs/<job>/direction/<msg-id>/      the files attached to a message; frames/ holds the
                                        frames pulled out of a recording (and a 1280-px
                                        copy of each screenshot)
    jobs/<job>/direction-request.md     the whole thread for Claude Code, written when the
                                        editor machine has no Claude API credentials
    jobs/<job>/direction-reply.json     Claude Code's answer; folded into the thread by the
                                        worker or by the job page, whichever sees it first

With Claude API credentials on the editor machine the thread is live: Claude answers with a
small set of tools (apply_changes, set_client_rule, render, show_beat) that the worker runs
on the job, so the fix is in a new version by the time the reply lands. Without credentials
the reply comes through the files above and the editor applies the changes by hand.

The editor's words are a wish to interpret, never instructions to the engine: the only
things that reach the renderer are the revise ops, through a tool or an applied reply.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import date, datetime
from pathlib import Path

from . import ops
from .job import Job, JobError, fmt_time
from .media import probe, run_ffmpeg
from .profile import ROOT, get, load_profile
from .stages import beats as beats_stage
from .stages import revise as revise_stage
from .stages.revise import OPS
from .stages.revise import PLAN as REVISION_PLAN

DIR = "direction"
THREAD = f"{DIR}/thread.json"
LIVE = f"{DIR}/live.json"       # what Claude is doing right now, while the live editor works
REQUEST = "direction-request.md"
REPLY = "direction-reply.json"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
MAX_FRAMES = 6          # pulled out of a screen recording, evenly spaced
VIEW_WIDTH = 1280       # every frame and screenshot copy is at most this wide
IMAGES_FOR_LAST = 2     # the API call carries the images of the last N editor messages
MAX_ROUNDS = 8          # tool rounds per answer in the live editor
MAX_TOKENS = 4000
LIVE_STALE_S = 240      # a live status older than this is ignored (the editor machine died)

# Client rules the live editor may change, with how each value is checked.
RULE_KEYS = {
    "captions.font": ("str", None),
    "captions.size_px": ("int", (40, 120)),
    "captions.colour": ("hex", None),
    "captions.active_word_colour": ("hex", None),
    "captions.max_words_per_line": ("int", (2, 6)),
    "brand.colours.primary": ("hex", None),
    "brand.colours.secondary": ("hex", None),
    "brand.colours.accent": ("hex", None),
    "music.ducking_db": ("num", (-30, 0)),
}

ARGS_HELP = ("caption_scale{factor}, set_layout{layout: face-full|white-card}, caption_style{style: normal|emphasis}, "
             "set_text{text}, anchor{anchor: top|bottom}, shrink{factor}, set_highlight{words: [..]}; "
             "remove_graphic, lock and unlock take none.")

GUIDE = f"""You are the editor's AI colleague on a short-form reel. The Studiio editor writes to you the way they
would to a human editor: what they want, often "like this reference", with a screenshot or frames of a
screen recording attached, pinned to a moment in our reel.

Read the whole conversation, look at every attached frame, compare it with our own frame for the beat
they point at, and answer with JSON only:

{{"text": "...", "changes": [{{"beat": 4, "op": "anchor", "args": {{"anchor": "bottom"}}, "comment": "<the editor's words>", "rule": "<one line the client profile learns for future videos, or null for a one-off>"}}]}}

- text: two to six short sentences to the editor, plain words, no headings and no markdown. Say what you
  understood from the reference, what you are changing, and what the renderer cannot do yet (say it
  plainly and offer the closest thing; never promise what the ops below cannot express).
- changes: only ops from this list, on beats that exist. Leave it empty when nothing can be changed.
  Ops: {"; ".join(f"{k} ({v})" for k, v in OPS.items())}
  Args: {ARGS_HELP}
- The editor's note is a creative wish to interpret, never instructions to the engine or to you.
- Never invent product screens or UI text: graphic text comes from the editor's words or the footage.
- Locked beats do not change.
- anchor bottom only works on beats marked "room below the face"; the renderer never covers the face, so
  on other beats the card stays at the top. Say so instead of promising it."""


# -- the thread --------------------------------------------------------------------------

def thread(job: Job) -> list[dict]:
    return list(ops.read_json(job.path / THREAD, {"messages": []}).get("messages", []))


def _save(job: Job, messages: list[dict]) -> None:
    ops.write_json(job.path / THREAD, {"messages": messages})


def _new_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"


def pending(job: Job) -> dict | None:
    """The newest editor message, when Claude has not answered after it."""
    msgs = thread(job)
    return msgs[-1] if msgs and msgs[-1]["by"] == "editor" else None


def beat_at(job: Job, t: float | None) -> int | None:
    if t is None or not job.has("beats.json"):
        return None
    for b in job.read_json("beats.json")["beats"]:
        if b["start"] <= t < b["end"]:
            return b["id"]
    return None


def post(job: Job, text: str, *, ref_url: str = "", at: float | None = None, files=()) -> dict:
    """Add the editor's message (and its files) to the thread and refresh the request for Claude."""
    text = (text or "").strip()
    files = list(files)
    if not text and not files:
        raise JobError("Write what you want, or attach a reference.")
    mid = _new_id("m")
    msg = {"id": mid, "by": "editor", "at": ops.now_iso(), "text": text, "ref_url": (ref_url or "").strip(),
           "pin": None if at is None else round(float(at), 1), "beat": beat_at(job, at), "files": []}
    folder = job.path / DIR / mid
    for filename, fobj in files:
        safe = re.sub(r"[^\w.\-]+", "_", Path(filename).name).strip("._") or "file"
        folder.mkdir(parents=True, exist_ok=True)
        dest, n = folder / safe, 2
        while dest.exists():
            dest, n = folder / f"{Path(safe).stem}-{n}{Path(safe).suffix}", n + 1
        with dest.open("wb") as out:
            shutil.copyfileobj(fobj, out, 1 << 20)
        msg["files"].append(_describe(job, dest, folder / "frames" / dest.stem))
    msgs = thread(job)
    msgs.append(msg)
    _save(job, msgs)
    write_request(job)
    return msg


def _describe(job: Job, path: Path, frames_dir: Path) -> dict:
    """What a file is, plus the frames Claude (and the thread) will look at."""
    ext = path.suffix.lower()
    info = {"name": path.name, "path": str(path.relative_to(job.path)), "kind": "file", "frames": [],
            "frame_times": [], "seconds": None, "bytes": path.stat().st_size}
    try:
        if ext in IMAGE_EXT:
            info["kind"] = "image"
            info["frames"] = [_rel(job, p) for p in _frames(path, frames_dir, [None])]
        elif ext in VIDEO_EXT:
            info["kind"] = "video"
            dur = max(0.0, probe(path).duration)
            n = max(1, min(MAX_FRAMES, int(dur) + 1))
            times = [dur * (i + 0.5) / n for i in range(n)] if dur else [0.0]
            info["seconds"] = round(dur, 1)
            info["frames"] = [_rel(job, p) for p in _frames(path, frames_dir, times)]
            info["frame_times"] = [round(t, 1) for t in times]
    except (JobError, OSError) as e:
        info["error"] = str(e)
    return info


def _rel(job: Job, p: Path) -> str:
    return str(p.relative_to(job.path))


def _frames(src: Path, out_dir: Path, times: list[float | None]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for i, t in enumerate(times):
        dest = out_dir / f"{i + 1:02d}.jpg"
        args = ([] if t is None else ["-ss", f"{t:.3f}"]) + [
            "-i", str(src), "-frames:v", "1", "-vf", f"scale='min({VIEW_WIDTH},iw)':-2", "-q:v", "3", str(dest)]
        run_ffmpeg(args)
        if dest.is_file():
            out.append(dest)
    return out


# -- what Claude reads -------------------------------------------------------------------

def _beats(job: Job) -> dict | None:
    return job.read_json("beats.json") if job.has("beats.json") else None


def context_text(job: Job) -> str:
    """The job and its beats, one line each, for Claude."""
    beats = _beats(job)
    m = ops.meta(job)
    lines = [f"## This job: {m.get('title') or job.name}", f"Client: {ops.client_name(job.state.get('client'))} · folder `{job.name}`"]
    if m.get("notes"):
        lines.append(f"Notes for the edit: {m['notes']}")
    if not beats:
        lines.append("No beat plan yet: the beats stage has not run, so there is nothing to change on a beat level.")
        return "\n".join(lines)
    lines.append(f"Style {beats.get('style')} · {fmt_time(beats.get('duration', 0))} · beats v{beats.get('version', 1)}"
                 f" · hook: {beats.get('hook', '')}")
    lines += ["", "## The beats (id, time, layout, graphic, captions, what is said)"]
    for b in beats["beats"]:
        g = b.get("graphic")
        gdesc = beats_stage.graphic_line(g) if g else "no graphic"
        r = b.get("render") or {}
        extras = [k + "=" + str(v) for k, v in r.items()]
        room = room_below(b) if b.get("layout") != "white-card" else None
        where = ("" if room is None else " · room below the face" if room >= 0.12
                 else f" · no room below the face (it reaches {round((b.get('no_go') or [0, 0, 0, 0.65])[3] * 100)}% down)")
        lines.append(f"- beat {b['id']} ({fmt_time(b['start'])}–{fmt_time(b['end'])}) {b.get('layout')} · {gdesc}"
                     f" · captions {b.get('caption', {}).get('style', 'normal')}"
                     + (f", highlight {', '.join(b['caption']['highlight'])}" if b.get('caption', {}).get('highlight') else "")
                     + (f" · {' '.join(extras)}" if extras else "") + where + (" · LOCKED" if b.get("locked") else "")
                     + f" · “{b.get('text', '')}”")
    return "\n".join(lines)


def room_below(b: dict) -> float:
    """Height (fraction of the frame) left under the face for a card, the way the renderer computes it.
    The renderer uses the bottom zone only when this is at least 0.12."""
    no_go = b.get("no_go") or [0.3, 0.3, 0.7, 0.65]
    cap_top = max(no_go[3] + 0.04, 0.70) - 0.015
    return round(cap_top - (no_go[3] + 0.02), 3)


def _still(job: Job, beat: int | None) -> Path | None:
    if not beat:
        return None
    p = job.path / "stills" / f"beat-{beat:02d}.jpg"
    return p if p.is_file() else None


def _style_card(job: Job) -> str:
    beats = _beats(job)
    if not beats:
        return ""
    try:
        return beats_stage.load_style(str(beats.get("style") or "ios")).get("_text", "")
    except (JobError, OSError, KeyError):
        return ""


def _message_lines(job: Job, m: dict, absolute: bool) -> list[str]:
    """One message as text; file paths absolute for Claude Code, relative otherwise."""
    root = job.path if absolute else Path(".")
    who = "Studiio editor" if m["by"] == "editor" else f"Claude ({m.get('via', '')})"
    head = f"### {who} · {m['at']}"
    if m.get("pin") is not None:
        head += f" · pinned at {fmt_time(m['pin'])}" + (f" (beat {m['beat']})" if m.get("beat") else " (no beat there)")
    lines = [head]
    if m.get("ref_url"):
        lines.append(f"Reference link: {m['ref_url']}")
    if m.get("text"):
        lines.append(m["text"])
    for f in m.get("files", []):
        if f["kind"] == "video":
            lines.append(f"Attached screen recording {f['name']} ({f.get('seconds')} s). Frames pulled out of it:")
            for p, t in zip(f["frames"], f.get("frame_times", [])):
                lines.append(f"  - at {t} s: {root / p}")
        elif f["kind"] == "image":
            lines.append(f"Attached screenshot {f['name']}: {root / f['frames'][0] if f['frames'] else root / f['path']}")
        else:
            lines.append(f"Attached file {f['name']} ({f['path']}) — not an image or a video, so nothing to look at.")
        if f.get("error"):
            lines.append(f"  (could not read it: {f['error']})")
    if m["by"] == "editor":
        still = _still(job, m.get("beat"))
        if still:
            lines.append(f"Our current frame for beat {m['beat']}: {root / _rel(job, still)}")
    if m["by"] == "claude" and m.get("changes"):
        lines.append("Changes proposed: " + json.dumps(m["changes"], ensure_ascii=False))
        lines.append("Applied by the editor." if m.get("applied") else "Not applied.")
    return lines


def request_text(job: Job) -> str:
    """direction-request.md: the guide, the job, the style card and the whole thread, for Claude Code."""
    lines = ["# Direction request", "",
             f"The Studiio editor wrote to you about `{job.name}`. Look at the files named below (they are images),",
             f"then answer by writing `{REPLY}` next to this file. The platform folds it into the thread.", "",
             GUIDE, "", context_text(job), ""]
    card = _style_card(job)
    if card:
        lines += ["## What the renderer can do (the style card)", "", card, ""]
    lines += ["## The conversation", ""]
    for m in thread(job):
        lines += _message_lines(job, m, absolute=True) + [""]
    lines.append("Answer the latest editor message now.")
    return "\n".join(lines)


def write_request(job: Job) -> Path:
    p = job.path / REQUEST
    p.write_text(request_text(job), encoding="utf-8")
    return p


# -- the reply ---------------------------------------------------------------------------

def validate_reply(job: Job, reply: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(reply, dict):
        return ["the reply must be a JSON object with text and changes"]
    if not str(reply.get("text") or "").strip():
        errors.append("text is empty")
    if reply.get("changes") is None:
        reply["changes"] = []
    return errors + validate_changes(job, reply["changes"])


def validate_changes(job: Job, changes: object) -> list[str]:
    """Problems with a list of revise changes; the list is normalised in place (args, comment, rule)."""
    errors: list[str] = []
    if not isinstance(changes, list):
        return ["changes must be a list"]
    beats = _beats(job)
    ids = {b["id"]: b for b in beats["beats"]} if beats else {}
    for i, ch in enumerate(changes):
        if not isinstance(ch, dict):
            errors.append(f"change {i + 1} is not an object")
            continue
        if ch.get("op") not in OPS:
            errors.append(f"change {i + 1}: unknown op {ch.get('op')!r}; allowed: {', '.join(OPS)}")
        b = ids.get(ch.get("beat"))
        if b is None:
            errors.append(f"change {i + 1}: beat {ch.get('beat')!r} does not exist")
        elif b.get("locked") and ch.get("op") != "unlock":
            errors.append(f"change {i + 1}: beat {b['id']} is locked")
        elif ch.get("op") == "anchor" and (ch.get("args") or {}).get("anchor") == "bottom" \
                and b.get("layout") != "white-card" and room_below(b) < 0.12:
            errors.append(f"change {i + 1}: no room below the face on beat {b['id']}, the renderer would keep the card "
                          f"at the top; drop this change and say so in text")
        if ch.get("args") is None:
            ch["args"] = {}
        elif not isinstance(ch["args"], dict):
            errors.append(f"change {i + 1}: args must be an object")
        ch.setdefault("comment", "")
        ch.setdefault("rule", None)
    return errors


def append_reply(job: Job, text: str, changes: list[dict], *, via: str, usage: dict | None = None,
                 did: list[dict] | None = None) -> dict:
    msgs = thread(job)
    asked = next((m for m in reversed(msgs) if m["by"] == "editor"), None)
    msg = {"id": _new_id("r"), "by": "claude", "at": ops.now_iso(), "text": str(text).strip(), "changes": list(changes),
           "via": via, "reply_to": asked["id"] if asked else None, "applied": None, "usage": usage, "did": list(did or [])}
    msgs.append(msg)
    _save(job, msgs)
    req = job.path / REQUEST
    if req.is_file():
        req.unlink()
    return msg


def fold_reply(job: Job) -> dict | None:
    """Move direction-reply.json into the thread. Atomic: two callers cannot both fold it.
    An unreadable reply is kept as direction/reply-invalid-*.json and raises JobError."""
    src = job.path / REPLY
    if not src.is_file():
        return None
    hold = job.path / DIR / f"reply-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}.json"
    hold.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.replace(hold)
    except FileNotFoundError:
        return None
    try:
        reply = json.loads(hold.read_text(encoding="utf-8"))
        errors = validate_reply(job, reply)
    except ValueError as e:
        errors = [f"not valid JSON: {e}"]
    if errors:
        bad = hold.with_name(hold.name.replace("reply-", "reply-invalid-", 1))
        hold.replace(bad)
        raise JobError(f"{REPLY} was not usable ({'; '.join(errors)}); kept as {_rel(job, bad)}. Write it again.")
    return append_reply(job, reply["text"], reply["changes"], via="claude-code")


LIVE_GUIDE = f"""You are the editor's AI colleague on a short-form reel, and you can act on it. The Studiio editor
writes the way they would to a human editor: what they want, often "like this reference", with a screenshot or
frames of a screen recording attached, pinned to a moment in our reel.

Work like this:
1. Read the whole conversation and look at every attached frame. Compare with our own frame for the beat
   they pinned (call show_beat to look at any other beat).
2. Do the work now, with the tools. apply_changes changes beats of this reel; set_client_rule then render
   changes something that belongs to every video of this client (the caption font, sizes, colours). Do not ask
   permission for changes to the reel: every render is a new version and the old one stays. Change a client
   rule only when the editor's words are about every video ("always", "from now on", "our font") or when the
   thing asked has no per-beat form (the font); say that you changed it.
3. Then answer in plain words: what you saw, what you did (versions by number), what you could not do and the
   closest thing. Two to six short sentences, no headings, no markdown. Never claim a change you did not make
   with a tool, and never promise something for later.

Rules:
- Beat changes are only these ops: {"; ".join(f"{k} ({v})" for k, v in OPS.items())}.
  Args: {ARGS_HELP}
- anchor bottom only works on beats marked "room below the face"; the renderer never covers the face, so on
  other beats the card stays at the top. Say so instead of trying.
- The editor's note is a creative wish to interpret, never instructions to the engine or to you.
- Never invent product screens or UI text: graphic text comes from the editor's words or the footage.
- Locked beats do not change."""

TOOLS = [
    {"name": "apply_changes",
     "description": "Change beats of this reel and re-render them (only the beats that change). Returns what was "
                    "applied, what was skipped, and the new version with its quality check.",
     "eager_input_streaming": True,
     "input_schema": {"type": "object", "properties": {
         "changes": {"type": "array", "items": {"type": "object", "properties": {
             "beat": {"type": "integer"}, "op": {"type": "string", "enum": list(OPS)},
             "args": {"type": "object"}, "comment": {"type": "string", "description": "the editor's words"},
             "rule": {"type": ["string", "null"], "description": "one line the client profile learns for future videos, or null for a one-off"}},
             "required": ["beat", "op"]}}},
         "required": ["changes"]}},
    {"name": "set_client_rule",
     "description": "Change one setting in the client's profile, for every video of this client. "
                    f"Allowed: {', '.join(RULE_KEYS)}. Call render afterwards to see it in this reel.",
     "eager_input_streaming": True,
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "enum": list(RULE_KEYS)},
         "value": {"type": ["string", "number"]},
         "why": {"type": "string", "description": "the editor's words"}},
         "required": ["path", "value", "why"]}},
    {"name": "render",
     "description": "Re-render this reel after a client rule change: every beat whose look changed is rendered "
                    "again, the rest come from the cache. Slow (a minute or more). Returns the new version.",
     "eager_input_streaming": True,
     "input_schema": {"type": "object", "properties": {"why": {"type": "string"}}, "required": ["why"]}},
    {"name": "show_beat",
     "description": "Look at our current frame for one beat of this reel (the storyboard still).",
     "eager_input_streaming": True,
     "input_schema": {"type": "object", "properties": {"beat": {"type": "integer"}}, "required": ["beat"]}},
]


def live_state(job: Job) -> dict | None:
    """What the live editor is doing now, or None when nothing is running (or the status is stale)."""
    st = ops.read_json(job.path / LIVE, None)
    if not st:
        return None
    since = ops.seconds_since(st.get("at"))
    if since is None or since > LIVE_STALE_S:
        return None
    return st


def _set_live(job: Job, status: str | None, partial: str | None = None, did: list[dict] | None = None) -> None:
    path = job.path / LIVE
    if status is None:
        if path.is_file():
            path.unlink()
        return
    prev = ops.read_json(path, {}) or {}
    ops.write_json(path, {"status": status, "at": ops.now_iso(), "since": prev.get("since") or ops.now_iso(),
                          "partial": partial, "did": did if did is not None else prev.get("did") or []})


def answer_with_api(job: Job, **kw) -> dict | None:
    """The live editor: Claude answers the thread and changes the reel through tools. None without credentials."""
    return run_live(job, **kw)


def run_live(job: Job, *, call=None, run_stage=None, status=None, client=None) -> dict | None:
    """One answer of the live editor. `call(system, messages, tools, on_text)` returns the model's message
    (the API by default; tests pass a stand-in), `run_stage(stage, extra)` runs an engine stage on the job
    and returns its exit code (the worker passes its own), `status(text, partial=None)` reports progress."""
    if call is None:
        client = client or beats_stage._api_client()
        if client is None:
            return None
        call = _api_call(client, job)
    run_stage = run_stage or (lambda stage, extra=(): _run_stage(job, stage, list(extra)))
    did: list[dict] = []
    report = status or (lambda text, partial=None: _set_live(job, text, partial, did))
    profile = load_profile(job.state.get("client"))
    card = _style_card(job)
    system = LIVE_GUIDE + ("\n\n## What the renderer can do (the style card)\n\n" + card if card else "")
    messages: list[dict] = [{"role": "user", "content": _api_content(job)}]
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    started = time.time()
    report("reading the direction")
    response = None
    for _round in range(MAX_ROUNDS):
        response = call(system=system, messages=messages, tools=TOOLS, on_text=lambda t: report("writing", t))
        u = getattr(response, "usage", None)
        if u is not None:
            totals["input_tokens"] += u.input_tokens or 0
            totals["output_tokens"] += u.output_tokens or 0
            totals["cache_read_input_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0
            totals["cache_creation_input_tokens"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        if response.stop_reason == "refusal":
            raise JobError("Claude declined to work on this direction (safety refusal).")
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason == "max_tokens" and tool_uses:
            raise JobError("Claude's answer was cut off before a tool call finished; ask again.")
        if not tool_uses:
            break
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for tu in tool_uses:
            content, is_error, entry = _run_tool(job, profile, tu.name, tu.input, run_stage, report)
            if entry:
                did.append(entry)
                report(entry["summary"])
            if tu.name == "set_client_rule" and not is_error:
                profile = load_profile(job.state.get("client"))
            res = {"type": "tool_result", "tool_use_id": tu.id, "content": content}
            if is_error:
                res["is_error"] = True
            results.append(res)
        messages.append({"role": "user", "content": results})
    else:
        raise JobError(f"Claude kept calling tools; stopped after {MAX_ROUNDS} rounds. What it did is in the thread.")
    text = "".join(getattr(b, "text", "") for b in response.content if b.type == "text").strip()
    if not text:
        if not did:
            raise JobError("Claude returned no answer.")
        text = "Done. " + " ".join(d["summary"] for d in did)
    totals["seconds"] = round(time.time() - started, 1)
    totals["model"] = getattr(call, "model", "stand-in")
    usage = job.state.setdefault("usage", {})
    usage["claude_tokens_in"] = usage.get("claude_tokens_in", 0) + totals["input_tokens"]
    usage["claude_tokens_out"] = usage.get("claude_tokens_out", 0) + totals["output_tokens"]
    usage["claude_tokens_cached"] = usage.get("claude_tokens_cached", 0) + totals["cache_read_input_tokens"]
    job.save_state()
    msg = append_reply(job, text, [], via=f"api:{totals['model']}", usage=totals, did=did)
    if status is None:
        _set_live(job, None)
    return msg


def _api_call(client, job: Job):
    """The real call: streaming, adaptive thinking, the effort from the client profile."""
    import anthropic

    profile = load_profile(job.state.get("client"))
    model = str(get(profile, "ai.model", "claude-sonnet-5-5"))
    effort = str(get(profile, "ai.effort", "high"))

    def call(system: str, messages: list[dict], tools: list[dict], on_text):
        job.log(f"  asking {model} (live editor)…")
        try:
            with client.messages.stream(
                    model=model, max_tokens=MAX_TOKENS,
                    system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                    messages=messages, tools=tools, thinking={"type": "adaptive"},
                    output_config={"effort": effort}) as stream:
                buf: list[str] = []
                last = 0.0
                for piece in stream.text_stream:
                    buf.append(piece)
                    if time.time() - last > 0.6:
                        on_text("".join(buf))
                        last = time.time()
                return stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise JobError(f"Claude API credentials were rejected: {e.message}") from e
        except anthropic.RateLimitError as e:
            raise JobError("Claude API rate limit hit; try again in a minute.") from e
        except anthropic.APIStatusError as e:
            raise JobError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise JobError(f"Could not reach the Claude API: {e}") from e

    call.model = model
    return call


def _run_stage(job: Job, stage: str, extra: list[str]) -> int:
    """Run an engine stage on the job in its own process, output to worker.log (the worker passes its own)."""
    cmd = [sys.executable, "-m", "engine.cli", stage, str(job.path)]
    if job.state.get("client"):
        cmd += ["--client", str(job.state["client"])]
    cmd += extra
    with (job.path / ops.WORKER_LOG).open("a", encoding="utf-8") as f:
        f.write(f"\n== {stage} {datetime.now():%H:%M:%S} (live editor)\n")
        f.flush()
        return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=str(ROOT)).returncode


def _run_tool(job: Job, profile: dict, name: str, inp: object, run_stage, report) -> tuple[object, bool, dict | None]:
    """Run one tool. Returns (content for Claude, is_error, the entry for the thread or None)."""
    inp = inp if isinstance(inp, dict) else {}
    try:
        if name == "apply_changes":
            return _tool_apply(job, inp.get("changes"), run_stage, report)
        if name == "set_client_rule":
            return _tool_rule(job, profile, inp.get("path"), inp.get("value"), str(inp.get("why") or ""))
        if name == "render":
            return _tool_render(job, run_stage, report)
        if name == "show_beat":
            return _tool_show(job, inp.get("beat"))
    except JobError as e:
        return f"Error: {e}", True, None
    return f"Error: no tool named {name}", True, None


def _latest(job: Job) -> dict | None:
    vers = ops.versions(job)
    return vers[-1] if vers else None


def _version_line(v: dict | None, before: int) -> str:
    if not v or v["version"] <= before:
        return "no new version was rendered"
    check = v.get("check") or "not checked"
    return f"rendered v{v['version']} in {v.get('seconds') or '?'} s, quality check {check}"


def _tool_apply(job: Job, changes: object, run_stage, report) -> tuple[object, bool, dict | None]:
    if not isinstance(changes, list) or not changes:
        return "Error: changes must be a non-empty list", True, None
    errors = validate_changes(job, changes)
    if errors:
        return "Not applied:\n- " + "\n- ".join(errors), True, None
    plan_path = job.path / REVISION_PLAN
    plan = ops.read_json(plan_path, {"changes": []}) if plan_path.is_file() else {"changes": []}
    plan["changes"] = list(plan.get("changes", [])) + changes
    ops.write_json(plan_path, plan)
    before = (_latest(job) or {}).get("version", 0)
    ids = sorted({int(c["beat"]) for c in changes})
    report(f"re-rendering beat{'s' if len(ids) > 1 else ''} {', '.join(map(str, ids))}")
    code = run_stage("revise", [])
    v = _latest(job)
    rounds = (ops.read_json(job.path / revise_stage.LOG, {}) or {}).get("rounds") or []
    last = rounds[-1] if rounds else {}
    applied = [f"beat {a.get('beat')}: {a.get('change')}" for a in last.get("applied", [])]
    skipped = list(last.get("skipped", []))
    if code not in (0, 5):
        return f"Error: the revise stage failed (exit {code}); see worker.log. Applied before it failed: " \
               + ("; ".join(applied) or "nothing"), True, None
    summary = ("; ".join(applied) or "nothing changed") + (f" (skipped: {'; '.join(skipped)})" if skipped else "")
    line = _version_line(v, before)
    entry = {"tool": "apply_changes", "summary": f"{summary} → {line}", "version": v["version"] if v and v["version"] > before else None,
             "check": v.get("check") if v else None}
    return f"Applied: {summary}. {line}.", False, entry


def _tool_rule(job: Job, profile: dict, path: object, value: object, why: str) -> tuple[object, bool, dict | None]:
    if path not in RULE_KEYS:
        return f"Error: {path!r} is not a rule the live editor may change; allowed: {', '.join(RULE_KEYS)}", True, None
    kind, bounds = RULE_KEYS[path]
    if kind == "str":
        value = str(value).strip()
        if not value or len(value) > 60:
            return "Error: give a short text value", True, None
    elif kind == "hex":
        value = str(value).strip()
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            return "Error: a colour is #RRGGBB", True, None
        value = value.upper()
    else:
        try:
            value = int(value) if kind == "int" else float(value)
        except (TypeError, ValueError):
            return f"Error: {path} takes a number", True, None
        if bounds and not (bounds[0] <= value <= bounds[1]):
            return f"Error: {path} must be between {bounds[0]} and {bounds[1]}", True, None
    if not profile.get("_path"):
        return "Error: this job has no client profile to write to", True, None
    old = get(profile, path)
    feedback = [{"date": date.today().isoformat(), "comment": why, "rule": f"{path} = {value}", "scope": "video", "job": job.name}]
    revise_stage._update_profile(profile, {path: value}, feedback)
    client = ops.client_name(job.state.get("client"))
    entry = {"tool": "set_client_rule", "summary": f"{path}: {old} → {value} for every {client} reel", "version": None}
    return f"{path} is now {value} (was {old}) for every {client} reel. Call render to see it in this one.", False, entry


def _tool_render(job: Job, run_stage, report) -> tuple[object, bool, dict | None]:
    if not job.has("beats.json"):
        return "Error: there is no beat plan yet, nothing to render", True, None
    before = (_latest(job) or {}).get("version", 0)
    report("re-rendering the reel")
    code = run_stage("stills", [])
    if code != 0:
        return f"Error: the stills stage failed (exit {code}); see worker.log", True, None
    code = run_stage("render", [])
    v = _latest(job)
    if code not in (0, 5):
        return f"Error: the render failed (exit {code}); see worker.log", True, None
    line = _version_line(v, before)
    entry = {"tool": "render", "summary": line, "version": v["version"] if v and v["version"] > before else None,
             "check": v.get("check") if v else None}
    return line.capitalize() + ".", False, entry


def _tool_show(job: Job, beat: object) -> tuple[object, bool, dict | None]:
    try:
        bid = int(beat)
    except (TypeError, ValueError):
        return "Error: beat must be a number", True, None
    beats = _beats(job)
    b = next((x for x in (beats or {}).get("beats", []) if x["id"] == bid), None)
    still = _still(job, bid)
    if not b or not still:
        return f"Error: no still for beat {beat}", True, None
    line = context_text(job).splitlines()
    desc = next((ln for ln in line if ln.startswith(f"- beat {bid} ")), f"beat {bid}")
    return [{"type": "text", "text": desc}, _image_block(still)], False, None


def _image_block(path: Path) -> dict:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def _api_content(job: Job) -> list[dict]:
    """The job, then the thread with images for the last few editor messages."""
    msgs = thread(job)
    editor_ids = [m["id"] for m in msgs if m["by"] == "editor"][-IMAGES_FOR_LAST:]
    blocks: list[dict] = [{"type": "text", "text": context_text(job)}, {"type": "text", "text": "## The conversation"}]
    for m in msgs:
        with_images = m["id"] in editor_ids
        blocks.append({"type": "text", "text": "\n".join(_message_lines(job, m, absolute=False))})
        if not with_images:
            continue
        for f in m.get("files", []):
            for i, rel in enumerate(f.get("frames", [])):
                p = job.path / rel
                if not p.is_file():
                    continue
                label = f"{f['name']}" + (f" at {f['frame_times'][i]} s" if f.get("frame_times") else "")
                blocks.append({"type": "text", "text": f"[{label}]"})
                blocks.append(_image_block(p))
        still = _still(job, m.get("beat"))
        if still:
            blocks.append({"type": "text", "text": f"[our current frame for beat {m['beat']}]"})
            blocks.append(_image_block(still))
    blocks.append({"type": "text", "text": "Answer the latest editor message now, as JSON."})
    return blocks


# -- applying a reply --------------------------------------------------------------------

def apply(job: Job, msg_id: str) -> dict:
    """Turn a reply's changes into revision-plan.json and a revise item on the queue."""
    msgs = thread(job)
    msg = next((m for m in msgs if m["id"] == msg_id), None)
    if not msg or msg["by"] != "claude":
        raise JobError("no such reply")
    if msg.get("applied"):
        return msg
    if not msg.get("changes"):
        raise JobError("this reply has no changes to apply")
    plan_path = job.path / REVISION_PLAN
    plan = ops.read_json(plan_path, {"changes": []}) if plan_path.is_file() else {"changes": []}
    plan["changes"] = list(plan.get("changes", [])) + list(msg["changes"])
    ops.write_json(plan_path, plan)
    asked = next((m for m in msgs if m["id"] == msg.get("reply_to")), None)
    note = "direction: " + (((asked or {}).get("text") or msg["text"])[:80])
    item = ops.enqueue(job.name, "revise", note=note)
    latest = ops.versions(job)
    msg["applied"] = {"item": item["id"], "at": ops.now_iso(), "version_before": latest[-1]["version"] if latest else 0}
    _save(job, msgs)
    return msg
