"""Réglages de l'imprimante des tickets (Paramètres > Imprimante).

Un fichier JSON par PC, à côté de stock.db (comme ticket_config.json) :
conservé lors des mises à jour.
"""

from __future__ import annotations

import json

from app.paths import get_data_dir

_CONFIG_FILE = get_data_dir() / "printer_config.json"

# Type de papier :
# - "label" : étiquette (ex. Niimbot B1), le ticket est réduit pour y tenir ;
# - "sheet" : feuille A4 (imprimante de bureau), ticket à taille réelle en
#   haut de la feuille, entouré d'un pointillé pour le découper ;
# - "roll"  : rouleau de ticket de caisse (imprimante thermique), page aussi
#   longue que le ticket.
PAPER_LABEL, PAPER_SHEET, PAPER_ROLL = "label", "sheet", "roll"

# Tailles d'étiquettes courantes pour la Niimbot B1 : (largeur, hauteur) en mm.
LABEL_PRESETS = [(50, 30), (50, 50), (50, 80), (40, 30), (40, 60)]

# Largeurs de ticket de caisse standard (rouleau, ou ticket sur feuille A4).
TICKET_WIDTHS = [80, 58]

DEFAULTS = {
    "printer_name": "",  # vide = imprimante par défaut de Windows
    "paper": PAPER_LABEL,
    "ticket_width_mm": 80,
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


def paper_description(cfg: dict) -> str:
    paper = cfg.get("paper", PAPER_LABEL)
    if paper == PAPER_SHEET:
        return f"Ticket {cfg['ticket_width_mm']} mm sur feuille A4 (à découper)"
    if paper == PAPER_ROLL:
        return f"Rouleau de ticket {cfg['ticket_width_mm']} mm"
    return f"Étiquette {cfg['label_width_mm']} × {cfg['label_height_mm']} mm"


def save_printer_config(cfg: dict) -> None:
    _CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
