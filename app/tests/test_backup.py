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


def test_close_backup_only_when_database_changed(config_dir, monkeypatch):
    import os
    import time

    db_path = config_dir / "stock.db"
    db_path.write_bytes(b"x")
    monkeypatch.setattr(backup, "get_database_path", lambda: db_path)
    config = {**CREDENTIALS, "enabled": True}

    assert backup.is_close_backup_due(config)  # jamais sauvegardé

    config["last_success_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    past = time.time() - 3600
    os.utime(db_path, (past, past))
    assert not backup.is_close_backup_due(config)  # rien changé depuis

    os.utime(db_path, None)  # base modifiée maintenant
    config["last_success_at"] = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat()
    assert backup.is_close_backup_due(config)

    assert not backup.is_close_backup_due({**config, "backup_on_close": False})
    assert not backup.is_close_backup_due({**config, "enabled": False})


def _make_mobidesk_db(path, value="x"):
    conn = sqlite3.connect(path)
    with conn:
        for table in ("displays", "stock_movements", "suppliers", "resellers", "users"):
            conn.execute(f"CREATE TABLE {table} (id INTEGER)")
        conn.execute("CREATE TABLE t (value TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.close()


def _read_value(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT value FROM t").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture()
def restore_dirs(config_dir, monkeypatch):
    monkeypatch.setattr(backup, "_PENDING_RESTORE_PATH", config_dir / "pending_restore.db")
    monkeypatch.setattr(backup, "_LOCAL_BACKUP_DIR", config_dir / "backups")
    db_path = config_dir / "stock.db"
    monkeypatch.setattr(backup, "get_database_path", lambda: db_path)
    return db_path


class _FakeListS3:
    def __init__(self, objects, files=None):
        self.objects = objects
        self.files = files or {}

    def get_paginator(self, _name):
        objects = self.objects

        class _P:
            def paginate(self, Bucket, Prefix):
                return [{"Contents": objects}]

        return _P()

    def download_file(self, bucket, key, dest):
        import shutil
        shutil.copy(self.files[key], dest)


def test_list_cloud_backups_sorted_by_upload_date_not_name(config_dir, monkeypatch):
    utc = datetime.timezone.utc
    objects = [
        {"Key": "p/stock_backup_MAGASIN_20991231_235959.db", "LastModified": datetime.datetime(2026, 11, 5, 10, tzinfo=utc), "Size": 10},
        {"Key": "p/ma_base.db", "LastModified": datetime.datetime(2026, 11, 6, 22, tzinfo=utc), "Size": 20},
        {"Key": "p/notes.txt", "LastModified": datetime.datetime(2026, 11, 7, tzinfo=utc), "Size": 1},
    ]
    monkeypatch.setattr(backup, "_make_client", lambda config: _FakeListS3(objects))
    result = backup.list_cloud_backups({**CREDENTIALS, "prefix": "p/"})
    assert [b.name for b in result] == ["ma_base.db", "stock_backup_MAGASIN_20991231_235959.db"]


def test_restore_rejects_a_bad_file_and_keeps_the_database(restore_dirs, config_dir, monkeypatch):
    _make_mobidesk_db(restore_dirs, "actuelle")
    bad = config_dir / "bad.db"
    bad.write_bytes(b"pas une base")
    monkeypatch.setattr(backup, "_make_client", lambda config: _FakeListS3([], {"k": bad}))
    with pytest.raises(ValueError):
        backup.download_for_restore(CREDENTIALS, "k")
    assert backup.apply_pending_restore() is None


def test_restore_replaces_database_and_keeps_a_copy(restore_dirs, config_dir, monkeypatch):
    _make_mobidesk_db(restore_dirs, "actuelle")
    good = config_dir / "cloud.db"
    _make_mobidesk_db(good, "cloud")
    monkeypatch.setattr(backup, "_make_client", lambda config: _FakeListS3([], {"k": good}))

    backup.download_for_restore(CREDENTIALS, "k")
    copy = backup.apply_pending_restore()

    assert _read_value(restore_dirs) == "cloud"
    assert _read_value(copy) == "actuelle"
    assert backup.apply_pending_restore() is None  # une seule fois
