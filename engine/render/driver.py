"""Drives renderer/page.html in headless Chromium: one beat at a time, any frame on demand.

The same page produces storyboard stills and every frame of the final video,
so what the client approves on the beat board is what gets rendered.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..job import JobError
from ..profile import ROOT

RENDERER_DIR = ROOT / "renderer"
STYLES_DIR = ROOT / "styles"
WIDTH, HEIGHT, FPS = 1080, 1920, 30
SAFE = {"top": 0.07, "bottom": 0.20, "side": 0.06}   # Instagram UI keeps the bottom fifth and the edges busy

_WEIGHTS = {"thin": 100, "extralight": 200, "light": 300, "regular": 400, "medium": 500,
            "semibold": 600, "bold": 700, "extrabold": 800, "black": 900}


class Renderer:
    """A headless browser page showing renderer/page.html. Use as a context manager."""

    def __init__(self, width: int = WIDTH, height: int = HEIGHT):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover
            raise JobError("Playwright is not installed. Run ./reel doctor.") from e
        self.width, self.height = width, height
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            self._pw.stop()
            raise JobError("Chromium for Playwright is missing. Run: .venv/bin/playwright install chromium") from e
        self._page = self._browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        self._page.goto((RENDERER_DIR / "page.html").as_uri())
        self._page.wait_for_load_state("load")

    def __enter__(self) -> "Renderer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._browser.close()
        finally:
            self._pw.stop()

    def setup(self, spec: dict) -> dict:
        return self._page.evaluate("spec => window.setup(spec)", spec)

    def seek(self, t: float, footage: Path | None) -> None:
        url = footage.resolve().as_uri() if footage else None
        self._page.evaluate("([t, u]) => window.seek(t, u)", [float(t), url])

    def screenshot(self, out: Path, quality: int = 92) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        self._page.screenshot(path=str(out), type="jpeg", quality=quality,
                              clip={"x": 0, "y": 0, "width": self.width, "height": self.height})

    def frame(self, t: float, footage: Path | None, out: Path, quality: int = 92) -> None:
        self.seek(t, footage)
        self.screenshot(out, quality)

    def boxes(self, t: float) -> dict:
        """Element boxes [x, y, w, h] at time t, without rendering a frame."""
        self._page.evaluate("t => window.seek(t, null)", float(t))
        return self._page.evaluate("() => window.boxes()")


# -- spec --------------------------------------------------------------------

def brand_spec(profile: dict) -> dict:
    """Colours, fonts and logos from the client profile, as the page needs them."""
    brand = profile.get("brand", {}) or {}
    colours = brand.get("colours", {}) or {}
    client_dir = Path(profile["_path"]).parent if profile.get("_path") else None

    def asset(rel: str | None) -> str | None:
        if not rel or not client_dir:
            return None
        p = client_dir / rel
        return p.resolve().as_uri() if p.is_file() else None

    font_file = (brand.get("fonts", {}).get("primary") or {}).get("file")
    family = str(profile.get("captions", {}).get("font") or "")
    faces = []
    if font_file and client_dir and (client_dir / font_file).is_file():
        primary = client_dir / font_file
        stem_family = re.split(r"[-_]", primary.stem)[0]
        family = family or stem_family
        for p in sorted(primary.parent.glob(f"{stem_family}-*.[ot]tf")):
            weight_name = re.split(r"[-_]", p.stem, maxsplit=1)[-1].lower().replace("italic", "")
            weight = _WEIGHTS.get(weight_name)
            if weight and "italic" not in p.stem.lower():
                faces.append({"url": p.resolve().as_uri(), "weight": weight})
        if not faces:
            faces.append({"url": primary.resolve().as_uri(), "weight": 600})
    return {
        "primary": str(colours.get("primary") or "#66023C"),
        "secondary": str(colours.get("secondary") or "#0f1a3a"),
        "accent": str(colours.get("accent") or "#ffffff"),
        "font": {"family": family or "Poppins", "faces": faces},
        "logo": asset(brand.get("logo")),
        "logo_dark": asset(brand.get("logo_dark")),
        "url": str(brand.get("url") or ""),
    }


def asset_src(props: dict, job_dir: Path | None, profile: dict) -> dict:
    """Where an asset component's file is (jobs/<job>/assets/ first, then the client's assets/), as the page needs it."""
    name = str(props.get("file") or "")
    dirs = [Path(job_dir) / "assets"] if job_dir else []
    if profile.get("_path"):
        dirs.append(Path(profile["_path"]).parent / "assets")
    for d in dirs:
        f = d / name
        if name and "/" not in name and f.is_file():
            st = f.stat()
            return {"src": f.resolve().as_uri(), "stamp": f"{st.st_size}-{int(st.st_mtime)}"}
    return {"src": "", "stamp": "missing"}


def layout_kind(style: dict, layout: str) -> str:
    """How a layout uses the footage: "full" frame, in a "card", or not at all ("stage")."""
    L = (style.get("layouts") or {}).get(layout) or {}
    return str(L.get("kind") or ("card" if L.get("footage_card") else "full"))


def neighbours(beats: list[dict], i: int, end_card: bool = False) -> tuple[str | None, str | None]:
    """The layouts just before and just after beat i, for transitions that depend on a layout change."""
    prev = beats[i - 1]["layout"] if i > 0 else None
    nxt = beats[i + 1]["layout"] if i + 1 < len(beats) else ("logo-end-card" if end_card else None)
    return prev, nxt


def beat_spec(beat: dict, profile: dict, style: dict, *, is_first: bool, is_last: bool, job_dir: Path | None = None,
              prev_layout: str | None = None, next_layout: str | None = None) -> dict:
    """Everything the page needs to render one beat, with times relative to the beat start."""
    caps = profile.get("captions", {}) or {}
    style_js = STYLES_DIR / style["name"] / "components.js"
    flare = str((style.get("motion") or {}).get("layout_switch") or "").startswith("flare")
    graphic = beat.get("graphic")
    if graphic and (graphic.get("props") or {}).get("file"):     # one of the client's own visuals, by file name
        props = graphic.get("props") or {}
        graphic = {**graphic, "props": {**props, **asset_src(props, job_dir, profile)}}
    words: list[dict] = []
    for w in beat.get("words", []):
        token = {"w": w["w"], "start": round(w["start"] - beat["start"], 3), "end": round(w["end"] - beat["start"], 3)}
        # Whisper emits "%" "," "." as their own words; glue them to the word before for the captions.
        if words and not re.search(r"[\w]", token["w"], re.UNICODE):
            words[-1]["w"] += token["w"]
            words[-1]["end"] = token["end"]
        else:
            words.append(token)
    return {
        "width": WIDTH, "height": HEIGHT, "fps": FPS,
        "style": {"name": style["name"], "layouts": style.get("layouts", {}), "motion_first_beat_delay": 0.4},
        "style_css": (STYLES_DIR / style["name"] / "components.css").resolve().as_uri(),
        "style_js": style_js.resolve().as_uri() if style_js.is_file() else None,
        "brand": brand_spec(profile),
        "captions": {
            "size_px": int(caps.get("size_px", 64)),
            "max_words_per_line": int(caps.get("max_words_per_line", 4)),
            "colour": str(caps.get("colour") or "#ffffff"),
            "active_word_colour": str(caps.get("active_word_colour") or "#66023C"),
        },
        "safe": SAFE,
        "beat": {
            "id": beat["id"], "layout": beat["layout"], "graphic": graphic,
            "caption": beat.get("caption") or {"style": "normal", "highlight": []},
            "duration": float(beat["duration"]), "words": words, "no_go": beat.get("no_go"),
            "is_first": is_first, "is_last": is_last, "hold_widget": False,
            "render": beat.get("render") or {},     # quality-check overrides: anchor, shrink
            "t0": round(float(beat.get("start") or 0.0), 3),   # where the beat sits in the reel, so a style's background moves on across beats
            "flare_in": bool(flare and prev_layout is not None and prev_layout != beat["layout"]),
            "flare_out": bool(flare and next_layout is not None and next_layout != beat["layout"]),
        },
    }


def end_card_spec(profile: dict, style: dict, duration: float, prev_layout: str | None = None) -> dict:
    cta = str(profile.get("format", {}).get("cta") or "")
    spec = beat_spec({"id": 0, "layout": "logo-end-card", "graphic": {"component": "logo-end-card", "props": {"cta": cta}, "anchor": "top"},
                      "start": 0.0, "duration": duration, "words": [], "no_go": None,
                      "caption": {"style": "normal", "highlight": []}}, profile, style, is_first=False, is_last=True,
                     prev_layout=prev_layout)
    return spec


def widget_in_time(spec: dict) -> float:
    return spec["style"]["motion_first_beat_delay"] if spec["beat"]["is_first"] else 0.1


def still_time(spec: dict) -> float:
    """When to take the storyboard still: the widget is fully in and some captions are showing."""
    d = spec["beat"]["duration"]
    t = widget_in_time(spec) + 1.3
    return round(min(max(0.5, t), max(0.3, d * 0.65)), 3)
