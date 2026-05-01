"""인증 키 강화 회귀 — 약한 기본값(PIN)이 자동으로 강한 토큰으로 교체되는지."""

import json
import os
import pytest

from config import AppConfig, save_config, load_config, CONFIG_FILE


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """CONFIG_FILE을 tmp_path로 리디렉트."""
    fake = tmp_path / "settings.json"
    import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_FILE", fake)
    yield fake


def test_empty_auth_key_gets_strong_token(isolated_config):
    c = AppConfig(auth_key="")
    # token_urlsafe(16) → base64 22자
    assert len(c.auth_key) >= 20
    assert c.auth_key != "change-me"


def test_old_6digit_pin_is_upgraded(isolated_config):
    c = AppConfig(auth_key="123456")  # 숫자 6자리 → 약한 PIN
    # 자동 교체되어야 함
    assert c.auth_key != "123456"
    assert len(c.auth_key) >= 20


def test_change_me_placeholder_replaced(isolated_config):
    c = AppConfig(auth_key="change-me")
    assert c.auth_key != "change-me"
    assert len(c.auth_key) >= 20


def test_strong_existing_key_preserved(isolated_config):
    strong = "abcDEFghi-1234567890_zyxWVU"  # 충분히 긴 비숫자 키
    c = AppConfig(auth_key=strong)
    assert c.auth_key == strong


@pytest.mark.skipif(os.name != "posix", reason="POSIX chmod만 검증")
def test_save_config_sets_0600(isolated_config):
    c = AppConfig()
    save_config(c)
    mode = isolated_config.stat().st_mode & 0o777
    assert mode == 0o600, f"settings.json 권한이 0o{mode:o} (기대: 0o600)"


def test_save_load_roundtrip(isolated_config):
    c = AppConfig(mode="server", port=12345)
    save_config(c)
    loaded = load_config()
    assert loaded.mode == "server"
    assert loaded.port == 12345
    assert loaded.auth_key == c.auth_key
