import pytest

from app.version import is_newer, parse_version


def test_parse_version_with_v_prefix():
    assert parse_version("v1.2.3") == (1, 2, 3)


def test_parse_version_without_prefix():
    assert parse_version("1.2.3") == (1, 2, 3)


def test_parse_version_pads_missing_minor_and_patch():
    assert parse_version("v1.0") == (1, 0, 0)


def test_parse_version_pads_major_only():
    assert parse_version("v2") == (2, 0, 0)


def test_parse_version_rejects_too_many_segments():
    with pytest.raises(ValueError):
        parse_version("1.2.3.4")


def test_parse_version_rejects_non_numeric():
    with pytest.raises(ValueError):
        parse_version("1.x.3")


def test_is_newer_true_with_short_remote_tag():
    assert is_newer("v1.0", "0.1.4") is True


def test_is_newer_true_when_remote_greater():
    assert is_newer("v0.2.0", "0.1.0") is True


def test_is_newer_false_when_equal():
    assert is_newer("v0.1.0", "0.1.0") is False


def test_is_newer_false_when_remote_older():
    assert is_newer("v0.1.0", "0.2.0") is False
