/* iOS style — how things move and sound. The components themselves are the renderer's built-in
 * ones (renderer/render.js); this file gives the style its motion: a card drops in like a
 * notification and settles with a bounce, the word being said pops as a chip, the footage card
 * springs into place when the layout changes. Everything is a pure function of time. */
(() => {
  const { clamp, prog, spring, easeOutCubic } = STUDIIO.util;
  const cue = STUDIIO.cue;

  STUDIIO.style("ios", {
    ready(ctx) {
      const b = ctx.spec.beat;
      if (b.layout === "logo-end-card") { cue(0.1, "chime", 0.9); return; }
      if (ctx.isFirst) cue(0.05, "impact", 0.5);
      if (ctx.hasWidget) cue(ctx.tIn + 0.03, "pop", 0.7);
      if (ctx.prev && ctx.prev !== b.layout) cue(0.04, "whoosh", 0.45);        // the layout changed: a little air
    },

    // a card drops in from the side of the frame it lives on, bounces once and floats
    widget(w, m) {
      const sp = spring(m.t, 3.1, 0.58), from = m.above ? -1 : 1;
      const float = Math.sin(m.T * 1.2) * 3 * Math.min(1, m.t);
      w.style.opacity = m.opacity;
      w.style.transform = `translateY(${((1 - sp) * 90 * from + float + m.out * -14 * from).toFixed(2)}px) scale(${(m.fit * (0.9 + 0.1 * sp) * (1 - 0.04 * m.out)).toFixed(4)})`;
    },

    // the word being said pops as a chip and settles back when the next one takes over
    caption(ln, c, t, active) {
      c.els.forEach((sp, j) => {
        const a = t - c.words[j].start;
        if (a < 0) { sp.style.transform = "none"; return; }
        const pop = spring(a, 4.2, 0.5);
        const hold = j === active ? 1 : 1 - easeOutCubic(clamp((t - c.words[Math.min(j + 1, c.words.length - 1)].start) / 0.16, 0, 1));
        sp.style.transform = `scale(${(1 + (0.02 + 0.06 * pop) * hold).toFixed(4)})`;
      });
    },

    frame(ctx) {
      // when the layout changes to a card, the card springs into place instead of just being there
      const b = ctx.spec.beat, fw = document.getElementById("footage-wrap");
      if (ctx.layoutKind === "card" && ctx.prev && ctx.prev !== b.layout) {
        const sp = spring(ctx.t, 2.8, 0.66);
        fw.style.transform = `translateY(${((1 - sp) * 60).toFixed(2)}px) scale(${(0.92 + 0.08 * sp).toFixed(4)})`;
        fw.style.opacity = clamp(ctx.t / 0.12, 0, 1);
      } else if (ctx.layoutKind === "card") {
        fw.style.transform = "none"; fw.style.opacity = 1;
      }
    },

    endcard(ctx) {
      const t = ctx.t, logo = document.querySelector(".endcard-logo"), cta = document.querySelector(".endcard-cta");
      if (logo) { logo.style.opacity = clamp((t - 0.08) / 0.15, 0, 1); logo.style.transform = `scale(${(0.7 + 0.3 * spring(t - 0.08, 2.8, 0.55)).toFixed(4)})`; }
      if (cta) { const q = prog(t, 0.5, 0.35); cta.style.opacity = q; cta.style.transform = `translateY(${((1 - q) * 14).toFixed(2)}px)`; }
    },
  });
})();
