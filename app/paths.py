"""Résolution des chemins de stockage de l'application.

En développement, les données sont conservées dans ``./data`` à la racine
du projet. Une fois transformée en exécutable (PyInstaller), le dossier
d'installation peut être en lecture seule ou remplacé lors d'une mise à
jour : les données sont alors stockées dans le profil utilisateur, pour
qu'elles survivent aux réinstallations.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR_NAME = "MobiDeskPro"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def get_data_dir() -> Path:
    if is_frozen():
        root = Path.home() / "AppData" / "Local" / APP_DIR_NAME / "data"
    else:
        root = Path(__file__).resolve().parent.parent / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_database_path() -> Path:
    return get_data_dir() / "stock.db"
