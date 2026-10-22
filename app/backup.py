"""Sauvegarde automatique de la base de données vers un stockage cloud S3.

Compatible avec Backblaze B2 et Cloudflare R2 (et tout service S3-compatible).
La même configuration fonctionne sur tous les PC sans autorisation par machine.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import threading
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
        backup_name = f"stock_backup_{timestamp}.db"
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
