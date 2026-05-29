"""v3.0 S1b: targeted relay routing 회귀 테스트.

별-토폴로지에서 서버가 receiver_peer 가진 메시지를 broadcast 대신 해당 peer 1
소켓에만 중계하는지 실제 socket 으로 검증한다. spec-review CRITICAL — 텍스트만으로
green 통과 금지: **실제 binary chunk 가 receiver_peer 1 소켓에만 도달**을 본다.

  - send_raw_to_peer (바이너리 chunk) → 대상 peer 만 수신, 나머지 미도달
  - send_to_peer (JSON) → 대상 peer 만 수신
  - 미존재 peer_id → graceful drop (False, 예외 없음)
  - broadcast → 여전히 전원 수신 (eager 경로 무변)
  - create_binary_chunk receiver_peer round-trip + 형식 위반 frame 거부
"""

import socket
import time
import uuid

from core.network import NetworkClient, NetworkServer
from core.protocol import (
    Protocol, MSG_FILE_CHUNK, MSG_CLIPBOARD,
    generate_peer_id,
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


def _valid_tid() -> str:
    return str(uuid.uuid4())


class _Peer:
    """수신 메시지를 누적하는 테스트용 클라이언트 래퍼."""

    def __init__(self, host, port, auth_key, name, peer_id):
        self.received = []
        self.client = NetworkClient(
            host=host, port=port, auth_key=auth_key,
            device_name=name, peer_id=peer_id,
        )
        self.client.on_message_received = self._on_msg
        self.peer_id = peer_id

    def _on_msg(self, message):
        self.received.append(message)

    def start(self):
        self.client.start()

    def stop(self):
        self.client.stop()

    def types(self):
        return [m.get("type") for m in self.received]


def _setup_server_and_clients(n=2):
    """서버 + n 클라이언트 기동, 핸드셰이크 완료 후 (server, [peers]) 반환."""
    port = _free_port()
    server = NetworkServer(
        port=port, auth_key="shared", tailscale_trust=False,
        bind_address="127.0.0.1", peer_id=generate_peer_id(),
    )
    server.start()

    peers = []
    for i in range(n):
        p = _Peer("127.0.0.1", port, "shared", f"dev{i}", generate_peer_id())
        p.start()
        peers.append(p)

    # 전원 연결 + 서버에 n 개 등록될 때까지 대기
    assert _wait_until(
        lambda: all(p.client.connected for p in peers) and len(server.clients) == n,
        timeout=4.0,
    ), "모든 클라이언트가 핸드셰이크 완료해야"
    return server, peers


def _teardown(server, peers):
    for p in peers:
        p.stop()
    server.stop()


# ─── send_raw_to_peer: 바이너리 chunk targeted relay (Gate 핵심) ──────


def test_send_raw_to_peer_reaches_only_target():
    """실제 binary chunk 를 receiver_peer 1 소켓에만 — 다른 peer 미도달."""
    server, peers = _setup_server_and_clients(2)
    b, c = peers
    try:
        # B 를 향한 실제 바이너리 chunk 프레임
        proto = Protocol("shared")
        raw = proto.create_binary_chunk(
            transfer_id=_valid_tid(), file_path="x.bin", chunk_index=0,
            chunk_data=b"\x00\x01\x02" * 1000, chunk_hash="ff",
            receiver_peer=b.peer_id,
        )

        ok = server.send_raw_to_peer(b.peer_id, raw)
        assert ok is True

        # B 는 file_chunk 수신, C 는 아무것도 수신 안 함
        assert _wait_until(lambda: MSG_FILE_CHUNK in b.types(), timeout=2.0), \
            "대상 peer B 가 chunk 를 받아야"
        time.sleep(0.2)  # C 에 잘못 도달했다면 이 사이에 들어옴
        assert MSG_FILE_CHUNK not in c.types(), \
            f"비대상 peer C 에 chunk 가 새어나감: {c.types()}"
    finally:
        _teardown(server, peers)


def test_send_to_peer_reaches_only_target():
    """JSON 메시지도 targeted — 대상만 수신."""
    server, peers = _setup_server_and_clients(2)
    b, c = peers
    try:
        ok = server.send_to_peer(b.peer_id, MSG_CLIPBOARD,
                                 {"content_type": "text", "content": "hi-B"})
        assert ok is True
        assert _wait_until(lambda: MSG_CLIPBOARD in b.types(), timeout=2.0)
        time.sleep(0.2)
        assert MSG_CLIPBOARD not in c.types(), \
            f"비대상 peer C 에 JSON 이 새어나감: {c.types()}"
    finally:
        _teardown(server, peers)


def test_send_to_peer_missing_peer_graceful():
    """미존재 peer_id → False 반환 + 예외 없음 (graceful drop)."""
    server, peers = _setup_server_and_clients(1)
    try:
        assert server.send_to_peer(generate_peer_id(), MSG_CLIPBOARD, {"x": 1}) is False
        assert server.send_raw_to_peer(generate_peer_id(), b"\x00garbage") is False
        # 실존 클라이언트는 영향 없음
        assert len(server.clients) == 1
    finally:
        _teardown(server, peers)


def test_broadcast_still_reaches_all():
    """eager 경로 무변 확인 — broadcast 는 여전히 전원 수신."""
    server, peers = _setup_server_and_clients(2)
    b, c = peers
    try:
        server.broadcast(MSG_CLIPBOARD, {"content_type": "text", "content": "all"})
        assert _wait_until(
            lambda: MSG_CLIPBOARD in b.types() and MSG_CLIPBOARD in c.types(),
            timeout=2.0,
        ), f"broadcast 가 전원 도달해야: B={b.types()} C={c.types()}"
    finally:
        _teardown(server, peers)


# ─── protocol: create_binary_chunk receiver_peer ─────────────────────


def test_binary_chunk_receiver_peer_roundtrip():
    p = Protocol()
    rcv = generate_peer_id()
    wire = p.create_binary_chunk(
        transfer_id=_valid_tid(), file_path="a.bin", chunk_index=3,
        chunk_data=b"payload", chunk_hash="ab", receiver_peer=rcv,
    )
    parsed = p.parse_message(wire[4:])
    assert parsed["type"] == MSG_FILE_CHUNK
    assert parsed["data"]["receiver_peer"] == rcv
    assert parsed["data"]["binary_data"] == b"payload"


def test_binary_chunk_empty_receiver_peer_is_broadcast():
    """receiver_peer 기본값 "" 은 broadcast 신호 — 정상 파싱."""
    p = Protocol()
    wire = p.create_binary_chunk(
        transfer_id=_valid_tid(), file_path="a.bin", chunk_index=0,
        chunk_data=b"x", chunk_hash="ab",
    )
    parsed = p.parse_message(wire[4:])
    assert parsed["data"]["receiver_peer"] == ""


def test_binary_chunk_invalid_receiver_peer_rejected():
    """형식 위반 receiver_peer 가 박힌 프레임은 거부 (None)."""
    p = Protocol()
    tid = _valid_tid()
    # 정상 프레임을 만든 뒤 meta 의 receiver_peer 를 손상시켜 재조립
    import json, struct
    meta = json.dumps({
        "type": MSG_FILE_CHUNK,
        "data": {
            "transfer_id": tid, "file_path": "a.bin", "chunk_index": 0,
            "hash": "ab", "receiver_peer": "not-a-valid-peer-id",
        },
    }).encode("utf-8")
    payload = b"\x00" + struct.pack(">I", len(meta)) + meta + b"data"
    assert p.parse_message(payload) is None
