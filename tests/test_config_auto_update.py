"""2026-09-29 자동 업데이트: AppConfig.auto_update_check 기본값·보정·저장 왕복."""

import json

import pytest

from config import AppConfig, load_config, save_config


def test_auto_update_check_default_on():
    assert AppConfig(auth_key="x" * 16).auto_update_check is True


@pytest.mark.parametrize("bad", ["yes", 1, 0, None, "false"])
def test_auto_update_check_non_bool_resets_to_true(bad):
    # bool 아닌 값(수동 편집 실수)은 기본값 True 로 — "false" 문자열도 bool 이 아니다
    c = AppConfig(auth_key="x" * 16, auto_update_check=bad)
    assert c.auto_update_check is True


def test_auto_update_check_false_is_kept():
    assert AppConfig(auth_key="x" * 16, auto_update_check=False).auto_update_check is False


def test_auto_update_check_roundtrip(tmp_path, monkeypatch):
    import config as cfg
    fake = tmp_path / "settings.json"
    monkeypatch.setattr(cfg, "CONFIG_FILE", fake)
    c = AppConfig(auth_key="x" * 16, auto_update_check=False)
    save_config(c)
    assert json.loads(fake.read_text(encoding="utf-8"))["auto_update_check"] is False
    assert load_config().auto_update_check is False
