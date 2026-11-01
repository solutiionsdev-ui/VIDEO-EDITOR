# Job folders

One folder per video. Nothing in here is committed to git (client footage is confidential).

```
jobs/<date>_<client>_<slug>/
  raw/            the client's talking-head footage (1+ files: MP4, MOV)
  broll/          optional B-roll
  music/          optional music the client supplied
  reference/      optional reference video to copy the style of
  note.md         optional short brief from the client
  job.json        stage status and timings (written by the engine)
  log.txt         everything the engine printed
  intake.json     stage 1 — pass / warnings / blockers
  audio/          stage 2 — voice track(s) as WAV
  transcript.json stage 3 — every word with its start/end time
  takes.json      stage 4 — retakes found, which one was kept and why
  cuts.json       stage 5 — the edit list (keep segments, removed pauses)
  face.json       stage 6 — face box per frame, no-go zone per segment
  previews/       low-res preview videos for checking stages 5 and 6
  beats.json      stage 7 — the beat plan            (not built yet)
  stills/         stage 8 — one storyboard still per beat (not built yet)
  renders/        stage 10 — v1.mp4, v2.mp4 ...       (not built yet)
```

Run everything with:

```
./reel run jobs/<folder> --client <client>
```

or one stage at a time: `./reel intake jobs/<folder>`, `./reel transcribe jobs/<folder> --show`, …
