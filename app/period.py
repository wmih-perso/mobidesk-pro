"""Calcul des bornes de dates pour les filtres de période (jour/semaine/mois)."""

from __future__ import annotations

import datetime

PERIOD_CHOICES = ["Aujourd'hui", "Cette semaine", "Ce mois-ci", "Tout", "Plage personnalisée"]


def bounds_for_period(
    period: str, *, now: datetime.datetime | None = None
) -> tuple[datetime.datetime | None, datetime.datetime | None]:
    """Renvoie (start, end) pour le libellé de période donné.

    `end` vaut toujours None (borne haute = maintenant) sauf pour "Tout" et
    "Plage personnalisée", qui n'ont pas de borne calculable ici — la plage
    personnalisée est résolue séparément côté UI à partir des sélecteurs de
    date.
    """
    now = now or datetime.datetime.now()

    if period == "Aujourd'hui":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, None

    if period == "Cette semaine":
        start = (now - datetime.timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start, None

    if period == "Ce mois-ci":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, None

    return None, None
