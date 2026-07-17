"""Conversion entre dinars (affichage) et centimes (stockage)."""

from __future__ import annotations


def cents_to_da(cents: int) -> float:
    return cents / 100


def da_to_cents(amount_da: float) -> int:
    return round(amount_da * 100)


def format_da(cents: int) -> str:
    return f"{round(cents_to_da(cents)):,} DA".replace(",", " ")
