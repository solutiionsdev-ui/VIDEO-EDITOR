"""Render one beat of a job at one moment, to check the renderer by eye.

    .venv/bin/python tests/render_one.py jobs/test 5 1.5 out.jpg
                                          job    beat  t   output

t is seconds from the beat start. Needs renders/base.mp4 (made by `./reel stills`),
or pass --no-footage to render the graphics on black.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.job import Job  # noqa: E402
from engine.profile import load_profile  # noqa: E402
from engine.render import compose  # noqa: E402
from engine.render.driver import Renderer, beat_spec, end_card_spec  # noqa: E402
from engine.stages.beats import load_style  # noqa: E402


def main() -> None:
    job = Job(Path(sys.argv[1]))
    beat_id = sys.argv[2]
    t = float(sys.argv[3])
    out = Path(sys.argv[4])
    no_footage = "--no-footage" in sys.argv
    profile = load_profile(job.state.get("client"))
    beats = job.read_json("beats.json")
    style = load_style(str(beats.get("style") or "ios"))
    if beat_id == "end":
        spec = end_card_spec(profile, style, 1.5)
        frame = None
    else:
        idx = next(i for i, b in enumerate(beats["beats"]) if str(b["id"]) == beat_id)
        beat = beats["beats"][idx]
        spec = beat_spec(beat, profile, style, is_first=(idx == 0), is_last=(idx == len(beats["beats"]) - 1))
        frame = None
        if not no_footage:
            base = job.path / "renders" / "base.mp4"
            if not base.is_file():
                base = compose.base_video(job, job.read_json("cuts.json"), job.read_json("face.json"))
            frame = compose.extract_frame(base, beat["start"] + t, out.with_suffix(".frame.jpg"))
    with Renderer() as r:
        geo = r.setup(spec)
        r.frame(t, frame, out)
    print(f"wrote {out}  layout={spec['beat']['layout']} zone={geo.get('zone')} captions={geo.get('captions')}")


if __name__ == "__main__":
    main()
