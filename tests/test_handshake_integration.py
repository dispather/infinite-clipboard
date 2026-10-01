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
import threading
import time

import pytest

from core.network import NetworkClient, NetworkServer
from core.protocol import (
    Protocol, MSG_HANDSHAKE, MSG_HANDSHAKE_CHALLENGE,
    generate_peer_id, generate_nonce, is_valid_peer_id,
)


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
    server_peer = generate_peer_id()
    client_peer = generate_peer_id()
    server = NetworkServer(
        port=port, auth_key="shared-secret",
        tailscale_trust=False, bind_address="127.0.0.1",
        peer_id=server_peer,
    )
    server.start()

    client = NetworkClient(
        host="127.0.0.1", port=port,
        auth_key="shared-secret", device_name="testdev",
        peer_id=client_peer,
    )
    client.start()

    try:
        assert _wait_until(lambda: client.connected, timeout=3.0), \
            "client should connect within timeout"

        # server 측에 client 가 등록됐는지
        assert _wait_until(
            lambda: len(server.clients) == 1, timeout=2.0
        ), "server should register the client"

        # v3.0: 등록 값이 {name, peer_id} dict — 이름 + peer_id 양쪽 확인
        with server.clients_lock:
            registered = list(server.clients.values())
        assert len(registered) == 1
        assert registered[0]["name"] == "testdev"
        # 서버가 클라이언트 peer_id 를 학습 (round-trip)
        assert registered[0]["peer_id"] == client_peer
        assert is_valid_peer_id(registered[0]["peer_id"])

        # 클라이언트가 서버 peer_id 를 학습 (반대 방향 round-trip)
        assert client.server_peer_id == server_peer
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


# ─── H1: peer_id 유일성 — identity squatting/라우팅 하이재킹 방어 ──────


def test_duplicate_peer_id_second_connection_rejected(caplog):
    """감사 High #1: auth_key 를 아는 두 번째 클라이언트가 이미 연결된 클라이언트와
    동일한 peer_id 를 자기신고하면 거부돼야 한다. 과거엔 유일성 검증이 없어
    identity squatting(다른 peer 사칭) → _socket_for_peer 라우팅 하이재킹이
    가능했다 — C1(peer 신원 스푸핑) 수정을 이 경로로 우회할 수 있었다."""
    caplog.set_level(logging.WARNING, logger="core.network")
    port = _free_port()
    server = NetworkServer(
        port=port, auth_key="shared-secret",
        tailscale_trust=False, bind_address="127.0.0.1",
    )
    server.start()

    victim_peer = generate_peer_id()
    victim = NetworkClient(
        host="127.0.0.1", port=port,
        auth_key="shared-secret", device_name="victim",
        peer_id=victim_peer,
    )
    victim.start()

    attacker = NetworkClient(
        host="127.0.0.1", port=port,
        auth_key="shared-secret", device_name="attacker",
        peer_id=victim_peer,  # 동일한 peer_id 자기신고 — squatting 시도
    )
    attacker.reconnect_interval = 60  # 한 번만 시도

    try:
        assert _wait_until(lambda: victim.connected, timeout=3.0), \
            "victim(첫 연결)은 정상 등록돼야 함"
        assert _wait_until(lambda: len(server.clients) == 1, timeout=2.0)

        attacker.start()
        # 공격자는 handshake ACK 까지는 받아도 서버가 곧 연결을 닫음
        _wait_until(lambda: not attacker.connected or attacker.connected, timeout=1.0)
        time.sleep(0.5)

        # 서버엔 여전히 victim 1개만 등록돼 있어야 함 (attacker 미등록)
        with server.clients_lock:
            assert len(server.clients) == 1, \
                "attacker 가 victim 의 peer_id 로 추가 등록되면 안 됨"
            registered = list(server.clients.values())[0]
            assert registered["name"] == "victim"
            assert registered["peer_id"] == victim_peer

        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("identity squatting" in m or "핸드셰이크 거부" in m for m in warnings), \
            f"squatting 거부 로그가 없음: {warnings}"
    finally:
        attacker.stop()
        victim.stop()
        server.stop()


def test_stale_duplicate_peer_id_is_replaced_by_new_connection(caplog):
    """같은 peer_id 의 기존 연결이 STALE_PEER_SECONDS 넘게 조용하면(네트워크가 끊겨
    서버만 옛 소켓을 쥔 half-open) 새 연결이 그 자리를 넘겨받아야 한다 — 전엔 TCP
    재전송 한도(약 15분)까지 5초마다 «identity squatting 의심»으로 거부됐다.

    콜백은 main.py 의 peers 레지스트리와 같은 규칙(연결=기록, 해제=pop)으로 흉내 낸다:
    옛 연결의 해제는 새 연결 등록 «전»에 정확히 1번 — 옛 핸들러가 나중에 또 부르면
    같은 peer_id 로 새 연결의 기록이 지워진다."""
    import core.network as network_mod
    caplog.set_level(logging.INFO, logger="core.network")
    port = _free_port()
    peers, events = {}, []
    events_lock = threading.Lock()

    def on_connected(sock, address, name, peer_id):
        with events_lock:
            events.append(("connected", name))
            peers[peer_id] = name

    def on_disconnected(sock, address, name, peer_id):
        with events_lock:
            events.append(("disconnected", name))
            peers.pop(peer_id, None)

    server = NetworkServer(
        port=port, auth_key="shared-secret",
        tailscale_trust=False, bind_address="127.0.0.1",
    )
    server.on_client_connected = on_connected
    server.on_client_disconnected = on_disconnected
    server.start()

    peer_id = generate_peer_id()
    old = NetworkClient(host="127.0.0.1", port=port, auth_key="shared-secret",
                        device_name="old", peer_id=peer_id)
    old.reconnect_interval = 60  # 교체당한 뒤 다시 붙으려 하지 않게
    new = NetworkClient(host="127.0.0.1", port=port, auth_key="shared-secret",
                        device_name="new", peer_id=peer_id)
    new.reconnect_interval = 0.3
    try:
        old.start()
        assert _wait_until(lambda: old.connected, timeout=3.0)
        assert _wait_until(lambda: len(server.clients) == 1, timeout=2.0)

        # 옛 연결을 «조용한 지 오래»로 만든다 — 실제로는 네트워크 단절 뒤 PING 주기가 지나며 생긴다
        with server.clients_lock:
            info = next(iter(server.clients.values()))
            info["last_rx"] -= network_mod.STALE_PEER_SECONDS + 1

        new.start()
        assert _wait_until(lambda: new.connected, timeout=5.0), "새 연결이 받아들여지지 않음"
        with server.clients_lock:
            registered = list(server.clients.values())
        assert [r["name"] for r in registered] == ["new"]
        with events_lock:
            assert events == [("connected", "old"), ("disconnected", "old"), ("connected", "new")], events

        # 옛 핸들러가 «끝난 뒤»에도 해제 콜백이 다시 안 와야 한다. Windows 는 shutdown 이
        # 대기 중인 recv 를 깨우지 않아 그 핸들러가 다음 수신 타임아웃(30s)까지 남으므로,
        # 끊긴 상대가 결국 사라지는 것을 옛 클라이언트 종료로 대신한다(Linux 는 이미 끝나 있다).
        old.stop()
        # finally 진입 신호 — 콜백 분기 «앞»에 찍히는 줄이라 그 분기와 독립
        assert _wait_until(
            lambda: any(r.getMessage() == "클라이언트 연결 종료: old" for r in caplog.records),
            timeout=5.0), "옛 핸들러의 finally 가 돌지 않음"
        time.sleep(0.2)
        with events_lock:
            assert events == [("connected", "old"), ("disconnected", "old"), ("connected", "new")], events
            assert peers == {peer_id: "new"}, "옛 연결 해제가 새 연결의 등록을 지움"
        assert new.connected, "옛 연결 정리가 새 연결을 끊음"
        msgs = [r.getMessage() for r in caplog.records]
        assert any("옛 연결 교체" in m for m in msgs), msgs
        assert not any("핸드셰이크 거부" in m for m in msgs), "조용한 옛 연결인데 거부됨"
    finally:
        new.stop()
        old.stop()
        server.stop()


# ─── M9: 버전 불일치 disconnect 사유가 콜백으로 전달되는지 ─────────────

def test_client_disconnected_reason_reports_version_mismatch():
    """M9: 정상 연결됐던 client 가 재연결 시 호환되지 않는 프로토콜 버전의
    서버를 만나면, on_disconnected 콜백이 그 사유(reason)를 받아야 한다 —
    과거엔 상세 사유가 로그에만 남고 콜백은 인자가 없어(`on_disconnected()`)
    UI 는 "연결 끊김"이라는 일반 문구만 보여줄 수 있었다. 흔한 실사용 시나리오:
    한쪽 PC 만 먼저 업그레이드해 기존에 잘 붙던 페어링이 갑자기 깨지는데,
    사용자는 원인(버전 불일치)조차 알 수 없었다.

    (재연결 시나리오 — "한 번도 연결된 적 없는" 첫 페어링 시도에서도 통지가
    나가야 하는 건 test_client_disconnected_reason_fires_on_first_ever_attempt
    가 별도로 검증한다. 리뷰에서 was_connected 게이트가 그 경우를 막고 있던
    걸 발견해 hard break 사유는 게이트를 우회하도록 수정했다.)"""
    port = _free_port()
    real_server = NetworkServer(
        port=port, auth_key="shared", tailscale_trust=False, bind_address="127.0.0.1",
    )
    real_server.start()

    reasons = []
    client = NetworkClient(
        host="127.0.0.1", port=port, auth_key="shared", device_name="testdev",
    )
    client.reconnect_interval = 0.3
    client.on_disconnected = lambda reason="": reasons.append(reason)
    client.start()

    listener = None
    server_thread = None
    stop_fake = threading.Event()
    try:
        assert _wait_until(lambda: client.connected, timeout=3.0), "최초 연결 실패"

        # 실서버 종료 → 같은 포트에 "다른 버전" 가짜 서버로 교체
        real_server.stop()

        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(5)
        listener.settimeout(0.5)

        def fake_incompatible_server():
            while not stop_fake.is_set():
                try:
                    conn, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                proto = Protocol("shared")
                # 형식은 유효하지만(nonce/peer_id 정상) 버전만 비호환
                fake_challenge = proto.create_message(MSG_HANDSHAKE_CHALLENGE, {
                    "server_nonce": generate_nonce(),
                    "server_version": "1.0",
                    "server_peer_id": generate_peer_id(),
                })
                try:
                    conn.sendall(fake_challenge)
                except OSError:
                    pass
                finally:
                    conn.close()

        server_thread = threading.Thread(target=fake_incompatible_server, daemon=True)
        server_thread.start()

        assert _wait_until(
            lambda: any("version mismatch" in r or "hard break" in r for r in reasons),
            timeout=5.0,
        ), f"버전 불일치 사유가 reason 에 없음: {reasons}"
    finally:
        stop_fake.set()
        client.stop()
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        if server_thread is not None:
            server_thread.join(timeout=2)


def _run_fake_incompatible_server(port, stop_event):
    """비호환 버전 challenge 만 보내는 가짜 서버 — 리스너 소켓과 스레드를 반환."""
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(5)
    listener.settimeout(0.5)

    def loop():
        while not stop_event.is_set():
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            proto = Protocol("shared")
            fake_challenge = proto.create_message(MSG_HANDSHAKE_CHALLENGE, {
                "server_nonce": generate_nonce(),
                "server_version": "1.0",
                "server_peer_id": generate_peer_id(),
            })
            try:
                conn.sendall(fake_challenge)
            except OSError:
                pass
            finally:
                conn.close()

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return listener, thread


def test_client_disconnected_reason_fires_on_first_ever_attempt():
    """리뷰 발견: on_disconnected(reason=...) 가 was_connected(=_ever_connected)
    게이트에 막혀, "한 번도 연결된 적 없는" 첫 페어링 시도에서 버전 불일치가
    나도 콜백이 전혀 안 불렸다 — 신규 페어링/한쪽만 업그레이드가 가장 흔한
    실사용 트리거인데 그 경우를 놓치는 것이었다. hard break 사유는
    was_connected 여부와 무관하게 통지돼야 한다."""
    port = _free_port()
    stop_fake = threading.Event()
    listener, server_thread = _run_fake_incompatible_server(port, stop_fake)

    reasons = []
    client = NetworkClient(
        host="127.0.0.1", port=port, auth_key="shared", device_name="testdev",
    )
    client.reconnect_interval = 0.3
    client.on_disconnected = lambda reason="": reasons.append(reason)
    try:
        client.start()  # 이 프로세스에서 이 client 는 한 번도 연결된 적 없음
        assert _wait_until(
            lambda: any("version mismatch" in r or "hard break" in r for r in reasons),
            timeout=3.0,
        ), f"첫 연결 시도(한 번도 연결된 적 없음)에서 통지가 안 옴: {reasons}"
    finally:
        stop_fake.set()
        client.stop()
        try:
            listener.close()
        except OSError:
            pass
        server_thread.join(timeout=2)


def test_client_disconnected_reason_does_not_spam_on_repeated_retries():
    """동일 사유(버전 불일치)로 재연결 루프가 여러 번 돌아도 on_disconnected
    는 1회만 불려야 한다 — 매번 통지하면 5초(테스트는 더 짧게)마다 스팸이 된다."""
    port = _free_port()
    stop_fake = threading.Event()
    listener, server_thread = _run_fake_incompatible_server(port, stop_fake)

    reasons = []
    client = NetworkClient(
        host="127.0.0.1", port=port, auth_key="shared", device_name="testdev",
    )
    client.reconnect_interval = 0.2
    client.on_disconnected = lambda reason="": reasons.append(reason)
    try:
        client.start()
        assert _wait_until(lambda: len(reasons) >= 1, timeout=3.0)
        # 재연결 루프가 여러 번 더 돌 시간을 준다 — 스팸이면 여기서 늘어남
        time.sleep(1.5)
        assert len(reasons) == 1, (
            f"동일 사유로 재연결마다 반복 통지됨 (스팸): {reasons}"
        )
    finally:
        stop_fake.set()
        client.stop()
        try:
            listener.close()
        except OSError:
            pass
        server_thread.join(timeout=2)


# ── 2026-09-28 UX 검토 B1: 첫 연결 실패 사유가 UI 로 올라와야 함 ──────────

def test_wrong_key_first_attempt_sets_last_error_and_notifies_once():
    """한 번도 연결된 적 없는 첫 시도의 키 불일치는 on_disconnected 가 안 불린다 —
    그래서 트레이가 "왜 안 붙는지" 를 몰랐다. last_error + on_error_changed 로 올린다."""
    from ui.tray_status import classify_client_error

    port = _free_port()
    server = NetworkServer(
        port=port, auth_key="server-key", tailscale_trust=False, bind_address="127.0.0.1",
    )
    server.start()
    client = NetworkClient(
        host="127.0.0.1", port=port, auth_key="WRONG-CLIENT-KEY", device_name="dev",
    )
    calls = []
    client.on_error_changed = lambda: calls.append(client.last_error)
    client.reconnect_interval = 0.2   # 재시도 여러 번 — 같은 사유는 1회만 통지돼야 함
    client.start()
    try:
        assert _wait_until(lambda: bool(client.last_error), timeout=3.0), "last_error 가 안 채워짐"
        time.sleep(1.0)  # 재시도 몇 번 더 돌게
        assert classify_client_error(client.last_error) == "auth", client.last_error
        assert len(calls) == 1, f"같은 사유가 재시도마다 통지됨: {calls}"
    finally:
        client.stop()
        server.stop()


def test_successful_connect_clears_last_error():
    port = _free_port()
    client = NetworkClient(host="127.0.0.1", port=port, auth_key="k", device_name="dev")
    client.reconnect_interval = 0.2
    client.start()   # 아직 서버 없음 → 거부
    server = None
    try:
        assert _wait_until(lambda: bool(client.last_error), timeout=3.0)
        server = NetworkServer(port=port, auth_key="k", tailscale_trust=False,
                               bind_address="127.0.0.1")
        server.start()
        assert _wait_until(lambda: client.connected, timeout=5.0)
        assert client.last_error == ""
    finally:
        client.stop()
        if server:
            server.stop()
