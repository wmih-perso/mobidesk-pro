import datetime
import sqlite3

import pytest

from app import backup

CREDENTIALS = dict(
    endpoint_url="https://s3.example.com",
    access_key_id="key",
    secret_access_key="secret",
    bucket_name="bucket",
)


@pytest.fixture()
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(backup, "_BACKUP_CONFIG_PATH", tmp_path / "backup_config.json")
    return tmp_path


def _at(hour, day=17):
    return datetime.datetime(2026, 10, day, hour, 5)


def test_backup_not_due_when_disabled_or_incomplete():
    assert not backup.is_backup_due({**CREDENTIALS, "enabled": False, "hour": 20}, _at(21))
    assert not backup.is_backup_due({"enabled": True, "hour": 20}, _at(21))


def test_backup_due_after_hour_and_caught_up_later_the_same_day():
    config = {**CREDENTIALS, "enabled": True, "hour": 20}
    assert not backup.is_backup_due(config, _at(19))
    assert backup.is_backup_due(config, _at(20))
    assert backup.is_backup_due(config, _at(23))  # appli ouverte après l'heure


def test_backup_not_due_twice_the_same_day():
    config = {**CREDENTIALS, "enabled": True, "hour": 20,
              "last_success_at": _at(20).isoformat()}
    assert not backup.is_backup_due(config, _at(22))
    assert backup.is_backup_due(config, _at(20, day=18))


def test_record_result_keeps_settings(config_dir):
    backup.save_backup_config({**CREDENTIALS, "enabled": True, "hour": 18})

    backup.record_backup_result(False, "pas d'internet")
    backup.record_backup_result(True, "ok")

    config = backup.load_backup_config()
    assert config["bucket_name"] == "bucket" and config["hour"] == 18
    assert config["last_backup_ok"] is True
    assert config["last_success_at"] == config["last_backup_at"]


class _FakeS3:
    def __init__(self):
        self.uploaded = {}

    def upload_file(self, path, bucket, key):
        conn = sqlite3.connect(path)
        try:
            self.uploaded[key] = conn.execute("SELECT value FROM t").fetchone()[0]
        finally:
            conn.close()

    def list_objects_v2(self, Bucket, Prefix):
        return {"Contents": []}


class _ImmediateThread:
    def __init__(self, target, daemon=None):
        self._target = target

    def start(self):
        self._target()


def test_upload_backup_sends_a_consistent_copy(config_dir, monkeypatch):
    db_path = config_dir / "stock.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t (value TEXT)")
        conn.execute("INSERT INTO t VALUES ('données')")
    fake = _FakeS3()
    monkeypatch.setattr(backup, "get_database_path", lambda: db_path)
    monkeypatch.setattr(backup, "_make_client", lambda config: fake)
    monkeypatch.setattr(backup.threading, "Thread", _ImmediateThread)
    results = []

    backup.upload_backup(lambda ok, msg: results.append(ok), config={**CREDENTIALS, "prefix": "p/"})

    assert results == [True]
    [(key, value)] = fake.uploaded.items()
    assert key.startswith("p/stock_backup_") and value == "données"
    assert backup.load_backup_config()["last_backup_ok"] is True
    assert list(config_dir.glob("stock_backup_*.db")) == []  # copie temporaire supprimée


def test_import_credentials_from_file_keeps_other_settings(config_dir, tmp_path):
    import json

    backup.save_backup_config({"enabled": True, "hour": 18, "keep_last": 10})
    key_file = tmp_path / "cles.json"
    key_file.write_text(json.dumps({**CREDENTIALS, "autre": "ignoré"}), encoding="utf-8")

    backup.import_credentials(key_file)

    config = backup.load_backup_config()
    assert backup.is_configured()
    assert config["bucket_name"] == "bucket" and config["hour"] == 18
    assert "autre" not in config


@pytest.mark.parametrize("content", [
    "pas du json",
    '{"endpoint_url": "https://x"}',
    None,  # modèle non rempli
    '{"endpoint_url": "http://x", "access_key_id": "a", "secret_access_key": "b", "bucket_name": "c"}',
])
def test_import_credentials_rejects_invalid_files(config_dir, tmp_path, content):
    import json

    key_file = tmp_path / "cles.json"
    key_file.write_text(content or json.dumps(backup.CREDENTIALS_TEMPLATE), encoding="utf-8")

    with pytest.raises(ValueError):
        backup.import_credentials(key_file)
    assert not backup.is_configured()
