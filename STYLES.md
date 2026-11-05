# Styles: a library that keeps growing, client-owned styles, and mixing per beat

Written 2026-11-05 from Mario's brief: "build different styles that are always updating; clients can
lock styles made by them; clients can mix styles for each frame." This is the plan; `PLATFORM.md`
is the pattern it follows.

## What exists today

One style per reel. `clients/<slug>/profile.yaml` names it (`style.name: ios`), `styles/ios/style.yaml`
is the contract (layouts, components and their props, captions, motion, rules) that the beat planner
is allowed to use and the quality check enforces, `styles/ios/components.css` is the look, and the
component builders live in `renderer/render.js`. A beat records only its layout and graphic; the
style is implied by the job.

## What changes

### 1. A style library with versions ("always updating")

- `styles/<name>/` holds `style.yaml`, `components.css`, `components.js` (the builders move out of
  `render.js`, so a new style can bring new components without touching the renderer), and a
  `CHANGELOG.md`. `style.yaml` gains `version: 3` and `status: draft | published | retired`.
- A job pins what it was planned with: `beats.json` records `styles: {ios: 3, paper: 1}`. Updating
  a style never changes a reel that is already planned or delivered; a new job takes the latest
  published version, an old job can be moved up on purpose ("update to the latest look" re-plans
  nothing and re-renders the changed beats).
- The platform gets a **Styles** page: every style as real frames (the stills the engine already
  makes), its components and layouts with the words to ask for them, its version and changelog,
  who owns it, and which clients may use it.

### 2. Styles owned by a client ("lock")

- A style made for a client lives in `clients/<slug>/styles/<name>/`, same files. It is visible to
  that client only and never offered to another client. Studiio's own styles stay in `styles/`.
- `locked: true` in its `style.yaml` freezes it: library updates do not touch it, the live editor
  may not change its rules, and a change proposed by Studiio becomes a new draft version the client
  approves on the Styles page before it is published. Unlock is the client's click, logged.
- The client profile lists what the client may use: `styles.allowed: [ios, paper, haqq-notice]`,
  with the first as default.

### 3. Mixing styles per beat

- A beat gains `style: paper`. The planner may use any allowed style; the renderer loads every
  style a reel uses (CSS and components namespaced per style) and switches per beat, with the
  style's own motion rules.
- Mixing rules in the profile keep it intentional: `styles.mixing: {max_per_reel: 2, min_run: 3}`
  means at most two styles in one reel and a style holds for at least three beats before it
  changes, unless the client sets it free. The quality check reports breaks of these rules as
  warnings, not failures.
- The Direction thread learns it: "beats 7 to 9 in the paper style" becomes a `set_style` change,
  and the Beat Board shows the style on every beat.

### 4. Where new styles come from

- **New style** on the Styles page: name, owner (Studiio or a client), two to five reference frames
  or clips, and a sentence on the look. Claude drafts `style.yaml` and the CSS from the references,
  the engine renders a sample sheet (every component on a neutral clip), Studiio reviews, then
  publishes. Nothing from a reference is copied into a product: the references are study material
  for the draft and stay in the style's `references/` folder, never in a client reel.
- Every style change goes the same way: draft → sample sheet → publish, with the version bumped.

## Order of work

1. Library and versions: move components out of the renderer, add versions, pin them in jobs,
   build the Styles page. The iOS style becomes version 1 of the library; nothing visible changes.
2. Per-beat styles: the beat field, the renderer loading several styles, the planner card and the
   quality check rules, `set_style` in the live editor, the Beat Board tag. Proved with a second
   style (a flat "paper" style: same components, different look) so mixing has something to mix.
3. Client-owned styles: the client folder, allowed lists, lock and approval on the Styles page.
4. New styles from references: the draft flow with the sample sheet.

## Decisions taken unless Mario says otherwise

- Mixing is per beat, with runs, not a different style on every single frame of video.
- "Lock" means private to the client and frozen until the client approves a new version.
- Studiio can still see and render a client's locked style (to make their reels); it cannot
  change it without the client's approval.
