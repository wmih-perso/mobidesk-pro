"""Authentification par mot de passe unique (compte partagé).

Le mot de passe n'est jamais stocké en clair — seulement son hash salé
(SHA-256, sel aléatoire), dans un fichier JSON à côté de stock.db.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path

from app.paths import get_data_dir

DEFAULT_PASSWORD = "mobidesk2026"


def _auth_path() -> Path:
    return get_data_dir() / "auth.json"


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def _ensure_auth_file() -> None:
    """Crée auth.json avec le mot de passe par défaut s'il n'existe pas encore."""
    if not _auth_path().exists():
        set_password(DEFAULT_PASSWORD)


def verify_password(password: str) -> bool:
    _ensure_auth_file()
    data = json.loads(_auth_path().read_text(encoding="utf-8"))
    return _hash_password(password, data["salt"]) == data["hash"]


def set_password(new_password: str) -> None:
    salt = secrets.token_hex(16)
    data = {"salt": salt, "hash": _hash_password(new_password, salt)}
    _auth_path().write_text(json.dumps(data), encoding="utf-8")
