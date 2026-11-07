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


def _glued_word_matches(word: str, haystack: str) -> bool:
    """Mot tapé sans espace (« affa76 ») : vrai s'il se découpe en morceaux
    qui commencent chacun un mot du texte (« aff » + « a76 »)."""
    if len(word) < 4:
        return False
    reachable = [True] + [False] * len(word)
    for start in range(len(word)):
        if not reachable[start]:
            continue
        for end in range(start + 2, len(word) + 1):
            if not reachable[end] and f" {word[start:end]}" in haystack:
                reachable[end] = True
    return reachable[-1]


def _word_matches(word: str, haystack: str) -> bool:
    return word in haystack or _glued_word_matches(word, haystack)


def matches(query: str, *fields: str) -> bool:
    """Tous les mots de `query` se trouvent dans l'un des `fields` réunis.
    Une recherche vide correspond à tout."""
    return _matches_words(search_words(query), fields)


def _matches_words(words: list[str], fields) -> bool:
    if not words:
        return True
    haystack = normalize(" ".join(field or "" for field in fields))
    return all(_word_matches(word, haystack) for word in words)


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


def filter_and_rank(query: str, items: list, fields, optional_words=frozenset()) -> list:
    """Éléments de `items` qui correspondent à `query`, les plus pertinents
    d'abord (ordre d'origine conservé à égalité). `fields(item)` renvoie
    les textes où chercher.

    `optional_words` (ex. les marques, déjà normalisées) : si la recherche
    en contient, on ajoute après les résultats exacts ceux qui
    correspondent sans ces mots — « aff oppo a76 » trouve aussi l'afficheur
    « REALME 8I 9I A76 A96 », commun aux deux marques."""
    words = search_words(query)
    if not words:
        return list(items)
    found = [item for item in items if _matches_words(words, fields(item))]
    found.sort(key=lambda item: rank(query, *fields(item)))
    remaining = [word for word in words if word not in optional_words]
    if remaining and len(remaining) < len(words):
        exact = {id(item) for item in found}
        extra = [
            item for item in items
            if id(item) not in exact and _matches_words(remaining, fields(item))
        ]
        reduced = " ".join(remaining)
        extra.sort(key=lambda item: rank(reduced, *fields(item)))
        found += extra
    return found


def brand_words(brands) -> frozenset[str]:
    """Mots des noms de marque, au format de la recherche (pour `optional_words`)."""
    return frozenset(word for brand in brands for word in search_words(brand))
