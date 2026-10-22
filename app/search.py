"""Recherche par mots, commune à toute l'application.

Le texte tapé est découpé en mots ; un élément correspond s'il contient
TOUS les mots, n'importe où et dans n'importe quel ordre — « samsung a17 »,
« a17 samsung » et « sam a17 » trouvent « Samsung Galaxy A17 ». Majuscules,
accents et séparateurs (- _ . / |) sont ignorés : « aff a17 » trouve la
référence « AFF-A17 », « ecran » trouve « Écran ». Pas de tolérance aux
fautes de frappe : chaque mot doit être bien écrit (même incomplet).
"""

from __future__ import annotations

import re
import unicodedata

_SEPARATORS = re.compile(r"[\s\-_./|·,;:]+")


def normalize(text: str) -> str:
    """Minuscules, sans accents, séparateurs remplacés par des espaces."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " " + _SEPARATORS.sub(" ", text.lower()).strip() + " "


def search_words(query: str) -> list[str]:
    return normalize(query).split()


def matches(query: str, *fields: str) -> bool:
    """Tous les mots de `query` se trouvent dans l'un des `fields` réunis.
    Une recherche vide correspond à tout."""
    words = search_words(query)
    if not words:
        return True
    haystack = normalize(" ".join(field or "" for field in fields))
    return all(word in haystack for word in words)


def rank(query: str, *fields: str) -> int:
    """Pertinence (plus petit = meilleur) : nombre de mots qui ne sont PAS
    au début d'un mot du texte — « a17 » classe « Galaxy A17 » avant « A170 »
    seulement à égalité, mais « Galaxy A17 » avant « TA17 »."""
    haystack = normalize(" ".join(field or "" for field in fields))
    score = 0
    for word in search_words(query):
        if f" {word} " in haystack:
            continue  # mot entier
        score += 1 if f" {word}" in haystack else 2
    return score


def filter_and_rank(query: str, items: list, fields) -> list:
    """Éléments de `items` qui correspondent à `query`, les plus pertinents
    d'abord (ordre d'origine conservé à égalité). `fields(item)` renvoie
    les textes où chercher."""
    if not search_words(query):
        return list(items)
    found = [item for item in items if matches(query, *fields(item))]
    return sorted(found, key=lambda item: rank(query, *fields(item)))
