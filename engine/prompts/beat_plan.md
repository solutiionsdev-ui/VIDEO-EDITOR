{job_intro}

# Style card
{style_card}

# Client
{client_summary}

# Client rules
{client_rules}

# How to plan

1. Beats. Each candidate below is one clause with its timing. Keep the split unless merging
   neighbours makes a better beat ({beat_length}).
   Every candidate must be used exactly once, in order.
2. Hook. The first 1–3 seconds decide whether anyone keeps watching. The first beat is the hook:
   the style's headline or statement, or the most striking number in the opening line, caption
   style "emphasis".
3. Graphics. Pick a widget only when it illustrates what is being said at that moment.
   {style_planning}
   {graphics_share} Never two widgets in one beat.
4. Text in graphics comes from what is said in that beat or from the client brief — never
   invented numbers, names or quotes. Five words maximum per graphic (lists: 1–4 words per item),
   unless the component's own entry in the style card gives a max_words.
5. Layouts. {layout_rules}
6. Captions. "normal" everywhere except the hook and the payoff (the one line the viewer should
   remember), which are "emphasis". highlight_words are 1–3 words of the beat's own text to
   colour in the accent; use them on roughly a third of the beats, on the word that carries the
   meaning.
7. B-roll. Only files listed under "B-roll available". If none are listed, broll is always null.
   The asset component works the same way: only a file listed under "Client visuals available".
   If none are listed, never use asset.
8. Music. One mood and tempo for the whole reel, from the client's preferences.

# Output

Return JSON only, matching this shape exactly:

{output_shape}

# This job

Duration after the cut: {duration}
Client note: {note}
B-roll available: {broll}
Client visuals available for the asset component: {assets}

Script (what is said, in order):
{script}

Candidate beats (id, start–end in seconds of the edited video, text):
{candidates}
