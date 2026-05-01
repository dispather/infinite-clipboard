"""v2.2 R3: HMAC handshake 통합 테스트.

실제 socket 으로 server + client 띄워 다음 시나리오 검증:
  - 정상 mutual handshake (양쪽 connected/clients 등록)
  - wrong key (client) → handshake 실패
  - wrong key (server) → client 가 server HMAC 검증 실패
  - v2.1 client 형식 (구 MSG_HANDSHAKE) → server 가 hard break 거부
"""

import logging
import socket
import struct
import time

import pytest

from core.network import NetworkClient, NetworkServer
from core.protocol import Protocol, MSG_HANDSHAKE


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ─── 정상 mutual handshake ──────────────────────────────────────────


def test_mutual_handshake_success():
    port = _free_port()
    server = NetworkServer(
        port=port, auth_key="shared-secret",
        tailscale_trust=False, bind_address="127.0.0.1",
    )
    server.start()

    client = NetworkClient(
        host="127.0.0.1", port=port,
        auth_key="shared-secret", device_name="testdev",
    )
    client.start()

    try:
        assert _wait_until(lambda: client.connected, timeout=3.0), \
            "client should connect within timeout"

        # server 측에 client 가 등록됐는지
        assert _wait_until(
            lambda: len(server.clients) == 1, timeout=2.0
        ), "server should register the client"

        # 등록된 이름이 device_name 과 일치
        with server.clients_lock:
            registered = list(server.clients.values())
        assert registered == ["testdev"]
    finally:
        client.stop()
        server.stop()


# ─── wrong key on client ────────────────────────────────────────────


def test_wrong_key_client_rejected(caplog):
    """client 가 잘못된 key 로 접속 → server 가 HMAC mismatch 로 거부."""
    caplog.set_level(logging.WARNING, logger="core.network")
    port = _free_port()
    server = NetworkServer(
        port=port, auth_key="server-key",
        tailscale_trust=False, bind_address="127.0.0.1",
    )
    server.start()

    client = NetworkClient(
        host="127.0.0.1", port=port,
        auth_key="WRONG-CLIENT-KEY", device_name="evil",
    )
    client.reconnect_interval = 60   # 한 번만 시도하도록
    client.start()

    try:
        # 잠깐 기다려도 connected 안 됨
        connected = _wait_until(lambda: client.connected, timeout=2.0)
        assert not connected, "wrong key should not authenticate"

        # server 의 clients 도 비어있음
        with server.clients_lock:
            assert len(server.clients) == 0

        # server 로그에 HMAC mismatch 가 떴어야
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("HMAC" in m or "인증 실패" in m for m in warnings), \
            f"Expected HMAC-mismatch warning, got {warnings}"
    finally:
        client.stop()
        server.stop()


# ─── wrong key on server ────────────────────────────────────────────


def test_wrong_key_server_rejected_by_client(caplog):
    """server 가 다른 key 로 ACK 를 보내면 client 가 HMAC verification 실패로 거부.

    구체적 시나리오: 사용자가 server PC 와 client PC 의 auth_key 를 다르게 입력.
    client 측에서 'server HMAC verification failed' 발견.
    """
    caplog.set_level(logging.ERROR, logger="core.network")
    port = _free_port()
    server = NetworkServer(
        port=port, auth_key="server-only-key",
        tailscale_trust=False, bind_address="127.0.0.1",
    )
    server.start()

    client = NetworkClient(
        host="127.0.0.1", port=port,
        auth_key="client-only-key", device_name="dev",
    )
    client.reconnect_interval = 60
    client.start()

    try:
        connected = _wait_until(lambda: client.connected, timeout=2.0)
        assert not connected
    finally:
        client.stop()
        server.stop()


# ─── v2.1 client (구 MSG_HANDSHAKE) → hard break 거부 ────────────────


def test_v21_legacy_handshake_rejected_by_server():
    """v2.1 클라이언트가 구 MSG_HANDSHAKE 형식으로 접속 → server 가 거부.

    Hard break 의 핵심: v2.2 server 가 v2.1 wire 를 받아도 동작하지 않음.
    """
    port = _free_port()
    server = NetworkServer(
        port=port, auth_key="k",
        tailscale_trust=False, bind_address="127.0.0.1",
    )
    server.start()

    try:
        # raw socket 으로 v2.1 흉내
        s = socket.socket()
        s.settimeout(3.0)
        s.connect(("127.0.0.1", port))

        # server 의 challenge 받기 (무시 — v2.1 client 는 이걸 처리 못 함)
        header = s.recv(4)
        assert len(header) == 4
        msg_len = struct.unpack(">I", header)[0]
        challenge_data = b""
        while len(challenge_data) < msg_len:
            chunk = s.recv(msg_len - len(challenge_data))
            if not chunk:
                break
            challenge_data += chunk

        # v2.1 형식의 (잘못된) handshake 송신
        proto = Protocol("k")
        bogus = proto.create_message(MSG_HANDSHAKE, {
            "name": "v21client",
            "auth_hash": "f" * 64,
        })
        s.sendall(bogus)

        # server 가 끊었는지 확인 (recv 가 0 byte 또는 connection reset)
        try:
            result = s.recv(1024)
            assert len(result) == 0, "server should close on v2.1 handshake"
        except (ConnectionResetError, socket.timeout):
            pass  # also acceptable — server closed abruptly
        s.close()

        # server 의 clients 비어있음 (등록 안 됨)
        with server.clients_lock:
            assert len(server.clients) == 0
    finally:
        server.stop()


# ─── nonce 매번 새로 생성 (replay 방어 기본) ────────────────────────


def test_two_connections_use_different_nonces(monkeypatch):
    """같은 server 에 두 번 connect → 매번 다른 server_nonce 가 challenge 에 박혀야."""
    from core import protocol as proto_mod

    captured_nonces = []
    real_gen = proto_mod.generate_nonce

    def spy_gen():
        n = real_gen()
        captured_nonces.append(n)
        return n

    monkeypatch.setattr(proto_mod, "generate_nonce", spy_gen)
    # network 모듈도 import 시점에 generate_nonce 를 가져갔으므로 거기도 patch
    from core import network as net_mod
    monkeypatch.setattr(net_mod, "generate_nonce", spy_gen)

    port = _free_port()
    server = NetworkServer(
        port=port, auth_key="k",
        tailscale_trust=False, bind_address="127.0.0.1",
    )
    server.start()

    clients = []
    try:
        for i in range(2):
            c = NetworkClient(
                host="127.0.0.1", port=port,
                auth_key="k", device_name=f"dev{i}",
            )
            c.start()
            clients.append(c)
            assert _wait_until(lambda c=c: c.connected, timeout=3.0), \
                f"client {i} should connect"

        # 최소 4개 nonce (2 server_nonce + 2 client_nonce). 모두 unique.
        assert len(captured_nonces) >= 4
        assert len(set(captured_nonces)) == len(captured_nonces), \
            "all nonces must be unique"
    finally:
        for c in clients:
            c.stop()
        server.stop()
