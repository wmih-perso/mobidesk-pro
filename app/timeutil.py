"""Heures : la base enregistre en UTC (models._now), l'écran affiche
l'heure de l'ordinateur (Algérie : UTC+1).

- to_local : date de la base → heure affichée.
- to_utc : borne choisie à l'écran (« aujourd'hui à minuit ») → borne à
  comparer aux dates de la base.

Les frais (Expense.expense_date) sont saisis en date locale : ils ne
passent pas par ces conversions.
"""

from __future__ import annotations

import datetime


def to_local(dt: datetime.datetime | None) -> datetime.datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=datetime.timezone.utc).astimezone().replace(tzinfo=None)


def to_utc(dt: datetime.datetime | None) -> datetime.datetime | None:
    if dt is None:
        return None
    return dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)  # naïf = heure locale
