"""M7 회귀 — send_to_peer 반환값을 버려 FETCH_FAIL_OFFLINE 이 죽은 코드였던 문제.

2026-07-03 감사 M7: `_server_route`가 `send_to_peer`의 성공 여부를 버려서,
MSG_CLIP_FETCH 의 대상(source_peer)이 서버에 연결돼 있지 않으면(오프라인)
requester 는 아무 응답도 못 받고 최대 600초(하드 타임아웃)까지 기다려야 했다.
`FETCH_FAIL_OFFLINE` 상수는 이미 정의돼 있었지만 실제로 쓰이는 곳이 없었다.
수정: `_server_route`가 relay 성공 여부를 반환하고, MSG_CLIP_FETCH 핸들러가
실패 시 즉시 FETCH_FAIL_OFFLINE 을 requester 에게 보낸다.
"""

import socket
import threading
import time
import uuid

from config import AppConfig
from core.protocol import FETCH_FAIL_OFFLINE, generate_peer_id
from main import InfiniteClipboard


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


_KEY = "test-shared-key-for-m7-offline-fetch-fail-01"


def test_fetch_to_offline_source_gets_immediate_fetch_fail_offline():
    """요청 대상(source_peer)이 서버에 연결돼있지 않으면, requester 는 최대
    600초 대기 대신 즉시 FETCH_FAIL_OFFLINE 을 받아야 한다."""
    port = _free_port()
    server_app = InfiniteClipboard(AppConfig(
        mode="server", port=port, auth_key=_KEY, peer_id=generate_peer_id(),
        tailscale_trust=False, bind_address="127.0.0.1",
    ))
    client_app = InfiniteClipboard(AppConfig(
        mode="client", server_host="127.0.0.1", port=port, auth_key=_KEY,
        peer_id=generate_peer_id(),
    ))

    server_app._start_server()
    client_app._start_client()
    try:
        assert _wait_until(lambda: client_app.client and client_app.client.connected), \
            "클라이언트가 서버에 연결되지 않음"
        assert _wait_until(lambda: len(server_app.peers) == 1), \
            "서버가 클라이언트 peer 를 학습하지 못함"

        offer_id = str(uuid.uuid4())
        offline_source_peer = generate_peer_id()  # 서버에 연결된 적 없는 peer

        event = threading.Event()
        client_app._active_fetch = {
            "offer_id": offer_id, "transfer_id": offer_id,
            "event": event, "paths": None, "fail": None,
        }

        raw = client_app.protocol.create_clip_fetch(
            offer_id, client_app.config.peer_id, receiver_peer=offline_source_peer,
        )
        client_app._send_raw_to(raw, offline_source_peer)

        assert _wait_until(lambda: event.is_set(), timeout=3.0), (
            "M7 회귀 — 오프라인 source 에 대한 fetch 가 즉시 실패 처리되지 않음 "
            "(최대 600초 하드 타임아웃까지 무응답이었을 위험)"
        )
        assert client_app._active_fetch["fail"] == FETCH_FAIL_OFFLINE
    finally:
        if client_app.client:
            client_app.client.stop()
        if server_app.server:
            server_app.server.stop()
