"""Vérification et application des mises à jour via GitHub Releases.

Windows verrouille un .exe pendant son exécution — un programme ne peut
pas se remplacer lui-même. Le mécanisme retenu ici : télécharger le
nouvel exe sous un nom temporaire, puis lancer un script .bat externe et
détaché qui attend la fermeture du process principal, remplace l'exe,
relance l'app, et s'auto-supprime.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.version import ASSET_NAME, GITHUB_OWNER, GITHUB_REPO, APP_VERSION, is_newer

API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
CHECK_TIMEOUT = 5
DOWNLOAD_TIMEOUT = 30
CHUNK_SIZE = 65536

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


class UpdateCheckError(Exception):
    """Erreur réseau, timeout, ou réponse GitHub invalide/inattendue."""


@dataclass
class UpdateInfo:
    version: str
    tag_name: str
    download_url: str
    notes: str = ""


def check_for_update() -> UpdateInfo | None:
    """Renvoie les infos de la dernière release si plus récente, sinon None.

    Lève UpdateCheckError en cas de problème réseau/parsing — à catcher
    par l'appelant pour afficher un message clair plutôt qu'un crash.
    """
    request = urllib.request.Request(
        API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "MobiDeskPro-Updater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=CHECK_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise UpdateCheckError(
            "Impossible de contacter GitHub (pas de connexion internet ?)."
        ) from error
    except (TimeoutError, json.JSONDecodeError) as error:
        raise UpdateCheckError(
            "Réponse invalide ou délai dépassé lors de la vérification."
        ) from error

    tag_name = payload.get("tag_name", "")
    if not tag_name:
        raise UpdateCheckError("Réponse GitHub inattendue (tag manquant).")

    download_url = next(
        (
            asset["browser_download_url"]
            for asset in payload.get("assets", [])
            if asset.get("name") == ASSET_NAME
        ),
        None,
    )
    if download_url is None:
        raise UpdateCheckError(f"Aucun exécutable « {ASSET_NAME} » trouvé dans la dernière release.")

    try:
        newer = is_newer(tag_name, APP_VERSION)
    except ValueError as error:
        raise UpdateCheckError(f"Version distante invalide : {tag_name!r}.") from error

    if not newer:
        return None

    return UpdateInfo(
        version=tag_name.strip().lstrip("vV"),
        tag_name=tag_name,
        download_url=download_url,
        notes=payload.get("body", ""),
    )


def download_update(
    info: UpdateInfo, progress_callback: Callable[[int, int], None] | None = None
) -> Path:
    """Télécharge le nouvel exe dans un dossier temporaire, renvoie son chemin."""
    destination = Path(tempfile.gettempdir()) / "MobiDeskPro_update" / ASSET_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)

    request = urllib.request.Request(
        info.download_url, headers={"User-Agent": "MobiDeskPro-Updater"}
    )
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
        total = int(response.headers.get("Content-Length", 0))
        read = 0
        with open(destination, "wb") as file:
            while chunk := response.read(CHUNK_SIZE):
                file.write(chunk)
                read += len(chunk)
                if progress_callback is not None:
                    progress_callback(read, total)

    return destination


def build_swap_script(new_exe: Path, current_exe: Path, pid: int) -> Path:
    """Génère un .bat qui attend la fin du process courant, remplace l'exe,
    relance l'app, puis s'auto-supprime. Renvoie le chemin du .bat.
    """
    script_dir = Path(tempfile.gettempdir()) / "MobiDeskPro_update"
    script_dir.mkdir(parents=True, exist_ok=True)
    bat_path = script_dir / "apply_update.bat"

    bat_content = f"""@echo off
setlocal
set "NEWEXE={new_exe}"
set "CUREXE={current_exe}"
set "PIDTOWAIT={pid}"

:waitloop
tasklist /FI "PID eq %PIDTOWAIT%" | find "%PIDTOWAIT%" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto waitloop
)

timeout /t 1 /nobreak >nul
move /Y "%CUREXE%" "%CUREXE%.old" >nul
move /Y "%NEWEXE%" "%CUREXE%" >nul
del /Q "%CUREXE%.old" >nul 2>&1

start "" "%CUREXE%"

(goto) 2>nul & del "%~f0"
"""
    bat_path.write_text(bat_content, encoding="utf-8")
    return bat_path


def launch_swap_and_exit(bat_path: Path) -> None:
    """Lance le script de remplacement sans fenêtre visible, détaché du
    process courant. L'appelant doit quitter l'app immédiatement après.
    """
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat_path)],
        creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
        close_fds=True,
    )
