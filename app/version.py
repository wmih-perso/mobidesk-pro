"""Version de l'application et configuration des mises à jour GitHub.

Synchronisée manuellement avec `pyproject.toml` à chaque release —
PyInstaller n'embarque pas pyproject.toml, donc importlib.metadata ne
fonctionne pas de façon fiable sur l'exe distribué.
"""

from __future__ import annotations

APP_VERSION = "0.1.4"

GITHUB_OWNER = "wmih-perso"
GITHUB_REPO = "mobidesk-pro"
ASSET_NAME = "MobiDeskPro.exe"


def parse_version(value: str) -> tuple[int, int, int]:
    """Parse « v1.2.3 » ou « 1.2.3 » en (1, 2, 3). Lève ValueError si invalide."""
    cleaned = value.strip().lstrip("vV")
    parts = cleaned.split(".")
    if len(parts) != 3:
        raise ValueError(f"Format de version invalide : {value!r}")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def is_newer(remote: str, local: str = APP_VERSION) -> bool:
    return parse_version(remote) > parse_version(local)
