"""Configuração persistente do PEEK em ~/.config/peek/config.json."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

CONFIG_DIR: Path = Path.home() / ".config" / "peek"
CONFIG_PATH: Path = CONFIG_DIR / "config.json"

VALID_TRIGGER_EDGES: dict[str, int] = {
    "TopRight": 1,
    "TopLeft": 7,
}

DEFAULT_CONFIG: dict[str, Any] = {
    "primary_color": "#cba6f7",
    "bg_color": "#1e1e2e",
    "bg_opacity": 92,
    "trigger_edge": "TopRight",
}

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def _sanitize_hex(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        if _HEX_COLOR_RE.match(candidate):
            if len(candidate) == 4:
                r, g, b = candidate[1], candidate[2], candidate[3]
                return f"#{r}{r}{g}{g}{b}{b}".lower()
            return candidate.lower()
    return fallback


def sanitize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normaliza e valida um dicionário de configuração contra os limites suportados."""
    data = raw if isinstance(raw, dict) else {}

    primary_color = _sanitize_hex(
        data.get("primary_color"), DEFAULT_CONFIG["primary_color"]
    )
    bg_color = _sanitize_hex(
        data.get("bg_color"), DEFAULT_CONFIG["bg_color"]
    )

    try:
        opacity_val = int(data.get("bg_opacity", DEFAULT_CONFIG["bg_opacity"]))
    except (TypeError, ValueError):
        opacity_val = int(DEFAULT_CONFIG["bg_opacity"])
    bg_opacity = max(30, min(100, opacity_val))

    edge_raw = data.get("trigger_edge", DEFAULT_CONFIG["trigger_edge"])
    trigger_edge = (
        edge_raw if isinstance(edge_raw, str) and edge_raw in VALID_TRIGGER_EDGES
        else str(DEFAULT_CONFIG["trigger_edge"])
    )

    return {
        "primary_color": primary_color,
        "bg_color": bg_color,
        "bg_opacity": bg_opacity,
        "trigger_edge": trigger_edge,
    }


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Carrega e sanitiza a configuração do disco com fallback seguro para defaults."""
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return sanitize_config(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return dict(DEFAULT_CONFIG)


def save_config(updates: dict[str, Any], path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Mescla updates à configuração atual, sanitiza e grava atomicamente no disco."""
    current = load_config(path)
    merged = {**current, **updates}
    sanitized = sanitize_config(merged)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sanitized, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return sanitized
