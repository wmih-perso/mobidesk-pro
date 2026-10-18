"""Réglages de l'imprimante des tickets (Paramètres > Imprimante).

Un fichier JSON par PC, à côté de stock.db (comme ticket_config.json) :
conservé lors des mises à jour.
"""

from __future__ import annotations

import json

from app.paths import get_data_dir

_CONFIG_FILE = get_data_dir() / "printer_config.json"

# Tailles d'étiquettes courantes pour la Niimbot B1 : (largeur, hauteur) en mm.
LABEL_PRESETS = [(50, 30), (50, 50), (50, 80), (40, 30), (40, 60)]

DEFAULTS = {
    "printer_name": "",  # vide = imprimante par défaut de Windows
    "label_width_mm": 50,
    "label_height_mm": 30,
    "copies": 1,
    "direct": True,  # imprimer sans la fenêtre de choix de Windows
}


def load_printer_config() -> dict:
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            return {**DEFAULTS, **data}
        except Exception:
            pass
    return dict(DEFAULTS)


def save_printer_config(cfg: dict) -> None:
    _CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
