# Studiio Reel Editor

Turns raw talking-head footage into a ready-to-post vertical reel, in the client's brand style.
It also makes **infographic videos**: a script and no camera (see "Infographic videos" below).
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
| 11. Quality check | `./reel check <job>` (also runs after every render) | `renders/v1-check.json` | ✅ |
| 12. Revision | `./reel revise <job> "beat 4: captions bigger"` | `beats.json` v2, profile feedback log, `renders/v2.mp4` | ✅ |

## Infographic videos

A job can be an infographic instead of a talking head: there is no footage, the frame is a stage,
and the picture is type, numbers and charts. It is chosen on **New job** (or `meta.kind:
infographic` in `job.json`), and its inputs are:

- `script.md` in the job folder: the words of the video, required. A blank line starts a new part.
- `voiceover/<file>` (mp3, wav, m4a…): optional, a recording of that script being read.

`./reel script <job>` stands in for stages 1 to 6. With a voice-over it wraps the recording in a
blank picture and runs the usual audio, transcription, takes and cut stages on it, then writes the
script's own spelling over what the transcriber heard. Without one it gives every word a time at
a reading pace (`infographic.words_per_second`, 2.6 by default, with a breath at commas, sentences
and paragraphs) and the video is silent with captions. Everything after that is the same pipeline:
beat plan, stills, render, quality check, revisions, comments. `./reel run` knows the difference.

What is different for an infographic:

- Only styles with a layout that shows no footage can make one (Explainer and Aurora today; iOS
  cannot). Explainer is offered first: it has the panels for figures (`stats`, `bars`, `flow`, `donut`, `versus`) next to
  `stat`, `flow` and `note`.
- **Every number on screen has to be in the script.** For a talking head an unsaid number is a
  warning; here it stops the plan, and a comment that asks for an invented figure is refused.
- A chart may stay through the sentences that give its figures (one beat of up to 9 s,
  `infographic.hard_max_s`), and each bar, ring or number arrives at the moment it is said.
- With no voice-over and no music the quality check says **review: silent**, not pass: add a
  track, or accept a silent video.
- There is no AI voice. The voice is a recording the client or the editor supplies.

## Motion, type and sound

**Motion.** Every move is a pure function of time, so a still is exactly a frame of the video. The
renderer carries a small motion kit every style shares (`STUDIIO.util` in `renderer/render.js`):
springs, eases, a camera over the caption line, measured word and letter positions for typing, and
`STUDIIO.cue()` to ask for a sound at a moment. A style's own moves live in
`styles/<name>/components.js`. A style is built by studying its reference frame by frame, at full
frame rate, for type, proportions and timing; none of a reference's content is reused.

**Type.** A style may bring its own typeface (`type:` in `style.yaml`, the file in the style's
`fonts/` folder with its licence). Explainer brings Inter (SIL Open Font License). A client that
must keep its own font everywhere says `style.type: client` in its profile.

**Sound.** The mix is the voice, music under it, and sound effects, set exactly to −14 LUFS
(`engine/render/compose.py`): the voice is brought to its level first, the music sits
`music.ducking_db` under it and dips a little more while someone speaks.

- *Music*: a licensed track in the job's `music/` folder (or the client's `music.tracks`) is used
  when there is one. Otherwise the engine composes a bed itself (`engine/music.py`, four moods,
  made to the length of the reel, nothing sampled). `music.source: none`, "No music" on New job, or
  the comment "no music" turns it off.
- *Sound effects*: styles mark their moves with cues (a change of scene, a count, a click).
  Effects are **off** by default (`sound.effects: off`). With `sound.effects: library` the files in
  `assets/sfx/` (one per cue name, see `assets/sfx/README.md`) are played at those cues; a client's
  `assets/sfx/` wins. `engine` plays sounds the engine synthesises itself (`engine/sound.py`).

## Quality check and revisions (stages 11 and 12)

Every render is checked before anyone sees it: format (1080×1920, 30 fps, length, audio), audio level
(−15 to −13 LUFS, true peak under −1 dBTP), caption sync (every caption word within 0.1 s of its
transcript time), face clear (no widget or caption box ever overlaps the tracked face), clean cuts
(no cut on speech, no click), black/frozen runs over 0.2 s, and a visual review that compares every
rendered frame with the approved still. Loudness, a widget on the face and a frozen beat are fixed and
re-rendered automatically (two rounds at most); anything else is reported as a fail.

`./reel revise jobs/test "beat 4: captions bigger" "0:32 no graphic here"` pins each comment to a
beat (by number or timestamp), turns it into a change, applies it (locked beats are skipped), appends
the rule to the client's `feedback_log` in `profile.yaml`, regenerates the stills and re-renders —
only the changed beats, the rest comes from the cache. The Beat Board then shows "Changes since last
version". Comments the plain rules don't understand go to Claude via `revision-request.md` →
`revision-plan.json`, exactly like the beat plan.

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

(An infographic job runs `script` in place of the first six stages: `./reel script <job> --show`.)

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
./reel render jobs/test                   # 10. the reel → renders/v1.mp4 (+ quality check)
./reel check jobs/test --show             # 11. check the latest render again
./reel revise jobs/test "beat 4: captions bigger"   # 12. a revision round
./reel status jobs/test                   # which stages ran and how long they took
./reel show jobs/test cuts                # print a result again without re-running
./reel view jobs/test                     # review page in the browser (http://localhost:8765)
./reel platform                           # the platform: jobs, queue, review, clients (http://localhost:8765)
./reel worker                             # the editor: takes queued jobs and runs the engine on them
./reel queue jobs/test --client haqq      # put an existing job folder on the queue
./reel doctor                             # check ffmpeg, models and libraries
```

`./reel view` opens the Beat Board: the full sequence with a beat scrubber (Play, click the bar,
click a beat row), the beats table, every shot as a card with a Replay button, and the on-screen copy
the client has to approve. The engine's own results (intake, transcript, takes, cut list, face
tracking) are folded underneath. Leave it running and reload after re-running a stage. Until the
renderer exists the player shows the cut preview and the shot cards show frames from it.

The client is remembered in the job's `job.json`, so `--client` is only needed the first time.

## The platform (Phase 2)

`./reel platform` serves the web layer over the job folders at http://localhost:8765, and
`./reel worker` turns this Mac into an editor that takes jobs from the queue. The plan behind
it is `PLATFORM.md`; the shape copies the editing loop of Brody's FounderOS, cut down to editing.

| Page | What it shows | Where it reads from |
|---|---|---|
| Jobs | every reel, its status, the Studiio verdict, the delivered file | the job folders |
| Queue | editors and their heartbeat, what is running (stage by stage), up next, waiting for Claude, finished today | `jobs/queue.json`, `jobs/workers/*.json`, `jobs/<job>/worker.json` |
| Job page | the Beat Board, Approve, "What should change?", the Direction thread, the versions | the job folder, `approvals.json`, `direction/thread.json` |
| New job | talking head or infographic; client, title, style, footage by link or upload (or a script and an optional voice-over), notes, due date → on the queue | writes `jobs/<client>-<slug>/` |
| Clients | brand, rules, the feedback log, the jobs per client | `clients/<slug>/profile.yaml` |
| Styles | every style with its version, layouts and components shown as real frames, the words to ask for each, who uses it | `styles/<name>/style.yaml`, the stills of the reels planned with it |

How a job moves: **New job** puts an `edit` item on the queue → the worker fetches the footage
(a Google Drive link works when the file is shared with anyone with the link), runs intake → stills,
then render and the quality check → the Jobs page says **To review**. When the beat plan needs
Claude, the item goes back on the queue as **waiting**: Claude Code reads `beats-request.md`, writes
`beats-plan.json`, and the worker picks the job up again by itself. A comment in **What should
change?** becomes a `revise` item (pinned to the player's time if you leave the box ticked); only the
changed beats re-render and the old version is marked superseded. **Approve** copies the file into
`jobs/<job>/delivery/`.

**Direction** is where the editor talks to Claude about one reel: "like the reference, when he says
the number the stat card sits low under the face", with a screenshot or a screen recording of the
reference attached (frames are pulled out of a recording) and the note pinned to the moment in our
reel. Cmd+Enter sends. A `direction` item goes on the queue and the worker hands it to Claude.

With Claude API credentials on the editor machine (`ANTHROPIC_API_KEY`; `./reel doctor` says whether
they resolve) the thread is **live**: Claude works with four tools the worker runs on the job —
`apply_changes` (beats of this reel, re-rendered), `set_client_rule` then `render` (a setting for every
video of the client, such as the caption font), and `show_beat` (look at a frame). The page shows what
Claude is doing while it works, and the reply lands with the new version linked; nothing to apply.
Without credentials the worker writes `direction-request.md`, the job waits for Claude Code to write
`direction-reply.json`, and the reply's changes get an **Apply** button. Either way Claude is told
which beats have room under the face and says plainly what the renderer cannot do. The thread lives
in `jobs/<job>/direction/`.

**The job page is the Beat Board.** One light column, like the HAQQ film board: the title and the
status line with Approve, the full sequence player, a box to talk to Claude about the whole reel, the
beat sheet, then every shot as a panel. Under each shot is that beat's own conversation: write a
comment (Cmd+Enter sends, a reference can be attached) and it is answered there. A note the engine's
own rules understand ("captions smaller", "no graphic", "show chart.png") is applied at once without
Claude; anything else goes to Claude, through the API when the machine has a key, otherwise through
the request file for Claude Code. Every comment gets its own answer under the beat it was made on,
and a conversation redraws only when its content changes. Versions, notes and the client's visuals
sit in a closed section at the bottom.

**Styles.** A style is a folder in `styles/`: `style.yaml` (the contract: layouts, components, their
props, planning advice), `components.css` (the look) and, when it needs its own, `components.js`
(component code, a background, a transition). Three exist: **iOS** (white cards, the client's accent),
**Aurora** (a black stage with a living glow in the client's colours, word-by-word type, frosted
glass; the glow blooms at every change of layout) and **Explainer** (the spoken words are the big
text on a dark or a light stage, key words in chips; the client's own product screens as panels with
a push-in and a cursor; steps joined by arrows). A layout is one of three kinds: the footage fills
the frame, sits in a card, or is not shown at all (a `stage` beat, where the voice carries on under
one statement or number). A client's profile names its usual style and may list others it can use
(`style.allowed`); **New job** then offers the choice, one style per reel. The brand colours, font and
logo stay the client's in every style. The Styles page shows both with real frames.

**Something specific in one beat.** A comment or a direction can change everything about a beat's
graphic, including its type: `set_graphic` swaps it for any other component of the style, checked
against the style card (props, word limits, anchors). The `asset` component shows one of the client's
own visuals — a screenshot, a chart, a photo — from `jobs/<job>/assets/` (upload under Notes &
assets on the job page) or `clients/<slug>/assets/`. `beat 7: show chart.png` is enough; the planner
also sees the list and may use them. Nothing that is not the client's goes into a reel.

The status of a job is never stored: it is derived from the files in its folder (`renders/vN.mp4`,
`vN-check.json`, `beats-request.md`, `approvals.json`, the queue), so a page can only show a state the
folder is actually in. Everything listens on 127.0.0.1 only.

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
