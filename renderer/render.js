/* Studiio renderer — deterministic beat rendering.
 *
 * window.setup(spec)          builds the beat: layout, footage frame, widget, captions
 * window.seek(t, footageUrl)  sets every element to its state at time t (seconds from beat start)
 * window.cues()               the sounds this beat asks for: [{t, name, gain}], t from the beat start
 *
 * No clocks, no transitions: the state at t is a pure function of the spec, so a frame can be
 * rendered in any order and a still is exactly the frame the video will have.
 */
(() => {
  const S = { spec: null, widget: null, chunks: [], footageUrl: null, fit: 1 };
  const $ = (id) => document.getElementById(id);
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
  const easeOutCubic = (p) => 1 - Math.pow(1 - p, 3);
  const easeOutBack = (p) => { const c1 = 1.25, c3 = c1 + 1; return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2); };
  const prog = (t, start, dur, ease = easeOutCubic) => ease(clamp((t - start) / dur, 0, 1));
  // ---- the motion kit: every move is a pure function of time
  const lerp = (a, b, p) => a + (b - a) * p;
  const easeOutExpo = (p) => (p >= 1 ? 1 : 1 - Math.pow(2, -10 * p));
  const easeOutQuint = (p) => 1 - Math.pow(1 - p, 5);
  const easeInCubic = (p) => p * p * p;
  const easeInOutCubic = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
  // a damped spring from 0 to 1 that starts at t = 0: f swings a second, z damping (1 = no overshoot)
  const spring = (t, f = 3.2, z = 0.6) => {
    if (t <= 0) return 0;
    const w = 2 * Math.PI * f;
    if (z >= 1) return 1 - Math.exp(-w * t) * (1 + w * t);
    const wd = w * Math.sqrt(1 - z * z);
    return 1 - Math.exp(-z * w * t) * (Math.cos(wd * t) + (z * w / wd) * Math.sin(wd * t));
  };
  // a smooth wander in [-1, 1], the same for the same (t, seed): for floating and breathing
  const hash = (n) => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
  const drift = (t, seed = 0) => { const i = Math.floor(t), f = t - i, u = f * f * (3 - 2 * f);
    return lerp(hash(i + seed * 17.3), hash(i + 1 + seed * 17.3), u) * 2 - 1; };
  const isRTL = (s) => /[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]/.test(String(s));
  // "$800,000" or "40%," as prefix, number, suffix, so it can count up
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
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const norm = (w) => String(w).toLowerCase().normalize("NFKC").replace(/[^\p{L}\p{N}]+/gu, "");

  // A style can bring its own code: styles/<name>/components.js calls STUDIIO.style(name, {...}) with
  //   components   {name: (props, spec) => ({html, animate})}   used before the built-in ones
  //   setup(ctx)   build the style's own background and effects for this beat
  //   frame(ctx)   set them at time ctx.t (ctx.T is the time since the start of the reel)
  //   ready(ctx)   once fonts are in and everything is measured: plan caption moves, ask for sounds
  //   widget(el, m) place the widget instead of the default slide-up (m: enter 0..1, opacity, fit, t, out)
  //   caption(line, chunk, t, active, ctx)  move the words of the caption line that is showing
  //   endcard(ctx) move the end card instead of the default
  // A sound is asked for with STUDIIO.cue(t, name, gain): t in seconds from the beat start. Inside a
  // component, STUDIIO.cueW(t, ...) counts t from the moment the widget arrives.
  const STYLES = {};
  window.STUDIIO = {
    style(name, def) { STYLES[name] = def || {}; },
    util: { clamp, lerp, easeOutCubic, easeOutBack, easeOutExpo, easeOutQuint, easeInCubic, easeInOutCubic, spring, drift, hash,
            prog, esc, norm, isRTL, parseNumber, showNumber },
    cue(t, name, gain = 1) {
      if (!S.cues || !isFinite(t) || !name) return;
      S.cues.push({ t: Math.round(Math.max(0, t) * 1000) / 1000, name: String(name), gain: Math.round(clamp(gain, 0, 2) * 100) / 100 });
    },
    cueW(t, name, gain = 1) { (S.cuesW = S.cuesW || []).push([t, name, gain]); },      // placed once the widget's arrival is known
  };
  window.cues = () => (S.cues || []).slice();
  window.STUDIIO.measureCaptions = () => measureCaptions();     // a style that reshapes a caption line measures it again
  const ctxOf = (t) => {
    const b = S.spec.beat;
    return { spec: S.spec, geo: S.geo, t, T: (b.t0 || 0) + t, duration: b.duration, $,
             flareIn: !!b.flare_in, flareOut: !!b.flare_out, layoutKind: S.geo ? S.geo.kind : "full",
             prev: b.prev_layout || null, next: b.next_layout || null, isFirst: !!b.is_first, isLast: !!b.is_last,
             chunks: S.chunks, hasWidget: !!S.widget, tIn: S.tIn || 0, fit: S.fit };
  };

  // ---------------------------------------------------------------- setup
  window.setup = async function (spec) {
    S.spec = spec;
    S.widget = null;
    S.chunks = [];
    S.cues = [];
    S.cuesW = [];
    S.tIn = 0;
    S.footageUrl = null;
    $("cam").style.transform = "";
    const W = spec.width, H = spec.height;
    const stage = $("stage");
    stage.style.width = W + "px";
    stage.style.height = H + "px";
    stage.style.setProperty("--font", `"${spec.brand.font.family}"`);
    stage.style.setProperty("--accent", spec.brand.primary);
    stage.style.setProperty("--secondary", spec.brand.secondary);
    const capScale = (spec.beat.render && spec.beat.render.caption_scale) || 1;
    stage.style.setProperty("--cap-size", Math.round(spec.captions.size_px * capScale) + "px");
    const styleName = (spec.style && spec.style.name) || "ios";
    stage.className = "style-" + styleName + " layout-" + spec.beat.layout;
    installFonts(spec);
    installStyle(spec.style_css);
    if (spec.style_js) await installScript(spec.style_js);
    S.style = STYLES[styleName] || {};

    $("footage").src = "";
    $("endcard").style.display = "none";
    $("endcard").innerHTML = "";
    $("bg").style.display = "none";
    $("widget").innerHTML = "";
    $("widget").style.cssText = "";
    $("captions").innerHTML = "";
    $("bg").innerHTML = "";
    $("fx").innerHTML = "";
    $("fx").style.display = "none";

    const geo = layout(spec);
    S.geo = geo;
    stage.classList.add("kind-" + geo.kind);
    if (spec.beat.layout === "logo-end-card") {
      buildEndCard(spec);
    } else {
      if (spec.beat.graphic) buildWidget(spec, geo);
      buildCaptions(spec, geo);
    }
    if (S.style.setup) S.style.setup(ctxOf(0));
    // every weight of the client's font, loaded before anything is measured: a hidden caption line
    // does not ask for its font, and a measure taken with the fallback font would be wrong
    const weights = (spec.brand.font.faces || []).flatMap((f) => (String(f.weight).includes(" ") ? [300, 400, 500, 600, 700] : [f.weight]));    // a variable font: the weights styles use
    await Promise.all(weights.map((w) => document.fonts.load(`${w} 48px "${spec.brand.font.family}"`).catch(() => {})));
    await document.fonts.ready;
    await Promise.all([...$("widget").querySelectorAll("img")].map((im) => im.decode().catch(() => {})));
    if (S.widget && S.widget.build) S.widget.animate = S.widget.build($("widget"), spec) || (() => {});
    (S.cuesW || []).forEach(([t, name, gain]) => window.STUDIIO.cue((S.tIn || 0) + t, name, gain));
    S.cuesW = [];
    fitWidget(geo);
    measureCaptions();
    if (S.style.ready) S.style.ready(ctxOf(0));
    else defaultCues(spec);
    return geo;
  };

  // Where things are on the frame at the current time (stage pixels), for the quality check.
  window.boxes = function () {
    const stage = $("stage").getBoundingClientRect();
    const rect = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return null;
      const op = parseFloat(getComputedStyle(el).opacity || "1");
      if (op < 0.05) return null;
      return [Math.round(r.left - stage.left), Math.round(r.top - stage.top), Math.round(r.width), Math.round(r.height)];
    };
    const line = [...document.querySelectorAll(".cap-line")].find((l) => l.style.display !== "none");
    return {
      widget: S.widget ? rect($("widget")) : null,
      captions: line ? rect(line) : null,
      card: S.geo && S.geo.card ? [S.geo.card.x, S.geo.card.y, S.geo.card.w, S.geo.card.h] : null,
    };
  };

  window.seek = async function (t, footageUrl) {
    const img = $("footage");
    if (footageUrl && footageUrl !== S.footageUrl) {
      img.src = footageUrl;
      try { await img.decode(); } catch (e) { /* a missing frame renders black; the QC stage catches it */ }
      S.footageUrl = footageUrl;
    }
    applyTime(t);
    return true;
  };

  function installFonts(spec) {
    let st = $("font-faces");
    if (!st) { st = document.createElement("style"); st.id = "font-faces"; document.head.appendChild(st); }
    st.textContent = (spec.brand.font.faces || []).map((f) =>
      `@font-face{font-family:"${spec.brand.font.family}";font-weight:${f.weight};src:url("${f.url}");}`).join("\n");
  }

  const loadedScripts = {};
  function installScript(url) {
    if (!loadedScripts[url]) {
      loadedScripts[url] = new Promise((resolve) => {
        const el = document.createElement("script");
        el.src = url; el.onload = () => resolve(true); el.onerror = () => resolve(false);
        document.head.appendChild(el);
      });
    }
    return loadedScripts[url];
  }

  function installStyle(url) {
    let link = $("style-css");
    if (!link) { link = document.createElement("link"); link.id = "style-css"; link.rel = "stylesheet"; document.head.appendChild(link); }
    if (link.getAttribute("href") !== url) link.setAttribute("href", url);
  }

  // ---------------------------------------------------------------- layout
  function layout(spec) {
    const W = spec.width, H = spec.height, b = spec.beat, safe = spec.safe;
    const noGo = b.no_go || [0.3, 0.3, 0.7, 0.65];
    const capLines = 1;
    const capScale = (b.render && b.render.caption_scale) || 1;
    const capH = Math.round(spec.captions.size_px * capScale * (b.caption.style === "emphasis" ? 1.15 : 1) * 1.45 * capLines);
    const geo = { W, H, capH };
    const fw = $("footage-wrap"), bg = $("bg"), zone = $("widget-zone"), caps = $("captions");

    // A layout is one of three kinds: the footage fills the frame ("full"), sits in a card ("card"),
    // or is not shown at all ("stage"). The style's layout definition says which, and where things go.
    const L = (spec.style.layouts || {})[b.layout] || {};
    const kind = L.kind || (L.footage_card ? "card" : "full");
    geo.kind = kind;
    if (kind === "card") {
      const c = L.footage_card;
      const rect = { x: Math.round(c.x * W), y: Math.round(c.y * H), w: Math.round(c.w * W), h: Math.round(c.h * H) };
      fw.style.cssText = `inset:auto;left:${rect.x}px;top:${rect.y}px;width:${rect.w}px;height:${rect.h}px;` +
        `border-radius:${c.radius || 48}px;box-shadow:var(--card-shadow, 0 30px 80px rgba(20,10,30,.22));`;
      bg.style.display = "block";
      bg.className = L.bg_class || "card-bg";
      geo.card = rect;
      const zoneTop = rect.y + rect.h + Math.round(0.02 * H);
      const solo = !b.graphic && L.captions_solo;      // nothing under the card: the words may move up under it
      const capTop = Math.round((solo ? solo.top : (L.captions_top || 0.71)) * H);
      geo.zone = { top: zoneTop, bottom: Math.max(zoneTop + 40, capTop - Math.round(0.015 * H)) };
      geo.captions = { top: capTop, bottom: solo ? Math.round(solo.bottom * H) : Math.round((1 - safe.bottom) * H) };
      geo.captionTheme = L.caption_theme || "light";
    } else if (kind === "stage") {
      fw.style.cssText = "display:none";
      bg.style.display = "block";
      bg.className = L.bg_class || "stage-bg";
      const z = L.zone || { top: 0.2, bottom: 0.62 };
      // a style may give the words their own place: above a component (captions), or alone (captions_solo)
      const cz = (!b.graphic && L.captions_solo) || L.captions || null;
      const capTop = Math.round((cz ? cz.top : (L.captions_top || 0.70)) * H);
      const capBottom = cz ? Math.round(cz.bottom * H) : Math.round((1 - safe.bottom) * H);
      const wordsAbove = cz && cz.top < z.top;
      geo.zone = { top: Math.round(z.top * H), bottom: wordsAbove ? Math.round(z.bottom * H) : Math.min(Math.round(z.bottom * H), capTop - Math.round(0.015 * H)) };
      geo.captions = { top: capTop, bottom: capBottom };
      geo.captionTheme = L.caption_theme || "dark";
    } else {
      fw.style.cssText = "";
      const anchor = (b.render && b.render.anchor) || (b.graphic && b.graphic.anchor) || "top";
      const topZone = { top: Math.round(safe.top * H), bottom: Math.round((noGo[1] - 0.02) * H) };
      let capTop = Math.round(Math.max(noGo[3] + 0.04, 0.70) * H);
      const capBottomMax = Math.round((1 - safe.bottom) * H);
      if (capTop + capH > capBottomMax) capTop = capBottomMax - capH;
      geo.captions = { top: capTop, bottom: capBottomMax };
      const bottomZone = { top: Math.round((noGo[3] + 0.02) * H), bottom: capTop - Math.round(0.015 * H) };
      const useBottom = anchor === "bottom" && (bottomZone.bottom - bottomZone.top) >= 0.12 * H;
      geo.zone = useBottom ? bottomZone : topZone;
      geo.captionTheme = L.caption_theme || "dark";
    }
    zone.style.top = geo.zone.top + "px";
    zone.style.height = Math.max(40, geo.zone.bottom - geo.zone.top) + "px";
    caps.style.top = geo.captions.top + "px";
    caps.style.height = (geo.captions.bottom - geo.captions.top) + "px";
    caps.className = "theme-" + geo.captionTheme + (b.caption.style === "emphasis" ? " emphasis" : "");
    return geo;
  }

  function fitWidget(geo) {
    const w = $("widget");
    if (!S.widget) return;
    w.style.transform = "none";
    const maxH = geo.zone.bottom - geo.zone.top - 8;
    const maxW = w.querySelector("[data-free-x]") ? 1e6 : geo.W * 0.86;     // a component the camera travels along may be wider than the frame
    const h = w.offsetHeight, wd = w.offsetWidth;
    // a style may let a small component grow into an empty stage (grow: {stage: 1.6}); it never outgrows its zone
    const grow = (S.style.grow && S.style.grow[geo.kind]) || 1;
    S.fit = Math.min(grow, maxH / Math.max(1, h), maxW / Math.max(1, wd)) * (S.shrink || 1);
    w.style.transformOrigin = "center center";
  }

  // ---------------------------------------------------------------- widgets
  const COMPONENTS = {};
  const cueW = (t, name, gain) => window.STUDIIO.cueW(t, name, gain);

  function buildWidget(spec, geo) {
    const g = spec.beat.graphic;
    const comp = (S.style.components || {})[g.component] || COMPONENTS[g.component];
    const w = $("widget");
    if (!comp) { w.innerHTML = `<div class="widget w-unknown">${esc(g.component)}</div>`; S.widget = { animate() {} }; return; }
    S.tIn = spec.beat.is_first ? spec.style.motion_first_beat_delay : 0.1;
    const built = comp(g.props || {}, spec);
    if (built.arrive != null) S.tIn = Math.max(S.tIn, Number(built.arrive) || 0);     // e.g. just before the first figure is said
    w.innerHTML = built.html;
    w.className = "widget w-" + g.component;
    S.widget = { animate() {}, build: built.animate || null };      // built once the fonts are in, so it can measure itself
    S.shrink = (spec.beat.render && spec.beat.render.shrink) || 1;   // quality-check fix: make the widget smaller
  }

  COMPONENTS["headline"] = (p) => {
    const words = String(p.text || "").split(/\s+/).filter(Boolean);
    const hl = norm(p.highlight || "");
    const html = `<div class="headline">${words.map((w) =>
      `<span class="hw${hl && norm(w) === hl ? " hl" : ""}">${esc(w)}</span>`).join(" ")}</div>`;
    return { html, animate: (el) => (t) => {
      el.querySelectorAll(".hw").forEach((s, i) => {       // each word lands with a small bounce
        const a = t - i * 0.08, sp = spring(a, 3.4, 0.6);
        s.style.opacity = clamp(a / 0.1, 0, 1); s.style.transform = `translateY(${((1 - sp) * 34).toFixed(2)}px) scale(${(0.92 + 0.08 * sp).toFixed(4)})`;
      });
    } };
  };

  COMPONENTS["ios-notification"] = (p) => {
    cueW(0.04, "ding", 0.7);
    return {
      html: `<div class="notif"><div class="notif-icon"></div><div class="notif-body">
        <div class="notif-top"><span class="notif-app">${esc(p.app || "Messages")}</span><span class="notif-time">${esc(p.time || "now")}</span></div>
        <div class="notif-title">${esc(p.title || "")}</div><div class="notif-text">${esc(p.body || "")}</div></div></div>`,
      animate: (el) => {
        const icon = el.querySelector(".notif-icon"), text = el.querySelector(".notif-text");
        return (t) => {                                     // the icon pops, the line follows
          icon.style.transform = `scale(${(0.6 + 0.4 * spring(t - 0.05, 3.6, 0.5)).toFixed(4)})`;
          const q = prog(t, 0.2, 0.3); text.style.opacity = q; text.style.transform = `translateY(${((1 - q) * 8).toFixed(2)}px)`;
        };
      },
    };
  };

  COMPONENTS["ios-message"] = (p) => {
    const bubbles = (p.bubbles || []).slice(0, 3);
    bubbles.forEach((b, i) => cueW(0.05 + i * 0.45, "bloop", 0.6));
    return { html: `<div class="msgs">${bubbles.map((b) =>
      `<div class="bubble ${b.direction === "out" ? "out" : "in"}">${esc(b.text)}</div>`).join("")}</div>`,
      animate: (el) => (t) => {
        el.querySelectorAll(".bubble").forEach((b, i) => {
          const a = t - (0.05 + i * 0.45), sp = spring(a, 3.8, 0.52);
          b.style.opacity = clamp(a / 0.09, 0, 1); b.style.transform = `translateY(${((1 - sp) * 26).toFixed(2)}px) scale(${(0.7 + 0.3 * sp).toFixed(4)})`;
        });
      } };
  };

  COMPONENTS["ios-progress"] = (p) => {
    const from = Number(p.from ?? 0), to = Number(p.to ?? 100), unit = p.unit ?? "%";
    cueW(0.25, "count", 0.6);
    return { html: `<div class="progress"><div class="progress-top"><span class="progress-label">${esc(p.label || "")}</span>
      <span class="progress-value">${from}${esc(unit)}</span></div><div class="progress-track"><div class="progress-fill"></div></div></div>`,
      animate: (el) => {
        const fill = el.querySelector(".progress-fill"), val = el.querySelector(".progress-value");
        return (t) => {
          const q = prog(t, 0.25, 1.0, easeInOutCubic);
          const v = from + (to - from) * q;
          fill.style.width = clamp(v, 0, 100) + "%";
          val.textContent = Math.round(v) + unit;
        };
      } };
  };

  COMPONENTS["ios-stat"] = (p) => {
    const value = String(p.value ?? "");
    const m = value.match(/^([^\d]*)([\d][\d,]*(?:\.\d+)?)([^\d]*)$/);
    const trend = p.trend === "up" ? "▲" : p.trend === "down" ? "▼" : "";
    if (m) cueW(0.15, "count", 0.6);
    return { html: `<div class="stat"><div class="stat-value"><span class="stat-num">${esc(value)}</span>
      ${trend ? `<span class="stat-trend ${esc(p.trend)}">${trend}${esc(p.delta || "")}</span>` : ""}</div>
      <div class="stat-label">${esc(p.label || "")}</div></div>`,
      animate: (el) => {
        const num = el.querySelector(".stat-num");
        if (!m) return () => {};
        const prefix = m[1], suffix = m[3], raw = m[2];
        const decimals = (raw.split(".")[1] || "").length;
        const target = parseFloat(raw.replace(/,/g, ""));
        const grouped = raw.includes(",");
        const badge = el.querySelector(".stat-trend");
        return (t) => {
          const q = prog(t, 0.15, 0.95, easeOutExpo);
          if (badge) badge.style.transform = `scale(${(0.4 + 0.6 * spring(t - 0.75, 3.8, 0.5)).toFixed(4)})`;     // the trend pops when the count rests
          const v = target * q;
          let s = v.toFixed(decimals);
          if (grouped) s = Number(s).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
          num.textContent = prefix + s + suffix;
        };
      } };
  };

  COMPONENTS["ios-list"] = (p, spec) => {
    const items = (p.items || []).slice(0, 5);
    const marker = p.marker || "check";
    const mark = (i) => marker === "number" ? `<span class="mk num">${i + 1}</span>` : marker === "dot" ? `<span class="mk dot"></span>` : `<span class="mk check">✓</span>`;
    return { html: `<div class="list">${p.title ? `<div class="list-title">${esc(p.title)}</div>` : ""}
      ${items.map((it, i) => `<div class="list-item">${mark(i)}<span class="list-text">${esc(it)}</span></div>`).join("")}</div>`,
      animate: (el) => {
        const rows = el.querySelectorAll(".list-item");
        const d = spec.beat.duration, tIn = spec.beat.is_first ? spec.style.motion_first_beat_delay : 0.1;
        const stagger = clamp((d - tIn - 0.9) / Math.max(1, rows.length), 0.18, 0.55);
        rows.forEach((r, i) => cueW(0.3 + i * stagger, "tick", 0.55));
        return (t) => rows.forEach((r, i) => {
          const a = t - (0.3 + i * stagger), sp = spring(a, 3.4, 0.62);
          r.style.opacity = clamp(a / 0.1, 0, 1); r.style.transform = `translateX(${((1 - sp) * -30).toFixed(2)}px)`;
          const mk = r.querySelector(".mk"); if (mk) mk.style.transform = `scale(${(0.3 + 0.7 * spring(a - 0.06, 4, 0.48)).toFixed(4)})`;
        });
      } };
  };

  COMPONENTS["asset"] = (p) => {
    const cap = p.caption ? `<div class="asset-cap">${esc(p.caption)}</div>` : "";
    return { html: `<div class="asset"><img class="asset-img" src="${esc(p.src || "")}" alt="">${cap}</div>`,
      animate: (el) => {
        const img = el.querySelector(".asset-img"), c = el.querySelector(".asset-cap");
        return (t) => {
          const sp = spring(t - 0.05, 2.8, 0.6);
          img.style.opacity = clamp((t - 0.05) / 0.12, 0, 1); img.style.transform = `scale(${(0.9 + 0.1 * sp).toFixed(4)})`;
          if (c) { const q2 = prog(t, 0.45, 0.3); c.style.opacity = q2; c.style.transform = `translateY(${(1 - q2) * 12}px)`; }
        };
      } };
  };

  COMPONENTS["ios-toggle"] = (p) => {
    const on = (p.state || "on") === "on";
    cueW(0.6, "click", 0.8);
    return { html: `<div class="toggle-row"><span class="toggle-label">${esc(p.label || "")}</span>
      <div class="toggle ${on ? "" : "on"}"><div class="knob"></div></div></div>`,
      animate: (el) => {
        const tg = el.querySelector(".toggle");
        return (t) => { const q = prog(t, 0.5, 0.25); tg.classList.toggle("on", on ? q >= 0.5 : q < 0.5); tg.style.setProperty("--q", q); };
      } };
  };

  COMPONENTS["ios-card"] = (p) => {
    const rows = (p.rows || []).slice(0, 4);
    return { html: `<div class="tcard">${p.eyebrow ? `<div class="eyebrow">${esc(p.eyebrow)}</div>` : ""}
      <div class="tcard-title">${esc(p.title || "")}</div>
      ${rows.map((r) => `<div class="tcard-row"><span class="k">${esc(r.label)}</span><span class="v">${esc(r.value)}</span></div>`).join("")}</div>`,
      animate: (el) => (t) => el.querySelectorAll(".tcard-row").forEach((r, i) => {
        const q = prog(t, 0.3 + i * 0.14, 0.32); r.style.opacity = q; r.style.transform = `translateY(${((1 - q) * 12).toFixed(2)}px)`; }) };
  };

  COMPONENTS["ios-suggested-edit"] = (p) => (cueW(0.9, "click", 0.8), {
    html: `<div class="suggest"><div class="eyebrow">Suggested edit</div><div class="suggest-text">${esc(p.text || "")}</div>
      <div class="suggest-actions"><span class="btn-accept">Accept</span><span class="btn-dismiss">Dismiss</span></div></div>`,
    animate: (el) => {
      const acc = el.querySelector(".btn-accept");
      return (t) => {
        const pressed = t >= 0.9 && t < 1.08, done = t >= 1.08;
        acc.classList.toggle("pressed", pressed); acc.classList.toggle("done", done);
        acc.textContent = done ? "Accepted ✓" : "Accept";
      };
    },
  });

  COMPONENTS["before-after"] = (p) => ({
    html: `<div class="ba"><div class="ba-before"><span class="ba-text">${esc(p.before || "")}</span><span class="ba-strike"></span></div>
      <div class="ba-after">${esc(p.after || "")}</div></div>`,
    animate: (el) => {
      const strike = el.querySelector(".ba-strike"), after = el.querySelector(".ba-after");
      return (t) => {
        strike.style.width = (prog(t, 0.3, 0.4) * 100) + "%";
        const q = prog(t, 0.6, 0.35, easeOutBack);
        after.style.opacity = clamp(q * 1.5, 0, 1); after.style.transform = `translateY(${(1 - q) * 18}px)`;
      };
    },
  });

  COMPONENTS["ios-timer"] = (p) => ({
    html: `<div class="timer"><span class="timer-icon"></span><span class="timer-text">${esc(p.text || "")}</span></div>`,
    animate: () => () => {},
  });

  COMPONENTS["logo-end-card"] = (p) => ({ html: "", animate: () => () => {} });

  function buildEndCard(spec) {
    const e = $("endcard");
    const p = (spec.beat.graphic && spec.beat.graphic.props) || {};
    e.style.display = "block";
    e.innerHTML = `<div class="endcard-inner"><img class="endcard-logo" src="${esc(spec.brand.logo_dark || spec.brand.logo || "")}" alt="">
      ${p.cta ? `<div class="endcard-cta">${esc(p.cta)}</div>` : ""}${spec.brand.url ? `<div class="endcard-url">${esc(spec.brand.url)}</div>` : ""}</div>`;
    $("footage-wrap").style.display = "none";
    S.widget = null;
  }

  // ---------------------------------------------------------------- captions
  function buildCaptions(spec, geo) {
    const words = spec.beat.words || [];
    const maxWords = (S.style.captionWords && S.style.captionWords(spec, geo)) || spec.captions.max_words_per_line || 4;
    const hl = new Set((spec.beat.caption.highlight || []).map(norm).filter(Boolean));
    const chunks = [];
    let cur = [];
    const endsSentence = (w) => /[.!?؟…]$/.test(w);
    for (const w of words) {
      cur.push(w);
      if (cur.length >= maxWords || endsSentence(w.w)) { chunks.push(cur); cur = []; }
    }
    if (cur.length) chunks.push(cur);
    // A lonely last word joins the previous chunk if that keeps the line short.
    if (chunks.length > 1 && chunks[chunks.length - 1].length === 1 && chunks[chunks.length - 2].length <= maxWords) {
      chunks[chunks.length - 2].push(chunks.pop()[0]);
    }
    S.chunks = chunks.map((ws, i) => ({
      start: ws[0].start,
      end: i + 1 < chunks.length ? chunks[i + 1][0].start : spec.beat.duration + 1,
      words: ws,
      html: ws.map((w, j) => `<span class="cw${hl.has(norm(w.w)) ? " hl" : ""}${isRTL(w.w) ? " rtl" : ""}" data-i="${j}"><span class="cwt">${esc(w.w)}</span></span>`).join(" "),
    }));
    $("captions").innerHTML = S.chunks.map((c, i) => `<div class="cap-line" data-c="${i}">${c.html}</div>`).join("");
  }

  // Where every word of every caption line sits (px inside its own line, before any move), and where
  // each letter ends inside its word, so a style can move a camera over the words and type them.
  function measureCaptions() {
    document.querySelectorAll(".cap-line").forEach((ln, i) => {
      const c = S.chunks[i];
      ln.style.transform = "none";
      ln.style.display = "block";
      const lr = ln.getBoundingClientRect();
      c.line = ln;
      c.size = { w: lr.width, h: lr.height };
      c.els = [...ln.querySelectorAll(".cw")];
      const box = (r) => ({ x: r.left - lr.left, y: r.top - lr.top, w: r.width, h: r.height });
      c.boxes = c.els.map((el) => box(el.getBoundingClientRect()));
      c.texts = c.els.map((el) => el.firstElementChild);
      c.tboxes = c.texts.map((el) => box(el.getBoundingClientRect()));
      c.stops = c.texts.map((el, j) => {       // the share of the word's width shown after 1, 2, … letters
        const node = el.firstChild, total = Math.max(1, c.tboxes[j].w);
        if (!node || node.nodeType !== 3) return [1];
        const letters = [...node.data], out = [];
        let pos = 0;
        const range = document.createRange();
        for (const ch of letters) {
          pos += ch.length;
          range.setStart(node, 0); range.setEnd(node, pos);
          out.push(clamp(range.getBoundingClientRect().width / total, 0, 1));
        }
        out[out.length - 1] = 1;
        return out;
      });
      ln.style.display = "none";
    });
  }

  // The sounds of a style that has no code of its own: a pop when a widget arrives, air at a change of layout.
  function defaultCues(spec) {
    const b = spec.beat;
    if (S.widget) window.STUDIIO.cue(S.tIn, "pop", 0.8);
    if (b.flare_out) window.STUDIIO.cue(b.duration, "whoosh", 0.8);
  }

  // ---------------------------------------------------------------- time
  function applyTime(t) {
    const spec = S.spec, b = spec.beat;
    if (b.layout === "logo-end-card") {
      const logo = document.querySelector(".endcard-logo"), cta = document.querySelector(".endcard-cta"), url = document.querySelector(".endcard-url");
      const q = prog(t, 0.05, 0.6, easeOutBack);
      if (logo) { logo.style.opacity = clamp(q * 1.4, 0, 1); logo.style.transform = `scale(${0.86 + 0.14 * q})`; }
      if (cta) cta.style.opacity = prog(t, 0.45, 0.4);
      if (url) url.style.opacity = prog(t, 0.75, 0.4);
      if (S.style.endcard) S.style.endcard(ctxOf(t));
      if (S.style.frame) S.style.frame(ctxOf(t));
      return;
    }
    const w = $("widget");
    if (S.widget) {
      const tIn = S.tIn;
      const enter = prog(t, tIn, 0.32, easeOutBack);
      let opacity = clamp(enter * 1.6, 0, 1);
      const exitStart = b.duration - 0.2;
      const out = b.hold_widget ? 0 : clamp((t - exitStart) / 0.2, 0, 1);
      opacity *= 1 - out;
      if (S.style.widget) {
        S.style.widget(w, { enter: prog(t, tIn, 0.42), opacity, fit: S.fit, t: Math.max(0, t - tIn), out, T: (b.t0 || 0) + t, duration: b.duration,
                            above: (S.geo.zone.top + S.geo.zone.bottom) / 2 < S.geo.H * 0.5 });
      } else {
        w.style.opacity = opacity;
        w.style.transform = `translateY(${(1 - enter) * 28}px) scale(${S.fit * (0.96 + 0.04 * enter)})`;
      }
      S.widget.animate(Math.max(0, t - tIn));
    }
    // captions
    const lines = document.querySelectorAll(".cap-line");
    lines.forEach((ln, i) => {
      const c = S.chunks[i];
      const on = t >= c.start && t < c.end;
      ln.style.display = on ? "block" : "none";
      if (!on) return;
      let active = -1;
      c.words.forEach((wd, j) => { if (t >= wd.start) active = j; });
      (c.els || ln.querySelectorAll(".cw")).forEach((sp, j) => {
        sp.classList.toggle("active", j === active);
        sp.classList.toggle("said", j < active);
      });
      if (S.style.caption) S.style.caption(ln, c, t, active, ctxOf(t));
    });
    if (S.style.frame) S.style.frame(ctxOf(t));
  }
})();
