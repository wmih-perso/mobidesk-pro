"""Conversion entre dinars (affichage) et centimes (stockage)."""

from __future__ import annotations


def cents_to_da(cents: int) -> float:
    return cents / 100


def da_to_cents(amount_da: float) -> int:
    return round(amount_da * 100)


def format_da(cents: int) -> str:
    return f"{round(cents_to_da(cents)):,} DA".replace(",", " ")


def format_balance(cents: int) -> str:
    """Solde avec signe pour l'écran : -10 DA (dette), +10 DA (crédit), 0 DA (soldé).

    Convention DB : balance_cents > 0 = dette, < 0 = crédit.
    Convention affichage : dette = négatif, crédit = positif.
    """
    if cents == 0:
        return "0 DA"
    sign = "-" if cents > 0 else "+"
    num = f"{round(abs(cents) / 100):,}".replace(",", " ")
    return f"{sign} {num} DA"


def format_balance_ticket(cents: int) -> str:
    """Solde pour ticket papier : nombre brut + label (dette)/(avoir)/✓."""
    num = f"{round(abs(cents) / 100):,}".replace(",", " ")
    if cents > 0:
        return f"{num} DA  (dette)"
    if cents < 0:
        return f"{num} DA  (avoir)"
    return "0 DA  ✓"
