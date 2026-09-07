import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from app.updater import UpdateCheckError, build_swap_script, check_for_update
from app.version import APP_VERSION, ASSET_NAME


def _mock_response(payload: dict):
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def test_check_for_update_returns_info_when_newer_version_available():
    payload = {
        "tag_name": "v9.9.9",
        "body": "notes",
        "assets": [{"name": ASSET_NAME, "browser_download_url": "https://example.com/exe"}],
    }
    with patch("app.updater.urllib.request.urlopen", return_value=_mock_response(payload)):
        info = check_for_update()

    assert info is not None
    assert info.version == "9.9.9"
    assert info.download_url == "https://example.com/exe"


def test_check_for_update_returns_none_when_up_to_date():
    payload = {
        "tag_name": f"v{APP_VERSION}",
        "assets": [{"name": ASSET_NAME, "browser_download_url": "https://example.com/exe"}],
    }
    with patch("app.updater.urllib.request.urlopen", return_value=_mock_response(payload)):
        info = check_for_update()

    assert info is None


def test_check_for_update_raises_when_asset_missing():
    payload = {"tag_name": "v9.9.9", "assets": []}
    with patch("app.updater.urllib.request.urlopen", return_value=_mock_response(payload)):
        with pytest.raises(UpdateCheckError):
            check_for_update()


def test_check_for_update_raises_on_network_error():
    with patch(
        "app.updater.urllib.request.urlopen",
        side_effect=urllib.error.URLError("no connection"),
    ):
        with pytest.raises(UpdateCheckError):
            check_for_update()


def test_check_for_update_raises_on_malformed_json():
    response = MagicMock()
    response.read.return_value = b"not json"
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    with patch("app.updater.urllib.request.urlopen", return_value=response):
        with pytest.raises(UpdateCheckError):
            check_for_update()


def test_build_swap_script_contains_paths_and_pid(tmp_path):
    new_exe = tmp_path / "new" / "MobiDeskPro.exe"
    current_exe = tmp_path / "current" / "MobiDeskPro.exe"

    bat_path = build_swap_script(new_exe=new_exe, current_exe=current_exe, pid=4242)

    assert bat_path.exists()
    content = bat_path.read_text(encoding="utf-8")
    assert str(new_exe) in content
    assert str(current_exe) in content
    assert "4242" in content
