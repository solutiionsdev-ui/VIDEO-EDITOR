/* Studiio renderer — deterministic beat rendering.
 *
 * window.setup(spec)          builds the beat: layout, footage frame, widget, captions
 * window.seek(t, footageUrl)  sets every element to its state at time t (seconds from beat start)
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
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const norm = (w) => String(w).toLowerCase().normalize("NFKC").replace(/[^\p{L}\p{N}]+/gu, "");

  // ---------------------------------------------------------------- setup
  window.setup = async function (spec) {
    S.spec = spec;
    S.widget = null;
    S.chunks = [];
    S.footageUrl = null;
    const W = spec.width, H = spec.height;
    const stage = $("stage");
    stage.style.width = W + "px";
    stage.style.height = H + "px";
    stage.style.setProperty("--font", `"${spec.brand.font.family}"`);
    stage.style.setProperty("--accent", spec.brand.primary);
    stage.style.setProperty("--secondary", spec.brand.secondary);
    const capScale = (spec.beat.render && spec.beat.render.caption_scale) || 1;
    stage.style.setProperty("--cap-size", Math.round(spec.captions.size_px * capScale) + "px");
    stage.className = "layout-" + spec.beat.layout;
    installFonts(spec);
    installStyle(spec.style_css);

    $("footage").src = "";
    $("endcard").style.display = "none";
    $("endcard").innerHTML = "";
    $("bg").style.display = "none";
    $("widget").innerHTML = "";
    $("widget").style.cssText = "";
    $("captions").innerHTML = "";

    const geo = layout(spec);
    S.geo = geo;
    if (spec.beat.layout === "logo-end-card") {
      buildEndCard(spec);
    } else {
      if (spec.beat.graphic) buildWidget(spec, geo);
      buildCaptions(spec, geo);
    }
    await document.fonts.ready;
    fitWidget(geo);
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

    if (b.layout === "white-card") {
      const c = spec.style.layouts["white-card"].footage_card;
      const rect = { x: Math.round(c.x * W), y: Math.round(c.y * H), w: Math.round(c.w * W), h: Math.round(c.h * H) };
      fw.style.cssText = `inset:auto;left:${rect.x}px;top:${rect.y}px;width:${rect.w}px;height:${rect.h}px;` +
        `border-radius:${c.radius || 48}px;box-shadow:0 30px 80px rgba(20,10,30,.22);`;
      bg.style.display = "block";
      bg.className = "card-bg";
      geo.card = rect;
      const zoneTop = rect.y + rect.h + Math.round(0.02 * H);
      const capTop = Math.round(0.71 * H);
      geo.zone = { top: zoneTop, bottom: capTop - Math.round(0.015 * H) };
      geo.captions = { top: capTop, bottom: Math.round((1 - safe.bottom) * H) };
      geo.captionTheme = "light";
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
      geo.captionTheme = "dark";
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
    const maxW = geo.W * 0.86;
    const h = w.offsetHeight, wd = w.offsetWidth;
    S.fit = Math.min(1, maxH / Math.max(1, h), maxW / Math.max(1, wd)) * (S.shrink || 1);
    if (S.fit < 0.999) w.style.transformOrigin = "center center";
  }

  // ---------------------------------------------------------------- widgets
  const COMPONENTS = {};

  function buildWidget(spec, geo) {
    const g = spec.beat.graphic;
    const comp = COMPONENTS[g.component];
    const w = $("widget");
    if (!comp) { w.innerHTML = `<div class="widget w-unknown">${esc(g.component)}</div>`; S.widget = { animate() {} }; return; }
    const built = comp(g.props || {}, spec);
    w.innerHTML = built.html;
    w.className = "widget w-" + g.component;
    S.widget = { animate: built.animate ? built.animate(w, spec) : () => {} };
    S.tIn = spec.beat.is_first ? spec.style.motion_first_beat_delay : 0.1;
    S.shrink = (spec.beat.render && spec.beat.render.shrink) || 1;   // quality-check fix: make the widget smaller
  }

  COMPONENTS["headline"] = (p) => {
    const words = String(p.text || "").split(/\s+/).filter(Boolean);
    const hl = norm(p.highlight || "");
    const html = `<div class="headline">${words.map((w) =>
      `<span class="hw${hl && norm(w) === hl ? " hl" : ""}">${esc(w)}</span>`).join(" ")}</div>`;
    return { html, animate: (el) => (t) => {
      el.querySelectorAll(".hw").forEach((s, i) => {
        const q = prog(t, i * 0.07, 0.35);
        s.style.opacity = q; s.style.transform = `translateY(${(1 - q) * 22}px)`;
      });
    } };
  };

  COMPONENTS["ios-notification"] = (p) => ({
    html: `<div class="notif"><div class="notif-icon"></div><div class="notif-body">
      <div class="notif-top"><span class="notif-app">${esc(p.app || "Messages")}</span><span class="notif-time">${esc(p.time || "now")}</span></div>
      <div class="notif-title">${esc(p.title || "")}</div><div class="notif-text">${esc(p.body || "")}</div></div></div>`,
    animate: () => () => {},
  });

  COMPONENTS["ios-message"] = (p) => {
    const bubbles = (p.bubbles || []).slice(0, 3);
    return { html: `<div class="msgs">${bubbles.map((b) =>
      `<div class="bubble ${b.direction === "out" ? "out" : "in"}">${esc(b.text)}</div>`).join("")}</div>`,
      animate: (el) => (t) => {
        el.querySelectorAll(".bubble").forEach((b, i) => {
          const q = prog(t, 0.05 + i * 0.45, 0.35, easeOutBack);
          b.style.opacity = clamp(q * 1.5, 0, 1); b.style.transform = `scale(${0.8 + 0.2 * q})`;
        });
      } };
  };

  COMPONENTS["ios-progress"] = (p) => {
    const from = Number(p.from ?? 0), to = Number(p.to ?? 100), unit = p.unit ?? "%";
    return { html: `<div class="progress"><div class="progress-top"><span class="progress-label">${esc(p.label || "")}</span>
      <span class="progress-value">${from}${esc(unit)}</span></div><div class="progress-track"><div class="progress-fill"></div></div></div>`,
      animate: (el) => {
        const fill = el.querySelector(".progress-fill"), val = el.querySelector(".progress-value");
        return (t) => {
          const q = prog(t, 0.25, 1.0);
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
        return (t) => {
          const q = prog(t, 0.15, 0.9);
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
        return (t) => rows.forEach((r, i) => {
          const q = prog(t, 0.3 + i * stagger, 0.3);
          r.style.opacity = q; r.style.transform = `translateX(${(1 - q) * -18}px)`;
          const mk = r.querySelector(".mk.check"); if (mk) mk.style.transform = `scale(${0.6 + 0.4 * easeOutBack(q)})`;
        });
      } };
  };

  COMPONENTS["ios-toggle"] = (p) => {
    const on = (p.state || "on") === "on";
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
      animate: (el) => (t) => el.querySelectorAll(".tcard-row").forEach((r, i) => { const q = prog(t, 0.3 + i * 0.14, 0.3); r.style.opacity = q; }) };
  };

  COMPONENTS["ios-suggested-edit"] = (p) => ({
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
    const maxWords = spec.captions.max_words_per_line || 4;
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
    if (chunks.length > 1 && chunks[chunks.length - 1].length === 1 && chunks[chunks.length - 2].length < maxWords) {
      chunks[chunks.length - 2].push(chunks.pop()[0]);
    }
    S.chunks = chunks.map((ws, i) => ({
      start: ws[0].start,
      end: i + 1 < chunks.length ? chunks[i + 1][0].start : spec.beat.duration + 1,
      words: ws,
      html: ws.map((w, j) => `<span class="cw${hl.has(norm(w.w)) ? " hl" : ""}" data-i="${j}">${esc(w.w)}</span>`).join(" "),
    }));
    $("captions").innerHTML = S.chunks.map((c, i) => `<div class="cap-line" data-c="${i}">${c.html}</div>`).join("");
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
      return;
    }
    const w = $("widget");
    if (S.widget) {
      const tIn = S.tIn;
      const enter = prog(t, tIn, 0.32, easeOutBack);
      let opacity = clamp(enter * 1.6, 0, 1);
      const exitStart = b.duration - 0.2;
      if (!b.hold_widget && t > exitStart) opacity *= 1 - clamp((t - exitStart) / 0.2, 0, 1);
      w.style.opacity = opacity;
      w.style.transform = `translateY(${(1 - enter) * 28}px) scale(${S.fit * (0.96 + 0.04 * enter)})`;
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
      ln.querySelectorAll(".cw").forEach((sp, j) => {
        sp.classList.toggle("active", j === active);
        sp.classList.toggle("said", j < active);
      });
    });
  }
})();
