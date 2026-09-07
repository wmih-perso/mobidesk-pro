"""Sauvegarde automatique de la base de données vers un stockage cloud S3.

Compatible avec Backblaze B2 et Cloudflare R2 (et tout service S3-compatible).
La même configuration fonctionne sur tous les PC sans autorisation par machine.
"""

from __future__ import annotations

import datetime
import json
import shutil
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


def save_backup_config(config: dict) -> None:
    _BACKUP_CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def is_configured() -> bool:
    c = load_backup_config()
    return bool(c.get("endpoint_url") and c.get("access_key_id")
                and c.get("secret_access_key") and c.get("bucket_name"))


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


def upload_backup(on_done: Callable[[bool, str], None], config: dict | None = None) -> None:
    """Copie la DB et l'uploade vers le stockage S3. Non-bloquant."""
    if config is None:
        config = load_backup_config()

    def _run() -> None:
        tmp = None
        try:
            db_path = get_database_path()
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"stock_backup_{timestamp}.db"
            tmp = _CONFIG_DIR / backup_name
            shutil.copy2(db_path, tmp)

            client = _make_client(config)
            bucket = config["bucket_name"]
            prefix = config.get("prefix", "mobidesk-backups/")
            key = f"{prefix}{backup_name}"

            client.upload_file(str(tmp), bucket, key)
            _prune_old_backups(client, bucket, prefix, config.get("keep_last", 30))
            on_done(True, f"Sauvegarde envoyée : {backup_name}")
        except Exception as exc:
            on_done(False, f"Échec de la sauvegarde : {exc}")
        finally:
            if tmp and tmp.exists():
                tmp.unlink(missing_ok=True)

    threading.Thread(target=_run, daemon=True).start()
