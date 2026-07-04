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

import logging
import os
import socket
import threading
import time
import uuid

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

    def owns_clipboard(self):
        # 핸들러 단위 테스트는 모니터 루프를 안 돌리므로 항상 False (소유 추적 불필요).
        return False

    def clear(self):
        self.cleared += 1

    def stop(self):
        pass


def _make_app(mode, port, download_path, lazy_paste=True) -> InfiniteClipboard:
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
        # 테스트는 paste 를 즉시 시뮬레이션(fetch_cb 직접 호출)하므로 grace 끔(eager).
        # 타이밍 가드 자체는 test_provider_fetch_grace_gate 가 별도 검증.
        fetch_grace_seconds=0,
        # v3.0.5: lazy round-trip 테스트는 opt-in lazy 경로(lazy_paste=True)를 검증한다.
        # 기본(False) '받기' 모드는 test_offer_default_receive_mode 가 별도 검증.
        lazy_paste=lazy_paste,
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


def test_offer_default_receive_mode(tmp_path):
    """v3.0.5 기본 동작: lazy_paste=False 면 offer 가 lazy provider 에 등록되지 않고
    (클립보드 미소유 → 매니저/앱의 자동 read 가 전송을 트리거할 수 없음) 받기 목록에 들어간다.
    '받기'(_receive_offer)로만 download_path 에 수신된다."""
    src = tmp_path / "src"; src.mkdir()
    # 파일명은 test_lazy_file_roundtrip_loopback 의 "a.txt" 와 겹치면 실제 OS
    # 임시 staging 디렉토리(/tmp/ic_clipboard, 테스트 간 공유되는 고정 경로)에서
    # rename_with_counter 정책이 발동해 "a (1).txt" 로 저장되고, 아래 dest 단언이
    # 실행 순서에 따라 flaky 하게 실패한다 (감사 세션 중 발견, C1~C7과는 무관).
    f1 = src / "receive-mode.txt"; f1.write_bytes(b"hello-receive " * 1000)  # ~13KB

    port = _free_port()
    server_app = _make_app("server", port, tmp_path / "srv_dl")  # 송신측 (lazy_paste 무관)
    client_app = _make_app("client", port, tmp_path / "cli_dl", lazy_paste=False)
    server_stub, client_stub = _StubProvider(), _StubProvider()
    server_app.lazy_provider = server_stub
    server_app._lazy_provider_inited = True
    client_app.lazy_provider = client_stub
    client_app._lazy_provider_inited = True
    server_app._start_server()
    client_app._start_client()
    try:
        assert _wait_until(lambda: client_app.client and client_app.client.connected)
        assert _wait_until(lambda: len(server_app.peers) == 1)

        server_app._announce_offer([str(f1)])

        # 받기 목록엔 들어오되, provider 에는 등록되지 않아야 한다 (자동전송 원천 차단)
        assert _wait_until(lambda: len(client_app.receivable_offers) == 1, timeout=4.0), \
            "offer 가 받기 목록에 안 들어옴"
        assert client_stub.captured is None, "받기 모드인데 provider 등록됨 (자동전송 위험)"

        # '받기' 실행 → download_path 저장 + 목록 비워짐
        offer_id = next(iter(client_app.receivable_offers))
        client_app._receive_offer(offer_id)
        dest = os.path.join(client_app.config.download_path, "receive-mode.txt")
        assert os.path.exists(dest), f"받기 저장 안 됨: {dest}"
        assert open(dest, "rb").read() == f1.read_bytes()
        assert len(client_app.receivable_offers) == 0
    finally:
        client_app.stop()
        server_app.stop()


def test_lazy_image_roundtrip_loopback(tmp_path):
    """이미지 복사(base64)→offer(kind=image)→fetch→FetchedContent.data 바이트 일치 (S3)."""
    import base64

    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 200  # ~51KB 임의 바이너리
    b64 = base64.b64encode(png).decode("ascii")

    server_app, client_app, _server_stub, stub = _setup_pair(tmp_path)
    try:
        # source(서버) 이미지 복사 → offer broadcast (kind=image)
        server_app._announce_image_offer(b64)
        assert _wait_until(lambda: stub.captured is not None, timeout=4.0), \
            "이미지 offer 등록 안 됨"
        offer, fetch_cb = stub.captured
        assert offer["kind"] == "image"

        # paste 흉내 — fetch → 이미지는 data 로 반환 (paths 아님)
        fetched = fetch_cb(offer["offer_id"])
        assert fetched.kind == "image"
        assert fetched.data == png, f"이미지 바이트 불일치: got={len(fetched.data)} want={len(png)}"
        assert not fetched.paths
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


def test_lazy_owns_clipboard_guard(tmp_path):
    """모니터 self-loop 가드: provider 없으면 False, 있으면 provider 값, 예외는 False.

    받는 PC 에서 lazy provider 가 클립보드를 소유 중이면 모니터가 읽기를 건너뛰어야
    한다(self-fetch/재broadcast 방지). 이 헬퍼가 그 판단의 단일 진입점.
    """
    app = _make_app("server", _free_port(), tmp_path / "dl")

    # ① provider 없음 → False (lazy 미지원/헤드리스 — 기존 동작 유지)
    app.lazy_provider = None
    assert app._lazy_owns_clipboard() is False

    # ② provider.owns_clipboard() 값을 그대로 반영
    class _Owns:
        def __init__(self, v): self.v = v
        def owns_clipboard(self): return self.v
    app.lazy_provider = _Owns(True)
    assert app._lazy_owns_clipboard() is True
    app.lazy_provider = _Owns(False)
    assert app._lazy_owns_clipboard() is False

    # ③ provider 가 예외를 던져도 모니터는 죽지 않고 False (안전 기본값)
    class _Boom:
        def owns_clipboard(self): raise RuntimeError("boom")
    app.lazy_provider = _Boom()
    assert app._lazy_owns_clipboard() is False


def test_provider_fetch_grace_gate(tmp_path, monkeypatch):
    """v3.0.3 타이밍 가드: 등록 직후 grace 안의 read 는 _GracePeek(전송 안 함),
    grace 후/끔(0) 은 정상 fetch. OS 셸의 즉시 peek 으로 인한 복사 즉시 전송 차단."""
    from main import _GracePeek

    app = _make_app("server", _free_port(), tmp_path / "dl")
    app.config.fetch_grace_seconds = 2.0
    oid = "grace-test-offer"
    with app._offer_lock:
        app.received_offers = {oid: {
            "offer_id": oid, "kind": "file", "source_peer": "a" * 32,
            "_registered_at": time.time(),  # 방금 등록 (grace 안)
        }}
    called = {"fetch": 0}
    monkeypatch.setattr(app, "_fetch_offer", lambda o: called.__setitem__("fetch", called["fetch"] + 1))

    # ① grace 안의 read = 셸 peek → GracePeek, _fetch_offer 호출 안 함
    with pytest.raises(_GracePeek):
        app._provider_fetch(oid)
    assert called["fetch"] == 0, "grace peek 인데 전송이 일어남"

    # ② grace 지난 뒤(=진짜 paste) → 정상 fetch
    with app._offer_lock:
        app.received_offers[oid]["_registered_at"] = time.time() - 5.0
    app._provider_fetch(oid)
    assert called["fetch"] == 1, "grace 후엔 전송돼야 함"

    # ③ grace=0 → 가드 끔(eager), 등록 직후라도 즉시 fetch
    app.config.fetch_grace_seconds = 0
    with app._offer_lock:
        app.received_offers[oid]["_registered_at"] = time.time()
    app._provider_fetch(oid)
    assert called["fetch"] == 2, "grace=0 이면 즉시 전송돼야 함"


def test_spoofed_requester_peer_is_dropped(tmp_path, caplog):
    """감사 Critical #1: MSG_CLIP_FETCH 의 requester_peer 자기신고가 실제 소켓
    identity 와 다르면 서버가 drop 해야 한다. 과거엔 검증 없이 라우팅되어, 인증된
    peer 가 다른 peer 를 사칭해 그 peer 에게 원치 않는 파일 전송/클립보드 강제
    주입을 유발할 수 있었다 (v3.0.5 '받기 명시적 동의' 설계의 프로토콜 계층 우회)."""
    src = tmp_path / "src"
    src.mkdir()
    f1 = src / "secret.txt"
    f1.write_bytes(b"top-secret-data")

    server_app, client_app, _server_stub, stub = _setup_pair(tmp_path)
    try:
        server_app._announce_offer([str(f1)])
        assert _wait_until(lambda: stub.captured is not None, timeout=4.0)
        offer, _fetch_cb = stub.captured

        # client 는 실제로는 자신의 진짜 peer_id 로만 handshake 했다 — 여기서
        # 전혀 다른(연결한 적도 없는) peer_id 를 자기신고해 스푸핑을 흉내낸다.
        forged_peer = generate_peer_id()
        assert forged_peer != client_app.config.peer_id
        raw = client_app.protocol.create_clip_fetch(
            offer["offer_id"], forged_peer, receiver_peer=offer["source_peer"],
        )
        caplog.set_level(logging.WARNING, logger="infinite-clipboard")
        client_app._send_raw_to(raw, offer["source_peer"])

        # 서버(source)가 위조를 감지해 drop 로그를 남겨야 함
        assert _wait_until(
            lambda: any(
                "MSG_CLIP_FETCH requester_peer 위조" in r.getMessage()
                for r in caplog.records
            ),
            timeout=2.0,
        ), "스푸핑 감지 로그가 없음 — 검증이 우회됐을 수 있음"

        # 어느 쪽도 실제 전송을 시작하지 않아야 함
        time.sleep(0.3)
        with client_app._transfers_lock:
            assert len(client_app.pending_transfers) == 0
        with server_app._transfers_lock:
            assert len(server_app.outgoing_files) == 0
    finally:
        client_app.stop()
        server_app.stop()


def test_chunk_hash_mismatch_wakes_fetch_immediately():
    """감사 Critical #2: 전송 도중 청크 해시 불일치 시 _signal_fetch(fail=...) 가
    호출되어 대기 중인 fetch 가 즉시 깨어나야 한다. 과거엔 이 실패가 레거시
    MSG_FILE_ERROR 로만 보고되고 _active_fetch 를 깨우지 않아, 받기 버튼을 누른
    쪽이 최대 600초(하드 타임아웃) 동안 무진행률로 멎어 있었다."""
    app = InfiniteClipboard(AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
    ))
    transfer_id = str(uuid.uuid4())
    event = threading.Event()
    app._active_fetch = {
        "offer_id": transfer_id, "transfer_id": transfer_id,
        "event": event, "paths": None, "fail": None,
    }
    with app._transfers_lock:
        app.pending_transfers[transfer_id] = object()  # 존재만 하면 early-return 통과

    app._handle_file_chunk({
        "transfer_id": transfer_id,
        "file_path": "x.bin",
        "chunk_index": 0,
        "binary_data": b"corrupted-bytes",
        "hash": "0" * 16,  # 실제 xxHash64 와 고의로 불일치
    })

    assert event.is_set(), "청크 해시 불일치가 대기 중인 fetch 를 깨우지 못함 (hang 가능)"
    assert app._active_fetch["fail"] == "chunk_hash_mismatch"


def test_assemble_failure_wakes_fetch_immediately():
    """감사 Critical #2: 조립(SHA-256) 실패도 동일하게 _signal_fetch(fail=...) 로
    즉시 깨워야 한다."""
    app = InfiniteClipboard(AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
    ))
    transfer_id = str(uuid.uuid4())
    event = threading.Event()
    app._active_fetch = {
        "offer_id": transfer_id, "transfer_id": transfer_id,
        "event": event, "paths": None, "fail": None,
    }
    with app._transfers_lock:
        app.pending_transfers[transfer_id] = object()

    # assemble_file 이 존재하지 않는 조립 파일에 대해 False 를 반환하도록 유도
    app._handle_file_end({
        "transfer_id": transfer_id,
        "file_path": "never-received.bin",
        "sha256": "0" * 64,
    })

    assert event.is_set(), "조립 실패가 대기 중인 fetch 를 깨우지 못함 (hang 가능)"
    assert app._active_fetch["fail"] == "assemble_failed"
