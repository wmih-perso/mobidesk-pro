"""Libellés de produits communs (ticket, bénéfices, retours)."""

from __future__ import annotations

import re


def split_models(phone_model: str) -> list[str]:
    """Modèles compatibles d'un produit : le champ texte est découpé sur
    « / », « , » et « ; » (ex. « 9A / 9C » → ["9A", "9C"]), sans doublon."""
    models: list[str] = []
    for part in re.split(r"[/,;]", phone_model or ""):
        part = part.strip()
        if part and part.casefold() not in {m.casefold() for m in models}:
            models.append(part)
    return models


def article_label(
    category: str, phone_model: str, reference: str = "", chosen_model: str = ""
) -> str:
    """« AFF | 9A » si un modèle a été choisi à la vente ; sinon catégorie en
    3 lettres + modèle compatible (ex. « AFF P20 LITE »), ou la référence si
    le modèle est vide."""
    cat3 = (category or "")[:3].upper()
    chosen = (chosen_model or "").strip()
    if chosen:
        return f"{cat3} | {chosen}".strip(" |")
    designation = (phone_model or "").strip() or (reference or "").strip()
    return f"{cat3} {designation}".strip() or "?"
