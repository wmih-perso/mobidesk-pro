"""Chargement et sauvegarde des informations du ticket de caisse."""

from __future__ import annotations

import json

from app.paths import get_data_dir

_CONFIG_FILE = get_data_dir() / "ticket_config.json"

DEFAULTS = {
    "store_name": "MOBISHOP",
    "store_phone": "0558 57 19 32",
    "store_tagline": "Pièces & Réparation Téléphones",
}


def load_ticket_config() -> dict:
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            return {**DEFAULTS, **data}
        except Exception:
            pass
    return dict(DEFAULTS)


def save_ticket_config(cfg: dict) -> None:
    _CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
