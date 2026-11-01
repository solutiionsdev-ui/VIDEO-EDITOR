"""Text helpers that work for English, Arabic and mixed lines."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Arabic diacritics (tashkeel) and the tatweel stretch character.
_TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")

TERMINAL_PUNCT = ".?!؟。…"

FILLERS_EN = {"um", "uh", "uhm", "umm", "erm", "er", "hmm", "hm", "mm", "mhm", "ah", "eh", "uhh", "ahh"}
FILLERS_AR = {"اه", "ااه", "ام", "امم", "اممم", "مم", "ممم", "هم", "اا", "ااا", "ايه"}
FILLERS = FILLERS_EN | FILLERS_AR


def normalize(text: str) -> str:
    """Lowercase, strip punctuation/diacritics, unify Arabic letter variants."""
    text = unicodedata.normalize("NFKC", text)
    text = _TASHKEEL.sub("", text)
    text = (text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
                .replace("ة", "ه").replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي"))
    text = _PUNCT.sub(" ", text.lower())
    return _SPACES.sub(" ", text).strip()


def tokens(text: str) -> list[str]:
    return normalize(text).split()


def is_filler(word: str) -> bool:
    return normalize(word) in FILLERS


def is_arabic(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    arabic = sum(1 for c in letters if "؀" <= c <= "ۿ")
    return arabic > len(letters) / 2


def similarity(a: list[str], b: list[str]) -> float:
    """0..1 how alike two token lists are (order-aware)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def prefix_overlap(short: list[str], long: list[str]) -> float:
    """How much of `short` matches the start of `long` (a false start, then the full line)."""
    if not short or not long:
        return 0.0
    head = long[:len(short) + 2]
    return SequenceMatcher(None, short, head, autojunk=False).ratio()


def ends_sentence(text: str) -> bool:
    t = text.rstrip()
    return bool(t) and t[-1] in TERMINAL_PUNCT


def looks_cut_off(text: str) -> bool:
    t = text.rstrip()
    return t.endswith(("-", "—", "–", ",", "،")) or (bool(t) and not ends_sentence(t) and len(t.split()) <= 4)


def stumbles(words: list[str]) -> int:
    """Immediate word repeats ("I I think") and one-letter fragments."""
    norm = [normalize(w) for w in words]
    count = 0
    for i in range(1, len(norm)):
        if norm[i] and norm[i] == norm[i - 1] and norm[i] not in FILLERS:
            count += 1
    count += sum(1 for n in norm if len(n) == 1 and n.isalpha() and n not in {"a", "i", "و", "ب", "ل", "ف", "ك"})
    return count


def preview(text: str, n: int = 60) -> str:
    text = _SPACES.sub(" ", text.strip())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"
