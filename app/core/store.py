"""Кэш «последнего корректного снимка» (last known good) — data/cache.json."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "data" / "cache.json"


def load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(snap: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, PATH)
