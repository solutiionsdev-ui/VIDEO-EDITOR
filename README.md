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
| 7. Beat plan (Claude) | | `beats.json` | next |
| 8. Storyboard stills | | `stills/` | next |
| 10. Render | | `renders/v1.mp4` | later |

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
./reel status jobs/test                   # which stages ran and how long they took
./reel show jobs/test cuts                # print a result again without re-running
./reel doctor                             # check ffmpeg, models and libraries
```

The client is remembered in the job's `job.json`, so `--client` is only needed the first time.

## Folders

```
engine/            pipeline stages (Python)         engine/stages/*.py
renderer/          HTML/GSAP graphics renderer      (Phase 1, next)
styles/ios/        style card, components, captions (Phase 1, next)
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
