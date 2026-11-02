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


def beat_spec(beat: dict, profile: dict, style: dict, *, is_first: bool, is_last: bool) -> dict:
    """Everything the page needs to render one beat, with times relative to the beat start."""
    caps = profile.get("captions", {}) or {}
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
        "style": {"layouts": style.get("layouts", {}), "motion_first_beat_delay": 0.4},
        "style_css": (STYLES_DIR / style["name"] / "components.css").resolve().as_uri(),
        "brand": brand_spec(profile),
        "captions": {
            "size_px": int(caps.get("size_px", 64)),
            "max_words_per_line": int(caps.get("max_words_per_line", 4)),
            "colour": str(caps.get("colour") or "#ffffff"),
            "active_word_colour": str(caps.get("active_word_colour") or "#66023C"),
        },
        "safe": SAFE,
        "beat": {
            "id": beat["id"], "layout": beat["layout"], "graphic": beat.get("graphic"),
            "caption": beat.get("caption") or {"style": "normal", "highlight": []},
            "duration": float(beat["duration"]), "words": words, "no_go": beat.get("no_go"),
            "is_first": is_first, "is_last": is_last, "hold_widget": False,
            "render": beat.get("render") or {},     # quality-check overrides: anchor, shrink
        },
    }


def end_card_spec(profile: dict, style: dict, duration: float) -> dict:
    cta = str(profile.get("format", {}).get("cta") or "")
    spec = beat_spec({"id": 0, "layout": "logo-end-card", "graphic": {"component": "logo-end-card", "props": {"cta": cta}, "anchor": "top"},
                      "start": 0.0, "duration": duration, "words": [], "no_go": None,
                      "caption": {"style": "normal", "highlight": []}}, profile, style, is_first=False, is_last=True)
    return spec


def widget_in_time(spec: dict) -> float:
    return spec["style"]["motion_first_beat_delay"] if spec["beat"]["is_first"] else 0.1


def still_time(spec: dict) -> float:
    """When to take the storyboard still: the widget is fully in and some captions are showing."""
    d = spec["beat"]["duration"]
    t = widget_in_time(spec) + 1.3
    return round(min(max(0.5, t), max(0.3, d * 0.65)), 3)
