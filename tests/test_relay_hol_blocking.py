"""서버 릴레이 head-of-line 차단 회귀 테스트 (infinite-clipboard::server-relay-hol-blocking).

수신자 한 대(R)가 소켓을 읽지 않으면 그 소켓으로의 sendall 이 송신 타임아웃까지
막힌다. 과거엔 그 막힘이 송신 «호출자» 스레드에서 일어나서:

  (a) 서버 broadcast 루프가 R 에서 멈춰 정상 수신자 C 도 늦게 받고,
  (b) 다른 발신자 B 의 수신 처리 스레드(중계 호출자)가 R 의 write lock 에 막혀
      B 가 보낸 텍스트가 C 에 늦게 닿고,
  (c) R 이 영영 안 읽어도 R 을 «끊기» 전까지 송신 타임아웃(30초) 만큼 걸렸다.

R 은 «메시지 콜백에서 영원히 멈추는» 실제 NetworkClient 로 만든다 — 그 클라이언트의
수신 스레드가 콜백 안에 묶여 소켓을 더 읽지 않으므로 커널 버퍼가 차면 서버 송신이 막힌다.
시간 상수는 monkeypatch 로 줄여 테스트가 수 초 안에 끝나게 한다.
"""

import threading
import time
import uuid

import pytest

import core.network as network
from core.network import NetworkClient, NetworkServer
from core.protocol import (
    Protocol, MSG_CLIPBOARD, MSG_FILE_CHUNK, generate_peer_id,
)

from tests.test_routing import _free_port, _wait_until, _Peer

STALL = 2.0  # 테스트용 송신 정지 판정 시간(초)
CHUNK = b"\xab" * (1024 * 1024)


@pytest.fixture(autouse=True)
def _short_timeouts(monkeypatch):
    # 소켓 타임아웃(수신·송신 공용)과 송신 정지 판정을 짧게
    monkeypatch.setattr(network, "CLIENT_RECV_TIMEOUT", STALL)
    if hasattr(network, "SEND_STALL_SECONDS"):
        monkeypatch.setattr(network, "SEND_STALL_SECONDS", STALL)


class _StalledPeer(_Peer):
    """첫 메시지를 받는 순간 수신 스레드를 영원히(테스트 끝까지) 멈추는 피어."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.release = threading.Event()

    def _on_msg(self, message):
        self.received.append(message)
        self.release.wait(30)

    def stop(self):
        self.release.set()
        super().stop()


def _start_server(port):
    server = NetworkServer(
        port=port, auth_key="shared", tailscale_trust=False,
        bind_address="127.0.0.1", peer_id=generate_peer_id(),
    )
    server.start()
    return server


def _chunk_for(peer_id):
    return Protocol("shared").create_binary_chunk(
        transfer_id=str(uuid.uuid4()), file_path="big.bin", chunk_index=0,
        chunk_data=CHUNK, chunk_hash="ff", receiver_peer=peer_id,
    )


def _flood(server, peer_id, n=64):
    """R 로 1MB 청크를 계속 밀어 커널 버퍼를 채운다(백그라운드)."""
    raw = _chunk_for(peer_id)

    def run():
        for _ in range(n):
            if not server.send_raw_to_peer(peer_id, raw):
                break

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _connect(server, port, *peers):
    for p in peers:
        p.start()
        # 연결 순서 = server.clients 삽입 순서(broadcast 순회 순서)
        assert _wait_until(lambda p=p: p.client.connected, timeout=4.0)
    assert _wait_until(lambda: len(server.clients) == len(peers), timeout=4.0)


def _latency_of_text(receiver, send_fn, marker):
    t0 = time.monotonic()
    send_fn()
    ok = _wait_until(
        lambda: any(
            m.get("type") == MSG_CLIPBOARD
            and (m.get("data") or {}).get("content") == marker
            for m in receiver.received
        ),
        timeout=STALL * 4,
    )
    return ok, time.monotonic() - t0


def test_broadcast_not_blocked_by_stalled_receiver():
    """(a) R 이 막혀 있어도 서버 broadcast 가 C 에 즉시 닿는다."""
    port = _free_port()
    server = _start_server(port)
    r = _StalledPeer("127.0.0.1", port, "shared", "stalled", generate_peer_id())
    c = _Peer("127.0.0.1", port, "shared", "normal", generate_peer_id())
    try:
        _connect(server, port, r, c)  # R 먼저 — broadcast 가 R 부터 돈다
        _flood(server, r.peer_id)
        time.sleep(0.5)  # 버퍼가 차서 R 송신이 막힐 시간

        ok, dt = _latency_of_text(
            c,
            lambda: server.broadcast(MSG_CLIPBOARD, {"content_type": "text", "content": "hello-a"}),
            "hello-a",
        )
        assert ok, "C 가 broadcast 를 받아야"
        assert dt < 1.0, f"정상 수신자 C 의 지연이 막힌 R 에 묶임: {dt:.2f}s"
    finally:
        r.stop(); c.stop(); server.stop()


def test_other_sender_not_blocked_by_stalled_receiver():
    """(b) 다른 발신자 B 의 텍스트가 R 막힘과 무관하게 C 에 닿는다.

    서버 라우팅은 main.py 와 같은 모양으로 최소 구성: receiver_peer 있는 청크는
    send_raw_to_peer, 텍스트는 broadcast(exclude 발신자).
    """
    port = _free_port()
    server = _start_server(port)

    def route(sock, message):
        data = message.get("data") or {}
        if message.get("type") == MSG_FILE_CHUNK and data.get("receiver_peer"):
            server.send_raw_to_peer(data["receiver_peer"], message["_raw"])
        elif message.get("type") == MSG_CLIPBOARD:
            server.broadcast(MSG_CLIPBOARD, data, exclude_sock=sock)

    server.on_message_received = route
    r = _StalledPeer("127.0.0.1", port, "shared", "stalled", generate_peer_id())
    a = _Peer("127.0.0.1", port, "shared", "sender-a", generate_peer_id())
    b = _Peer("127.0.0.1", port, "shared", "sender-b", generate_peer_id())
    c = _Peer("127.0.0.1", port, "shared", "normal", generate_peer_id())
    try:
        _connect(server, port, r, a, b, c)
        raw = _chunk_for(r.peer_id)

        def a_stream():
            for _ in range(64):
                if not a.client.connected:
                    break
                a.client.send_raw(raw)

        threading.Thread(target=a_stream, daemon=True).start()
        time.sleep(0.8)  # A→R 중계가 R 버퍼를 채워 막힐 시간

        ok, dt = _latency_of_text(
            c,
            lambda: b.client.send(MSG_CLIPBOARD, {"content_type": "text", "content": "hello-b"}),
            "hello-b",
        )
        assert ok, "C 가 B 의 텍스트를 받아야"
        assert dt < 1.0, f"B→C 텍스트가 막힌 R 에 묶임: {dt:.2f}s"
    finally:
        for p in (r, a, b, c):
            p.stop()
        server.stop()


def test_stalled_receiver_is_dropped_and_sends_fail_fast():
    """(c) 진행 없는 R 은 정지 판정 시간 안에 끊기고, 이후 R 행 송신은 즉시 False."""
    port = _free_port()
    server = _start_server(port)
    r = _StalledPeer("127.0.0.1", port, "shared", "stalled", generate_peer_id())
    try:
        _connect(server, port, r)
        flood = _flood(server, r.peer_id, n=256)
        assert _wait_until(lambda: server._socket_for_peer(r.peer_id) is None,
                           timeout=STALL * 3), "막힌 R 이 정지 판정 뒤 라우팅 표에서 빠져야"
        flood.join(timeout=STALL * 2)
        assert not flood.is_alive(), "R 이 끊긴 뒤 R 행 송신 호출이 계속 막혀 있으면 안 됨"
        t0 = time.monotonic()
        assert server.send_raw_to_peer(r.peer_id, b"x") is False
        assert time.monotonic() - t0 < 0.1
    finally:
        r.stop(); server.stop()


def test_slow_but_progressing_receiver_is_not_dropped():
    """느리지만 계속 읽는 수신자는 정지 판정으로 끊지 않는다(진행량 기준)."""
    port = _free_port()
    server = _start_server(port)
    got = []

    class _SlowPeer(_Peer):
        def _on_msg(self, message):
            got.append(1)
            time.sleep(0.15)  # 느린 소비자 — 그래도 계속 읽는다

    s = _SlowPeer("127.0.0.1", port, "shared", "slow", generate_peer_id())
    try:
        _connect(server, port, s)
        raw = _chunk_for(s.peer_id)
        t0 = time.monotonic()
        sent = 0
        # STALL 의 두 배가 넘게 계속 보낸다
        while time.monotonic() - t0 < STALL * 2.5:
            assert server.send_raw_to_peer(s.peer_id, raw), "진행 중인 수신자가 끊김"
            sent += 1
        assert server._socket_for_peer(s.peer_id) is not None
        assert _wait_until(lambda: len(got) >= sent, timeout=sent * 0.3 + 5)
    finally:
        s.stop(); server.stop()


def test_relay_failure_tells_source_to_stop():
    """중계 대상이 없으면 서버가 발신자에게 FILE_ERROR 를 1회 보내 발신자가 전송을 멈춘다.

    과거엔 send_raw_to_peer 의 False 를 버려 발신자가 끊긴 수신자에게 파일 나머지를
    계속 보내며 outgoing 슬롯(_outgoing_fetch_lock)을 붙잡았다.
    """
    from config import AppConfig
    from main import InfiniteClipboard

    key = "test-shared-key-for-relay-failure-notify-0001"
    port = _free_port()
    server_app = InfiniteClipboard(AppConfig(
        mode="server", port=port, auth_key=key, peer_id=generate_peer_id(),
        tailscale_trust=False, bind_address="127.0.0.1",
    ))
    source_app = InfiniteClipboard(AppConfig(
        mode="client", server_host="127.0.0.1", port=port, auth_key=key,
        peer_id=generate_peer_id(),
    ))
    server_app._start_server()
    source_app._start_client()
    try:
        assert _wait_until(lambda: source_app.client and source_app.client.connected)
        assert _wait_until(lambda: len(server_app.peers) == 1)
        tid = str(uuid.uuid4())
        gone = generate_peer_id()  # 서버에 없는 수신자
        sent_errors = []
        orig_send_to = server_app.server.send_to

        def spy(sock, msg_type, data=None):
            sent_errors.append(msg_type)
            return orig_send_to(sock, msg_type, data)

        server_app.server.send_to = spy
        for i in range(3):
            raw = source_app.protocol.create_binary_chunk(
                tid, "big.bin", i, b"x" * 1024, "ff", receiver_peer=gone,
            )
            source_app.client.send_raw(raw)

        assert _wait_until(lambda: tid in source_app._cancelled_transfers, timeout=3.0), \
            "발신자가 중계 실패 알림(FILE_ERROR)을 받아 전송을 멈춰야"
        time.sleep(0.2)
        from core.protocol import MSG_FILE_ERROR
        assert sent_errors.count(MSG_FILE_ERROR) == 1, f"transfer 당 1회만: {sent_errors}"
    finally:
        if source_app.client:
            source_app.client.stop()
        if server_app.server:
            server_app.server.stop()
