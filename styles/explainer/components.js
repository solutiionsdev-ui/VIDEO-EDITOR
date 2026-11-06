/* Explainer style — the moves. Loaded by renderer/render.js through STUDIIO.style(). Everything is
 * a pure function of time: no clocks, no CSS transitions, so a still is exactly the frame the video has.
 *
 * The grammar, as measured frame by frame on the reference this style follows (none of its content
 * is reused):
 *   type      a line starts large and snaps back when the next word needs the room; a new word is
 *             born in the colour (grey on white) and settles to white (black); a payoff line is
 *             typed letter by letter behind a thick cursor
 *   chips     a short highlight is a solid chip that pops in; a number in a chip counts up; a longer
 *             highlighted phrase is simply set in the colour
 *   panels    white, hairline, long shadow; they rise out of a blur and the view keeps pushing in;
 *             the row being talked about lifts out with a glow in the colour
 *   chain     steps as black pills joined by coloured tiles; the camera travels along it
 *   scenes    a scene ends when its last chip grows until it is the whole frame, and the next
 *             opens out of that colour; black and white scenes alternate
 *   the end   a dot inside expanding rings shrinks into the mark, and the name is typed next to it
 */
(() => {
  const { clamp, lerp, prog, esc, easeOutCubic, easeOutExpo, easeInCubic, easeInOutCubic, spring, parseNumber, showNumber } = STUDIIO.util;
  const cue = STUDIIO.cue, cueW = STUDIIO.cueW;
  const el = (id) => document.getElementById(id);
  const easeInOut = (p) => (p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2);
  const ARROW = `<svg viewBox="0 0 24 24"><path d="M4 12h15M13 6l6 6-6 6"/></svg>`;
  const BELL = `<svg viewBox="0 0 24 24"><path d="M6 9a6 6 0 1 1 12 0c0 6 2.5 7 2.5 7h-17S6 15 6 9zM10 20a2 2 0 0 0 4 0"/></svg>`;
  const CURSOR = `<svg class="xcursor" viewBox="0 0 24 24"><path d="M5 3l14 8-6.2 1.6L10.6 19z" fill="#0a0a0a" stroke="#fff" stroke-width="1.2" stroke-linejoin="round"/></svg>`;
  const DOTS = `<div class="xpanel-bar"><i></i><i></i><i></i></div>`;
  const FOCUS = { "top-left": [22, 22], top: [50, 22], "top-right": [78, 22], left: [22, 50], center: [50, 50], right: [78, 50],
                  "bottom-left": [22, 78], bottom: [50, 78], "bottom-right": [78, 78] };
  const OUT = 0.3, IN = 0.42;           // the shape between scenes: seconds before the cut, seconds after it

  // ---- the colour: the client's own, lifted when it would be too dark to carry a black stage
  const lin = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  const unlin = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055);
  function toOklch(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim());
    const n = m ? parseInt(m[1], 16) : 0x17d651;
    const r = lin(((n >> 16) & 255) / 255), g = lin(((n >> 8) & 255) / 255), b = lin((n & 255) / 255);
    const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b), mm = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b),
          s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    const L = 0.2104542553 * l + 0.793617785 * mm - 0.0040720468 * s, A = 1.9779984951 * l - 2.428592205 * mm + 0.4505937099 * s, B = 0.0259040371 * l + 0.7827717662 * mm - 0.808675766 * s;
    return [L, Math.hypot(A, B), Math.atan2(B, A)];
  }
  function toRgb(L, C, h) {
    for (let k = 0; k < 24; k++) {                       // pull the colour in until the screen can show it
      const A = C * Math.cos(h), B = C * Math.sin(h);
      const l = Math.pow(L + 0.3963377774 * A + 0.2158037573 * B, 3), m = Math.pow(L - 0.1055613458 * A - 0.0638541728 * B, 3), s = Math.pow(L - 0.0894841775 * A - 1.291485548 * B, 3);
      const rgb = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s];
      if (rgb.every((v) => v >= -0.001 && v <= 1.001)) return rgb.map((v) => Math.round(clamp(unlin(clamp(v, 0, 1)), 0, 1) * 255));
      C *= 0.94;
    }
    return [255, 255, 255];
  }
  const themes = {};
  function theme(spec) {
    const layout = spec.beat.layout, L0 = (spec.style.layouts || {})[layout] || {};
    const dark = layout === "logo-end-card" || L0.caption_theme !== "light";
    const key = spec.brand.primary + (dark ? "d" : "l");
    if (!themes[key]) {
      const [L, C, h] = toOklch(spec.brand.primary);
      // on black a dark brand colour is lifted only as far as it needs to read; a bright one is left alone
      const lifted = [clamp(L, 0.56, 0.82), Math.max(C, 0.17)];
      const chip = dark ? toRgb(lifted[0], lifted[1], h) : toRgb(Math.min(L, 0.78), C, h);
      const born = dark ? toRgb(Math.max(lifted[0], 0.68), lifted[1], h) : [172, 172, 178];        // type born in the colour has to read on black
      themes[key] = { dark, chip, css: `rgb(${chip.join(",")})`, ink: dark ? [255, 255, 255] : [11, 11, 12], born,
                      onChip: (dark ? lifted[0] : Math.min(L, 0.78)) > 0.62 ? "#0a0a0a" : "#ffffff" };            // what an icon on the colour is drawn in
    }
    return themes[key];
  }
  const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a.toFixed(3)})`;
  const mix = (a, b, p) => `rgb(${Math.round(lerp(a[0], b[0], p))},${Math.round(lerp(a[1], b[1], p))},${Math.round(lerp(a[2], b[2], p))})`;

  // ---- small shared moves
  const digits = (v) => String(v ?? "").replace(/[^\d.,]/g, "").replace(/,/g, "").replace(/\.+$/, "");
  // when a figure is said in this beat (seconds from the beat's start); null when it is not said here
  const saidAt = (spec, value) => {
    const want = digits(value);
    if (!want) return null;
    const hit = (spec.beat.words || []).find((w) => digits(w.w) === want);
    return hit ? Math.max(0.1, hit.start - 0.14) : null;
  };
  // When a component arrives and when each of its figures shows, counted from that arrival. With
  // late, the component waits and arrives just before its first figure is said, so it never sits empty.
  const schedule = (spec, values, { first = 0.25, gap = 0.3, late = false } = {}) => {
    const said = values.map((v) => saidAt(spec, v));
    const base = spec.beat.is_first ? spec.style.motion_first_beat_delay : 0.1;
    const lead = Math.min(Infinity, ...said.filter((x) => x != null));
    const arrive = late && isFinite(lead) ? Math.max(base, lead - 0.6) : base;
    const at = [];
    said.forEach((x, i) => at.push(Math.max(x != null ? x - arrive : (i ? at[i - 1] + gap : first), i ? at[i - 1] + 0.12 : 0.12)));
    return { arrive, at };
  };
  const land = (node, a, rise = 30, from = 0.9, f = 3.2, z = 0.66) => {       // quick fade, a rise, a small bounce
    const sp = spring(a, f, z);
    node.style.opacity = clamp(a / 0.09, 0, 1);
    node.style.transform = `translateY(${((1 - sp) * rise).toFixed(2)}px) scale(${(from + (1 - from) * sp).toFixed(4)})`;
  };
  const sweep = (node, q, radius) => {                                       // revealed from the left
    node.style.opacity = q > 0 ? 1 : 0;
    node.style.clipPath = q >= 1 ? "none" : `inset(0 ${((1 - q) * 100).toFixed(2)}% 0 0 round ${radius}px)`;
  };
  const head = (p) => (p.title ? `<div class="xpanel-title">${esc(p.title)}</div>${p.sub ? `<div class="xpanel-sub">${esc(p.sub)}</div>` : ""}` : "");

  const components = {};

  // one of the client's screens, in a window: a slow push towards the focus while a cursor glides there and clicks
  components.screen = (p, spec) => {
    const [fx, fy] = FOCUS[String(p.focus || "center")] || FOCUS.center;
    cueW(1.38, "click", 0.9);
    return {
      html: `<div class="xpanel">${DOTS}<div class="xscreen"><img class="xshot" src="${esc(p.src || "")}" alt="" style="transform-origin:${fx}% ${fy}%">${CURSOR}<span class="xclick"></span></div></div>`,
      animate: (root) => {
        const panel = root.querySelector(".xscreen"), shot = root.querySelector(".xshot");
        const cursor = root.querySelector(".xcursor"), click = root.querySelector(".xclick");
        const d = spec.beat.duration;
        return (t) => {
          const w = panel.offsetWidth, h = panel.offsetHeight;
          const push = easeInOut(clamp((t - 0.3) / Math.max(1.4, d - 0.9), 0, 1));
          shot.style.transform = `scale(${(1 + 0.3 * push).toFixed(4)})`;
          const go = prog(t, 0.45, 0.85, easeOutExpo);
          const x = w * (0.90 + (fx / 100 - 0.90) * go), y = h * (0.94 + (fy / 100 - 0.94) * go);
          cursor.style.opacity = clamp((t - 0.35) / 0.2, 0, 1);
          cursor.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) scale(${t >= 1.36 && t < 1.5 ? 0.86 : 1})`;
          const c = clamp((t - 1.38) / 0.5, 0, 1);
          click.style.opacity = c > 0 && c < 1 ? (1 - c) * 0.9 : 0;
          click.style.transform = `translate(${(w * fx / 100).toFixed(1)}px, ${(h * fy / 100).toFixed(1)}px) scale(${(0.3 + 0.8 * easeOutCubic(c)).toFixed(3)})`;
        };
      },
    };
  };

  // numbers in a dark band: light, large, counting up, each when it is said
  const band = (items, spec, extra = "") => {
    const list = items.slice(0, 4).map((it) => ({ value: String(it.value ?? ""), label: String(it.label ?? ""), n: parseNumber(it.value) }));
    const { arrive, at } = schedule(spec, list.map((it) => it.value), { late: true });
    at.forEach((a) => cueW(a, "count", 0.55));
    return {
      arrive,
      html: `<div class="xband n${list.length}${extra}">${list.map((it) => `<div class="xband-item"><div class="xband-num">${esc(it.value)}</div>` +
        `${it.label ? `<div class="xband-label">${esc(it.label)}</div>` : ""}</div>`).join("")}</div>`,
      run: (root) => {
        const cells = [...root.querySelectorAll(".xband-item")], bandEl = root.querySelector(".xband");
        cells.forEach((cell) => {                                   // a long figure still fits its cell
          const num = cell.firstElementChild, room = cell.clientWidth - 90, size = parseFloat(getComputedStyle(num).fontSize);
          if (num.scrollWidth > room) num.style.fontSize = Math.floor(size * room / num.scrollWidth) + "px";
        });
        const done = Math.max(...at) + 0.9;
        return (t) => {
          cells.forEach((cell, i) => {
            const a = t - at[i], num = cell.firstElementChild, it = list[i];
            cell.style.opacity = clamp(a / 0.12, 0, 1);
            cell.style.transform = `translateY(${((1 - spring(a, 3.2, 0.7)) * 26).toFixed(2)}px)`;
            if (it.n) num.textContent = showNumber(it.n, it.n.target * prog(t, at[i], 0.85, easeOutExpo));
          });
          // once the numbers rest, the band lifts out of its panel a little, the way the eye is led to it
          bandEl.style.transform = `scale(${(1 + 0.035 * spring(t - done, 2.2, 0.7)).toFixed(4)})`;
        };
      },
    };
  };
  components.stats = (p, spec) => {
    const b = band(p.items || [], spec);
    const framed = !!p.title;
    return {
      arrive: b.arrive,
      html: framed ? `<div class="xpanel xstats">${DOTS}<div class="xpanel-body">${head(p)}${b.html}</div></div>` : `<div class="xstats" style="width:920px">${b.html}</div>`,
      animate: (root) => b.run(root),
    };
  };
  components.stat = (p, spec) => {
    const b = band([{ value: p.value, label: p.label }], spec);
    return { arrive: b.arrive, html: `<div style="width:${String(p.value ?? "").length > 5 ? 920 : 720}px">${b.html}</div>`, animate: (root) => {
      const num = root.querySelector(".xband-num"), box = root.querySelector(".xband");
      const fit = Math.min(1, (box.clientWidth - 90) / Math.max(1, num.scrollWidth));        // a long figure still fits its band
      if (fit < 1) num.style.fontSize = Math.floor(210 * fit) + "px";
      return b.run(root);
    } };
  };

  // a few things compared: rows in a panel, each with a thin line that fills when its figure is said; the row lifts out while it is talked about
  components.bars = (p, spec) => {
    const items = (p.items || []).slice(0, 5).map((it) => ({ label: String(it.label ?? ""), value: String(it.value ?? ""), n: parseNumber(it.value) }));
    const max = Math.max(1e-9, ...items.map((it) => (it.n ? it.n.target : 0)));
    const stagger = clamp((spec.beat.duration - 1.6) / Math.max(1, items.length), 0.16, 0.45);
    const { at } = schedule(spec, items.map((it) => it.value), { first: 0.3, gap: stagger });
    const th = theme(spec);
    at.forEach((a) => cueW(a, "tick", 0.6));
    return {
      html: `<div class="xpanel">${DOTS}<div class="xpanel-body">${head(p)}<div class="xrows">${items.map((it, i) =>
        `<div class="xrow"><span class="xrow-n">${i + 1}</span><span class="xrow-label">${esc(it.label)}</span><span class="xrow-value">${esc(it.value)}</span>` +
        `<div class="xrow-track"><div class="xrow-fill"></div></div></div>`).join("")}</div></div></div>`,
      animate: (root) => {
        const rows = [...root.querySelectorAll(".xrow")];
        return (t) => rows.forEach((r, i) => {
          const it = items[i], a = t - (0.1 + i * 0.08), v = r.querySelector(".xrow-value");
          const until = Math.min(at[i + 1] ?? 1e9, at[i] + 2.2);
          const focus = prog(t, at[i], 0.24) * (1 - prog(t, until, 0.3));          // lifted while this row is the one being said
          r.style.opacity = clamp(a / 0.1, 0, 1);
          r.style.transform = `translateY(${((1 - spring(a, 3.2, 0.7)) * 22).toFixed(2)}px) scale(${(1 + 0.045 * focus).toFixed(4)})`;
          r.style.zIndex = focus > 0.01 ? 2 : 1;
          r.style.boxShadow = focus > 0.01 ? `0 0 0 ${(2 * focus).toFixed(2)}px ${rgba(th.chip, 0.42 * focus)}, 0 18px ${(70 * focus).toFixed(1)}px ${rgba(th.chip, 0.4 * focus)}` : "none";
          r.querySelector(".xrow-fill").style.width = clamp((it.n ? it.n.target / max : 0) * 100 * prog(t, at[i], 0.9, easeOutExpo), 0, 100).toFixed(2) + "%";
          v.style.opacity = prog(t, at[i], 0.14);
          if (it.n) v.textContent = showNumber(it.n, it.n.target * prog(t, at[i], 0.7, easeOutExpo));
        });
      },
    };
  };

  // a chain of steps: black pills, a coloured tile with the rate between them; the camera travels along it as it is said
  components.flow = (p, spec) => {
    const steps = (p.steps || []).slice(0, 5).map((s) => {
      const text = typeof s === "string" ? s : `${s.value ?? ""} ${s.label ?? ""}`.trim();
      const m = text.match(/^(\S*\d\S*)\s+(.+)$/);
      return m ? { value: m[1], label: m[2], text } : { value: text, label: "", text };
    });
    const rates = p.rates || [];
    const stagger = clamp((spec.beat.duration - 1.2) / Math.max(1, steps.length), 0.3, 0.9);
    const { at } = schedule(spec, steps.map((s) => s.value), { first: 0.15, gap: stagger });
    for (let i = 1; i < at.length; i++) at[i] = Math.max(at[i], at[i - 1] + 0.22);        // never two at once
    at.forEach((a, i) => cueW(a, i ? "pop" : "tick", 0.55));
    const parts = [];
    steps.forEach((s, i) => {
      if (i) parts.push(`<div class="xlink${rates[i - 1] ? "" : " bare"}" data-k="${i}">${ARROW}${rates[i - 1] ? `<em>${esc(rates[i - 1])}</em>` : ""}</div>`);
      parts.push(`<div class="xpill" data-k="${i}">${esc(s.value)}${s.label ? `<span>${esc(s.label)}</span>` : ""}</div>`);
    });
    return {
      html: `<div class="xflow" data-free-x="1"><div class="xchain">${parts.join("")}</div></div>`,
      animate: (root) => {
        const chain = root.querySelector(".xchain"), items = [...chain.children];
        const right = steps.map((_, i) => { const pill = items.filter((n) => n.classList.contains("xpill"))[i]; return pill.offsetLeft + pill.offsetWidth; });
        const view = 940;                                                    // how far right the newest step may sit, in the chain's own pixels
        const camera = right.map((r) => Math.min(0, view - r));
        return (t) => {
          items.forEach((node) => {
            const k = Number(node.dataset.k), link = node.classList.contains("xlink"), a = t - (link ? at[k] - 0.1 : at[k]);
            const sp = spring(a, 3.4, 0.6);
            node.style.opacity = clamp(a / 0.08, 0, 1);
            node.style.transform = link ? `scale(${(0.3 + 0.7 * sp).toFixed(4)})` : `translateX(${((1 - sp) * 60).toFixed(2)}px) scale(${(0.92 + 0.08 * sp).toFixed(4)})`;
          });
          let x = camera[0];
          for (let i = 1; i < at.length; i++) x = lerp(x, camera[i], prog(t, at[i] - 0.14, 0.6, easeOutExpo));
          chain.style.transform = `translateX(${x.toFixed(2)}px)`;
        };
      },
    };
  };

  // a notification: glass that drops in while the colour rises behind it
  components.note = (p) => {
    cueW(0.1, "ding", 0.7);
    return {
      html: `<div class="xnote"><div class="xnote-icon">${BELL}</div><div class="xnote-text"><div class="xnote-title"><span>${esc(p.title || "")}</span><em>now</em></div>` +
        `<div class="xnote-body">${esc(p.body || "")}</div></div></div>`,
      animate: (root) => {
        const bell = root.querySelector(".xnote-icon svg"), body = root.querySelector(".xnote-body"), icon = root.querySelector(".xnote-icon");
        return (t) => {
          const a = t - 0.14;
          icon.style.transform = `scale(${(0.6 + 0.4 * spring(t - 0.05, 3.4, 0.55)).toFixed(4)})`;
          bell.style.transform = a > 0 ? `rotate(${(Math.sin(a * 26) * 16 * Math.exp(-a * 3.4)).toFixed(2)}deg)` : "none";
          const q = prog(t, 0.24, 0.3);
          body.style.opacity = q; body.style.transform = `translateY(${((1 - q) * 10).toFixed(2)}px)`;
        };
      },
    };
  };

  // one share of a whole: a thin ring that turns into place and fills
  components.donut = (p, spec) => {
    const n = parseNumber(p.value), share = clamp((n ? n.target : 0) / 100, 0, 1), R = 92, C = 2 * Math.PI * R;
    const { arrive, at: [start] } = schedule(spec, [p.value], { first: 0.2, late: true });
    cueW(start, "count", 0.6);
    return {
      arrive,
      html: `<div class="xdonut"><div class="xdonut-ring"><svg viewBox="0 0 200 200"><circle class="xdonut-bg" cx="100" cy="100" r="${R}"/>` +
        `<circle class="xdonut-fg" cx="100" cy="100" r="${R}" stroke-dasharray="${C.toFixed(2)}" stroke-dashoffset="${C.toFixed(2)}"/></svg>` +
        `<div class="xdonut-num">${esc(p.value ?? "")}</div></div>${p.label ? `<div class="xdonut-label">${esc(p.label)}</div>` : ""}</div>`,
      animate: (root) => {
        const fg = root.querySelector(".xdonut-fg"), num = root.querySelector(".xdonut-num"), label = root.querySelector(".xdonut-label"), ring = root.querySelector(".xdonut-ring svg");
        return (t) => {
          const g = prog(t, start, 1.05, easeOutExpo);
          fg.setAttribute("stroke-dashoffset", (C * (1 - share * g)).toFixed(2));
          ring.style.transform = `rotate(${(-90 - (1 - prog(t, 0, 1.0, easeOutExpo)) * 80).toFixed(2)}deg)`;
          num.style.opacity = prog(t, start, 0.14);
          if (n) num.textContent = showNumber(n, n.target * g);
          if (label) { const q = prog(t, start + 0.5, 0.35); label.style.opacity = q; label.style.transform = `translateY(${((1 - q) * 12).toFixed(2)}px)`; }
        };
      },
    };
  };

  // two numbers against each other: a white panel, a tile with an arrow, a dark band
  components.versus = (p, spec) => {
    const sides = (p.sides || []).slice(0, 2).map((sd) => {
      const value = String(sd.value ?? "").trim(), m = value.match(/^(\S*\d\S*)\s+(.+)$/);
      return { label: String(sd.label ?? ""), value, figure: m ? m[1] : value, unit: m ? m[2] : "", n: parseNumber(m ? m[1] : value) };
    });
    const { arrive, at } = schedule(spec, sides.map((sd) => sd.figure), { first: 0.2, gap: 0.6, late: true });
    at.forEach((a, i) => cueW(a, "thud", i ? 0.9 : 0.65));
    const side = (sd, i) => `<div class="xvs-side${i === 1 ? " b" : ""}"><div class="xvs-num">${esc(sd.figure)}</div>` +
      `${sd.unit ? `<div class="xvs-unit">${esc(sd.unit)}</div>` : ""}<div class="xvs-label">${esc(sd.label)}</div></div>`;
    return {
      arrive,
      html: `<div class="xvs">${side(sides[0] || {}, 0)}<div class="xvs-mid">${ARROW}</div>${side(sides[1] || {}, 1)}</div>`,
      animate: (root) => {
        const tiles = [...root.querySelectorAll(".xvs-side")], nums = tiles.map((tile) => tile.querySelector(".xvs-num")), mid = root.querySelector(".xvs-mid");
        const fit = Math.min(1, ...tiles.map((tile, i) => (tile.clientWidth - 70) / Math.max(1, nums[i].scrollWidth)));      // one size for both figures
        if (fit < 1) nums.forEach((num) => { num.style.fontSize = Math.floor(230 * fit) + "px"; });
        return (t) => {
          tiles.forEach((tile, i) => {
            const a = t - (0.06 + i * 0.12), unit = tile.querySelector(".xvs-unit"), sp = spring(a, 3.0, 0.7);
            tile.style.opacity = clamp(a / 0.1, 0, 1);
            tile.style.transform = `translateY(${((1 - sp) * 40).toFixed(2)}px)`;
            nums[i].style.opacity = prog(t, at[i], 0.12);
            nums[i].style.transform = `translateY(${((1 - spring(t - at[i], 3.2, 0.62)) * 24).toFixed(2)}px)`;
            if (unit) unit.style.opacity = prog(t, at[i] + 0.1, 0.2);
            if (sides[i].n) nums[i].textContent = showNumber(sides[i].n, sides[i].n.target * prog(t, at[i], 0.65, easeOutExpo));
          });
          const m = t - (at[1] - 0.2);                                       // the arrow pops just before the second figure
          mid.style.opacity = clamp(m / 0.08, 0, 1);
          mid.style.transform = `scale(${(0.3 + 0.7 * spring(m, 3.6, 0.55)).toFixed(4)})`;
        };
      },
    };
  };

  // a title that comes through the frame: huge, out of a blur, gone into the next thing
  components.title = (p, spec) => ({
    html: `<div class="xtitlebox" data-free-x="1"><span class="xtitle">${esc(p.text || "")}</span></div>`,
    animate: (root) => {
      const word = root.querySelector(".xtitle"), d = spec.beat.duration;
      const fit = Math.min(1, 980 / Math.max(1, word.scrollWidth));
      if (fit < 1) word.style.fontSize = Math.floor(330 * fit) + "px";
      return (t) => {
        const q = prog(t, 0, 0.5, easeOutExpo), out = prog(t, d - 0.5, 0.36, easeInCubic);
        word.style.opacity = clamp(q * 1.2, 0, 1) * (1 - out);
        word.style.filter = `blur(${((1 - q) * 44 + out * 40).toFixed(2)}px)`;
        word.style.transform = `scale(${(1.34 - 0.34 * q - 0.05 * clamp((t - 0.5) / Math.max(0.5, d - 1), 0, 1) - 0.12 * out).toFixed(4)})`;
      };
    },
  });

  // ------------------------------------------------------------------ captions
  // Reshape the caption lines the way this style sets type, then plan the camera and the word moves.
  function planCaptions(ctx) {
    const { geo, spec } = ctx, stage = ctx.layoutKind === "stage";
    const typed = spec.beat.caption.style === "emphasis";            // the payoff lines are typed
    const th = theme(spec);
    // a short highlight becomes one chip around its words; a longer one is set in the colour
    ctx.chunks.forEach((c) => {
      const hl = c.els.map((e) => e.classList.contains("hl"));
      c.runs = [];
      for (let j = 0; j < hl.length; j++) {
        if (!hl[j]) continue;
        let k = j;
        while (k + 1 < hl.length && hl[k + 1]) k++;
        const run = { from: j, to: k, chip: k - j < 2, el: null };
        if (run.chip) {
          const chip = document.createElement("span");
          chip.className = "xchip";
          c.els[j].parentNode.insertBefore(chip, c.els[j]);
          for (let i = j; i <= k; i++) { if (i > j) chip.appendChild(document.createTextNode(" ")); chip.appendChild(c.els[i]); }
          run.el = chip;
          const n = k === j ? parseNumber(c.words[j].w) : null;
          run.n = n && n.target >= 3 ? n : null;
          if (run.n) { c.line.style.display = "block"; chip.style.minWidth = Math.ceil(chip.getBoundingClientRect().width) + "px"; chip.style.textAlign = "center"; c.line.style.display = "none"; }
        } else {
          for (let i = j; i <= k; i++) c.els[i].classList.add("ink");
        }
        c.runs.push(run);
        j = k;
      }
    });
    STUDIIO.measureCaptions();
    const fitW = geo.W * 0.86, fitH = (geo.captions.bottom - geo.captions.top) * 0.96;
    const levels = !stage ? [1] : ctx.hasWidget ? [1.2, 1] : [1.55, 1.25, 1];
    ctx.chunks.forEach((c) => {
      const fs = parseFloat(getComputedStyle(c.line).fontSize) || 80;
      const cam = c.boxes.map((_, k) => {
        if (!stage) return { s: 1, cx: c.size.w / 2, cy: c.size.h / 2 };
        let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
        for (let j = 0; j <= k; j++) { const b = c.boxes[j]; x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y); x1 = Math.max(x1, b.x + b.w); y1 = Math.max(y1, b.y + b.h); }
        const sFit = Math.min(fitW / Math.max(1, x1 - x0 + fs * 0.5), fitH / Math.max(1, y1 - y0));
        return { s: levels.find((L) => L <= sFit) || Math.min(1, sFit), cx: (x0 + x1) / 2, cy: (y0 + y1) / 2 };
      });
      const inRun = c.words.map((_, j) => c.runs.find((r) => j >= r.from && j <= r.to) || null);
      let cursor = null;
      if (typed) { cursor = document.createElement("span"); cursor.className = "xcur"; c.line.appendChild(cursor); }
      c.plan = { stage, typed, fs, cam, inRun, cursor, th, reveal: c.words.map(() => 0),
                 hidden: !!(spec.beat.graphic && spec.beat.graphic.component === "title"),      // a title is the text of its beat
                 rtl: c.els.map((e) => e.classList.contains("rtl")),
                 typeDur: c.words.map((w) => clamp((w.end - w.start) * 0.85, 0.07, 0.3)) };
    });
  }

  // where the shape that closes the scene starts from: the last chip on screen, else the cursor, else the last word (stage px)
  function anchorRect() {
    const stage = el("stage").getBoundingClientRect();
    const line = [...document.querySelectorAll(".cap-line")].find((l) => l.style.display !== "none");
    if (!line) return null;
    const chips = [...line.querySelectorAll(".xchip")].filter((c) => parseFloat(c.style.opacity || "0") > 0.5);
    const words = [...line.querySelectorAll(".cw")].filter((c) => parseFloat(c.style.opacity || "0") > 0.5);
    const node = chips.length ? chips[chips.length - 1] : line.querySelector(".xcur") || words[words.length - 1];
    if (!node) return null;
    const r = node.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return null;
    if (!chips.length && !node.classList.contains("xcur")) return { x: r.right - stage.left + 8, y: r.top - stage.top + r.height * 0.2, w: r.height * 0.5, h: r.height * 0.6 };
    return { x: r.left - stage.left, y: r.top - stage.top, w: r.width, h: r.height };
  }

  STUDIIO.style("explainer", {
    components,
    grow: { stage: 1.0 },
    captionWords: (spec, geo) => (geo.kind === "stage" ? 7 : 0),      // on a stage a sentence stays together, on two lines

    setup(ctx) {
      const spec = ctx.spec, b = spec.beat, isEnd = b.layout === "logo-end-card", th = theme(spec);
      el("stage").style.setProperty("--chip", th.css);
      el("stage").style.setProperty("--on-chip", th.onChip);
      el("captions").classList.toggle("solo", !ctx.hasWidget);
      const g = b.graphic && b.graphic.component;
      if (ctx.layoutKind === "stage") el("bg").innerHTML = (g === "note" ? `<div class="xwashup"></div>` : "") + (g === "title" ? `<div class="xglow"></div>` : "");
      if (isEnd) {
        const end = el("endcard"), logo = spec.brand.logo_dark || spec.brand.logo;
        end.insertAdjacentHTML("afterbegin", `<div class="xring"></div><div class="xring"></div><div class="xring"></div><div class="xdot"></div>`);
        const inner = end.querySelector(".endcard-inner"), img = end.querySelector(".endcard-logo");
        if (!logo && img) {                                           // no logo file: the mark is the dot and the name, typed
          img.insertAdjacentHTML("afterend", `<div class="xmark"><i></i><span>${esc(spec.brand.name || "")}</span></div>`);
          img.remove();
        }
        if (spec.brand.tagline && inner) (end.querySelector(".xmark") || end.querySelector(".endcard-logo")).insertAdjacentHTML("afterend", `<div class="xtag">${esc(spec.brand.tagline)}</div>`);
      }
      if (ctx.flareIn || ctx.flareOut || ctx.isFirst) {
        const fx = el("fx");
        fx.style.display = "block";
        fx.innerHTML = `<div class="xmorph"></div>${ctx.isFirst ? `<div class="xfade"></div>` : ""}`;
      }
    },

    // fonts are in and the words are measured: plan the captions, ask for the sounds
    ready(ctx) {
      const b = ctx.spec.beat, d = ctx.duration;
      if (b.layout === "logo-end-card") { cue(0.12, "chime", 0.9); return; }
      planCaptions(ctx);
      if (ctx.isFirst) cue(0.05, "impact", 0.7);
      if (ctx.flareOut) cue(d, "whoosh", 0.8);
      if (ctx.hasWidget) cue(ctx.tIn + 0.03, "pop", 0.55);
      ctx.chunks.forEach((c) => {
        c.runs.forEach((r) => { if (r.chip) cue(c.words[r.from].start, r.n ? "count" : "sweep", 0.5); });
        if (c.plan.typed) c.words.forEach((w, j) => { if (!c.plan.inRun[j]) cue(w.start, "key", 0.4); });
      });
    },

    // the words of the line that is showing
    caption(ln, c, t, active) {
      const P = c.plan;
      if (!P) return;
      if (P.hidden) { ln.style.opacity = 0; return; }
      const th = P.th;
      c.runs.forEach((r) => {                                        // a chip pops in whole when its first word is said; a number in it counts up
        if (!r.chip) return;
        const a = t - c.words[r.from].start, pop = spring(a, 4.0, 0.55);
        r.el.style.opacity = clamp(a / 0.07, 0, 1);
        r.el.style.transform = `scale(${(0.5 + 0.5 * pop).toFixed(4)}) rotate(${((1 - pop) * -5).toFixed(2)}deg)`;
        if (r.n) c.texts[r.from].textContent = showNumber(r.n, r.n.target * easeOutCubic(clamp(a / 0.5, 0, 1)));
      });
      c.els.forEach((sp, j) => {
        const w = c.words[j], a = t - w.start, txt = c.texts[j], dir = P.rtl[j] ? -1 : 1, run = P.inRun[j];
        if (run && run.chip) { P.reveal[j] = a >= 0 ? 1 : 0; return; }
        if (a < 0) { sp.style.opacity = 0; P.reveal[j] = 0; return; }
        let settle;                                                  // 0 → 1: from the colour it is born in to the colour it keeps
        if (P.typed) {                                               // typed: letters show one by one
          const stops = c.stops[j], n = stops.length;
          const r = stops[clamp(Math.ceil((a / P.typeDur[j]) * n), 1, n) - 1];
          sp.style.opacity = 1;
          sp.style.transform = "none";
          txt.style.clipPath = r >= 1 ? "none" : (dir > 0 ? `inset(-30% ${((1 - r) * 100).toFixed(2)}% -30% -10%)` : `inset(-30% -10% -30% ${((1 - r) * 100).toFixed(2)}%)`);
          P.reveal[j] = r;
          settle = clamp((a - P.typeDur[j]) / 0.32, 0, 1);
        } else {                                                     // said: the word slides in from the side
          const q = easeOutCubic(clamp(a / 0.22, 0, 1));
          sp.style.opacity = clamp(a / 0.07, 0, 1);
          sp.style.transform = `translateX(${((1 - q) * 0.3 * P.fs * dir).toFixed(2)}px)`;
          txt.style.clipPath = "none";
          P.reveal[j] = 1;
          settle = clamp((a - 0.06) / 0.34, 0, 1);
        }
        sp.style.color = run ? "" : (settle >= 1 ? "" : mix(th.born, th.ink, settle));
      });

      // the cursor rides the edge of what has been typed, and blinks while the line holds
      if (P.cursor) {
        const j = Math.max(0, active), tb = c.tboxes[j], bx = c.boxes[j], r = P.reveal[j] ?? 1, cur = P.cursor, run = P.inRun[j];
        const curW = Math.max(6, P.fs * 0.09), gap = P.fs * 0.07;
        let edge = P.rtl[j] ? tb.x + tb.w * (1 - r) - curW - gap : tb.x + tb.w * r + gap;
        if (run && run.chip) { const last = c.boxes[run.to]; edge = last.x + last.w + P.fs * 0.34; }
        const idle = t - (c.words[j].start + P.typeDur[j]);
        cur.style.width = curW.toFixed(1) + "px";
        cur.style.height = (tb.h * 0.84).toFixed(1) + "px";
        cur.style.opacity = idle < 0.2 || (idle % 0.9) < 0.55 ? 1 : 0;
        cur.style.transform = `translate(${edge.toFixed(1)}px, ${(tb.y + tb.h * 0.08).toFixed(1)}px)`;
      }

      // the camera: frame what has been said, large while there is room, a step back when there is not
      if (P.stage) {
        const k = Math.max(0, active), now = P.cam[k], was = k > 0 ? P.cam[k - 1] : null;
        const p = was ? easeOutExpo(clamp((t - c.words[k].start) / 0.28, 0, 1)) : 1;
        let s = was ? lerp(was.s, now.s, p) : now.s;
        const cx = was ? lerp(was.cx, now.cx, p) : now.cx, cy = was ? lerp(was.cy, now.cy, p) : now.cy;
        const age = t - c.start;
        s *= 1 + 0.06 * (1 - easeOutExpo(clamp(age / 0.34, 0, 1))) + 0.028 * (1 - clamp(age / 2.8, 0, 1));     // lands a touch large, keeps easing back
        ln.style.transform = `translate(${(c.size.w / 2 - cx * s).toFixed(2)}px, ${(c.size.h / 2 - cy * s).toFixed(2)}px) scale(${s.toFixed(4)})`;
      } else {
        ln.style.transform = "none";
      }
    },

    frame(ctx) {
      const { W, H } = ctx.geo, t = ctx.t, d = ctx.duration, b = ctx.spec.beat;
      const wash = document.querySelector("#bg .xwashup");           // the colour rises behind a notification
      if (wash) wash.style.opacity = (prog(t, ctx.tIn, 0.7) * (1 - prog(t, d - 0.25, 0.25))).toFixed(3);
      const glow = document.querySelector("#bg .xglow");             // light under a big title
      if (glow) glow.style.opacity = (prog(t, 0.05, 0.5) * (1 - prog(t, d - 0.45, 0.4)) * 0.9).toFixed(3);

      // the end: rings open around the dot, the dot shrinks into its place in the mark
      if (b.layout === "logo-end-card") {
        document.querySelectorAll("#endcard .xring").forEach((r, i) => {
          const a = t - 0.12 - i * 0.2, q = clamp(a / 1.25, 0, 1);
          r.style.opacity = a < 0 ? 0 : (Math.min(1, a / 0.12) * (1 - q * q) * (0.95 - i * 0.2)).toFixed(3);
          r.style.transform = `scale(${(0.42 + easeOutCubic(q) * (1.2 + i * 0.42)).toFixed(4)}) rotate(${(i * 140 + 200 + a * 55).toFixed(1)}deg)`;
        });
      }

      // the shape: the last chip grows into the whole frame; the next scene opens out of that colour
      const morph = document.querySelector("#fx .xmorph");
      if (morph) {
        if (ctx.flareOut && t >= d - OUT) {
          const p = easeInCubic(clamp((t - (d - OUT)) / OUT, 0, 1));
          const a = anchorRect() || { x: W / 2 - 45, y: H * 0.46 - 45, w: 90, h: 90 };
          const w = lerp(a.w, W * 1.1, p), h = lerp(a.h, H * 1.1, p);
          const x = lerp(a.x, -W * 0.05, p), y = lerp(a.y, -H * 0.05, p);
          const radius = lerp(Math.min(w, h) * 0.5 * Math.min(1, p * 5 + 0.24), 0, clamp((p - 0.7) / 0.3, 0, 1));     // chip, then a pill, then the frame
          morph.style.cssText = `display:block;left:${x.toFixed(1)}px;top:${y.toFixed(1)}px;width:${w.toFixed(1)}px;height:${h.toFixed(1)}px;border-radius:${radius.toFixed(1)}px;opacity:${clamp(p * 9, 0, 1).toFixed(3)}`;
        } else if (ctx.flareIn && t < IN) {
          const p = easeOutExpo(clamp(t / IN, 0, 1));
          const R = Math.hypot(W, H) * 0.64 * (1 - p), cy = b.layout === "logo-end-card" ? H * 0.44 : (ctx.geo.captions.top + ctx.geo.captions.bottom) / 2;
          morph.style.cssText = `display:block;left:${(W / 2 - R).toFixed(1)}px;top:${(cy - R).toFixed(1)}px;width:${(2 * R).toFixed(1)}px;height:${(2 * R).toFixed(1)}px;border-radius:50%;opacity:1`;
        } else {
          morph.style.display = "none";
        }
      }
      const fade = document.querySelector("#fx .xfade");
      if (fade) fade.style.opacity = (1 - clamp(t / 0.3, 0, 1)).toFixed(3);                     // the reel opens out of black
    },

    // a panel rises out of a blur and the view keeps pushing in; a chain and a title make their own entrance
    widget(w, m) {
      w.style.opacity = m.opacity;
      if (w.classList.contains("w-title") || w.classList.contains("w-flow")) { w.style.transform = `scale(${m.fit.toFixed(4)})`; w.style.filter = "none"; return; }
      const sp = spring(m.t, 2.3, 0.82), q = clamp(m.t / 0.36, 0, 1), push = 0.035 * clamp(m.t / Math.max(1, m.duration), 0, 1);
      w.style.filter = q < 1 ? `blur(${((1 - q) * 12).toFixed(2)}px)` : "none";
      w.style.transform = `translateY(${((1 - sp) * 150 + m.out * 24).toFixed(2)}px) scale(${(m.fit * (0.9 + 0.1 * sp) * (1 + push) * (1 - 0.04 * m.out)).toFixed(4)})`;
    },

    // the dot shrinks into the mark; the name is typed beside it; the line and the chip follow
    endcard(ctx) {
      const t = ctx.t, end = el("endcard"), dot = end.querySelector(".xdot"), logo = end.querySelector(".endcard-logo"), mark = end.querySelector(".xmark");
      const tag = end.querySelector(".xtag"), cta = end.querySelector(".endcard-cta"), url = end.querySelector(".endcard-url");
      const { W, H } = ctx.geo, stage = el("stage").getBoundingClientRect();
      const go = prog(t, 0.95, 0.55, easeInOutCubic);                 // the dot travels from the middle of the rings to its place
      let tx = 0, ty = 0, k = 1;
      if (mark) {
        const slot = mark.querySelector("i").getBoundingClientRect();
        tx = slot.left - stage.left + slot.width / 2 - W / 2; ty = slot.top - stage.top + slot.height / 2 - H * 0.44; k = slot.width / 400;
      } else { k = 0.02; }
      const breathe = 1 + 0.04 * Math.sin(t * 5) * (1 - go);
      dot.style.opacity = clamp(t / 0.1, 0, 1) * (mark ? 1 : 1 - prog(t, 1.25, 0.2));
      dot.style.transform = `translate(${(tx * go).toFixed(2)}px, ${(ty * go).toFixed(2)}px) scale(${(lerp(lerp(1.5, 1, prog(t, 0, 0.6, easeOutExpo)) * breathe, k, go)).toFixed(4)})`;
      if (mark) {                                                     // the name, typed letter by letter once the dot has landed
        const name = mark.querySelector("span"), letters = [...(name.textContent || "")].length, n = clamp(Math.ceil((t - 1.45) / 0.075), 0, letters);
        mark.style.opacity = 1;
        name.style.clipPath = n >= letters ? "none" : `inset(-20% ${((1 - n / Math.max(1, letters)) * 100).toFixed(2)}% -20% 0)`;
      }
      if (logo) { const q = prog(t, 1.15, 0.5, easeOutExpo); logo.style.opacity = q; logo.style.transform = `scale(${(0.86 + 0.14 * q).toFixed(4)})`; }
      if (tag) { const q = prog(t, 2.0, 0.5, easeOutExpo); tag.style.opacity = q > 0 ? 1 : 0; tag.style.clipPath = q >= 1 ? "none" : `inset(-40% ${((1 - q) * 100).toFixed(2)}% -40% -20%)`; }      // the light opens to the right
      if (cta) { const a = t - 2.4, pop = spring(a, 4.0, 0.55); cta.style.opacity = clamp(a / 0.07, 0, 1); cta.style.transform = `scale(${(0.5 + 0.5 * pop).toFixed(4)}) rotate(${((1 - pop) * -5).toFixed(2)}deg)`; cta.style.clipPath = "none"; }
      if (url) url.style.opacity = prog(t, 2.7, 0.35);
    },
  });
})();
