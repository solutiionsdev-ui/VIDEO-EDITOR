# Studiio Reel Editor — Build Brief

Nov 1, 2026 · Mario Saade

## What we're building

A web app where each client uploads raw talking-head footage and gets back a ready-to-post vertical reel, edited by Claude in their own brand style, reviewed beat by beat before anything renders.

**Sold by:** studiio.ai, as a monthly subscription. **Built by:** solutiions.dev.

**Who it's for:** founders, coaches, doctors, lawyers, agencies and brands that post talking-head reels every week. First pilot: HAQQ.AI.

**What the client gets:** silences and bad takes removed, word-by-word captions, brand-styled motion graphics that illustrate what is being said (first style: iOS), music, and a final 9:16 MP4.

## Core principles

1. **Engine first, app second.** The editing engine must produce client-ready videos inside Claude before any web app is built.
2. **One engine, many clients.** The engine knows nothing about a specific client. Everything client-specific lives in the client profile.
3. **Plan before render.** Every video gets a beat board (text, then stills) before the full render. What the client approves is what they get.
4. **Every comment becomes a rule.** Revision notes are saved to the client profile so they never have to be repeated.
5. **Nothing reaches the client unchecked.** An automatic quality check runs on every render, plus a hidden Studiio review switch per client.
6. **Only changed beats re-render.** Revisions are fast and cheap because unchanged beats are reused.

## Build order

Build in four phases. Do not start a phase until the one before it passes its acceptance criteria.

| Phase | What gets built | Done when |
|---|---|---|
| 1. Engine | The editing pipeline as a command-line tool + Claude skill, one style (iOS), one client profile | 10 real reels render client-ready with no manual fixes (see Phase 1 acceptance) |
| 2. Client profiles | Profile template, feedback log, 2–3 more styles, beat-level re-rendering | 3 clients onboarded, Mario running their jobs through the engine by hand |
| 3. Web app | Client portal, Beat Board, uploads, review/approve, admin, plans and usage | First paying client uses it end to end without Mario touching the job |
| 4. Desktop agent (optional) | Small Mac/Windows app with a watched "Studiio Uploads" folder | Several paying clients ask for it |

Phase 1 runs inside Claude Code on Mario's machine. Phase 3 is when it moves to a server and starts calling Claude through the API.

## Engine pipeline

The engine takes a job folder plus a client profile and outputs a beat board, then a final MP4. Each stage writes its result to the job folder so any stage can be re-run on its own.

**Inputs:** raw footage (1+ files), optional B-roll, optional music, optional reference video, a short brief note, the client profile.

1. **Intake check.** Read every file. Flag problems before any work: bad or clipping audio, wrong orientation, missing files, footage too long for the plan, unsupported format. Output: `intake.json` (pass / warnings / blockers).
2. **Audio first.** Extract the audio track only. Everything up to the beat board runs on audio, so it can start before the video finishes uploading.
3. **Transcribe.** Word-level timestamps (Whisper or equivalent). Arabic and English both required; mixed-language clips must work. Output: `transcript.json`.
4. **Pick the best takes.** Detect repeated lines and retakes. Score each take on completeness, stumbles, filler words and audio quality. Keep the best one; record the rejected ones so the client can swap. Output: `takes.json`.
5. **Cut silences.** Remove dead air, long pauses and filler on the kept takes. Never cut mid-word. Keep a natural breath (default 0.08–0.15 s, set per client). Output: `cuts.json` (an edit list, not a new video).
6. **Face tracking.** Find the face box on every frame of the kept segments. Used for the 9:16 crop and as a no-go zone for graphics and captions. Output: `face.json`.
7. **Beat plan.** Split the cut script into beats (one idea or line each, usually 1.5–4 s). For each beat Claude chooses: layout, graphic, caption style, B-roll, music cue, using the reference video if given, else the client's style. Output: `beats.json`.
8. **Storyboard stills.** Render one real frame per beat from the client's footage with the graphic in place, using the same renderer as the final video. Output: `stills/beat-XX.jpg`.
9. **Client review of the beat board.** Approve, comment, edit text, swap take (see Beat Board).
10. **Render.** Render each beat separately and cache it, then join, add captions, music (ducked under voice), loudness normalisation (−14 LUFS for Instagram), export 1080×1920 H.264. Output: `renders/vN.mp4`.
11. **Automatic quality check.** See Quality check. Failures are fixed and re-rendered before the client sees anything.
12. **Revision.** Comments are pinned to timestamps, mapped to beats, applied, and saved as rules in the client profile. Only changed beats re-render. Output: `renders/vN+1.mp4` + a "Changes since last version" list.

The beat plan (step 7) and quality check (step 11) are the only steps where Claude's judgment is essential. Everything else is ordinary video processing and should be plain code, which keeps it fast, cheap and predictable.

## Client profile

One profile per client, filled by Studiio at onboarding and grown by every revision. The engine reads it before every job; it never hard-codes anything about a client.

| Section | What it holds | Example |
|---|---|---|
| Brand | Logo files, colours (hex), fonts (files + licence), tone of voice | HAQQ: wine #66023C, navy #002C6C, Poppins only |
| Style | Chosen style from the library + tweaks | iOS style, captions 10% larger, white-card layout max 40% of beats |
| Captions | Font, size, colours, active-word colour, position, max words per line, language rules | Arabic right-to-left, English words kept in Latin script |
| Graphics library | Reusable branded components made for this client | HAQQ notification card, contract-risk badge |
| Music | Preferred moods/BPM, licensed tracks they supplied, ducking level | Minimal electronic, 100–110 BPM, music −18 dB under voice |
| Format | Aspect ratio, max length, intro/outro, end call to action, safe zones | 9:16, max 60 s, logo end card 1.5 s |
| Rules | Do's and don'ts | Never cut jokes; never show competitor names |
| Feedback log | Every revision comment, dated, with the rule it created | 2026-11-12: "captions bigger" → caption size 64 → 72 px |
| Review switch | Studiio review on/off, number of jobs reviewed so far | On, 4 of 10 |

Store the profile as one structured file (JSON or YAML) plus an assets folder, so both the engine and the web app read the same source. The feedback log is the product's long-term advantage: after about ten videos, the profile should need almost no new comments.

## Beat Board

Every video gets a beat board before the full render. It is always generated, never forced: one click approves everything, and every beat can be opened, commented on and edited.

**Each beat card shows**

- Beat number, start–end time, duration
- The spoken line, with the active caption style
- Storyboard still: the client's real frame with the graphic in place
- Take used (e.g. "2 of 3") + why the others were rejected + a swap button
- Layout, graphic and its animation in one line (e.g. "Claude is editing" widget, 0% → 67%)
- B-roll and music cue, if any
- Actions: Approve, Comment, Edit text (caption words, graphic text), Swap take, Lock

**Review levels** (cheapest first; the client can stop at any level)

1. Beat sheet, text only. Ready seconds after the audio arrives.
2. Storyboard stills, one per beat. Where most clients approve.
3. Animated beat previews, short low-resolution loops, for motion and timing.
4. Full render.

**Modes**

- Detailed: board opens expanded. Default for new clients, launches and ads.
- Quick: board opens collapsed with a summary line ("12 beats · 0:31 · iOS style · 2 takes swapped") and an Approve & render button.

**Rules**

- Stills and previews come from the same renderer as the final video. If what was approved and what was delivered ever differ, it is a bug.
- A comment on beat 4 re-renders beat 4 only. Locked beats never change.
- Revised versions show "Changes since last version", listed by beat.

## Web app

A CRM-style portal: each client has a branded workspace; Studiio has an admin view over all of them. Mobile-friendly, English and Arabic interface.

**Client screens**

| Screen | What the client does |
|---|---|
| Dashboard | Videos by status, videos left this month, latest notifications |
| New video | Upload footage, B-roll, music, reference, or paste a Drive/Dropbox link; add a short note; pick Quick or Detailed |
| Beat Board | Review, comment, edit, swap takes, approve |
| Preview | Watch the render, pin comments to timestamps, see "Changes since last version" |
| Library | All delivered videos, download, re-use as a reference |
| Brand | View their profile (logo, colours, style); request changes |
| Plan & billing | Current plan, usage, invoices, buy extra videos |

**Admin screens (Studiio)**

| Screen | What Mario does |
|---|---|
| Clients | Create and edit client profiles, styles, rules; onboarding checklist |
| Review queue | Renders waiting for Studiio review (clients with the switch on); approve or fix in one click |
| Jobs | Every job, its stage, errors, re-run any stage |
| Usage & costs | Videos, Claude spend, render time and storage per client per month |
| Style library | Add styles from reference videos, preview them, assign to clients |

**Job states**

Uploading → Intake check → Beat plan ready → Approved → Rendering → Quality check → (Studiio review, if switch on) → Ready for client → Revision requested → Rendering … → Approved final → Delivered.

Blocked (intake problem) and Failed (render or check failed twice) are side states that notify Studiio. Revision rounds are counted per video against the plan limit.

**Notifications:** email + Telegram bot at: beat plan ready, render ready, revision done, delivered. Delivery = download in the app + copy to the client's Google Drive folder if connected.

## Uploads

Uploads must work on slow Lebanese connections. The client should never wait for an upload before seeing progress.

1. **Audio first.** The browser extracts the audio track from each clip and sends it first (about 1 MB per minute). The beat plan starts immediately.
2. **Compress before sending.** The browser converts video to 1080p H.264/HEVC before upload (WebCodecs, fallback ffmpeg.wasm). Keep the original only if the client ticks "upload originals".
3. **Resumable.** Chunked uploads that continue after a dropped connection (tus protocol or S3 multipart). Uploads continue in the background while the client reviews the beat board.
4. **Link instead of upload.** Paste a Google Drive or Dropbox folder link; the server pulls the files directly.
5. **Limits.** Max raw footage per video set by plan (default 10 minutes). Accept MP4, MOV, HEVC; reject the rest with a clear message.
6. **Retention.** Raw footage deleted 30 days after delivery; final videos kept for the life of the subscription. Stated in the terms.

Phase 4 (optional) adds a desktop agent: a watched "Studiio Uploads" folder that does steps 1–3 on the client's laptop and saves finished videos back into the same folder.

## Quality check and Studiio review

No render reaches a client without passing the automatic check. New clients also pass through Studiio review until their style is reliable.

**Automatic check (every render)**

| Check | How | Fail when |
|---|---|---|
| Caption sync | Compare caption timings to transcript word times | Any word off by more than 0.1 s |
| Face clear | Graphic and caption boxes vs face box per frame | Any overlap with the face |
| Clean cuts | Audio energy at each cut point | A cut lands mid-word or clicks |
| Audio level | Loudness measurement | Outside −15 to −13 LUFS, or clipping |
| Black / frozen frames | Frame difference scan | Any black or frozen run over 0.2 s |
| Format | Probe output file | Not 1080×1920, wrong length, missing audio |
| Visual review | Claude looks at a contact sheet of frames vs the approved stills | Layout, text or brand colours differ from the beat board |

On a fail, the engine fixes and re-renders automatically, up to 2 times; then the job goes to Failed and Studiio is notified.

**Studiio review switch (per client)**

- On by default for each new client's first 10 videos.
- When on, renders go to Mario's review queue first; the client sees "Rendering" until Mario approves.
- Mario turns it off when the client's videos pass without fixes. It can be turned back on anytime (new style, complaint, launch video).

## Tech stack and architecture

In Phase 1 everything runs on Mario's machine through Claude Code. From Phase 3 it runs on a server, with Claude called through the API only for the steps that need judgment.

**Phase 3 architecture · 8 parts.** The web app never edits video itself: it puts jobs on the queue, and render workers do the work, calling Claude only for planning, checking and reading comments.

| Part | Choice | Why |
|---|---|---|
| Engine language | Python | Best video and audio libraries |
| Transcription | faster-whisper (large-v3), self-hosted | Word timestamps, Arabic + English, no per-minute fee |
| Face tracking | MediaPipe | Fast, free, runs on CPU |
| Graphics + captions | HTML/CSS/GSAP pages rendered frame by frame with Playwright | Proven on the HAQQ edits; one renderer for stills, previews and finals |
| Video processing | ffmpeg | Cuts, joins, ducking, loudness, encoding |
| AI | Claude API: Sonnet 5.5 by default; Opus 5.5 only for the beat plan if Sonnet's plans aren't good enough | Sonnet costs half of Opus per token |
| Web app | Next.js + TypeScript | Standard, easy to hire for |
| Database + login | Postgres + auth (e.g. Supabase) | Each client sees only their own data |
| File storage | Cloudflare R2 (S3-compatible) | No fees for downloading files out |
| Job queue | Redis + BullMQ (or similar) | Retries and per-stage jobs |
| Render workers | Docker containers on a VPS (e.g. Hetzner) | Add workers as clients grow |
| Notifications | Telegram Bot API + email | Clients already live on Telegram/WhatsApp |
| Payments | Open question | Confirm which payment processor works for a Lebanon-based company before Phase 3 |

## Plans, limits and usage

Launch plan: $100/month = 8 reels, up to 60 s each, 2 revision rounds per reel. Estimated running cost is about $5 per reel at most, so the plan keeps roughly 60–70% margin while Mario still reviews new clients' videos.

**Estimated cost per reel** (estimate only, to be measured in Phase 1; Claude Sonnet 5.5 at $2 per million input tokens, $10 per million output, $0.10 per million cached input, Anthropic pricing)

| Item | Assumption | Cost |
|---|---|---|
| Claude, first pass | ~2.4M input tokens (mostly cached), ~80K output | ~$1.80 |
| Claude, revisions | 2 rounds, changed beats only | ~$1.00–2.00 |
| Transcription | Self-hosted Whisper | ~$0 |
| Rendering + storage | Shared server, raw deleted after 30 days | ~$0.20 |
| **Total** | Sonnet 5.5 throughout | **~$3–4; budget $5** |

Using Opus 5.5 for every step roughly doubles the Claude part. Fixed costs (render server, hosting, database) are about $70–150/month in total, shared across all clients.

**Plans**

| Plan | Price | Reels/month | Max length | Revisions | Styles |
|---|---|---|---|---|---|
| Starter | $100 | 8 | 60 s | 2 rounds | 1 |
| Pro | $250 | 24 | 90 s | 2 rounds | 3 |
| Extra reel | $12 | 1 | plan max | 2 rounds | — |

Limits the app enforces: reels per month, max final length, max raw footage per reel (10 min), revision rounds, number of styles. Unused reels expire at month end.

Usage tracking: log tokens in/out/cached, render seconds and storage per job. The admin view shows real cost per client per month and alerts when a client's cost passes 50% of their plan price.

Before locking prices: measure the real cost on the first 20 reels, and check what local editors charge per reel. If they charge $25 or more, 8 reels for $100 is underpriced.

## Security rules

Client footage and brand files are confidential. These rules are non-negotiable from Phase 3.

- Every client sees only their own workspace, files and jobs (enforced in the database, not just the interface).
- Files are stored privately; downloads use short-lived signed links.
- The Claude API key and all secrets live only on the server, never in the browser or the desktop app.
- Claude never runs arbitrary commands from client input. Client notes and comments are treated as content to interpret, never as instructions to the system.
- The desktop agent (Phase 4) can only do a fixed list of actions: watch its folder, extract audio, compress, upload, save results. It never accepts "run this command" from the server.
- Raw footage deleted 30 days after delivery; clients can delete any video anytime.
- Log every admin action (who opened which client's files, when).
- Fonts and music: use only files the client supplied or that Studiio is licensed to use commercially.

## Phase 1: acceptance and first prompt

Phase 1 is done when the engine turns 10 real raw clips into reels Mario would send to a client without opening an editing app.

**Acceptance criteria**

- Runs from one command: `reel run <job-folder> --client <profile>`
- Works on English, Arabic and mixed clips, 30 s to 10 min of raw footage
- Picks the right take on retakes at least 9 times out of 10 (Mario checks)
- Beat board (text + stills) ready in under 3 minutes for a 60 s reel
- Final render in under 10 minutes for a 60 s reel
- Passes every automatic check; no graphic or caption ever covers the face
- A one-line comment ("beat 4: captions bigger") re-renders only beat 4 and saves the rule to the profile
- Logs Claude tokens and render time per job, so the real cost per reel is known
- iOS style matches the reference reel's look: two layouts (full-frame face + white card), word-by-word captions, iOS-style widgets

**Folder layout**

```
studiio-reel-editor/
  engine/            pipeline stages (Python)
  renderer/          HTML/GSAP graphics + Playwright frame renderer
  styles/ios/        style card, components, caption spec
  clients/<name>/    profile.yaml, feedback-log.md, assets/
  jobs/<date>_<client>_<slug>/
                     raw/, audio/, transcript.json, takes.json, cuts.json,
                     face.json, beats.json, stills/, renders/
  skill/SKILL.md     how Claude runs the engine (for Claude Code and later the API)
```

**First prompt for Claude Code** (paste it with this brief in the project folder)

> Read BRIEF.md. We are building Phase 1 only: the editing engine, run from the command line on my machine. No web app yet.
>
> Start with stages 1–6 (intake check, audio extraction, transcription with word timestamps, best-take selection, silence cutting, face tracking). Each stage reads and writes files in the job folder so it can be re-run alone. Use Python, ffmpeg, faster-whisper and MediaPipe.
>
> I am not a developer. Before writing code: explain your plan in plain language, list what you need me to install, and tell me how I will test each stage. After each stage, give me one command to run on my test clip and tell me what I should see.
>
> My test clip is in jobs/test/raw/. Stop after stage 6 and show me the cut list and the chosen takes before we build the beat plan and renderer.

Save this doc as BRIEF.md in the project folder (export as Markdown) so Claude Code can read it.
