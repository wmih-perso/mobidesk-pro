"""Version de l'application et configuration des mises à jour GitHub.

Synchronisée manuellement avec `pyproject.toml` à chaque release —
PyInstaller n'embarque pas pyproject.toml, donc importlib.metadata ne
fonctionne pas de façon fiable sur l'exe distribué.
"""

from __future__ import annotations

APP_VERSION = "1.0.1"

GITHUB_OWNER = "wmih-perso"
GITHUB_REPO = "mobidesk-pro"
ASSET_NAME = "MobiDeskPro.exe"


def parse_version(value: str) -> tuple[int, int, int]:
    """Parse « v1.2.3 », « 1.2.3 », « v1.0 » ou « v1 » en un triplet (majeur,
    mineur, patch) — les segments manquants sont complétés par des zéros
    (« v1.0 » devient (1, 0, 0)), pour tolérer des tags moins stricts que
    le format x.y.z habituel. Lève ValueError si invalide."""
    cleaned = value.strip().lstrip("vV")
    parts = cleaned.split(".")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"Format de version invalide : {value!r}")
    numbers = [int(part) for part in parts]
    numbers += [0] * (3 - len(numbers))
    return tuple(numbers)  # type: ignore[return-value]


def is_newer(remote: str, local: str = APP_VERSION) -> bool:
    return parse_version(remote) > parse_version(local)
