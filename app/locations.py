"""Emplacement physique des pièces : code libre (B32, C5, T2…).

Les afficheurs sont rangés dans des boîtes, les batteries dans des
compartiments, les nappes dans des tiroirs… L'emplacement s'affiche dans
le stock et la vente pour savoir où prendre la pièce, mais n'est jamais
utilisé par la recherche (taper « B32 » ne doit pas trouver la boîte B32).

La colonne displays.location_kind (type choisi dans une liste, v2.4.19)
n'est plus utilisée : seul location_number compte.
"""

from __future__ import annotations

import re


def normalize_location(code: str) -> str:
    """« b32 » → « B32 » : même écriture partout (affichage et tri)."""
    return (code or "").strip().upper()


def location_label(code: str) -> str:
    return normalize_location(code)


def location_sort_key(code: str) -> tuple:
    """Ordre naturel : B2, B10, B32, C1, C5, T2… ; sans emplacement à la fin."""
    code = normalize_location(code)
    if not code:
        return (1, ())
    parts = tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in re.findall(r"\d+|\D+", code)
    )
    return (0, parts)
