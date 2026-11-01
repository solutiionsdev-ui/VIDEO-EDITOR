"""Client profiles: one YAML per client, merged over the engine defaults."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from .job import JobError

ROOT = Path(__file__).resolve().parent.parent
CLIENTS_DIR = ROOT / "clients"
DEFAULTS_FILE = Path(__file__).resolve().parent / "defaults.yaml"


def load_defaults() -> dict[str, Any]:
    with DEFAULTS_FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_profile_path(client: str | None) -> Path | None:
    """Accepts a client slug (clients/<slug>/profile.yaml) or a path to a YAML file."""
    if not client:
        return None
    p = Path(client)
    if p.is_file():
        return p.resolve()
    if p.is_dir() and (p / "profile.yaml").is_file():
        return (p / "profile.yaml").resolve()
    candidate = CLIENTS_DIR / client / "profile.yaml"
    if candidate.is_file():
        return candidate.resolve()
    known = sorted(d.name for d in CLIENTS_DIR.iterdir() if d.is_dir() and not d.name.startswith("_"))
    raise JobError(f"No client profile for '{client}'. Known clients: {', '.join(known) or 'none'}")


def load_profile(client: str | None) -> dict[str, Any]:
    """Defaults, with the client's values layered on top. Works with no client too."""
    merged = load_defaults()
    path = resolve_profile_path(client)
    if path is None:
        merged["_path"] = None
        merged.setdefault("client", {})["slug"] = None
        return merged
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    merged = deep_merge(merged, data)
    merged["_path"] = str(path)
    merged.setdefault("client", {}).setdefault("slug", path.parent.name)
    return merged


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def get(profile: dict[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = profile
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
