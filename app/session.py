"""Session utilisateur courant — singleton module-level."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UserSession:
    id: int
    username: str
    display_name: str
    role: str  # "admin" | "cashier"


_current: UserSession | None = None


def set_current_user(user: UserSession) -> None:
    global _current
    _current = user


def get_current_user() -> UserSession | None:
    return _current


def get_current_cashier_name() -> str:
    return _current.display_name if _current else ""


def is_admin() -> bool:
    return _current is not None and _current.role == "admin"


def clear() -> None:
    global _current
    _current = None
