"""v2.2 R2: NetworkServer bind 주소 결정 로직 회귀."""

import logging
import socket as sock_module
import time

import pytest

from core.network import NetworkServer
from config import AppConfig
from core.protocol import generate_peer_id
from main import InfiniteClipboard


def _free_port() -> int:
    s = sock_module.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_explicit_loopback_bind():
    """bind_address='127.0.0.1' → 그 인터페이스에만 bind."""
    server = NetworkServer(
        port=_free_port(), auth_key="k",
        tailscale_trust=False, bind_address="127.0.0.1",
    )
    server.start()
    try:
        # 실제 sockname 확인
        actual = server.server_socket.getsockname()
        assert actual[0] == "127.0.0.1"
    finally:
        server.stop()


def test_explicit_all_interfaces_bind(caplog):
    """bind_address='0.0.0.0' → opt-in warning 발생."""
    caplog.set_level(logging.WARNING, logger="core.network")
    server = NetworkServer(
        port=_free_port(), auth_key="k",
        tailscale_trust=False, bind_address="0.0.0.0",
    )
    server.start()
    try:
        actual = server.server_socket.getsockname()
        assert actual[0] == "0.0.0.0"
        # opt-in warning 출력 확인
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("0.0.0.0" in m or "노출" in m for m in warnings), \
            f"Expected explicit-0.0.0.0 warning, got {warnings}"
    finally:
        server.stop()


def test_resolve_bind_returns_explicit_value():
    """_resolve_bind_address: 명시 IP 가 있으면 그대로 반환 (Tailscale 조회 안 함)."""
    server = NetworkServer(
        port=_free_port(), auth_key="k",
        tailscale_trust=False, bind_address="127.0.0.1",
    )
    assert server._resolve_bind_address() == "127.0.0.1"


def test_resolve_bind_auto_with_tailscale(monkeypatch):
    """_resolve_bind_address: 빈 문자열 + Tailscale 감지 → Tailscale IP 반환."""
    from core import network as net_mod
    # tailscale 모듈을 mock — get_tailscale_ip 가 IP 반환하도록
    import core.tailscale as ts_mod
    monkeypatch.setattr(ts_mod, "get_tailscale_ip", lambda: "100.64.0.5")

    server = NetworkServer(
        port=_free_port(), auth_key="k",
        tailscale_trust=False, bind_address="",
    )
    assert server._resolve_bind_address() == "100.64.0.5"


def test_resolve_bind_auto_fallback_to_all_interfaces(monkeypatch, caplog):
    """_resolve_bind_address: 빈 문자열 + Tailscale 미감지 → 0.0.0.0 + warning."""
    import core.tailscale as ts_mod
    monkeypatch.setattr(ts_mod, "get_tailscale_ip", lambda: None)

    caplog.set_level(logging.WARNING, logger="core.network")
    server = NetworkServer(
        port=_free_port(), auth_key="k",
        tailscale_trust=False, bind_address="",
    )
    result = server._resolve_bind_address()
    assert result == "0.0.0.0"
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Tailscale" in m for m in warnings), \
        f"Expected Tailscale-fallback warning, got {warnings}"


def test_app_start_survives_port_conflict(tmp_path):
    """C3: InfiniteClipboard._start_server() 가 포트 충돌(OSError) 을 삼키고
    무음 크래시 대신 self.connected=False + self._startup_error 로 보고해야 한다."""
    port = _free_port()

    # 포트를 먼저 점유해 실제 bind 충돌을 유발
    blocker = sock_module.socket(sock_module.AF_INET, sock_module.SOCK_STREAM)
    blocker.setsockopt(sock_module.SOL_SOCKET, sock_module.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)

    config = AppConfig(
        mode="server",
        port=port,
        auth_key="conflict-test-shared-secret-key-0123456789",
        peer_id=generate_peer_id(),
        download_path=str(tmp_path / "dl"),
        tailscale_trust=False,
        bind_address="127.0.0.1",
    )
    app = InfiniteClipboard(config)
    try:
        app.start()  # OSError 를 raise 하면 이 줄에서 테스트가 즉시 실패한다
        assert app.connected is False
        assert app.server is None
        assert app._startup_error is not None
        assert str(port) in app._startup_error
    finally:
        app.stop()
        blocker.close()
