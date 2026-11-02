"""Stage 7 — the beat plan.

Splits the cut script into candidate beats (one clause each, 1.5-4 s) with
plain code, then asks Claude to group them and choose, per beat, a layout, a
graphic from the style card, a caption style and B-roll. Claude's choices are
validated against the style card and the client profile before beats.json is
assembled, so timing is always right and only listed components are used.

Two planners:
  api     the Claude API (needs credentials; logs tokens into job.json)
  manual  Claude Code: the stage writes beats-request.md, the operator writes
          beats-plan.json, the stage is run again to validate and assemble.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from rich.table import Table

from ..job import Job, JobError, console, fmt_clock, fmt_time
from ..profile import ROOT, get
from ..text import TERMINAL_PUNCT, join_words, normalize, number_tokens, preview

OUTPUT = "beats.json"
CANDIDATES = "beats-candidates.json"
REQUEST = "beats-request.md"
PLAN = "beats-plan.json"

PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "beat_plan.md"
SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schemas" / "beat_plan.schema.json"
STYLES_DIR = ROOT / "styles"

CLAUSE_PUNCT = ",;:–—-،؛"
NEEDS_OPERATOR = 4   # exit code: a person (or Claude Code) has to write beats-plan.json


class NeedsOperator(JobError):
    """The plan has to be written by hand (Claude Code) before the stage can finish."""


# -- entry point -------------------------------------------------------------

def run(job: Job, profile: dict, show: bool = False, planner: str = "auto", force: bool = False) -> dict:
    cuts = job.read_json("cuts.json")
    face = job.read_json("face.json") if job.has("face.json") else None
    takes = job.read_json("takes.json") if job.has("takes.json") else None
    style = load_style(str(get(profile, "style.name", "ios")))

    candidates = build_candidates(cuts, face, takes, profile)
    job.write_json(CANDIDATES, {"duration": cuts["summary"]["seconds_out"], "candidates": candidates})
    prompt = build_prompt(job, profile, style, candidates, cuts)
    (job.path / REQUEST).write_text(prompt, encoding="utf-8")
    job.log(f"  {len(candidates)} candidate beats · request written to {REQUEST}")

    plan_path = job.path / PLAN
    plan: dict | None = None
    generated_by = None
    usage = None
    if plan_path.is_file() and not force and planner != "api":
        # An existing plan is used as is; validation below catches one that no longer fits the cut.
        plan = _read_plan(plan_path)
        generated_by = plan.get("_generated_by") or "claude-code"
        job.log(f"  using existing {PLAN} (re-plan with --force)")
    elif planner in ("auto", "api"):
        client = _api_client()
        if client is None and planner == "api":
            raise JobError("No Claude API credentials found (set ANTHROPIC_API_KEY). Use --planner manual instead.")
        if client is not None:
            plan, usage, generated_by = _plan_with_api(job, client, profile, prompt, candidates, style)
            plan["_generated_by"] = generated_by
            job.write_json(PLAN, plan)
    if plan is None:
        raise NeedsOperator(
            f"The beat plan needs Claude's judgment. Read {job.file(REQUEST)} and write the plan to "
            f"{job.file(PLAN)}, then run `reel beats {job.path}` again to check it and build beats.json."
        )

    errors, warnings = validate(plan, candidates, style, profile, job)
    if errors:
        for e in errors:
            console.print(f"  [red]✖[/] {e}")
        raise JobError(f"{len(errors)} problem(s) in {PLAN}. Fix them and run `reel beats {job.path}` again.")
    for w in warnings:
        console.print(f"  [yellow]![/] {w}")

    result = assemble(plan, candidates, style, profile, cuts, warnings, generated_by or "claude-code")
    if usage:
        result["usage"] = usage
        u = job.state.setdefault("usage", {})
        u["claude_tokens_in"] = u.get("claude_tokens_in", 0) + usage["input_tokens"]
        u["claude_tokens_out"] = u.get("claude_tokens_out", 0) + usage["output_tokens"]
        u["claude_tokens_cached"] = u.get("claude_tokens_cached", 0) + usage["cache_read_input_tokens"]
        job.save_state()
    job.write_json(OUTPUT, result)
    job.log(f"  {result['summary']}")
    made = thumbnails(job, result["beats"])
    if made:
        job.log(f"  {made} beat thumbnails → previews/beats/")
    if show:
        report(job, result)
    return result


# -- style card --------------------------------------------------------------

def load_style(name: str) -> dict:
    path = STYLES_DIR / name / "style.yaml"
    if not path.is_file():
        known = sorted(p.parent.name for p in STYLES_DIR.glob("*/style.yaml"))
        raise JobError(f"Style '{name}' not found. Known styles: {', '.join(known) or 'none'}")
    with path.open(encoding="utf-8") as f:
        style = yaml.safe_load(f)
    style["_path"] = str(path)
    style["_text"] = _strip_comments(path.read_text(encoding="utf-8"))
    return style


def _strip_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#")).strip()


# -- candidates --------------------------------------------------------------

def build_candidates(cuts: dict, face: dict | None, takes: dict | None, profile: dict) -> list[dict]:
    """One candidate per clause, tiling the edited timeline from 0 to its end."""
    min_s = float(get(profile, "beats.min_s", 1.5))
    max_s = float(get(profile, "beats.max_s", 4.0))
    duration = float(cuts["summary"]["seconds_out"])
    no_go = {s["id"]: s.get("no_go") for s in (face or {}).get("segments", [])}
    take_of_unit: dict[int, str] = {}
    if takes:
        for g in takes.get("groups", []):
            take_of_unit[g["chosen"]] = g["take_used"]

    words: list[dict] = []
    for seg in cuts["segments"]:
        for i, w in enumerate(seg["words"]):
            start = seg["out_start"] + (w["start"] - seg["start"])
            end = seg["out_start"] + (w["end"] - seg["start"])
            words.append({
                "w": w["w"], "start": round(max(seg["out_start"], start), 3), "end": round(min(seg["out_start"] + seg["duration"], end), 3),
                "segment": seg["id"], "source": seg["source"],
                "src_start": w["start"], "src_end": w["end"],
                "last_in_segment": i == len(seg["words"]) - 1,
                "seg_out_end": round(seg["out_start"] + seg["duration"], 3),
            })

    clauses: list[list[dict]] = []
    current: list[dict] = []
    for i, w in enumerate(words):
        current.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        clause_start = current[0]["start"]
        clause_end = nxt["start"] if nxt and not w["last_in_segment"] else w["seg_out_end"]
        length = clause_end - clause_start
        close = False
        if nxt is None or w["last_in_segment"]:
            close = True
        elif w["w"].rstrip()[-1:] in TERMINAL_PUNCT:
            close = True
        elif w["w"].rstrip()[-1:] in CLAUSE_PUNCT and length >= min_s:
            close = True
        elif length >= max_s and nxt is not None:
            close = True
        if close:
            clauses.append(current)
            current = []
    if current:
        clauses.append(current)

    # Fold clauses that are too short into a neighbour.
    merged: list[list[dict]] = []
    for c in clauses:
        if merged and _clause_len(merged[-1], words, duration) < min_s * 0.6 and _same_segment(merged[-1], c) \
                and _clause_len(merged[-1] + c, words, duration) <= max_s * 1.5:
            merged[-1] = merged[-1] + c
        else:
            merged.append(c)
    final: list[list[dict]] = []
    for c in merged:
        if final and _clause_len(c, words, duration) < min_s * 0.6 and _same_segment(final[-1], c) \
                and _clause_len(final[-1] + c, words, duration) <= max_s * 1.5:
            final[-1] = final[-1] + c
        else:
            final.append(c)

    candidates: list[dict] = []
    for idx, c in enumerate(final):
        start = 0.0 if idx == 0 else candidates[-1]["end"]
        nxt_first = final[idx + 1][0] if idx + 1 < len(final) else None
        end = duration if nxt_first is None else (nxt_first["start"] if nxt_first["segment"] == c[-1]["segment"] else c[-1]["seg_out_end"])
        end = round(max(end, start + 0.1), 3)
        seg_ids = sorted({w["segment"] for w in c})
        mid_word = c[len(c) // 2]
        candidates.append({
            "id": idx + 1,
            "start": round(start, 3),
            "end": end,
            "duration": round(end - start, 3),
            "text": join_words([w["w"] for w in c]),
            "words": [{"w": w["w"], "start": w["start"], "end": w["end"]} for w in c],
            "segments": seg_ids,
            "source": {"source": c[0]["source"], "start": c[0]["src_start"], "end": c[-1]["src_end"],
                       "mid": round((mid_word["src_start"] + mid_word["src_end"]) / 2, 3)},
            "no_go": no_go.get(seg_ids[0]),
            "take_used": "1 of 1",
        })
    return candidates


def _clause_len(c: list[dict], words: list[dict], duration: float) -> float:
    return (c[-1]["end"] if c[-1]["last_in_segment"] else c[-1]["end"]) - c[0]["start"]


def _same_segment(a: list[dict], b: list[dict]) -> bool:
    return a[-1]["segment"] == b[0]["segment"]


# -- prompt ------------------------------------------------------------------

OUTPUT_SHAPE = """{
  "title": "2-6 word working title",
  "hook": "one sentence: why beat 1 works as the hook",
  "music": {"mood": "from the client's preferences", "bpm": "e.g. 100-110", "energy": "low | medium | high"},
  "beats": [
    {
      "name": "Hook",
      "from": 1, "to": 1,
      "layout": "face-full | white-card",
      "graphic": null  or  {"component": "ios-stat", "props": {"value": "$800,000", "label": "in sales", "trend": "up"}, "anchor": "top | bottom | below-card"},
      "caption_style": "normal | emphasis",
      "highlight_words": ["one", "word"],
      "broll": null  or  {"file": "name from the B-roll list", "note": "why"},
      "note": "one short line on the choice"
    }
  ]
}"""


def build_prompt(job: Job, profile: dict, style: dict, candidates: list[dict], cuts: dict) -> str:
    template = PROMPT_FILE.read_text(encoding="utf-8")
    white_max = float(get(profile, "style.white_card_max", style.get("layouts", {}).get("white-card", {}).get("max_share", 0.4)))
    for tweak in get(profile, "style.tweaks", []) or []:
        m = re.search(r"white-card layout max (\d+)%", str(tweak))
        if m:
            white_max = int(m.group(1)) / 100
    broll = [p.name for p in job.optional_files("broll")]
    lines = [f"{c['id']:>3} | {fmt_time(c['start'])}–{fmt_time(c['end'])} ({c['duration']:.1f}s) | {c['text']}" for c in candidates]
    return template.format(
        style_title=style.get("title", style.get("name")),
        style_card=style["_text"],
        client_summary=_client_summary(profile),
        client_rules=_client_rules(profile),
        white_card_max=white_max,
        output_shape=OUTPUT_SHAPE,
        duration=fmt_clock(cuts["summary"]["seconds_out"]),
        note=job.note() or "(none)",
        broll=", ".join(broll) if broll else "(none)",
        script=cuts["script"],
        candidates="\n".join(lines),
    )


def _client_summary(profile: dict) -> str:
    c, b = profile.get("client", {}), profile.get("brand", {})
    parts = [
        f"Name: {c.get('name') or c.get('slug') or 'unknown'}",
        f"Languages on camera: {', '.join(c.get('languages', []) or ['en'])}",
        f"Tone: {b.get('tone') or '(not set)'}",
        f"Colours: primary {b.get('colours', {}).get('primary')}, secondary {b.get('colours', {}).get('secondary')}",
        f"Font: {(b.get('fonts', {}).get('primary') or {}).get('file') or 'default'}",
        f"Style tweaks: {', '.join(map(str, get(profile, 'style.tweaks', []) or [])) or '(none)'}",
        f"Caption language rules: {', '.join(map(str, get(profile, 'captions.language_rules', []) or [])) or '(none)'}",
        f"Client graphics library: {', '.join(map(str, profile.get('graphics_library', []) or [])) or '(none)'}",
        f"Music: moods {', '.join(map(str, get(profile, 'music.moods', []) or [])) or 'any'}, bpm {get(profile, 'music.bpm') or 'any'}",
        f"Format: {get(profile, 'format.aspect')}, max {get(profile, 'format.max_length_s')} s, outro: {get(profile, 'format.outro') or 'none'}",
    ]
    return "\n".join(parts)


def _client_rules(profile: dict) -> str:
    do = [f"- do: {r}" for r in (get(profile, "rules.do", []) or [])]
    dont = [f"- don't: {r}" for r in (get(profile, "rules.dont", []) or [])]
    log = [f"- learned ({e.get('date')}): {e.get('rule')}" for e in (profile.get("feedback_log") or []) if e.get("rule")]
    return "\n".join(do + dont + log) or "(none)"


# -- planners ----------------------------------------------------------------

def _api_client():
    """An Anthropic client if credentials resolve (API key, auth token or an `ant auth` profile)."""
    try:
        import anthropic
    except ImportError:
        return None
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or (Path.home() / ".config" / "anthropic").is_dir()):
        return None
    try:
        return anthropic.Anthropic()
    except Exception:  # noqa: BLE001 - no usable credentials
        return None


def _plan_with_api(job: Job, client, profile: dict, prompt: str, candidates: list[dict], style: dict):
    import anthropic

    model = str(get(profile, "ai.model", "claude-sonnet-5-5"))
    effort = str(get(profile, "ai.effort", "high"))
    system_text, _, user_text = prompt.partition("# This job")
    user_text = "# This job" + user_text
    job.log(f"  asking {model} for the beat plan…")
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    messages = [{"role": "user", "content": user_text}]
    started = time.time()
    plan = None
    for attempt in range(2):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=16000,
                system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                output_config={"effort": effort},
            )
        except anthropic.AuthenticationError as e:
            raise JobError(f"Claude API credentials were rejected: {e.message}") from e
        except anthropic.RateLimitError as e:
            raise JobError("Claude API rate limit hit; try again in a minute.") from e
        except anthropic.APIStatusError as e:
            raise JobError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise JobError(f"Could not reach the Claude API: {e}") from e
        u = response.usage
        totals["input_tokens"] += u.input_tokens
        totals["output_tokens"] += u.output_tokens
        totals["cache_read_input_tokens"] += u.cache_read_input_tokens or 0
        totals["cache_creation_input_tokens"] += u.cache_creation_input_tokens or 0
        if response.stop_reason == "refusal":
            raise JobError("Claude declined to plan this video (safety refusal).")
        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            plan = _parse_json(text)
            errors, _ = validate(plan, candidates, style, profile, job)
        except (ValueError, json.JSONDecodeError) as e:
            errors = [f"not valid JSON: {e}"]
        if not errors:
            break
        job.log(f"  plan had {len(errors)} problem(s); asking for a fix…")
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "Fix these problems and return the full corrected JSON:\n- " + "\n- ".join(errors)}]
    if plan is None:
        raise JobError("The planner returned no usable plan.")
    totals["seconds"] = round(time.time() - started, 1)
    totals["model"] = model
    return plan, totals, f"api:{model}"


def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object found")
    return json.loads(text[start:end + 1])


def _read_plan(path: Path) -> dict:
    try:
        return _parse_json(path.read_text(encoding="utf-8"))
    except (ValueError, json.JSONDecodeError) as e:
        raise JobError(f"{path.name} is not valid JSON: {e}") from e


# -- validation --------------------------------------------------------------

def validate(plan: dict, candidates: list[dict], style: dict, profile: dict, job: Job) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        import jsonschema
        schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        clean = {k: v for k, v in plan.items() if not k.startswith("_")}
        for err in sorted(jsonschema.Draft202012Validator(schema).iter_errors(clean), key=lambda e: list(e.path)):
            where = "/".join(str(p) for p in err.path) or "plan"
            errors.append(f"{where}: {err.message}")
    except ImportError:
        pass
    if errors:
        return errors, warnings

    by_id = {c["id"]: c for c in candidates}
    n = len(candidates)
    layouts = style.get("layouts", {})
    components = style.get("components", {})
    hard_max = float(get(profile, "beats.hard_max_s", 6.5))
    min_s, max_s = float(get(profile, "beats.min_s", 1.5)), float(get(profile, "beats.max_s", 4.0))
    white_max = float(get(profile, "style.white_card_max", layouts.get("white-card", {}).get("max_share", 0.4)))
    for tweak in get(profile, "style.tweaks", []) or []:
        m = re.search(r"white-card layout max (\d+)%", str(tweak))
        if m:
            white_max = int(m.group(1)) / 100
    broll_files = {p.name for p in job.optional_files("broll")}
    script_numbers = number_tokens(" ".join(c["text"] for c in candidates))

    expected = 1
    white = 0
    with_graphic = 0
    for i, b in enumerate(plan["beats"], start=1):
        if b["from"] != expected:
            errors.append(f"beat {i}: starts at candidate {b['from']}, expected {expected} (beats must use every candidate once, in order)")
        if b["to"] < b["from"]:
            errors.append(f"beat {i}: 'to' ({b['to']}) is before 'from' ({b['from']})")
        if b["to"] > n:
            errors.append(f"beat {i}: candidate {b['to']} does not exist (there are {n})")
        expected = b["to"] + 1
        if errors:
            continue
        members = [by_id[k] for k in range(b["from"], b["to"] + 1)]
        dur = members[-1]["end"] - members[0]["start"]
        text = join_words([w["w"] for m in members for w in m["words"]])
        text_norm = normalize(text)
        if dur > hard_max:
            errors.append(f"beat {i}: {dur:.1f}s is too long (max {hard_max}s) — split it")
        elif dur > max_s * 1.3 or dur < min_s * 0.6:
            warnings.append(f"beat {i}: {dur:.1f}s is outside the usual {min_s}-{max_s}s")
        if b["layout"] not in layouts:
            errors.append(f"beat {i}: unknown layout '{b['layout']}'")
        if b["layout"] == "white-card":
            white += 1
        g = b.get("graphic")
        if g:
            with_graphic += 1
            comp = components.get(g["component"])
            if comp is None:
                errors.append(f"beat {i}: unknown component '{g['component']}'")
            else:
                allowed = layouts.get(b["layout"], {}).get("graphic_anchors", [])
                if allowed and g["anchor"] not in allowed:
                    errors.append(f"beat {i}: anchor '{g['anchor']}' is not allowed for layout {b['layout']} (use {', '.join(allowed)})")
                texts = _graphic_texts(g["props"], comp)
                total_words = sum(len(t.split()) for t in texts)
                if g["component"] == "ios-list":
                    items = g["props"].get("items") or []
                    if not (2 <= len(items) <= 5):
                        errors.append(f"beat {i}: ios-list needs 2-5 items, has {len(items)}")
                    for it in items:
                        if len(str(it).split()) > 4:
                            errors.append(f"beat {i}: list item '{it}' is longer than 4 words")
                elif total_words > 5 and g["component"] not in ("ios-card", "ios-message", "ios-suggested-edit"):
                    errors.append(f"beat {i}: graphic has {total_words} words; the limit is 5")
                elif total_words > 12:
                    errors.append(f"beat {i}: graphic has {total_words} words; keep it under 12")
                for num in number_tokens(" ".join(texts)):
                    if num not in number_tokens(text) and num not in script_numbers:
                        warnings.append(f"beat {i}: the number '{num}' in the graphic is not said in the video — check it")
                if g["component"] == "logo-end-card" and i != len(plan["beats"]):
                    errors.append(f"beat {i}: logo-end-card is only for the last beat")
        kept = []
        for hw in b.get("highlight_words", []) or []:
            if normalize(hw) and normalize(hw) in text_norm:
                kept.append(hw)
            else:
                warnings.append(f"beat {i}: highlight word '{hw}' is not in the beat text; dropped")
        b["highlight_words"] = kept
        br = b.get("broll")
        if br and br.get("file") not in broll_files:
            errors.append(f"beat {i}: B-roll file '{br.get('file')}' is not in the job's broll/ folder")
        if i == 1 and b["caption_style"] != "emphasis":
            warnings.append("beat 1 (the hook) is not in 'emphasis' caption style")
    if not errors and expected != n + 1:
        errors.append(f"beats stop at candidate {expected - 1}; all {n} candidates must be used")
    total = len(plan["beats"])
    if total and white / total > white_max + 0.05:
        warnings.append(f"white-card layout on {white / total:.0%} of beats; the profile allows {white_max:.0%}")
    if total and with_graphic / total > 0.75:
        warnings.append(f"graphics on {with_graphic / total:.0%} of beats — probably too busy")
    return errors, warnings


def _graphic_texts(props: dict, comp: dict) -> list[str]:
    out: list[str] = []
    for field in comp.get("text_fields", []):
        v = props.get(field)
        if v is None:
            continue
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    out.extend(str(x) for x in item.values())
                else:
                    out.append(str(item))
        else:
            out.append(str(v))
    return out


# -- assembly ----------------------------------------------------------------

def assemble(plan: dict, candidates: list[dict], style: dict, profile: dict, cuts: dict,
             warnings: list[str], generated_by: str) -> dict:
    by_id = {c["id"]: c for c in candidates}
    beats = []
    swapped = 0
    for i, b in enumerate(plan["beats"], start=1):
        members = [by_id[k] for k in range(b["from"], b["to"] + 1)]
        words = [w for m in members for w in m["words"]]
        take = next((m["take_used"] for m in members if m["take_used"] != "1 of 1"), "1 of 1")
        if take != "1 of 1":
            swapped += 1
        text = join_words([w["w"] for w in words])
        beats.append({
            "id": i,
            "name": beat_name(b, text, i == 1),
            "start": members[0]["start"],
            "end": members[-1]["end"],
            "duration": round(members[-1]["end"] - members[0]["start"], 3),
            "text": text,
            "words": words,
            "candidates": [m["id"] for m in members],
            "segments": sorted({s for m in members for s in m["segments"]}),
            "source": {"source": members[0]["source"]["source"], "start": members[0]["source"]["start"],
                       "end": members[-1]["source"]["end"], "mid": members[len(members) // 2]["source"]["mid"]},
            "no_go": members[0]["no_go"],
            "layout": b["layout"],
            "graphic": b.get("graphic"),
            "caption": {"style": b["caption_style"], "highlight": b.get("highlight_words", [])},
            "broll": b.get("broll"),
            "note": b.get("note", ""),
            "take_used": take,
            "locked": False,
            "status": "proposed",
        })
    duration = float(cuts["summary"]["seconds_out"])
    summary = (f"{len(beats)} beats · {fmt_clock(duration)} · {style.get('title', style.get('name'))} style · "
               f"{sum(1 for b in beats if b['graphic'])} graphics · {swapped} takes swapped")
    return {
        "style": style.get("name"),
        "client": profile.get("client", {}).get("slug"),
        "title": plan.get("title", ""),
        "hook": plan.get("hook", ""),
        "music": {**plan.get("music", {}), "ducking_db": get(profile, "music.ducking_db", -18),
                  "tracks": get(profile, "music.tracks", []) or []},
        "end_card": bool(get(profile, "format.outro")),
        "duration": duration,
        "generated_by": generated_by,
        "planned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": 1,
        "summary": summary,
        "warnings": warnings,
        "beats": beats,
    }


def beat_name(b: dict, text: str, first: bool) -> str:
    """A 1-3 word label for the beat board: the planner's name, else the graphic's text, else the line."""
    if b.get("name"):
        return str(b["name"]).strip()
    if first:
        return "Hook"
    g = b.get("graphic")
    if g:
        p = g.get("props", {})
        comp = g.get("component")
        candidates = {
            "headline": p.get("text"), "ios-stat": f"{p.get('value', '')} {p.get('label', '')}".strip(),
            "ios-list": p.get("title") or " · ".join(str(x) for x in (p.get("items") or [])[:3]),
            "ios-timer": p.get("text"), "ios-toggle": p.get("label"), "ios-suggested-edit": p.get("text"),
            "before-after": p.get("after"), "ios-notification": p.get("title") or p.get("body"),
            "ios-message": next((bb.get("text") for bb in (p.get("bubbles") or []) if bb.get("text")), None),
            "ios-card": p.get("title"), "ios-progress": p.get("label"), "logo-end-card": "End card",
        }
        value = candidates.get(comp)
        if value:
            return _first_words(str(value), 3)
    return _first_words(text, 3)


_LEADING_FILLER = {"and", "but", "so", "then", "or", "also", "و", "ف", "ثم"}


def _first_words(text: str, n: int) -> str:
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    while words and normalize(words[0]) in _LEADING_FILLER:
        words.pop(0)
    name = " ".join(words[:n]).rstrip(",.;:!?")
    return name[:1].upper() + name[1:] if name else "Beat"


def graphic_copy(g: dict | None) -> str:
    """The on-screen words a graphic shows — what the client has to approve."""
    if not g:
        return ""
    p = g.get("props", {})
    comp = g.get("component")
    if comp == "ios-list":
        return (f"{p['title']}: " if p.get("title") else "") + " · ".join(str(x) for x in (p.get("items") or []))
    if comp == "ios-stat":
        return f"{p.get('value', '')} {p.get('label', '')}".strip()
    if comp == "ios-progress":
        return f"{p.get('label', '')} {p.get('from', '')}{p.get('unit', '%')} → {p.get('to', '')}{p.get('unit', '%')}"
    if comp == "before-after":
        return f"{p.get('before', '')} → {p.get('after', '')}"
    if comp == "ios-message":
        return " / ".join(str(bb.get("text", "")) for bb in (p.get("bubbles") or []))
    if comp == "ios-notification":
        return " — ".join(str(p.get(k)) for k in ("title", "body") if p.get(k))
    if comp == "ios-card":
        rows = "; ".join(f"{r.get('label')}: {r.get('value')}" for r in (p.get("rows") or []) if isinstance(r, dict))
        return f"{p.get('title', '')}" + (f" ({rows})" if rows else "")
    if comp == "ios-toggle":
        return f"{p.get('label', '')} — {p.get('state', '')}"
    return " ".join(str(p.get(k, "")) for k in ("text", "cta") if p.get(k))


def thumbnails(job: Job, beats: list[dict]) -> int:
    """One frame per beat from the cut preview, as a stand-in until the renderer makes real stills."""
    src = job.path / "previews" / "cut-preview.mp4"
    if not src.is_file():
        return 0
    out_dir = job.path / "previews" / "beats"
    out_dir.mkdir(parents=True, exist_ok=True)
    from ..media import run_ffmpeg

    made = 0
    for b in beats:
        dst = out_dir / f"beat-{b['id']:02d}.jpg"
        t = b["start"] + min(0.5, b["duration"] / 2)
        run_ffmpeg(["-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "4", str(dst)])
        made += 1
    return made


# -- report ------------------------------------------------------------------

def graphic_line(g: dict | None) -> str:
    if not g:
        return "—"
    p = g.get("props", {})
    comp = g.get("component", "")
    if comp == "ios-list":
        body = " · ".join(str(x) for x in (p.get("items") or []))
    elif comp == "ios-progress":
        body = f"{p.get('label', '')} {p.get('from', '')}{p.get('unit', '%')} → {p.get('to', '')}{p.get('unit', '%')}"
    elif comp == "before-after":
        body = f"{p.get('before', '')} → {p.get('after', '')}"
    elif comp == "ios-message":
        body = " / ".join(str(b.get("text", "")) for b in (p.get("bubbles") or []))
    elif comp == "ios-card":
        body = str(p.get("title", ""))
    else:
        body = " ".join(str(p.get(k, "")) for k in ("text", "title", "body", "value", "label", "cta") if p.get(k))
    return f"{comp} “{preview(body, 40)}” ({g.get('anchor', '')})"


def report(job: Job, data: dict) -> None:
    console.print(f"\n[bold]Beat board[/] — {data.get('title') or job.name} · {data['summary']}")
    if data.get("hook"):
        console.print(f"  hook: {data['hook']}")
    m = data.get("music", {})
    console.print(f"  music: {m.get('mood', '?')} · {m.get('bpm', '?')} bpm · {m.get('energy', '?')} energy · {m.get('ducking_db')} dB under voice")
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("#", justify="right")
    table.add_column("time")
    table.add_column("dur", justify="right")
    table.add_column("line")
    table.add_column("layout · graphic")
    table.add_column("captions")
    table.add_column("take")
    for b in data["beats"]:
        cap = b["caption"]["style"] + (" ★" + ",".join(b["caption"]["highlight"]) if b["caption"]["highlight"] else "")
        table.add_row(str(b["id"]), f"{fmt_time(b['start'])}–{fmt_time(b['end'])}", f"{b['duration']:.1f}s",
                      preview(b["text"], 48), f"{b['layout']} · {graphic_line(b['graphic'])}", cap, b["take_used"])
    console.print(table)
    for w in data.get("warnings", []):
        console.print(f"  [yellow]![/] {w}")
    if data.get("usage"):
        u = data["usage"]
        console.print(f"  claude: {u.get('model')} · {u['input_tokens']} in ({u['cache_read_input_tokens']} cached) · "
                      f"{u['output_tokens']} out · {u.get('seconds')}s")
