from app.auth import DEFAULT_PASSWORD, set_password, verify_password


def test_default_password_works_on_first_use(tmp_path, monkeypatch):
    monkeypatch.setattr("app.auth.get_data_dir", lambda: tmp_path)
    assert verify_password(DEFAULT_PASSWORD) is True


def test_wrong_password_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("app.auth.get_data_dir", lambda: tmp_path)
    assert verify_password("mauvais") is False


def test_set_password_changes_verification(tmp_path, monkeypatch):
    monkeypatch.setattr("app.auth.get_data_dir", lambda: tmp_path)
    set_password("nouveau_mdp")
    assert verify_password("nouveau_mdp") is True
    assert verify_password(DEFAULT_PASSWORD) is False


def test_password_never_stored_in_clear(tmp_path, monkeypatch):
    monkeypatch.setattr("app.auth.get_data_dir", lambda: tmp_path)
    set_password("secret123")
    content = (tmp_path / "auth.json").read_text()
    assert "secret123" not in content
