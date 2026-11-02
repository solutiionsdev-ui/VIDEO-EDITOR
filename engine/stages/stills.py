"""Stage 8 — storyboard stills.

One real frame per beat from the client's footage with the graphic and captions
in place, made by the same renderer as the final video. Writes stills/beat-XX.jpg
and stills/index.json. Also builds renders/base.mp4 (the cut at full quality),
which the render stage reuses.
"""

from __future__ import annotations

import json
import shutil

from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from ..job import Job, console
from ..render import compose
from ..render.driver import Renderer, beat_spec, end_card_spec, still_time
from .beats import load_style

OUTPUT = "stills/index.json"
END_CARD_SECONDS = 1.5


def run(job: Job, profile: dict, show: bool = False) -> dict:
    beats = job.read_json("beats.json")
    cuts = job.read_json("cuts.json")
    face = job.read_json("face.json") if job.has("face.json") else None
    style = load_style(str(beats.get("style") or "ios"))

    job.log("  building the edited base video (full quality)…")
    base = compose.base_video(job, cuts, face)
    stills_dir = job.path / "stills"
    tmp = job.path / "renders" / "tmp-stills"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    items = []
    n = len(beats["beats"])
    with Renderer() as r, Progress(TextColumn("  rendering stills"), BarColumn(), TextColumn("{task.completed}/{task.total}"),
                                   TimeElapsedColumn(), console=console, transient=True) as progress:
        task = progress.add_task("s", total=n + (1 if beats.get("end_card") else 0))
        for i, beat in enumerate(beats["beats"]):
            spec = beat_spec(beat, profile, style, is_first=(i == 0), is_last=(i == n - 1))
            t = still_time(spec)
            frame = compose.extract_frame(base, beat["start"] + t, tmp / f"frame-{beat['id']:02d}.jpg")
            out = stills_dir / f"beat-{beat['id']:02d}.jpg"
            r.setup(spec)
            r.frame(t, frame, out, quality=90)
            items.append({"id": beat["id"], "name": beat["name"], "time": round(beat["start"] + t, 3), "offset": t,
                          "file": f"stills/{out.name}"})
            progress.advance(task)
        if beats.get("end_card"):
            spec = end_card_spec(profile, style, END_CARD_SECONDS)
            out = stills_dir / "end-card.jpg"
            r.setup(spec)
            r.frame(1.2, None, out, quality=90)
            items.append({"id": "end", "name": "End card", "time": beats["duration"] + 1.2, "offset": 1.2, "file": f"stills/{out.name}"})
            progress.advance(task)
    shutil.rmtree(tmp, ignore_errors=True)

    result = {"base": "renders/base.mp4", "count": len(items), "stills": items}
    job.write_json(OUTPUT, result)
    job.log(f"  {len(items)} stills → stills/")
    if show:
        report(job, result)
    return result


def report(job: Job, data: dict) -> None:
    console.print(f"\nStoryboard: {data['count']} stills in {job.path / 'stills'}")
    for s in data["stills"]:
        console.print(f"  {str(s['id']).rjust(3)}  {s['file']}  (frame at {s['time']}s)")
