/* Explainer style — component code, the two stage lights, the wash at a change of layout and the
 * pulse behind the logo. Loaded by renderer/render.js through STUDIIO.style(). Everything is a pure
 * function of time: no clocks, no CSS transitions, so a still is exactly the frame the video has. */
(() => {
  const { clamp, prog, esc, easeOutCubic } = STUDIIO.util;
  const el = (id) => document.getElementById(id);
  const easeInOut = (p) => (p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2);
  const ARROW = `<svg viewBox="0 0 24 24"><path d="M12 5v14M6 13l6 6 6-6"/></svg>`;
  const BELL = `<svg viewBox="0 0 24 24"><path d="M6 9a6 6 0 1 1 12 0c0 6 2.5 7 2.5 7h-17S6 15 6 9zM10 20a2 2 0 0 0 4 0"/></svg>`;
  const CURSOR = `<svg class="xcursor" viewBox="0 0 24 24"><path d="M5 3l14 8-6.2 1.6L10.6 19z" fill="#111" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
  const FOCUS = { "top-left": [22, 22], top: [50, 22], "top-right": [78, 22], left: [22, 50], center: [50, 50], right: [78, 50],
                  "bottom-left": [22, 78], bottom: [50, 78], "bottom-right": [78, 78] };

  const parseNumber = (value) => {
    const m = String(value ?? "").match(/^([^\d]*)([\d][\d,]*(?:\.\d+)?)([^\d]*)$/);
    if (!m) return null;
    const raw = m[2];
    return { prefix: m[1], suffix: m[3], target: parseFloat(raw.replace(/,/g, "")), decimals: (raw.split(".")[1] || "").length, grouped: raw.includes(",") };
  };
  const showNumber = (n, v) => {
    let s = v.toFixed(n.decimals);
    if (n.grouped) s = Number(s).toLocaleString("en-US", { minimumFractionDigits: n.decimals, maximumFractionDigits: n.decimals });
    return n.prefix + s + n.suffix;
  };

  const components = {
    // one of the client's screens: a slow push towards the focus while a cursor glides there and clicks
    screen: (p, spec) => {
      const [fx, fy] = FOCUS[String(p.focus || "center")] || FOCUS.center;
      return {
        html: `<div class="xscreen"><img class="xshot" src="${esc(p.src || "")}" alt="" style="transform-origin:${fx}% ${fy}%">${CURSOR}<span class="xclick"></span></div>`,
        animate: (root) => {
          const panel = root.querySelector(".xscreen"), shot = root.querySelector(".xshot");
          const cursor = root.querySelector(".xcursor"), click = root.querySelector(".xclick");
          const d = spec.beat.duration;
          return (t) => {
            const w = panel.offsetWidth, h = panel.offsetHeight;
            const push = easeInOut(clamp((t - 0.3) / Math.max(1.4, d - 0.9), 0, 1));
            shot.style.transform = `scale(${(1 + 0.26 * push).toFixed(4)})`;
            const go = prog(t, 0.45, 0.85);
            const x = w * (0.90 + (fx / 100 - 0.90) * go), y = h * (0.94 + (fy / 100 - 0.94) * go);
            cursor.style.opacity = clamp((t - 0.35) / 0.2, 0, 1);
            cursor.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
            const c = clamp((t - 1.38) / 0.5, 0, 1);
            click.style.opacity = c > 0 && c < 1 ? (1 - c) * 0.9 : 0;
            click.style.transform = `translate(${(w * fx / 100).toFixed(1)}px, ${(h * fy / 100).toFixed(1)}px) scale(${(0.3 + 0.8 * c).toFixed(3)})`;
          };
        },
      };
    },

    flow: (p, spec) => {
      const steps = (p.steps || []).slice(0, 4), rates = p.rates || [];
      const parts = [];
      steps.forEach((s, i) => {
        parts.push(`<div class="xstep xpart">${esc(s)}</div>`);
        if (i < steps.length - 1) parts.push(`<div class="xarrow xpart${rates[i] ? " rated" : ""}">${ARROW}${rates[i] ? `<span>${esc(rates[i])}</span>` : ""}</div>`);
      });
      return {
        html: `<div class="xflow">${parts.join("")}</div>`,
        animate: (root) => {
          const items = root.querySelectorAll(".xpart");
          const stagger = clamp((spec.beat.duration - 1.0) / Math.max(1, items.length), 0.12, 0.36);
          return (t) => items.forEach((it, i) => {
            const q = prog(t, 0.1 + i * stagger, 0.32);
            it.style.opacity = q;
            it.style.transform = `translateY(${((1 - q) * 22).toFixed(2)}px) scale(${(0.96 + 0.04 * q).toFixed(3)})`;
          });
        },
      };
    },

    note: (p) => ({
      html: `<div class="xnote"><div class="xnote-icon">${BELL}</div><div><div class="xnote-title">${esc(p.title || "")}</div><div class="xnote-body">${esc(p.body || "")}</div></div></div>`,
      animate: () => () => {},
    }),

    stat: (p) => {
      const n = parseNumber(p.value);
      return {
        html: `<div class="xstat"><div class="xstat-num">${esc(p.value ?? "")}</div>${p.label ? `<div class="xstat-label">${esc(p.label)}</div>` : ""}</div>`,
        animate: (root) => {
          const num = root.querySelector(".xstat-num"), label = root.querySelector(".xstat-label");
          return (t) => {
            if (n) num.textContent = showNumber(n, n.target * prog(t, 0.1, 1.0));
            if (label) { const q = prog(t, 0.6, 0.35); label.style.opacity = q; label.style.transform = `translateY(${((1 - q) * 10).toFixed(2)}px)`; }
          };
        },
      };
    },
  };

  STUDIIO.style("explainer", {
    components,

    setup(ctx) {
      const isEnd = ctx.spec.beat.layout === "logo-end-card";
      if (ctx.layoutKind !== "full" && !isEnd) el("bg").innerHTML = `<div class="xlight a"></div><div class="xlight b"></div>`;
      if (isEnd) el("endcard").insertAdjacentHTML("afterbegin", `<div class="xcore"></div><div class="xring"></div><div class="xring"></div><div class="xring"></div>`);
      if (ctx.flareIn || ctx.flareOut) {
        const fx = el("fx");
        fx.style.display = "block";
        fx.innerHTML = `<div class="xwash"></div>`;
      }
    },

    frame(ctx) {
      const { W, H } = ctx.geo, T = ctx.T, s = Math.sin, c = Math.cos;
      const dark = ctx.spec.beat.layout === "stage-dark";
      const place = (sel, size, x, y, k) => {
        const g = document.querySelector(sel);
        if (g) g.style.transform = `translate(${(x - size / 2).toFixed(1)}px, ${(y - size / 2).toFixed(1)}px) scale(${k.toFixed(3)})`;
      };
      if (dark) {      // one light low in the middle, breathing
        place(".xlight.a", 1500, W * 0.5 + s(T * 0.3) * W * 0.12, H * 0.98 + c(T * 0.26) * H * 0.03, 1 + 0.07 * s(T * 0.55));
        place(".xlight.b", 900, W * 0.22 + s(T * 0.24 + 1) * W * 0.08, H * 0.06 + c(T * 0.2) * H * 0.03, 1);
      } else {         // a light in the bottom right corner, a trace of it top left
        place(".xlight.a", 1500, W * 1.02 + s(T * 0.3) * W * 0.06, H * 1.0 + c(T * 0.26) * H * 0.03, 1 + 0.06 * s(T * 0.55));
        place(".xlight.b", 900, W * -0.02 + s(T * 0.24 + 1) * W * 0.05, H * 0.02 + c(T * 0.2) * H * 0.02, 1);
      }
      // the pulse behind the logo on the end card
      document.querySelectorAll("#endcard .xring").forEach((r, i) => {
        const ph = ((ctx.t * 0.55 + i / 3) % 1 + 1) % 1;
        r.style.transform = `scale(${(0.7 + ph * 3.0).toFixed(3)})`;
        r.style.opacity = ((1 - ph) * 0.55).toFixed(3);
      });
      // the wash: it covers the frame at the cut, clears into the beat and gathers out of it
      const wash = document.querySelector("#fx .xwash");
      if (wash) {
        let p = 0;
        if (ctx.flareIn) p = Math.max(p, 1 - easeOutCubic(clamp(ctx.t / 0.27, 0, 1)));
        if (ctx.flareOut) { const q = clamp((ctx.t - (ctx.duration - 0.2)) / 0.2, 0, 1); p = Math.max(p, q * q); }
        wash.style.opacity = clamp(p * 1.25, 0, 1);
        wash.style.transform = `scale(${(1 + (1 - p) * 0.06).toFixed(4)})`;
      }
    },

    // components arrive with a short rise, crisp, no blur
    widget(w, m) {
      w.style.opacity = m.opacity;
      w.style.transform = `translateY(${((1 - m.enter) * 26).toFixed(2)}px) scale(${(m.fit * (0.98 + 0.02 * m.enter)).toFixed(4)})`;
    },
  });
})();
