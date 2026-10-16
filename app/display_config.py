"""Préférences d'affichage (taille du texte / zoom de l'interface).

Stockées dans un petit JSON à côté de stock.db, comme ticket_config.json.
Le zoom est appliqué au démarrage via QT_SCALE_FACTOR (voir main.py) : il
agrandit proportionnellement tout l'écran (textes, boutons, tableaux,
fenêtres), y compris les tailles en pixels écrites en dur dans le QSS et
dans le code — d'où un redémarrage nécessaire pour changer de taille.
"""

from __future__ import annotations

import json

from app.paths import get_data_dir

_CONFIG_FILE = get_data_dir() / "display_config.json"

# (libellé affiché, facteur de zoom)
SCALE_CHOICES = [
    ("Normal (100 %)", 1.0),
    ("Grand (115 %)", 1.15),
    ("Très grand (130 %)", 1.3),
]
# Pas au-delà de 130 % : la barre de navigation ne tiendrait plus sur un
# écran de 1920 px de large.

DEFAULT_SCALE = 1.15


def load_ui_scale() -> float:
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            scale = float(data["ui_scale"])
            if any(abs(scale - value) < 0.001 for _label, value in SCALE_CHOICES):
                return scale
        except Exception:
            pass
    return DEFAULT_SCALE


def save_ui_scale(scale: float) -> None:
    _CONFIG_FILE.write_text(json.dumps({"ui_scale": scale}, indent=2), encoding="utf-8")
