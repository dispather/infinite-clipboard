"""v3.0 S2c: lazy offer/fetch 오케스트레이션 loopback 통합 테스트.

실제 localhost 소켓으로 server + client InfiniteClipboard 를 띄우고, OS 클립보드/GUI
없이 lazy 흐름 전체를 검증한다:
  copy(_announce_offer) → MSG_CLIP_OFFER → client 등록 → paste(fetch_callback 호출)
  → MSG_CLIP_FETCH → source 가 FILE_READY/CHUNK/END/COMPLETE 를 requester 에게만
  targeted 전송 → client 조립 → FetchedContent(paths) 반환.

이 개발 환경은 헤드리스라 OS provider round-trip 은 못 돌리지만(각 백엔드 CI 가 담당),
**오케스트레이션 로직(라우팅·offer 상태·fetch 대기·eager 제거)** 은 stub provider 로
종단 검증한다. provider 는 fetch_callback 을 캡처만 하고, 테스트가 paste 처럼 호출한다.
"""

import os
import socket
import time

import pytest

from config import AppConfig
from core.protocol import generate_peer_id
from main import InfiniteClipboard


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


_KEY = "loopback-shared-secret-key-0123456789ab"  # 강한 키 (자동 업그레이드 회피)


class _StubProvider:
    """OS 등록 대신 fetch_callback 을 캡처하는 lazy provider 더미.

    register_offer 가 (offer, fetch_callback) 을 보관하면, 테스트가 paste 를 흉내내
    fetch_callback(offer_id) 를 직접 호출한다(= OS 가 paste 시점에 부르는 것과 동치).
    """

    def __init__(self):
        self.captured = None
        self.cleared = 0

    def is_supported(self, kind):
        return kind in ("file", "image")

    def register_offer(self, offer, fetch_callback):
        self.captured = (offer, fetch_callback)
        return True

    def clear(self):
        self.cleared += 1

    def stop(self):
        pass


def _make_app(mode, port, download_path) -> InfiniteClipboard:
    download_path.mkdir(parents=True, exist_ok=True)
    cfg = AppConfig(
        mode=mode,
        server_host="127.0.0.1",
        port=port,
        auth_key=_KEY,
        peer_id=generate_peer_id(),
        download_path=str(download_path),
        tailscale_trust=False,
        bind_address="127.0.0.1",
    )
    return InfiniteClipboard(cfg)


def _setup_pair(tmp_path):
    """연결된 server+client 쌍. 양쪽에 stub provider 설치.

    반환: (server_app, client_app, server_stub, client_stub). source 가 누구냐에
    따라 receiver 쪽 stub 의 captured 를 본다 (server 복사→client_stub, 반대→server_stub).
    """
    port = _free_port()
    server_app = _make_app("server", port, tmp_path / "srv_dl")
    client_app = _make_app("client", port, tmp_path / "cli_dl")

    server_stub, client_stub = _StubProvider(), _StubProvider()
    server_app.lazy_provider = server_stub
    server_app._lazy_provider_inited = True
    client_app.lazy_provider = client_stub
    client_app._lazy_provider_inited = True

    server_app._start_server()
    client_app._start_client()

    assert _wait_until(lambda: client_app.client and client_app.client.connected), \
        "client 가 연결되지 않음"
    assert _wait_until(lambda: len(server_app.peers) == 1), \
        "server 가 client peer 를 학습하지 못함"
    return server_app, client_app, server_stub, client_stub


def test_lazy_file_roundtrip_loopback(tmp_path):
    """copy→offer→(paste)fetch→targeted 전송→조립 종단. eager 자동전송 없음 확인."""
    src = tmp_path / "src"
    src.mkdir()
    f1 = src / "a.txt"; f1.write_bytes(b"hello-A " * 2000)       # ~16KB
    f2 = src / "b.bin"; f2.write_bytes(bytes(range(256)) * 300)  # ~76KB
    file_paths = [str(f1), str(f2)]

    server_app, client_app, _server_stub, stub = _setup_pair(tmp_path)
    try:
        # 1. source(서버) 복사 → offer broadcast (eager 전송 안 함)
        server_app._announce_offer(file_paths)

        # 2. client 가 offer 수신 + stub 에 등록 (paste 전엔 전송 0)
        assert _wait_until(lambda: stub.captured is not None, timeout=4.0), \
            "client 가 offer 를 등록하지 못함"
        offer, fetch_cb = stub.captured
        assert offer["kind"] == "file"
        assert offer["source_peer"] == server_app.config.peer_id
        # 아직 아무 파일도 수신 안 됨 (lazy — paste 전)
        with client_app._transfers_lock:
            assert len(client_app.pending_transfers) == 0

        # 3. paste 흉내 — fetch_callback 호출 (전송 완료까지 블록)
        fetched = fetch_cb(offer["offer_id"])

        # 4. 검증 — 조립된 파일 내용이 원본과 일치
        assert fetched.kind == "file"
        assert len(fetched.paths) == 2, f"paths={fetched.paths}"
        for p in fetched.paths:
            assert os.path.exists(p), f"스테이징 파일 없음: {p}"
        got = sorted(open(p, "rb").read() for p in fetched.paths)
        want = sorted([f1.read_bytes(), f2.read_bytes()])
        assert got == want, "조립된 내용이 원본과 불일치"
    finally:
        client_app.stop()
        server_app.stop()


def test_lazy_fetch_missing_files_graceful(tmp_path):
    """offer 후 원본 삭제 → fetch 시 source 가 FETCH_FAIL(missing) → fetch 예외(→fallback)."""
    src = tmp_path / "src"
    src.mkdir()
    f1 = src / "gone.txt"; f1.write_bytes(b"temporary")
    file_paths = [str(f1)]

    server_app, client_app, _server_stub, stub = _setup_pair(tmp_path)
    try:
        server_app._announce_offer(file_paths)
        assert _wait_until(lambda: stub.captured is not None, timeout=4.0)
        offer, fetch_cb = stub.captured

        # 원본 삭제 → source 의 _handle_clip_fetch 가 missing 으로 거부
        f1.unlink()

        with pytest.raises((RuntimeError, TimeoutError)) as exc:
            fetch_cb(offer["offer_id"])
        # FETCH_FAIL 사유가 전달됐는지 (missing) — graceful 실패
        assert "missing" in str(exc.value).lower() or "fail" in str(exc.value).lower()
    finally:
        client_app.stop()
        server_app.stop()


def test_lazy_unknown_offer_raises(tmp_path):
    """등록 안 된 offer_id fetch → 즉시 예외 (provider fallback 신호)."""
    server_app, client_app, _server_stub, _client_stub = _setup_pair(tmp_path)
    try:
        import uuid
        with pytest.raises(RuntimeError):
            client_app._fetch_offer(str(uuid.uuid4()))
    finally:
        client_app.stop()
        server_app.stop()


def test_lazy_receive_fallback_to_download_path(tmp_path):
    """lazy provider 없음(헤드리스/미지원) → offer 가 receivable 로 → 받기→download_path 저장.

    Gate S4: 미지원 환경에서 사용성 확보. happy-path 가 아니므로 receivable 등록됨.
    """
    src = tmp_path / "fsrc"
    src.mkdir()
    f1 = src / "report.txt"; f1.write_bytes(b"fallback-content " * 1000)
    file_paths = [str(f1)]

    server_app, client_app, _server_stub, _client_stub = _setup_pair(tmp_path)
    try:
        # client 에 lazy provider 없음 강제 (이미 _inited=True 라 None 유지)
        client_app.lazy_provider = None

        server_app._announce_offer(file_paths)
        # 미지원 → 받기 fallback 으로 등록 (provider 등록 안 됨)
        assert _wait_until(
            lambda: client_app.config.peer_id and bool(client_app.receivable_offers),
            timeout=4.0,
        ), "receivable 로 등록되지 않음"
        offer_id = next(iter(client_app.receivable_offers))

        # 받기 실행 (전송창 버튼 → IPC → _receive_offer 와 동일 경로)
        dl = client_app.config.download_path
        client_app._receive_offer(offer_id)

        # download_path 에 저장됐고 내용 일치 + receivable 비워짐
        saved = os.path.join(dl, "report.txt")
        assert os.path.exists(saved), f"download_path 에 저장 안 됨: {os.listdir(dl)}"
        assert open(saved, "rb").read() == f1.read_bytes()
        assert not client_app.receivable_offers, "받기 후 receivable 비워져야"
    finally:
        client_app.stop()
        server_app.stop()


def test_lazy_happy_path_no_receivable(tmp_path):
    """provider 등록 OK(happy-path) → receivable 비어있음 (Gate S4: 무음, 받기 행 없음)."""
    src = tmp_path / "hsrc"
    src.mkdir()
    f1 = src / "x.txt"; f1.write_bytes(b"happy")
    server_app, client_app, _server_stub, client_stub = _setup_pair(tmp_path)
    try:
        server_app._announce_offer([str(f1)])
        assert _wait_until(lambda: client_stub.captured is not None, timeout=4.0)
        # provider 가 등록을 수락(stub 은 True) → 받기 fallback 불필요
        assert not client_app.receivable_offers, "happy-path 에선 receivable 0 이어야"
    finally:
        client_app.stop()
        server_app.stop()


def test_lazy_file_roundtrip_reverse(tmp_path):
    """역방향: client 복사 → server paste. 다른 라우팅 분기 검증.

    client._announce_offer → 서버로 OFFER → server 등록(stub) → server paste(fetch)
    → CLIP_FETCH 가 client(source)로 routed → client 가 server 에게 targeted 전송 →
    server 조립. (_server_route receiver==self, client targeted send 경로 커버.)
    """
    src = tmp_path / "csrc"
    src.mkdir()
    f1 = src / "doc.txt"; f1.write_bytes(b"reverse-direction " * 1500)
    file_paths = [str(f1)]

    server_app, client_app, server_stub, _client_stub = _setup_pair(tmp_path)
    try:
        # client 가 source
        client_app._announce_offer(file_paths)
        assert _wait_until(lambda: server_stub.captured is not None, timeout=4.0), \
            "server 가 client 의 offer 를 등록하지 못함"
        offer, fetch_cb = server_stub.captured
        assert offer["source_peer"] == client_app.config.peer_id

        fetched = fetch_cb(offer["offer_id"])
        assert fetched.kind == "file"
        assert len(fetched.paths) == 1
        assert open(fetched.paths[0], "rb").read() == f1.read_bytes()
    finally:
        client_app.stop()
        server_app.stop()
