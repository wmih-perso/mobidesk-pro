"""Libellés de produits communs (ticket, bénéfices, retours)."""

from __future__ import annotations


def article_label(category: str, phone_model: str, reference: str = "") -> str:
    """Catégorie en 3 lettres + modèle compatible (ex. « AFF P20 LITE ») ;
    la référence si le modèle est vide."""
    cat3 = (category or "")[:3].upper()
    designation = (phone_model or "").strip() or (reference or "").strip()
    return f"{cat3} {designation}".strip() or "?"
