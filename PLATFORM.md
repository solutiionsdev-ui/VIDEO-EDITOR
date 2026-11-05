# Studiio Reel Editor — Platform plan

*Written 2026-11-03. A plan for the web layer around the engine, modelled on the editing loop
in Brody's FounderOS, cut down to video editing only.*

Sources: Brody's reel (instagram.com/reels/DfQjdC4NgJi), the product site (founderos.sh), the
open-source code (github.com/Bennettxai/FounderOS, MIT licence, 1.3k stars) and the two
Instagram accounts (@brodyautomates, @foundeross).

---

## 1. What Brody actually has

FounderOS is a whole-business operating system: inbox, client funnel, finances, brand deals,
knowledge graph, agents, and a content department. It is open source (one Node.js app with a
SQLite database and JSON files, no separate backend), and the paid part is the hosted
version plus "Compute": a cloud computer per agent (their own MIOSA sandboxes). Prices on the
site: the platform is free to self-host, "Done for you" is $5,800, and the AI bill is quoted at
$100–200 a month.

**The editing module in the reel is not in the public code.** It is his newer private build
(a project called "glassjaw" in his workspace at os.exosoft.io, behind a login). What *is*
public and relevant to us:

| Public module | What it does | Ours? |
|---|---|---|
| Content Lab | Scrapes creators with Apify, transcribes with Whisper, finds "breakouts" (a post doing far better than that creator's normal), mines hooks and beats, scores ideas, drafts scripts | Not for now |
| Production queue | A list of AI-generation tasks (Higgsfield models: clips, upscale, thumbnails, ad variants), each with a full spec, fulfilled by an operator or agent | The idea, yes |
| Pipelines | A flowchart of generation steps compiled into queue tasks with dependencies | Later |
| Media library | Every delivered file, with the task that produced it | Yes, as our job folders |

What the reel shows of the private editing module, screen by screen:

| Screen | What is on it |
|---|---|
| **Queue** | Counters: In edit 7 (1 live) · To review 54 · Revisions 0 · Approved 58 · This month 4 / 50 planned · Open places 46. **Editors:** "MacBook · busy with HF01 · last seen 11 s ago". **Now running:** the job card with its stage ("done: assembling (8 min) · now: props · 3 more"). Up next. Finished today. |
| **Idea card** | Title, day, pillar, type (Main edit), format (Talking head + screen), length, caption keyword / CTA, angles from research, hook, body, talking points, notes. **Footage and video:** Raw footage status + a Google Drive footage link. "Final video (replaces the idea on the calendar)". Buttons: Post · Log a post · Re-render · New variant. |
| **Revisions box** | "Nothing to change yet. Pause the video where something is off and write what to change." A text box "What should change?", a tag (General), the timestamp ("Add at 0:00"), the editor to send it to ("MacBook · idle"), **Add to queue**. Notes tab beside it. Links: Final in Drive · Open in the editor's watch screen. |
| **Media tracker** | One row per video: thumbnail, title, pillar, Final video (Open), **My review** (Approved / Changes requested / Not watched), **Edit status** (Posted / Scheduled / Approved / Review), a checkbox per platform (Instagram, TikTok, YouTube, LinkedIn, X, Facebook), planned date. "Group by folder" and "Show superseded and archived". A folder "Apify · 24" holds 24 cuts from one shoot. |
| **Calendar** | Weeks colour-coded by content pillar, idea cards dragged into posting slots, progress bars per video (30 %, 70 %, 95 %). |
| **Content Lab** | Creator network graph, "breakouts in your niche: 17", a scatter of posts from "Ideal" to "Clickbait", ideas with a "write script" button. |

Job names tell you the system: **H1B1-T2** = hook 1, body 1, take 2. He films 5 hooks and 3
bodies, and the editor builds every combination, so one shoot becomes 24 videos. "HF01
Higgsfield reel" and "BA-PASSION" are one-off jobs.

## 2. How it works underneath — the parts worth copying

1. **Status is derived, never typed in.** In his code a task reads "delivered" only when its
   output file actually exists; "blocked" only when a dependency has not delivered. Nothing is
   stored as a status that could go stale. His house rule, written at the top of the repo:
   *"Honest or absent. Nothing in the UI may claim a state the system is not actually in."*
2. **The queue is a plain file** (`data/content-queue.json`). No message broker. A new row
   appears on the next page load.
3. **Editors are machines, not people.** A Mac running an agent polls the queue, says "Picked
   up", then sends progress reports; the dashboard shows the heartbeat ("last seen 11 s ago")
   and the current stage. The person is the reviewer, not the editor.
4. **Every job carries a complete spec** (model, parameters, media bindings) so the editor
   "executes it verbatim, no re-interpreting prose". Notes are for the reviewer; the spec is for
   the machine.
5. **A revision is a comment pinned to a timestamp**, sent back to the queue as a "Revise"
   job. The old version is superseded, not deleted, and stays visible under "Show superseded".
6. **Review and publishing are separate columns.** "My review" (Approved / Changes requested /
   Not watched) is a human verdict; "Edit status" (Review / Approved / Scheduled / Posted) is the
   pipeline's.
7. **One app, one language, local first.** Real data starts blank, demo data lives apart,
   secrets never reach the browser, colours are tokens, no emojis in the UI, and every page has
   a smoke test that it renders.

All seven are compatible with our brief. Points 1, 3, 5 and 6 are the ones we are missing.

## 3. What we build: the same loop, editing only

In scope: footage in → edit → review → revise → approve → deliver. Out of scope for now: the
calendar, idea generation, creator research, posting, hooks × bodies variants. Those stay in
the client's tools or come in a later phase.

Five screens:

1. **Jobs** (his Media tracker). One row per reel: thumbnail, title, client, version, Studiio
   review, client review, status, final link, date. Group by shoot. Superseded versions hidden
   by default.
2. **Queue** (his Queue). Editors (your Mac first; any other Mac with the engine installed is a
   second editor), Now running with the stage and its timer (intake → audio → transcribe → takes
   → cuts → face → beats → stills → render → check), Up next, Finished today, the counters.
3. **Job page** (our Beat Board, plus his revision box). Play the reel; pause; "What should
   change?" at the current time → the comment becomes a rule → only the changed beats
   re-render → a new version. Version list (v1, v2, v3) with what changed. Approve / Request
   changes. Quality-check results. The "Copy to approve" section we already have.
4. **New job** (his Idea card, cut to what an editor needs). Client, title, footage (Drive link
   or upload), notes for the edit, format (9:16, max length), style, language, due date → Add
   to queue.
5. **Clients** (the client profile, made visible). Brand colours and fonts, do / don't rules,
   the feedback log (every comment that became a rule, with the job it came from), review
   settings (Studiio reviews the first N jobs).

## 4. How it maps onto what already exists

| His piece | Ours | State |
|---|---|---|
| Queue file | `jobs/queue.json` + each job's `state/job.json` | job.json exists; queue file is new |
| Editor machine polling the queue | `reel worker`: a loop that takes the next job and runs the stages as it does today | New (the stages exist) |
| Progress reports and heartbeat | Stage timings already written to `state/job.json`; add a heartbeat and show it live | Partly |
| Footage link on the idea card | `jobs/<job>/raw/` filled from a Drive link or an upload | raw/ exists; the link is new |
| Revision box | `reel revise "beat 4: captions bigger"` | Exists on the command line; the UI is new |
| Superseded versions | `renders/v1.mp4`, `v2.mp4`, `v3.mp4` + versioned `beats.json` | Exists |
| My review / Edit status | `approvals.json` per job | New |
| Media tracker | A Jobs page reading every job folder | New (the single-job page exists: `reel view`) |
| Comments become rules | `clients/<slug>/profile.yaml` → `feedback_log` | Exists |
| Quality check | Stage 11 (`reel check`) | Exists |
| Final in Drive | A `delivery/` folder per job, Drive upload later | New |
| Honest status | Status computed from files: `v3.mp4` exists → rendered; `v3-check.json` says pass → checked; `approvals.json` → approved | New rule, cheap to apply |

The engine stays exactly as it is. The platform only reads the job folders and writes three
small files: the queue, approvals, and the worker heartbeat.

## 5. Technical shape (plain words)

- **Stay in Python.** One small web app (FastAPI) in a `platform/` folder, serving the five
  pages in the same dark shot-panel look as the Beat Board. The job folders remain the truth;
  the app never keeps its own copy of a status.
- **The worker is the engine with a loop around it.** `reel worker` polls the queue every few
  seconds, claims a job, runs the stages as subprocesses (as now), writes a heartbeat and the
  current stage to `state/job.json`, and goes back to the queue. Kill it and restart it and
  nothing is lost; a half-done job just resumes at the next stage.
- **Local first, like his.** Runs on your Mac at a local address, same as `reel view` today.
  The Mac is needed anyway (Whisper, face tracking and Chromium run there). A hosted version
  with client logins is Phase 3, after the first ten HAQQ reels.
- **Footage in:** paste a Drive link (as he does) or drop a file. **Footage out:** the approved
  mp4 lands in `delivery/` and the Jobs row gets its link.

## 6. Build order

1. Jobs page and Queue page over the existing job folders (read-only, nothing new to store).
2. `reel worker` with the queue file, heartbeat and live stage progress.
3. Job page: Beat Board + revision box + versions + Approve / Request changes.
4. New job form (client, footage link or upload, notes, format, due date).
5. Clients page (profile, rules, feedback log).
6. Delivery folder and superseded versions.
7. Run HAQQ's first ten reels through it. That is the Phase 1 acceptance test from the brief,
   done through the platform instead of the command line.

Later phases, in the order they pay off: hooks × bodies variants (one shoot → many reels),
client review links, a second editor machine, posting.

## 7. Decisions needed before building

1. **Where it runs first.** Recommendation: your Mac only, local address, like `reel view`
   today. Hosting comes after the first ten reels.
2. **Who reviews in v1.** Recommendation: Studiio only (that is what the profile's
   `review.studiio_review` already says); client review by shared link in a later phase.
3. **How footage arrives.** Recommendation: both a Drive link and a file drop; the link is what
   clients already do.

---

## Built on 2026-11-03

Steps 1 to 6 of the build order exist and ran end to end on this Mac: `./reel platform` (the five
pages, in `engine/web/`), `./reel worker` (`engine/worker.py`), the stores and the derived status
(`engine/ops.py`). Tested: a job created on the New job page with a footage link, fetched and edited
by the worker, paused for the beat plan, resumed once the plan file appeared, rendered and checked;
a time-pinned comment on the test job that became a revise item, a v4 with one beat re-rendered and
the earlier versions marked superseded; Approve, which put the file in `delivery/`. Step 7, the
first ten HAQQ reels, still needs real footage. One change from the plan above: the code lives in
`engine/web/` and `engine/ops.py` rather than a `platform/` folder, because Python already has a
module called `platform`.

## Borrowed and refused (2026-11-05)

Three of Torsten Roscoe's posts were studied this week (the style menu, the seven reel styles, the
breakdown sheet). What was taken and what was left, so the next idea gets the same test.

Taken:
- A catalog of what the engine can render, as real frames: the Styles page.
- A direction box on every beat of the Beat Board, with a graphic choice and "keep as is", posted
  as one round. The editor never types a beat number.
- Something specific in one beat: the graphic swap and the client's own visuals as a component.

Refused:
- A sheet that lives as an artifact inside one chat session. Ours is server-backed: it survives
  sessions and machines, the worker and the live editor read the same files.
- Headless Claude Code behind a customer product. Logins expire and usage windows stall every
  customer at once; the API is the backbone, Claude Code stays the operator's tool.
- Mixing styles beat by beat. One visual language per reel; variety comes from layouts, components
  and the client's own visuals (`STYLES.md`).
- "10X" claims. Every feature here is judged by what it does to a real reel, not by a hook line.

