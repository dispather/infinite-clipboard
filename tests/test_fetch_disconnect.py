"""연결이 끊기면 진행 중 fetch 가 하드 타임아웃까지 기다리지 않고 즉시 실패한다.

2026-09-28 mac 실기(함정 #46 부수): 교착으로 서버가 연결을 끊은 뒤, 두 번째 fetch 는 이미
죽은 연결로 요청을 보내고 청크 0개로 256s 를 기다렸다. 끊긴 연결로는 응답이 올 수 없으므로
- 연결 끊김 이벤트가 진행 중 fetch 를 FETCH_FAIL_OFFLINE 으로 실패시키고
  (클라이언트: 서버 연결 끊김 / 서버: 원본 peer 연결 끊김)
- 요청 송신 자체가 실패하면(연결 없음) 즉시 실패해야 한다.
macOS 는 fetch 동안 메인 스레드가 paste 콜백에 묶이므로 이 대기가 곧 UI 정지 시간이다.
"""

import socket
import threading
import time

from config import AppConfig
from core.protocol import FETCH_FAIL_OFFLINE, generate_peer_id
from main import FetchFailure, InfiniteClipboard

_KEY = "loopback-shared-secret-key-0123456789ab"
# 수정 전에는 이 시간까지 기다렸다 — 단언은 이보다 한참 짧은 시간 안에 끝나기를 본다.
_LONG_FETCH_TIMEOUT = 20.0


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_until(predicate, timeout: float = 4.0, interval: float = 0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class _StubProvider:
    def __init__(self):
        self.captured = None

    def is_supported(self, kind):
        return kind in ("file", "image")

    def register_offer(self, offer, fetch_callback):
        self.captured = (offer, fetch_callback)
        return True

    def owns_clipboard(self):
        return False

    def clear(self):
        pass

    def stop(self):
        pass


def _make_app(mode, port, download_path) -> InfiniteClipboard:
    download_path.mkdir(parents=True, exist_ok=True)
    cfg = AppConfig(
        mode=mode, server_host="127.0.0.1", port=port, auth_key=_KEY,
        peer_id=generate_peer_id(), download_path=str(download_path),
        tailscale_trust=False, bind_address="127.0.0.1",
        fetch_grace_seconds=0, lazy_paste=True,
    )
    app = InfiniteClipboard(cfg)
    app.lazy_provider = _StubProvider()
    app._lazy_provider_inited = True
    app._fetch_timeout = lambda total_size: _LONG_FETCH_TIMEOUT
    return app


def _gate_serve(source_app):
    """원본의 _serve_fetch 를 게이트로 붙잡는다 — fetch 가 «진행 중» 인 상태를 만든다."""
    gate, arrived = threading.Event(), threading.Event()
    orig = source_app._serve_fetch

    def _gated(*args, **kwargs):
        arrived.set()
        gate.wait(15.0)
        return orig(*args, **kwargs)

    source_app._serve_fetch = _gated
    return gate, arrived


def _fetch_in_thread(fetch_cb, offer_id):
    holder = {"done": threading.Event(), "t0": time.monotonic()}

    def _run():
        try:
            holder["result"] = fetch_cb(offer_id)
        except Exception as e:  # noqa: BLE001
            holder["error"] = e
        finally:
            holder["elapsed"] = time.monotonic() - holder["t0"]
            holder["done"].set()

    threading.Thread(target=_run, daemon=True).start()
    return holder


def _src_file(tmp_path, name):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    f = src / name
    f.write_bytes(bytes(range(256)) * 400)
    return str(f)


def _connect(server_app, client_app):
    server_app._start_server()
    client_app._start_client()
    assert _wait_until(lambda: client_app.client and client_app.client.connected)
    assert _wait_until(lambda: len(server_app.peers) == 1)


def _assert_offline_fast(holder, max_elapsed=_LONG_FETCH_TIMEOUT / 2):
    assert holder["done"].wait(6.0), "연결 끊김 뒤에도 fetch 가 계속 기다림(하드 타임아웃 대기)"
    err = holder.get("error")
    assert isinstance(err, FetchFailure) and err.reason == FETCH_FAIL_OFFLINE, f"error={err!r}"
    assert holder["elapsed"] < max_elapsed, f"elapsed={holder['elapsed']:.2f}s"


def test_client_fetch_fails_fast_when_server_connection_drops(tmp_path):
    """[client=receiver] fetch 진행 중 서버 연결이 끊기면 즉시 offline 실패."""
    port = _free_port()
    server_app = _make_app("server", port, tmp_path / "srv_dl")
    client_app = _make_app("client", port, tmp_path / "cli_dl")
    gate, arrived = _gate_serve(server_app)
    try:
        _connect(server_app, client_app)
        server_app._announce_offer([_src_file(tmp_path, "drop-a.bin")])
        stub = client_app.lazy_provider
        assert _wait_until(lambda: stub.captured is not None)
        offer, fetch_cb = stub.captured
        holder = _fetch_in_thread(fetch_cb, offer["offer_id"])
        assert arrived.wait(4.0), "fetch 요청이 원본에 도착하지 않음"

        server_app.stop()  # 서버 연결 끊김
        _assert_offline_fast(holder)
    finally:
        gate.set()
        client_app.stop()
        server_app.stop()


def test_client_fetch_fails_immediately_when_request_cannot_be_sent(tmp_path):
    """[client=receiver] 이미 연결이 없으면 요청 송신 실패로 즉시 offline 실패."""
    port = _free_port()
    server_app = _make_app("server", port, tmp_path / "srv_dl")
    client_app = _make_app("client", port, tmp_path / "cli_dl")
    try:
        _connect(server_app, client_app)
        server_app._announce_offer([_src_file(tmp_path, "nosend-a.bin")])
        stub = client_app.lazy_provider
        assert _wait_until(lambda: stub.captured is not None)
        offer, fetch_cb = stub.captured

        server_app.stop()
        assert _wait_until(lambda: not client_app.client.connected, timeout=4.0)
        holder = _fetch_in_thread(fetch_cb, offer["offer_id"])
        # 즉시여야 한다 — 이 검사가 없으면 클라이언트 재연결 시도(5초 주기)가 실패하며
        # 부르는 연결 끊김 콜백이 대신 풀어 줘서 «5초 뒤 실패»로도 통과해 버린다.
        _assert_offline_fast(holder, max_elapsed=2.0)
    finally:
        client_app.stop()
        server_app.stop()


def test_server_fetch_fails_fast_when_source_client_disconnects(tmp_path):
    """[server=receiver] 원본 client 가 fetch 중 끊기면 그 fetch 만 즉시 offline 실패."""
    port = _free_port()
    server_app = _make_app("server", port, tmp_path / "srv_dl")
    client_app = _make_app("client", port, tmp_path / "cli_dl")
    gate, arrived = _gate_serve(client_app)
    try:
        _connect(server_app, client_app)
        client_app._announce_offer([_src_file(tmp_path, "srcdrop-a.bin")])
        stub = server_app.lazy_provider
        assert _wait_until(lambda: stub.captured is not None)
        offer, fetch_cb = stub.captured
        holder = _fetch_in_thread(fetch_cb, offer["offer_id"])
        assert arrived.wait(4.0), "fetch 요청이 원본에 도착하지 않음"

        client_app.stop()  # 원본 peer 연결 끊김
        _assert_offline_fast(holder)
    finally:
        gate.set()
        client_app.stop()
        server_app.stop()


def test_server_disconnect_of_unrelated_peer_keeps_fetch(tmp_path):
    """[server=receiver] 원본이 아닌 peer 의 연결 끊김은 진행 중 fetch 를 건드리지 않는다."""
    port = _free_port()
    server_app = _make_app("server", port, tmp_path / "srv_dl")
    client_app = _make_app("client", port, tmp_path / "cli_dl")
    gate, arrived = _gate_serve(client_app)
    try:
        _connect(server_app, client_app)
        client_app._announce_offer([_src_file(tmp_path, "unrelated-a.bin")])
        stub = server_app.lazy_provider
        assert _wait_until(lambda: stub.captured is not None)
        offer, fetch_cb = stub.captured
        holder = _fetch_in_thread(fetch_cb, offer["offer_id"])
        assert arrived.wait(4.0)

        server_app._fail_active_fetch_on_disconnect(generate_peer_id())  # 무관한 peer
        assert not holder["done"].wait(0.5), "무관한 peer 끊김이 fetch 를 실패시킴"

        gate.set()  # 원본 송신 재개 → 정상 완료
        assert holder["done"].wait(6.0)
        assert "error" not in holder, f"error={holder.get('error')!r}"
        assert len(holder["result"].paths) == 1
    finally:
        gate.set()
        client_app.stop()
        server_app.stop()
