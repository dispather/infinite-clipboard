"""H6 회귀 — handshake 이전 연결 수 제한 (slow-loris 방어).

2026-07-03 감사 H6: handshake 를 완료하지 않고 연결만 열어두는 방식으로
서버 스레드/fd 를 고갈시키는 공격이 방어되지 않았음. 상한(MAX_PENDING_CONNECTIONS)
초과 연결은 accept 는 되지만 스레드 생성 없이 즉시 닫혀야 한다.
"""

import logging
import socket as sock_module
import time

import core.network as network_module
from core.network import NetworkServer


def _free_port() -> int:
    s = sock_module.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_pending_connection_limit_rejects_excess(monkeypatch, caplog):
    """상한(2)만큼 핸드셰이크 미완료 연결을 채운 뒤, 초과분은 즉시 거부돼야 한다."""
    caplog.set_level(logging.WARNING, logger="core.network")
    monkeypatch.setattr(network_module, "MAX_PENDING_CONNECTIONS", 2)

    port = _free_port()
    server = NetworkServer(
        port=port, auth_key="k", tailscale_trust=False, bind_address="127.0.0.1",
    )
    server.start()
    time.sleep(0.1)

    # 상한만큼 "행" 연결 — accept 는 되지만 handshake 응답을 보내지 않음
    hung_sockets = []
    try:
        for _ in range(2):
            s = sock_module.socket()
            s.settimeout(2)
            s.connect(("127.0.0.1", port))
            hung_sockets.append(s)
            time.sleep(0.1)  # accept thread 가 순서대로 pending_count 반영할 시간

        # 세 번째 연결은 상한 초과 — 서버가 스레드 생성 없이 즉시 닫아야 함
        extra = sock_module.socket()
        extra.settimeout(2)
        extra.connect(("127.0.0.1", port))
        data = extra.recv(16)
        assert data == b"", "상한 초과 연결이 거부되지 않음 (slow-loris 방어 회귀)"
        extra.close()

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "동시 연결 상한" in r.getMessage()
        ]
        assert warnings, "상한 초과 시 경고 로그가 기록되지 않음"
    finally:
        for s in hung_sockets:
            s.close()
        server.stop()


def test_pending_slot_freed_after_disconnect(monkeypatch):
    """행 연결이 닫히면 pending 슬롯이 반환되어 새 연결을 다시 받을 수 있어야 한다."""
    monkeypatch.setattr(network_module, "MAX_PENDING_CONNECTIONS", 1)

    port = _free_port()
    server = NetworkServer(
        port=port, auth_key="k", tailscale_trust=False, bind_address="127.0.0.1",
    )
    server.start()
    time.sleep(0.1)

    first = sock_module.socket()
    first.settimeout(2)
    first.connect(("127.0.0.1", port))
    time.sleep(0.1)

    # 상한(1) 도달 상태 — 두 번째는 거부되어야 함
    blocked = sock_module.socket()
    blocked.settimeout(2)
    blocked.connect(("127.0.0.1", port))
    assert blocked.recv(16) == b""
    blocked.close()

    # 첫 연결을 닫아 슬롯 반환
    first.close()
    time.sleep(0.3)

    # 이제 새 연결은 accept 되어 challenge 를 받아야 함(즉시 닫히지 않음)
    third = sock_module.socket()
    third.settimeout(2)
    third.connect(("127.0.0.1", port))
    data = third.recv(16)
    assert data != b"", "슬롯 반환 후에도 새 연결이 거부됨"
    third.close()

    server.stop()
