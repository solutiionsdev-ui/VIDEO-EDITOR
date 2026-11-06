/* Aurora style — component code, the living glow and the burst at a change of layout.
 * Loaded by renderer/render.js through STUDIIO.style(). Everything is a pure function of time:
 * no clocks, no CSS transitions, so a still is exactly the frame the video will have.
 *
 * The moves, as measured on the reference this style was studied from (nothing of it is reused):
 *   - the glow is the main actor: it rests as a low band, gathers into a small orb before a
 *     change, bursts open top and bottom until the frame is almost white, and settles again
 *   - glass arrives out of a blur, tipped back in space, and keeps hovering; light runs across it once
 *   - type is light and comes word by word out of a blur
 *   - a number fills its pill while it counts
 */
(() => {
  const { clamp, lerp, prog, esc, norm, easeOutCubic, easeOutExpo, easeInOutCubic, spring, parseNumber, showNumber } = STUDIIO.util;
  const cue = STUDIIO.cue, cueW = STUDIIO.cueW;
  const SPARK = `<svg viewBox="0 0 24 24"><path d="M12 2.5v6M12 15.5v6M2.5 12h6M15.5 12h6M5.3 5.3l4.2 4.2M14.5 14.5l4.2 4.2M18.7 5.3l-4.2 4.2M9.5 14.5l-4.2 4.2"/></svg>`;
  const el = (id) => document.getElementById(id);
  const OUT = 0.46, IN = 0.6;           // the burst: seconds before the cut (gather, then open), seconds after it (settle)

  // out of a blur: opacity, a little rise, the blur clearing
  const arrive = (node, q, rise = 14, blur = 12) => {
    node.style.opacity = q;
    node.style.filter = q < 1 ? `blur(${((1 - q) * blur).toFixed(2)}px)` : "none";
    node.style.transform = `translateY(${((1 - q) * rise).toFixed(2)}px)`;
  };

  const components = {
    statement: (p) => {
      const words = String(p.text || "").split(/\s+/).filter(Boolean);
      const accent = new Set(String(p.accent || "").split(/\s+/).map(norm).filter(Boolean));
      return {
        html: `<div class="statement">${words.map((w) => `<span class="sw${accent.has(norm(w)) ? " acc" : ""}">${esc(w)}</span>`).join(" ")}</div>`,
        animate: (root) => (t) => root.querySelectorAll(".sw").forEach((s, i) => {
          // each word comes forward out of the blur, a touch large, and settles
          const q = prog(t, 0.06 + i * 0.16, 0.6, easeOutExpo);
          s.style.opacity = clamp(q * 1.3, 0, 1);
          s.style.filter = q < 1 ? `blur(${((1 - q) * 18).toFixed(2)}px)` : "none";
          s.style.transform = `translateY(${((1 - q) * 20).toFixed(2)}px) scale(${(1.14 - 0.14 * q).toFixed(4)})`;
        }),
      };
    },

    "glass-stat": (p) => {
      const n = parseNumber(p.value);
      if (n) cueW(0.12, "count", 0.6);
      return {
        html: `<div class="gstat"><div class="gpill"><span class="gnum">${esc(p.value ?? "")}</span></div><div class="glabel">${esc(p.label || "")}</div></div>`,
        animate: (root) => {
          const pill = root.querySelector(".gpill"), num = root.querySelector(".gnum"), label = root.querySelector(".glabel");
          const k = (parseFloat(getComputedStyle(num).fontSize) || 112) / 112;      // on a stage the number is larger, and so is its pill
          return (t) => {
            const q = prog(t, 0.12, 1.1, easeOutExpo);
            const full = clamp((300 + String(p.value ?? "").length * 66) * k, 440 * k, 920);   // a short number gets a short pill
            pill.style.width = Math.round(full * (0.42 + 0.58 * spring(t - 0.1, 1.9, 0.7))) + "px";
            if (n) num.textContent = showNumber(n, n.target * q);
            arrive(label, prog(t, 0.7, 0.45), 12, 10);
          };
        },
      };
    },

    "glass-progress": (p) => {
      const from = Number(p.from ?? 0), to = Number(p.to ?? 100), unit = p.unit ?? "%";
      cueW(0.2, "count", 0.6);
      return {
        html: `<div class="gprog"><div class="gprog-label">${esc(p.label || "")}</div><div class="gpill"><div class="gfill"></div><span class="gnum">${from}${esc(unit)}</span></div></div>`,
        animate: (root) => {
          const fill = root.querySelector(".gfill"), num = root.querySelector(".gnum");
          return (t) => {
            const v = from + (to - from) * prog(t, 0.2, 1.25, easeInOutCubic);
            fill.style.width = clamp(v, 0, 100) + "%";
            num.textContent = Math.round(v) + unit;
          };
        },
      };
    },

    "glass-list": (p, spec) => {
      const items = (p.items || []).slice(0, 5);
      const stagger = clamp((spec.beat.duration - 1.0) / Math.max(1, items.length), 0.18, 0.5);
      items.forEach((_, i) => cueW(0.25 + i * stagger, "tick", 0.5));
      return {
        html: `<div class="glist">${p.title ? `<div class="glist-title">${esc(p.title)}</div>` : ""}${items.map((it) =>
          `<div class="gitem"><span class="gmk"></span><span>${esc(it)}</span></div>`).join("")}</div>`,
        animate: (root) => {
          const rows = root.querySelectorAll(".gitem");
          return (t) => rows.forEach((r, i) => {
            const a = t - (0.25 + i * stagger);
            arrive(r, prog(t, 0.25 + i * stagger, 0.45, easeOutExpo), 16, 10);
            const mk = r.querySelector(".gmk");                    // the dot lights up as its line lands
            mk.style.transform = `scale(${(0.3 + 0.7 * spring(a, 3.6, 0.5)).toFixed(3)})`;
          });
        },
      };
    },

    "glass-chip": (p) => ({
      html: `<div class="gchip"><span class="gtile">${p.src ? `<img src="${esc(p.src)}" alt="">` : SPARK}</span><span class="gchip-label">${esc(p.label || "")}</span></div>`,
      animate: (root) => {
        const tile = root.querySelector(".gtile"), label = root.querySelector(".gchip-label");
        return (t) => {
          const sp = spring(t - 0.05, 2.8, 0.55);
          tile.style.transform = `scale(${(0.6 + 0.4 * sp).toFixed(3)}) rotate(${((1 - sp) * -18).toFixed(2)}deg)`;
          const ql = prog(t, 0.22, 0.5, easeOutExpo);
          label.style.opacity = ql;
          label.style.filter = ql < 1 ? `blur(${((1 - ql) * 12).toFixed(2)}px)` : "none";
          label.style.transform = `translateX(${((1 - ql) * 30).toFixed(2)}px)`;
        };
      },
    }),
  };

  // how far the burst is open (0…1) and how far the glow has gathered into an orb (0…1) at this moment
  function burstAt(ctx) {
    const t = ctx.t, d = ctx.duration;
    let open = 0, orb = 0;
    if (ctx.flareIn) open = Math.max(open, 1 - easeOutCubic(clamp(t / IN, 0, 1)));
    if (ctx.flareOut && t > d - OUT) {
      const a = (t - (d - OUT)) / OUT;                             // 0…1 through the gathering and the opening
      orb = a < 0.55 ? easeInOutCubic(a / 0.55) : 1 - easeInOutCubic((a - 0.55) / 0.45);
      open = Math.max(open, a < 0.5 ? 0 : Math.pow((a - 0.5) / 0.5, 2.2));
    }
    return { open, orb };
  }

  STUDIIO.style("aurora", {
    components,

    // the glow lives behind the stage and the card; the burst lies over everything
    setup(ctx) {
      if (ctx.layoutKind !== "full") {
        el("bg").innerHTML = `<div class="glow g4"></div><div class="glow g1"></div><div class="glow g2"></div><div class="glow g3"></div><div class="stage-shade"></div>`;
      }
      const isEnd = ctx.spec.beat.layout === "logo-end-card";
      if (ctx.flareIn || ctx.flareOut || isEnd || ctx.isFirst) {
        const fx = el("fx");
        fx.style.display = "block";
        fx.innerHTML = `<div class="flare top"></div><div class="flare"></div><div class="flare-fill"></div>${ctx.isFirst ? `<div class="gfade"></div>` : ""}`;
      }
    },

    ready(ctx) {
      const b = ctx.spec.beat, d = ctx.duration;
      if (b.layout === "logo-end-card") { cue(0.1, "chime", 0.9); return; }
      if (ctx.isFirst) cue(0.05, "impact", 0.5);
      if (ctx.flareOut) cue(d, "swell", 0.85);                    // the rise into the burst
      if (ctx.hasWidget) cue(ctx.tIn + 0.05, "shimmer", 0.6);
    },

    frame(ctx) {
      const { W, H } = ctx.geo, T = ctx.T, t = ctx.t, s = Math.sin, c = Math.cos;
      const { open, orb } = burstAt(ctx);
      const pulse = ctx.hasWidget && t > ctx.tIn ? Math.exp(-(t - ctx.tIn) * 4.2) : 0;      // the glow answers when glass arrives
      // each light is a wide ellipse (kx wider than ky): together they read as one band of light.
      // Three poses: resting as a band, gathered into an orb, burst open top and bottom.
      const place = (sel, size, band, gathered, burst) => {
        const g = document.querySelector(sel);
        if (!g) return;
        const m = band.map((v, i) => lerp(lerp(v, gathered[i], orb), burst[i], open));
        g.style.transform = `translate(${(m[0] * W - size / 2).toFixed(1)}px, ${(m[1] * H - size / 2).toFixed(1)}px) scale(${m[2].toFixed(3)}, ${m[3].toFixed(3)})`;
        g.style.opacity = clamp(m[4], 0, 1).toFixed(3);
      };
      const breathe = 1 + 0.06 * s(T * 0.5) + 0.1 * pulse;
      //                 x                                y                                 kx                                  ky              opacity
      place(".glow.g1", 1700, [0.5 + s(T * 0.33) * 0.16, 0.80 + c(T * 0.27) * 0.04, 1.30 * breathe, 0.86 * breathe, 1],
            [0.5, 0.66, 0.30, 0.30, 0.9], [0.5, 1.02, 1.55, 1.25, 1]);
      place(".glow.g2", 1150, [0.5 + s(T * 0.52 + 1.3) * 0.30, 0.83 + c(T * 0.41) * 0.035, 1.55 + 0.12 * s(T * 0.7 + 0.6), 0.72, 0.82],
            [0.5, 0.66, 0.26, 0.26, 0.9], [0.5, 0.5, 0.62, 2.3, 0.95]);
      place(".glow.g3", 560, [0.5 + s(T * 0.29 + 2.1) * 0.22, 0.90 + c(T * 0.37 + 0.7) * 0.025, 2.5 + 0.3 * s(T * 0.9 + 1.1), 0.85, 0.7 + 0.3 * pulse],
            [0.5, 0.66, 0.34, 0.34, 1], [0.5, 0.5, 2.6, 4.2, 1]);
      place(".glow.g4", 1300, [0.82 + s(T * 0.21) * 0.10, 0.08 + c(T * 0.19) * 0.05, 1, 1, 0.55],
            [0.5, 0.66, 0.2, 0.2, 0.2], [0.5, -0.04, 1.5, 1.15, 0.9]);

      // the burst over everything: two lobes, top and bottom, and the frame goes almost white at the cut
      const low = document.querySelector("#fx .flare:not(.top)"), high = document.querySelector("#fx .flare.top"), fill = document.querySelector("#fx .flare-fill");
      if (low) {
        low.style.opacity = clamp(open * 1.7, 0, 1);
        low.style.transform = `scale(${(0.35 + open * 3.2).toFixed(3)}, ${(0.3 + open * 2.6).toFixed(3)})`;
        high.style.opacity = clamp(open * 1.5, 0, 1);
        high.style.transform = `scale(${(0.3 + open * 3.0).toFixed(3)}, ${(0.25 + open * 2.4).toFixed(3)})`;
        fill.style.opacity = clamp((open - 0.6) / 0.4, 0, 1);
      }
      const fade = document.querySelector("#fx .gfade");
      if (fade) fade.style.opacity = (1 - clamp(t / 0.45, 0, 1)).toFixed(3);          // the reel opens out of black
    },

    // glass arrives out of a blur, tipped back in space, and keeps hovering; type just comes forward
    widget(w, m) {
      const text = w.classList.contains("w-statement"), asset = w.classList.contains("w-asset");
      const e = spring(m.t, 2.2, 0.72), q = clamp(m.t / 0.45, 0, 1);
      w.style.opacity = m.opacity;
      w.style.filter = q < 1 ? `blur(${((1 - q) * 16).toFixed(2)}px)` : "none";
      w.style.setProperty("--sheen", prog(m.t, 0.3, 1.0, easeInOutCubic).toFixed(4));       // light runs across the glass once
      if (text) {
        w.style.transform = `scale(${(m.fit * (1 - 0.03 * m.out)).toFixed(4)})`;
        return;
      }
      const hover = Math.min(1, m.t / 0.8);
      const rx = (1 - e) * 26 + (3 + Math.cos(m.T * 0.6) * 1.6) * hover;
      const ry = (1 - e) * -12 + (Math.sin(m.T * 0.75) * 2.4 + (asset ? -5 : 0)) * hover;
      const lift = (1 - e) * 80 + Math.sin(m.T * 0.9) * 6 * hover + m.out * 20;
      w.style.transform = `perspective(1500px) translateY(${lift.toFixed(2)}px) rotateX(${rx.toFixed(2)}deg) rotateY(${ry.toFixed(2)}deg) ` +
        `scale(${(m.fit * (0.86 + 0.14 * e) * (1 - 0.04 * m.out)).toFixed(4)})`;
    },

    // a word comes up out of a soft blur as it is said
    caption(ln, c, t) {
      c.els.forEach((sp, j) => {
        const a = t - c.words[j].start;
        if (a < 0) { sp.style.transform = "translateY(7px)"; sp.style.filter = "blur(1px)"; return; }
        const q = easeOutCubic(clamp(a / 0.24, 0, 1));
        sp.style.transform = `translateY(${((1 - q) * 7).toFixed(2)}px) scale(${(1 + 0.05 * (1 - q)).toFixed(4)})`;
        sp.style.filter = q < 1 ? `blur(${((1 - q) * 1).toFixed(2)}px)` : "none";
      });
    },

    // the logo comes forward out of the light
    endcard(ctx) {
      const t = ctx.t, logo = document.querySelector(".endcard-logo"), cta = document.querySelector(".endcard-cta"), url = document.querySelector(".endcard-url");
      if (logo) {
        const q = prog(t, 0.18, 0.7, easeOutExpo);
        logo.style.opacity = clamp(q * 1.4, 0, 1);
        logo.style.filter = q < 1 ? `blur(${((1 - q) * 16).toFixed(2)}px)` : "none";
        logo.style.transform = `scale(${(1.18 - 0.18 * q).toFixed(4)})`;
      }
      if (cta) arrive(cta, prog(t, 0.6, 0.45), 12, 10);
      if (url) url.style.opacity = prog(t, 0.9, 0.4);
    },
  });
})();
