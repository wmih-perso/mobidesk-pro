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

    Le remplacement retente plusieurs fois avant d'abandonner : même après
    la disparition du PID de `tasklist`, Windows peut garder le fichier
    verrouillé une fraction de seconde de plus (flush disque, antivirus
    scannant l'exe fraîchement écrit). Toute erreur est journalisée dans
    update_log.txt (à côté du script) pour pouvoir diagnostiquer un échec
    silencieux — les versions précédentes redirigeaient tout vers `nul`.
    """
    script_dir = Path(tempfile.gettempdir()) / "MobiDeskPro_update"
    script_dir.mkdir(parents=True, exist_ok=True)
    bat_path = script_dir / "apply_update.bat"
    log_path = script_dir / "update_log.txt"

    bat_content = f"""@echo off
setlocal enabledelayedexpansion
set "NEWEXE={new_exe}"
set "CUREXE={current_exe}"
set "PIDTOWAIT={pid}"
set "LOGFILE={log_path}"

echo [%date% %time%] Debut mise a jour, attente fin du process %PIDTOWAIT% > "%LOGFILE%"

set "WAITCOUNT=0"
:waitloop
tasklist /FI "PID eq %PIDTOWAIT%" | find "%PIDTOWAIT%" >nul
if not errorlevel 1 (
    set /a WAITCOUNT+=1
    if !WAITCOUNT! GEQ 30 (
        echo [%date% %time%] Timeout apres 30s d'attente - le PID %PIDTOWAIT% semble toujours actif ou reutilise, on continue quand meme >> "%LOGFILE%"
        goto afterwait
    )
    timeout /t 1 /nobreak >nul
    goto waitloop
)
:afterwait

echo [%date% %time%] Process termine ou timeout atteint, tentative de remplacement >> "%LOGFILE%"

set "SWAPPED=0"
for /L %%i in (1,1,10) do (
    if "!SWAPPED!"=="0" (
        move /Y "%CUREXE%" "%CUREXE%.old" >> "%LOGFILE%" 2>&1
        if exist "%CUREXE%.old" (
            move /Y "%NEWEXE%" "%CUREXE%" >> "%LOGFILE%" 2>&1
            if exist "%CUREXE%" (
                set "SWAPPED=1"
                del /Q "%CUREXE%.old" >> "%LOGFILE%" 2>&1
                echo [%date% %time%] Remplacement reussi >> "%LOGFILE%"
            ) else (
                echo [%date% %time%] Echec copie nouvel exe, restauration >> "%LOGFILE%"
                move /Y "%CUREXE%.old" "%CUREXE%" >> "%LOGFILE%" 2>&1
            )
        ) else (
            echo [%date% %time%] Tentative %%i echouee, fichier encore verrouille >> "%LOGFILE%"
            timeout /t 1 /nobreak >nul
        )
    )
)

if "!SWAPPED!"=="0" (
    echo [%date% %time%] Abandon apres 10 tentatives - mise a jour non appliquee >> "%LOGFILE%"
)

start "" "%CUREXE%"

(goto) 2>nul & del "%~f0"
"""
    bat_path.write_text(bat_content, encoding="utf-8")
    return bat_path


def launch_swap_and_exit(bat_path: Path) -> None:
    """Lance le script de remplacement sans fenêtre visible, détaché du
    process courant. L'appelant doit quitter l'app immédiatement après.

    L'app packagée (PyInstaller, console=False) n'a elle-même aucune
    console attachée. Dans ce contexte, CREATE_NO_WINDOW seul ne suffit
    pas toujours à empêcher Windows de créer une nouvelle fenêtre de
    console visible pour le processus enfant. On force donc explicitement
    la fenêtre à démarrer masquée via STARTUPINFO/SW_HIDE, en plus de
    CREATE_NO_WINDOW — combinaison plus fiable dans ce cas précis.
    """
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE

    subprocess.Popen(
        ["cmd.exe", "/c", str(bat_path)],
        creationflags=CREATE_NO_WINDOW,
        startupinfo=startupinfo,
        close_fds=True,
    )
