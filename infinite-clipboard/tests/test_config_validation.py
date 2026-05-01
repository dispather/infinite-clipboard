"""v2.2 R1: AppConfig 설정 검증 강화 회귀 테스트."""

import pytest

from config import AppConfig


def test_valid_defaults():
    c = AppConfig()
    assert c.mode == "client"
    assert c.port == 9999
    assert 1 <= c.max_file_size_gb <= 1000


def test_invalid_mode_clamped():
    c = AppConfig(mode="malicious", auth_key="x" * 16)
    assert c.mode == "client"


def test_invalid_port_clamped():
    for bad in (0, 65536, -1, 100000):
        c = AppConfig(port=bad, auth_key="x" * 16)
        assert c.port == 9999, f"port={bad} should reset to 9999"


def test_port_string_clamped():
    c = AppConfig(port="not-an-int", auth_key="x" * 16)
    assert c.port == 9999


def test_max_file_size_clamped():
    for bad in (0, -5, 99999, 100000):
        c = AppConfig(max_file_size_gb=bad, auth_key="x" * 16)
        assert c.max_file_size_gb == 10


def test_clipboard_check_interval_clamped():
    for bad in (0, -1, 61, 1000):
        c = AppConfig(clipboard_check_interval=bad, auth_key="x" * 16)
        assert c.clipboard_check_interval == 0.5

    # 정상 범위는 유지
    c = AppConfig(clipboard_check_interval=1.5, auth_key="x" * 16)
    assert c.clipboard_check_interval == 1.5


def test_reconnect_interval_clamped():
    for bad in (-1, 3601, -100):
        c = AppConfig(reconnect_interval=bad, auth_key="x" * 16)
        assert c.reconnect_interval == 5

    # 0 (즉시 재연결) 도 허용
    c = AppConfig(reconnect_interval=0, auth_key="x" * 16)
    assert c.reconnect_interval == 0


def test_clipboard_history_size_clamped():
    for bad in (-1, 1001, 99999):
        c = AppConfig(clipboard_history_size=bad, auth_key="x" * 16)
        assert c.clipboard_history_size == 20

    # 0 (history 안 씀) 도 허용
    c = AppConfig(clipboard_history_size=0, auth_key="x" * 16)
    assert c.clipboard_history_size == 0


def test_history_privacy_mode_default_on():
    """v2.2 R1: history_privacy_mode 기본값은 ON."""
    c = AppConfig()
    assert c.history_privacy_mode is True


def test_short_auth_key_warning_only(caplog):
    """짧은 auth_key 는 강제 변경 없이 warning 만."""
    import logging
    caplog.set_level(logging.WARNING, logger="config")
    c = AppConfig(auth_key="abc12345")  # 8 chars, len < 16
    # auth_key 그대로 유지 (사용자 의도 가능)
    assert c.auth_key == "abc12345"
    # warning 발생
    assert any("auth_key length" in r.getMessage() for r in caplog.records)


def test_weak_auth_key_replaced():
    """빈 키, change-me, 6자리 PIN 은 강한 키로 교체."""
    for weak in ("", "change-me", "123456"):
        c = AppConfig(auth_key=weak)
        assert c.auth_key not in ("", "change-me", "123456")
        assert len(c.auth_key) >= 16


def test_invalid_download_path_fallback(tmp_path, monkeypatch):
    """존재 불가 경로는 default 로 fallback."""
    # 쓰기 불가능한 가상 경로 (Windows 에서 잘못된 드라이브)
    bad = "Z:\\nonexistent\\path\\that\\cannot\\be\\made\\for\\sure"
    c = AppConfig(download_path=bad, auth_key="x" * 16)
    # 빈 문자열이 아니거나 default 디렉토리로 보정됨
    assert c.download_path != bad
    assert c.download_path  # 빈 문자열 아님


# ── v2.2 R2: bind_address 검증 ────────────────────────────────────────


def test_bind_address_default_empty():
    """bind_address 기본값은 빈 문자열 (자동 Tailscale)."""
    c = AppConfig()
    assert c.bind_address == ""


def test_bind_address_explicit_ipv4_kept():
    """유효한 IPv4 명시는 그대로 유지."""
    for ip in ("0.0.0.0", "127.0.0.1", "100.99.126.25", "192.168.1.10"):
        c = AppConfig(bind_address=ip, auth_key="x" * 16)
        assert c.bind_address == ip


def test_bind_address_invalid_reset(caplog):
    """잘못된 IP 형식은 빈 문자열로 reset (자동 모드)."""
    import logging
    caplog.set_level(logging.WARNING, logger="config")
    for bad in ("not-an-ip", "999.999.999.999", "abc.def", "1.2.3"):
        c = AppConfig(bind_address=bad, auth_key="x" * 16)
        assert c.bind_address == "", f"{bad} should reset to ''"


def test_bind_address_non_string_reset():
    """비-string 입력은 빈 문자열로 reset."""
    c = AppConfig(bind_address=12345, auth_key="x" * 16)
    assert c.bind_address == ""
