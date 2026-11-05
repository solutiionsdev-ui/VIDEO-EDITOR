/* Aurora style — component code, the living glow and the bloom at a change of layout.
 * Loaded by renderer/render.js through STUDIIO.style(). Everything is a pure function of time:
 * no clocks, no CSS transitions, so a still is exactly the frame the video will have. */
(() => {
  const { clamp, prog, esc, norm, easeOutCubic } = STUDIIO.util;
  const SPARK = `<svg viewBox="0 0 24 24"><path d="M12 2.5v6M12 15.5v6M2.5 12h6M15.5 12h6M5.3 5.3l4.2 4.2M14.5 14.5l4.2 4.2M18.7 5.3l-4.2 4.2M9.5 14.5l-4.2 4.2"/></svg>`;
  const el = (id) => document.getElementById(id);

  // a number like "$800,000" or "40%" split into prefix, digits, suffix, so it can count up
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
    statement: (p) => {
      const words = String(p.text || "").split(/\s+/).filter(Boolean);
      const accent = new Set(String(p.accent || "").split(/\s+/).map(norm).filter(Boolean));
      return {
        html: `<div class="statement">${words.map((w) => `<span class="sw${accent.has(norm(w)) ? " acc" : ""}">${esc(w)}</span>`).join(" ")}</div>`,
        animate: (root) => (t) => root.querySelectorAll(".sw").forEach((s, i) => {
          const q = prog(t, 0.06 + i * 0.17, 0.52);
          s.style.opacity = q;
          s.style.filter = q < 1 ? `blur(${((1 - q) * 14).toFixed(2)}px)` : "none";
          s.style.transform = `translateY(${((1 - q) * 16).toFixed(2)}px)`;
        }),
      };
    },

    "glass-stat": (p) => {
      const n = parseNumber(p.value);
      return {
        html: `<div class="gstat"><div class="gpill"><span class="gnum">${esc(p.value ?? "")}</span></div><div class="glabel">${esc(p.label || "")}</div></div>`,
        animate: (root) => {
          const pill = root.querySelector(".gpill"), num = root.querySelector(".gnum"), label = root.querySelector(".glabel");
          return (t) => {
            const q = prog(t, 0.12, 1.15);
            const full = clamp(300 + String(p.value ?? "").length * 66, 440, 860);   // a short number gets a short pill
            pill.style.width = Math.round(full * (0.46 + 0.54 * q)) + "px";
            if (n) num.textContent = showNumber(n, n.target * q);
            const ql = prog(t, 0.75, 0.4);
            label.style.opacity = ql; label.style.transform = `translateY(${((1 - ql) * 12).toFixed(2)}px)`;
          };
        },
      };
    },

    "glass-progress": (p) => {
      const from = Number(p.from ?? 0), to = Number(p.to ?? 100), unit = p.unit ?? "%";
      return {
        html: `<div class="gprog"><div class="gprog-label">${esc(p.label || "")}</div><div class="gpill"><div class="gfill"></div><span class="gnum">${from}${esc(unit)}</span></div></div>`,
        animate: (root) => {
          const fill = root.querySelector(".gfill"), num = root.querySelector(".gnum");
          return (t) => {
            const v = from + (to - from) * prog(t, 0.2, 1.15);
            fill.style.width = clamp(v, 0, 100) + "%";
            num.textContent = Math.round(v) + unit;
          };
        },
      };
    },

    "glass-list": (p, spec) => {
      const items = (p.items || []).slice(0, 5);
      return {
        html: `<div class="glist">${p.title ? `<div class="glist-title">${esc(p.title)}</div>` : ""}${items.map((it) =>
          `<div class="gitem"><span class="gmk"></span><span>${esc(it)}</span></div>`).join("")}</div>`,
        animate: (root) => {
          const rows = root.querySelectorAll(".gitem");
          const stagger = clamp((spec.beat.duration - 1.0) / Math.max(1, rows.length), 0.18, 0.5);
          return (t) => rows.forEach((r, i) => {
            const q = prog(t, 0.25 + i * stagger, 0.4);
            r.style.opacity = q;
            r.style.filter = q < 1 ? `blur(${((1 - q) * 10).toFixed(2)}px)` : "none";
            r.style.transform = `translateY(${((1 - q) * 12).toFixed(2)}px)`;
          });
        },
      };
    },

    "glass-chip": (p) => ({
      html: `<div class="gchip"><span class="gtile">${p.src ? `<img src="${esc(p.src)}" alt="">` : SPARK}</span><span class="gchip-label">${esc(p.label || "")}</span></div>`,
      animate: (root) => {
        const tile = root.querySelector(".gtile"), label = root.querySelector(".gchip-label");
        return (t) => {
          const q = prog(t, 0.05, 0.45);
          tile.style.transform = `scale(${(0.8 + 0.2 * q).toFixed(3)}) rotate(${((1 - q) * -10).toFixed(2)}deg)`;
          const ql = prog(t, 0.22, 0.45);
          label.style.opacity = ql;
          label.style.filter = ql < 1 ? `blur(${((1 - ql) * 12).toFixed(2)}px)` : "none";
          label.style.transform = `translateX(${((1 - ql) * 22).toFixed(2)}px)`;
        };
      },
    }),
  };

  STUDIIO.style("aurora", {
    components,

    // the glow lives behind the stage and the card; the bloom lies over everything
    setup(ctx) {
      if (ctx.layoutKind !== "full") {
        el("bg").innerHTML = `<div class="glow g4"></div><div class="glow g1"></div><div class="glow g2"></div><div class="glow g3"></div><div class="stage-shade"></div>`;
      }
      const isEnd = ctx.spec.beat.layout === "logo-end-card";
      if (ctx.flareIn || ctx.flareOut || isEnd) {
        const fx = el("fx");
        fx.style.display = "block";
        fx.innerHTML = `<div class="flare"></div><div class="flare-fill"></div>`;
      }
    },

    frame(ctx) {
      const { W, H } = ctx.geo, T = ctx.T, s = Math.sin, c = Math.cos;
      // each light is a wide ellipse (kx wider than ky), so together they read as one band of light
      const place = (sel, size, x, y, kx, ky) => {
        const g = document.querySelector(sel);
        if (g) g.style.transform = `translate(${(x - size / 2).toFixed(1)}px, ${(y - size / 2).toFixed(1)}px) scale(${kx.toFixed(3)}, ${ky.toFixed(3)})`;
      };
      const breathe = 1 + 0.06 * s(T * 0.5);
      place(".glow.g1", 1700, W * 0.5 + s(T * 0.33) * W * 0.16, H * 0.80 + c(T * 0.27) * H * 0.04, 1.30 * breathe, 0.86 * breathe);
      place(".glow.g2", 1150, W * 0.5 + s(T * 0.52 + 1.3) * W * 0.30, H * 0.83 + c(T * 0.41) * H * 0.035, 1.55 + 0.12 * s(T * 0.7 + 0.6), 0.72);
      place(".glow.g3", 560, W * 0.5 + s(T * 0.29 + 2.1) * W * 0.22, H * 0.90 + c(T * 0.37 + 0.7) * H * 0.025, 2.5 + 0.3 * s(T * 0.9 + 1.1), 0.85);
      place(".glow.g4", 1300, W * 0.82 + s(T * 0.21) * W * 0.10, H * 0.08 + c(T * 0.19) * H * 0.05, 1, 1);

      // the bloom: 1 at the cut, receding into the beat and growing out of it
      const flare = document.querySelector("#fx .flare"), fill = document.querySelector("#fx .flare-fill");
      if (flare) {
        let p = 0;
        if (ctx.flareIn) p = Math.max(p, 1 - easeOutCubic(clamp(ctx.t / 0.27, 0, 1)));
        if (ctx.flareOut) { const q = clamp((ctx.t - (ctx.duration - 0.2)) / 0.2, 0, 1); p = Math.max(p, q * q); }
        flare.style.opacity = clamp(p * 1.7, 0, 1);
        flare.style.transform = `scale(${(0.35 + p * 3.4).toFixed(3)})`;
        fill.style.opacity = clamp((p - 0.62) / 0.38, 0, 1);
      }
    },

    // glass arrives out of a blur instead of sliding up; the client's own visual keeps a slight tilt in space
    widget(w, m) {
      const e = m.enter;
      w.style.opacity = m.opacity;
      w.style.filter = e < 1 ? `blur(${((1 - e) * 14).toFixed(2)}px)` : "none";
      const tilt = w.classList.contains("w-asset") ? `perspective(1700px) rotateY(${(-9 + 4 * e).toFixed(2)}deg) rotateX(${(5 - 3 * e).toFixed(2)}deg) ` : "";
      w.style.transform = `${tilt}scale(${(m.fit * (0.97 + 0.03 * e)).toFixed(4)})`;
    },
  });
})();
