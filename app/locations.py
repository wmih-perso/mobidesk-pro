"""Emplacement physique des pièces : type de rangement + numéro.

Les afficheurs sont rangés dans des boîtes, les batteries dans des
compartiments, les nappes dans des tiroirs… L'emplacement s'affiche dans
le stock et la vente pour savoir où prendre la pièce, mais n'est jamais
utilisé par la recherche (taper « A22 » ne doit pas trouver la boîte A22).
"""

from __future__ import annotations

import re

# (type enregistré, libellé court affiché dans les tableaux)
LOCATION_KINDS = [
    ("Boîte", "Boîte"),
    ("Compartiment", "Comp."),
    ("Tiroir", "Tiroir"),
    ("Chariot", "Chariot"),
]
_SHORT = dict(LOCATION_KINDS)
_ORDER = {kind: index for index, (kind, _short) in enumerate(LOCATION_KINDS)}

# Type proposé à la création d'un produit, selon le début de sa catégorie.
_SUGGESTED = (
    ("AFF", "Boîte"),
    ("LCD", "Boîte"),
    ("BAT", "Compartiment"),
    ("NAP", "Tiroir"),
)


def location_label(kind: str, number: str) -> str:
    """« Boîte 7 », « Comp. 3 »… ; vide si aucun emplacement."""
    kind, number = (kind or "").strip(), (number or "").strip()
    if not kind and not number:
        return ""
    return f"{_SHORT.get(kind, kind)} {number}".strip()


def location_sort_key(kind: str, number: str) -> tuple:
    """Boîtes 1, 2, 10… puis compartiments, tiroirs, chariots ; sans
    emplacement à la fin."""
    kind, number = (kind or "").strip(), (number or "").strip()
    if not kind and not number:
        return (1, 0, 0, "")
    match = re.match(r"\d+", number)
    return (0, _ORDER.get(kind, len(_ORDER)), int(match.group()) if match else 0, number.lower())


def suggested_kind(category: str) -> str:
    name = (category or "").strip().upper()
    for prefix, kind in _SUGGESTED:
        if name.startswith(prefix):
            return kind
    return ""
