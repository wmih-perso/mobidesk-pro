"""Sauvegarde automatique de la base de données vers un stockage cloud S3.

Compatible avec Backblaze B2 et Cloudflare R2 (et tout service S3-compatible).
La même configuration fonctionne sur tous les PC sans autorisation par machine.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.paths import get_data_dir, get_database_path

_CONFIG_DIR = get_data_dir()
_BACKUP_CONFIG_PATH = _CONFIG_DIR / "backup_config.json"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_backup_config() -> dict:
    if _BACKUP_CONFIG_PATH.exists():
        try:
            return json.loads(_BACKUP_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "enabled": False,
        "hour": 20,
        "keep_last": 30,
        "endpoint_url": "",
        "access_key_id": "",
        "secret_access_key": "",
        "bucket_name": "",
        "prefix": "mobidesk-backups/",
    }


def record_backup_result(success: bool, message: str) -> None:
    """Mémorise le résultat de la dernière sauvegarde (affiché dans les
    Paramètres). Relit le fichier pour ne pas écraser une modification de
    la configuration faite entre-temps."""
    config = load_backup_config()
    config["last_backup_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    config["last_backup_ok"] = success
    config["last_backup_message"] = message
    if success:
        config["last_success_at"] = config["last_backup_at"]
    save_backup_config(config)


def is_backup_due(config: dict, now: datetime.datetime) -> bool:
    """Sauvegarde automatique à faire maintenant ? Oui si elle est activée
    et configurée, que l'heure choisie est passée, et qu'aucune sauvegarde
    n'a réussi aujourd'hui — l'application ouverte après l'heure rattrape
    donc la sauvegarde du jour."""
    if not config.get("enabled") or not _has_credentials(config):
        return False
    if now.hour < config.get("hour", 20):
        return False
    last_success = config.get("last_success_at")
    if last_success:
        try:
            if datetime.datetime.fromisoformat(last_success).date() == now.date():
                return False
        except ValueError:
            pass
    return True


def save_backup_config(config: dict) -> None:
    _BACKUP_CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _has_credentials(c: dict) -> bool:
    return bool(c.get("endpoint_url") and c.get("access_key_id")
                and c.get("secret_access_key") and c.get("bucket_name"))


def is_configured() -> bool:
    return _has_credentials(load_backup_config())


# ---------------------------------------------------------------------------
# Client S3
# ---------------------------------------------------------------------------

def _make_client(config: dict):
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=config["endpoint_url"],
        aws_access_key_id=config["access_key_id"],
        aws_secret_access_key=config["secret_access_key"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def test_connection(config: dict) -> tuple[bool, str]:
    """Teste la connexion de façon synchrone. Retourne (succès, message)."""
    try:
        client = _make_client(config)
        client.head_bucket(Bucket=config["bucket_name"])
        return True, "Connexion réussie."
    except Exception as exc:
        return False, f"Erreur : {exc}"


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def _prune_old_backups(client, bucket: str, prefix: str, keep_last: int) -> None:
    response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    objects = response.get("Contents", [])
    objects.sort(key=lambda o: o["LastModified"], reverse=True)
    for obj in objects[keep_last:]:
        client.delete_object(Bucket=bucket, Key=obj["Key"])


def perform_backup(config: dict) -> tuple[bool, str]:
    """Copie la base et l'envoie au stockage S3 (bloquant). Mémorise le
    résultat (record_backup_result) et le renvoie (succès, message)."""
    tmp = None
    try:
        db_path = get_database_path()
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"stock_backup_{_computer_name()}_{timestamp}.db"
        tmp = _CONFIG_DIR / backup_name
        # API de sauvegarde SQLite : copie cohérente même si l'application
        # écrit dans la base au même moment (contrairement à une copie
        # brute du fichier).
        source = sqlite3.connect(db_path)
        target = sqlite3.connect(tmp)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

        client = _make_client(config)
        bucket = config["bucket_name"]
        prefix = config.get("prefix", "mobidesk-backups/")
        key = f"{prefix}{backup_name}"

        client.upload_file(str(tmp), bucket, key)
        _prune_old_backups(client, bucket, prefix, config.get("keep_last", 30))
        success, message = True, f"Sauvegarde envoyée : {backup_name}"
    except Exception as exc:
        success, message = False, f"Échec de la sauvegarde : {exc}"
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass  # copie temporaire verrouillée : sans gravité
    record_backup_result(success, message)
    return success, message


def upload_backup(on_done: Callable[[bool, str], None], config: dict | None = None) -> None:
    """perform_backup dans un thread (non bloquant).

    `on_done(succès, message)` est appelé depuis le thread d'envoi : côté
    interface, passer par un signal Qt (voir app/ui/backup_panel.py) avant
    de toucher aux widgets."""
    if config is None:
        config = load_backup_config()
    threading.Thread(target=lambda: on_done(*perform_backup(config)), daemon=True).start()


def _computer_name() -> str:
    """Nom du PC (dans le nom des sauvegardes, pour savoir d'où elles viennent)."""
    import os
    import re

    name = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "PC"
    return re.sub(r"[^A-Za-z0-9-]", "", name.upper())[:30] or "PC"


# ---------------------------------------------------------------------------
# Récupération depuis le cloud
# ---------------------------------------------------------------------------

_PENDING_RESTORE_PATH = _CONFIG_DIR / "pending_restore.db"
# Argument passé à la nouvelle instance lancée après une récupération.
RESTART_FLAG = "--apres-recuperation"
_LOCAL_BACKUP_DIR = _CONFIG_DIR / "backups"

# Tables indispensables : un fichier sans elles n'est pas une base MobiDesk.
_REQUIRED_TABLES = {"displays", "stock_movements", "suppliers", "resellers", "users"}


@dataclass
class CloudBackup:
    key: str
    name: str
    uploaded_at: datetime.datetime  # heure locale du dépôt sur le cloud
    size: int


def list_cloud_backups(config: dict) -> list[CloudBackup]:
    """Les fichiers .db du dossier de sauvegarde, le plus récemment déposé
    en premier (date de dépôt sur le cloud, pas le nom du fichier)."""
    client = _make_client(config)
    prefix = config.get("prefix", "mobidesk-backups/")
    backups: list[CloudBackup] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=config["bucket_name"], Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.lower().endswith(".db"):
                continue
            backups.append(CloudBackup(
                key=key,
                name=key[len(prefix):] if key.startswith(prefix) else key,
                uploaded_at=obj["LastModified"].astimezone().replace(tzinfo=None),
                size=obj.get("Size", 0),
            ))
    backups.sort(key=lambda b: b.uploaded_at, reverse=True)
    return backups


def check_database_file(path: Path) -> None:
    """Lève ValueError si le fichier n'est pas une base MobiDesk valide."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"Ce fichier n'est pas une base de données valide ({exc}).") from exc
    if not result or result[0] != "ok":
        raise ValueError("Le fichier est abîmé (contrôle d'intégrité échoué).")
    missing = _REQUIRED_TABLES - tables
    if missing:
        raise ValueError("Ce fichier n'est pas une base MobiDesk (tables manquantes : "
                         + ", ".join(sorted(missing)) + ").")


def download_for_restore(config: dict, key: str) -> None:
    """Télécharge `key`, le vérifie et le prépare : il remplacera la base au
    prochain démarrage (apply_pending_restore). Lève une exception en cas
    d'échec ; la base actuelle n'est alors pas touchée."""
    tmp = _CONFIG_DIR / "restore_download.tmp"
    try:
        _make_client(config).download_file(config["bucket_name"], key, str(tmp))
        check_database_file(tmp)
        _PENDING_RESTORE_PATH.unlink(missing_ok=True)
        tmp.replace(_PENDING_RESTORE_PATH)
    finally:
        tmp.unlink(missing_ok=True)


def apply_pending_restore() -> Path | None:
    """Au démarrage, avant d'ouvrir la base : si une base récupérée du cloud
    attend, garde une copie de la base actuelle dans data/backups puis la
    remplace. Retourne le chemin de la copie, ou None si rien à faire."""
    if not _PENDING_RESTORE_PATH.exists():
        return None
    import time

    db_path = get_database_path()
    _LOCAL_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    copy = _LOCAL_BACKUP_DIR / f"stock_avant_recuperation_{datetime.datetime.now():%Y%m%d_%H%M%S}.db"
    # L'ancienne instance peut mettre un instant à libérer le fichier.
    for attempt in range(20):
        try:
            if db_path.exists():
                source = sqlite3.connect(str(db_path))
                target = sqlite3.connect(str(copy))
                try:
                    source.backup(target)
                finally:
                    target.close()
                    source.close()
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(db_path) + suffix).unlink(missing_ok=True)
            _PENDING_RESTORE_PATH.replace(db_path)
            break
        except (OSError, sqlite3.Error):
            if attempt == 19:
                raise
            time.sleep(0.5)
    for old in sorted(_LOCAL_BACKUP_DIR.glob("stock_avant_recuperation_*.db"))[:-10]:
        old.unlink(missing_ok=True)
    return copy


def is_close_backup_due(config: dict) -> bool:
    """Sauvegarde à faire à la fermeture de MobiDesk (ou à l'arrêt du PC) ?
    Oui si la sauvegarde automatique est activée, l'option « à la
    fermeture » cochée, et si la base a changé depuis la dernière
    sauvegarde réussie — inutile d'envoyer (et de faire tourner les
    anciennes sauvegardes) quand rien n'a bougé."""
    if not config.get("enabled") or not config.get("backup_on_close", True):
        return False
    if not _has_credentials(config):
        return False
    last_success = config.get("last_success_at")
    if not last_success:
        return True
    try:
        last = datetime.datetime.fromisoformat(last_success)
        modified = datetime.datetime.fromtimestamp(get_database_path().stat().st_mtime)
    except (ValueError, OSError):
        return True
    return modified > last
