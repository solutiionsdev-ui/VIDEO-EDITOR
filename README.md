# Studiio Reel Editor

Turns raw talking-head footage into a ready-to-post vertical reel, in the client's brand style.
Read [BRIEF.md](BRIEF.md) for the full plan. This repo is **Phase 1: the engine**, run from the
command line on one machine.

## What's built so far

| Stage | Command | Output | Status |
|---|---|---|---|
| 1. Intake check | `./reel intake <job>` | `intake.json` | ✅ |
| 2. Audio extraction | `./reel audio <job>` | `audio/*.wav` | ✅ |
| 3. Transcription (word timestamps, EN/AR/mixed) | `./reel transcribe <job> --show` | `transcript.json` | ✅ |
| 4. Best-take selection | `./reel takes <job> --show` | `takes.json` | ✅ |
| 5. Silence cutting (edit list) | `./reel cuts <job> --show` | `cuts.json` + `previews/cut-preview.mp4` | ✅ |
| 6. Face tracking | `./reel face <job> --show` | `face.json` + `previews/face-preview.mp4` | ✅ |
| 7. Beat plan (Claude's judgment) | `./reel beats <job> --show` | `beats.json` (+ `beats-request.md`, `beats-plan.json`) | ✅ |
| 8. Storyboard stills | `./reel stills <job>` | `stills/beat-XX.jpg` + `renders/base.mp4` | ✅ |
| 10. Render | `./reel render <job>` | `renders/v1.mp4` (+ `renders/v1.json`) | ✅ |
| 11. Quality check | | | next |
| 12. Revision | | | next |

## The renderer (stages 8 and 10)

One HTML page, [renderer/page.html](renderer/page.html) + [renderer/render.js](renderer/render.js), draws a
beat: the footage frame (full-bleed or in a white card), one widget from the style's component set
([styles/ios/components.css](styles/ios/components.css)), and word-by-word captions with the active
word in the brand colour. It has no clock: `seek(t)` sets every element to its state at time *t*, so
a frame can be rendered in any order and a still is exactly the frame the video will have.

- `./reel stills <job>` first builds `renders/base.mp4` — the cut applied to the raw footage at full
  quality, cropped to 9:16, 30 fps — then screenshots one frame per beat through the page.
- `./reel render <job>` renders every beat frame by frame (several browsers in parallel), caches each
  beat by a fingerprint of everything that went into it (only changed beats re-render on a revision),
  joins them, adds the logo end card, normalises the voice to −14 LUFS with any music in `music/`
  ducked under it, and writes `renders/vN.mp4` at 1080×1920. `./reel view` then plays the render.

`./reel run <job>` stops after the stills (the beat board the client approves); the render is a
separate command on purpose.

## How the beat plan works (stage 7)

Plain code splits the cut script into candidate beats (one clause each, about 1.5–4 s) and writes
`beats-request.md`: the style card, the client profile and rules, and the candidates. Claude reads it
and writes `beats-plan.json` — which candidates to group, and per beat a layout, one graphic from
the style card, a caption style and B-roll. The engine validates that plan (every candidate used
once, only listed components, five words max per graphic, no invented numbers, white-card share)
and assembles `beats.json`, the Beat Board.

Two ways to get Claude's plan:

- **Claude Code (Phase 1, today):** `./reel beats jobs/test` writes the request and stops with
  "Needs Claude". Ask Claude Code to read `beats-request.md` and write `beats-plan.json`, then run
  the command again. `--force` throws the existing plan away.
- **Claude API (Phase 3):** with `ANTHROPIC_API_KEY` set, the same command calls the model named in
  the profile (`ai.model`, default Sonnet 5.5), logs tokens into `job.json`, and finishes in one go.

The iOS style — layouts, components and their props, caption and motion rules — lives in
[styles/ios/style.yaml](styles/ios/style.yaml). It is the contract between the planner and the renderer.

## Running it

Nothing to install by hand. The first run sets up a private Python and every library (including
ffmpeg) inside this folder, and the first transcription downloads the speech model (about 1.6 GB).

```bash
./reel run jobs/test --client haqq
```

runs every stage built so far and ends by printing the chosen takes and the cut list. Or go one
stage at a time:

```bash
./reel intake jobs/test --client haqq     # 1. problems with the footage? pass / warnings / blocked
./reel audio jobs/test                    # 2. voice track → audio/mario.wav
./reel transcribe jobs/test --show        # 3. every line with timestamps
./reel takes jobs/test --show             # 4. retakes found, which take was kept and why
./reel cuts jobs/test --show              # 5. the edit list + previews/cut-preview.mp4 to watch
./reel face jobs/test --show              # 6. face box per frame + previews/face-preview.mp4
./reel beats jobs/test --show             # 7. the beat board (see below)
./reel stills jobs/test                   # 8. one rendered still per beat → stills/
./reel render jobs/test                   # 10. the reel → renders/v1.mp4
./reel status jobs/test                   # which stages ran and how long they took
./reel show jobs/test cuts                # print a result again without re-running
./reel view jobs/test                     # review page in the browser (http://localhost:8765)
./reel doctor                             # check ffmpeg, models and libraries
```

`./reel view` opens the Beat Board: the full sequence with a beat scrubber (Play, click the bar,
click a beat row), the beats table, every shot as a card with a Replay button, and the on-screen copy
the client has to approve. The engine's own results (intake, transcript, takes, cut list, face
tracking) are folded underneath. Leave it running and reload after re-running a stage. Until the
renderer exists the player shows the cut preview and the shot cards show frames from it.

The client is remembered in the job's `job.json`, so `--client` is only needed the first time.

## Folders

```
engine/            pipeline stages (Python)         engine/stages/*.py, prompts/, schemas/, render/
renderer/          the HTML renderer page           page.html, render.js (driven by Playwright)
styles/ios/        style card + component CSS       style.yaml (contract), components.css (look)
clients/<slug>/    profile.yaml + assets/           clients/haqq/ is the pilot
jobs/<name>/       one folder per video             see jobs/README.md (never committed)
skill/SKILL.md     how Claude runs the engine
```

## How a client profile works

`clients/<slug>/profile.yaml` is the only place the engine learns anything about a client: brand
colours and fonts, style, caption rules, music, do's and don'ts, editing preferences (how long a
pause is kept, whether fillers are cut, which retake wins a tie) and the feedback log that grows
with every revision. Anything not set in the profile falls back to `engine/defaults.yaml`.
Copy `clients/_template/` to start a new client.
