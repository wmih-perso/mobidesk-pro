"""Personnes autorisées à utiliser le bot, et leur rôle.

Fichier JSON sur le VPS (BOT_DATA_DIR/users.json). Les administrateurs
indiqués dans BOT_ADMIN_IDS (configuration) le sont toujours ; les autres
sont ajoutés depuis Telegram (/start → l'administrateur accepte).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

ADMIN = "admin"
EMPLOYEE = "employee"
ROLE_LABELS = {ADMIN: "👑 Admin", EMPLOYEE: "👤 Employé"}


class UserStore:
    def __init__(self, path: Path, fixed_admins: set[int]) -> None:
        self._path = path
        self._fixed_admins = fixed_admins
        self._lock = threading.Lock()
        self._data = {"users": {}, "pending": {}}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        self._data.setdefault("users", {})
        self._data.setdefault("pending", {})

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def role(self, user_id: int) -> str | None:
        if user_id in self._fixed_admins:
            return ADMIN
        user = self._data["users"].get(str(user_id))
        return user["role"] if user else None

    def admins(self) -> set[int]:
        return self._fixed_admins | {
            int(uid) for uid, u in self._data["users"].items() if u["role"] == ADMIN
        }

    def add_pending(self, user_id: int, name: str) -> bool:
        """True si c'est une nouvelle demande (les administrateurs sont prévenus)."""
        with self._lock:
            if str(user_id) in self._data["pending"]:
                return False
            self._data["pending"][str(user_id)] = name
            self._save()
            return True

    def pending_name(self, user_id: int) -> str | None:
        return self._data["pending"].get(str(user_id))

    def authorize(self, user_id: int, name: str, role: str) -> None:
        with self._lock:
            self._data["pending"].pop(str(user_id), None)
            self._data["users"][str(user_id)] = {"name": name, "role": role}
            self._save()

    def refuse(self, user_id: int) -> None:
        with self._lock:
            self._data["pending"].pop(str(user_id), None)
            self._save()

    def remove(self, query: str) -> str | None:
        """Retire une personne (par nom ou identifiant) ; renvoie son nom."""
        query = query.strip().lower()
        with self._lock:
            for uid, user in list(self._data["users"].items()):
                if uid == query or user["name"].lower() == query or query in user["name"].lower():
                    del self._data["users"][uid]
                    self._save()
                    return user["name"]
        return None

    def listing(self) -> list[tuple[int, str, str]]:
        rows = [(uid, "(configuration)", ADMIN) for uid in sorted(self._fixed_admins)]
        rows += [(int(uid), u["name"], u["role"]) for uid, u in self._data["users"].items()
                 if int(uid) not in self._fixed_admins]
        return rows
