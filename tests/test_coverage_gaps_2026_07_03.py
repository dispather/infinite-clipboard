"""2026-07-03 감사 — 테스트 커버리지 갭 8건 중 실사용 가치가 있는 항목들의
통합 레벨 회귀 테스트.

갭 #6(is_compatible_version 단위 테스트)은 tests/test_handshake_hmac.py 의
test_version_compatibility 가 이미 커버 — 여기선 생략.

나머지 매핑:
  #1 (중요도 9) 동시/경합 fetch 경로
  #2 (중요도 8) FETCH_FAIL_EXPIRED/SUPERSEDED 오케스트레이션 (OFFLINE 은
      test_fetch_offline_source.py 가 M7 회귀로 이미 커버)
  #3 (중요도 8) 전송 도중 연결 끊김
  #4 (중요도 7) peer_id 재연결 레이스 (H1 트레이드오프의 self-heal 검증)
  #5 (중요도 7) dedup hash-injection 배선 통합 레벨
  #7 (중요도 6) 이어받기(resume) — 2026-07-04 재설계로 MSG_CLIP_FETCH 의
      resume 필드를 통한 실제 경로로 재구현. 아래 테스트 참조.
  #8 (중요도 5) conflict policy 4종 종단(socket)
"""

import os
import socket
import threading
import time
import uuid
from pathlib import Path

import pytest
import xxhash

from config import AppConfig
from core.file_transfer import Checkpoint, CHUNK_SIZE
from core.network import NetworkClient, NetworkServer
from core.protocol import generate_peer_id
from main import FetchFailure, InfiniteClipboard


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


_KEY = "coverage-gap-shared-secret-key-0123456789"


class _StubProvider:
    """test_lazy_orchestration.py 와 동일한 lazy provider 더미."""

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


def _make_app(mode, port, download_path, peer_id=None) -> InfiniteClipboard:
    download_path.mkdir(parents=True, exist_ok=True)
    cfg = AppConfig(
        mode=mode,
        server_host="127.0.0.1",
        port=port,
        auth_key=_KEY,
        peer_id=peer_id or generate_peer_id(),
        download_path=str(download_path),
        tailscale_trust=False,
        bind_address="127.0.0.1",
        fetch_grace_seconds=0,
        lazy_paste=True,
    )
    return InfiniteClipboard(cfg)


def _setup_pair(tmp_path):
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

    assert _wait_until(lambda: client_app.client and client_app.client.connected)
    assert _wait_until(lambda: len(server_app.peers) == 1)
    return server_app, client_app, server_stub, client_stub


def _setup_trio(tmp_path):
    port = _free_port()
    server_app = _make_app("server", port, tmp_path / "srv_dl")
    client_a = _make_app("client", port, tmp_path / "cli_a_dl")
    client_b = _make_app("client", port, tmp_path / "cli_b_dl")

    stub_a, stub_b = _StubProvider(), _StubProvider()
    client_a.lazy_provider = stub_a
    client_a._lazy_provider_inited = True
    client_b.lazy_provider = stub_b
    client_b._lazy_provider_inited = True

    server_app._start_server()
    client_a._start_client()
    client_b._start_client()

    assert _wait_until(lambda: client_a.client and client_a.client.connected)
    assert _wait_until(lambda: client_b.client and client_b.client.connected)
    assert _wait_until(lambda: len(server_app.peers) == 2)
    return server_app, client_a, client_b, stub_a, stub_b


# ── 갭 #1 (중요도 9): 동시/경합 fetch 경로 ──────────────────────────────

def test_concurrent_fetch_from_two_receivers_not_corrupted(tmp_path, monkeypatch):
    """두 receiver 가 같은 offer 를 거의 동시에 fetch 해도 _outgoing_fetch_lock
    직렬화 덕에 둘 다 손상 없이 온전한 내용을 받아야 한다."""
    src = tmp_path / "src"
    src.mkdir()
    content = bytes(range(256)) * 4000  # ~1MB, 여러 청크로 나뉨
    f1 = src / "shared.bin"
    f1.write_bytes(content)

    # 이 테스트는 두 receiver 를 같은 프로세스/파일시스템에서 흉내낸다. 실제로는
    # 서로 다른 PC 라 절대 공유되지 않는 get_temp_dir() 조립 스테이징 경로가,
    # 같은 offer_id(==transfer_id) 를 쓰는 두 receiver 사이에서 충돌해 진짜
    # 손상과 무관한 FileExistsError 를 유발한다. 스레드별로 경로를 분리해 이
    # 테스트-전용 아티팩트만 제거하고, 검증하려는 실제 동시성(_outgoing_fetch_lock
    # 직렬화)은 그대로 둔다.
    import core.file_transfer as ft_mod
    original_get_temp_dir = ft_mod.get_temp_dir

    def isolated_get_temp_dir(transfer_id):
        base = original_get_temp_dir(transfer_id)
        return base.parent / f"{base.name}_{threading.get_ident()}"

    monkeypatch.setattr(ft_mod, "get_temp_dir", isolated_get_temp_dir)

    server_app, client_a, client_b, stub_a, stub_b = _setup_trio(tmp_path)
    try:
        server_app._announce_offer([str(f1)])
        assert _wait_until(lambda: stub_a.captured is not None, timeout=4.0)
        assert _wait_until(lambda: stub_b.captured is not None, timeout=4.0)
        offer_a, fetch_cb_a = stub_a.captured
        offer_b, fetch_cb_b = stub_b.captured
        assert offer_a["offer_id"] == offer_b["offer_id"], "두 receiver 가 다른 offer 를 받음"

        results = {}
        errors = []

        def do_fetch(name, cb, offer_id):
            try:
                results[name] = cb(offer_id)
            except Exception as e:
                errors.append((name, e))

        t1 = threading.Thread(target=do_fetch, args=("a", fetch_cb_a, offer_a["offer_id"]))
        t2 = threading.Thread(target=do_fetch, args=("b", fetch_cb_b, offer_b["offer_id"]))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert not errors, f"동시 fetch 중 예외 발생: {errors}"
        assert "a" in results and "b" in results, "동시 fetch 중 하나가 완료되지 않음"
        for name, fetched in results.items():
            assert len(fetched.paths) == 1
            got = open(fetched.paths[0], "rb").read()
            assert got == content, f"{name} 가 받은 내용이 원본과 다름 (손상 의심)"
    finally:
        client_a.stop()
        client_b.stop()
        server_app.stop()


# ── 갭 #2 (중요도 8): FETCH_FAIL_EXPIRED/SUPERSEDED 오케스트레이션 레벨 ──
# (OFFLINE 은 test_fetch_offline_source.py 의 M7 회귀 테스트가 이미 커버)

def test_fetch_fail_expired_orchestration(tmp_path):
    """offer TTL 만료 시 FETCH_FAIL_EXPIRED 가 핸들러 체인 전체를 거쳐
    requester 의 _active_fetch 까지 정확히 전달돼야 한다."""
    src = tmp_path / "src"
    src.mkdir()
    f1 = src / "expiring.txt"
    f1.write_bytes(b"soon to expire")

    server_app, client_app, _server_stub, stub = _setup_pair(tmp_path)
    try:
        server_app._announce_offer([str(f1)])
        assert _wait_until(lambda: stub.captured is not None, timeout=4.0)
        offer, fetch_cb = stub.captured

        # created_at 을 TTL(기본 24h) 훨씬 지난 과거로 조작
        with server_app._offer_lock:
            server_app.current_offer["created_at"] = time.time() - 3600 * 48

        with pytest.raises(FetchFailure) as exc:
            fetch_cb(offer["offer_id"])
        assert "expired" in str(exc.value).lower(), f"실패 사유에 expired 없음: {exc.value}"
    finally:
        client_app.stop()
        server_app.stop()


def test_fetch_fail_superseded_orchestration(tmp_path):
    """이전 offer_id 로 fetch 를 시도하면(새 offer 로 superseded 된 뒤)
    FETCH_FAIL_SUPERSEDED 가 오케스트레이션 레벨에서 전달돼야 한다."""
    src = tmp_path / "src"
    src.mkdir()
    f1 = src / "old.txt"
    f1.write_bytes(b"old content")
    f2 = src / "new.txt"
    f2.write_bytes(b"new content")

    server_app, client_app, _server_stub, stub = _setup_pair(tmp_path)
    try:
        server_app._announce_offer([str(f1)])
        assert _wait_until(lambda: stub.captured is not None, timeout=4.0)
        old_offer, _old_cb = stub.captured
        old_offer_id = old_offer["offer_id"]

        server_app._announce_offer([str(f2)])
        assert _wait_until(
            lambda: stub.captured is not None and stub.captured[0]["offer_id"] != old_offer_id,
            timeout=4.0,
        ), "새 offer 로 supersede 되지 않음"

        # 클라이언트가 옛(superseded) offer_id 로 fetch 를 직접 구성해 시도
        event = threading.Event()
        client_app._active_fetch = {
            "offer_id": old_offer_id, "transfer_id": old_offer_id,
            "event": event, "paths": None, "fail": None,
        }
        raw = client_app.protocol.create_clip_fetch(
            old_offer_id, client_app.config.peer_id, receiver_peer=server_app.config.peer_id,
        )
        client_app._send_raw_to(raw, server_app.config.peer_id)

        assert _wait_until(lambda: event.is_set(), timeout=3.0), \
            "superseded fetch 가 응답을 못 받음"
        assert client_app._active_fetch["fail"] == "superseded"
    finally:
        client_app.stop()
        server_app.stop()


# ── 갭 #3 (중요도 8): 전송 도중 연결 끊김 ────────────────────────────────

def test_connection_drop_mid_transfer_does_not_hang(tmp_path):
    """전송 도중(첫 청크 수신 직후) 서버가 통째로 죽어도 receiver 의 fetch 가
    무한 대기하지 않고 유한 시간 안에 실패로 끝나야 한다."""
    src = tmp_path / "src"
    src.mkdir()
    f1 = src / "big.bin"
    f1.write_bytes(bytes(range(256)) * 20000)  # ~5MB → 1MB 청크 기준 여러 개

    server_app, client_app, _server_stub, stub = _setup_pair(tmp_path)
    try:
        # 짧은 타임아웃으로 교체 — 기본 최소 30초는 테스트로 너무 길다
        client_app._fetch_timeout = lambda total: 2.0

        # 첫 청크를 받는 순간 서버를 통째로 죽여 "전송 도중 연결 끊김"을
        # 타이밍에 의존하지 않고 확정적으로 재현한다.
        original_handle_chunk = client_app._handle_file_chunk
        killed = threading.Event()

        def handle_chunk_then_kill(data):
            original_handle_chunk(data)
            if not killed.is_set():
                killed.set()
                try:
                    server_app.server.stop()
                except Exception:
                    pass

        client_app._handle_file_chunk = handle_chunk_then_kill

        server_app._announce_offer([str(f1)])
        assert _wait_until(lambda: stub.captured is not None, timeout=4.0)
        offer, fetch_cb = stub.captured

        start = time.time()
        with pytest.raises(FetchFailure):
            fetch_cb(offer["offer_id"])
        elapsed = time.time() - start
        assert killed.is_set(), "첫 청크를 받기 전에 fetch 가 끝남 (재현 실패)"
        assert elapsed < 5.0, f"연결 끊김 후 fetch 가 예상보다 오래 걸림: {elapsed:.1f}s"
    finally:
        client_app.stop()
        try:
            server_app.stop()
        except Exception:
            pass


# ── 갭 #4 (중요도 7): peer_id 재연결 레이스 (H1 트레이드오프 self-heal) ──

def test_peer_reconnect_with_same_peer_id_eventually_succeeds():
    """H1 identity-squatting 방어의 알려진 트레이드오프 — stale 소켓 정리 전
    재연결은 일시 거부될 수 있으나, 자동 재시도로 결국 성공해야 한다(영구
    거부 회귀 방지)."""
    port = _free_port()
    server = NetworkServer(
        port=port, auth_key="k", tailscale_trust=False, bind_address="127.0.0.1",
    )
    server.start()

    peer_id = generate_peer_id()
    client1 = NetworkClient(host="127.0.0.1", port=port, auth_key="k", device_name="c1", peer_id=peer_id)
    client1.start()
    try:
        assert _wait_until(lambda: client1.connected, timeout=3.0)
        assert _wait_until(lambda: len(server.clients) == 1, timeout=3.0)
    finally:
        client1.stop()

    client2 = NetworkClient(host="127.0.0.1", port=port, auth_key="k", device_name="c2", peer_id=peer_id)
    client2.reconnect_interval = 0.3
    client2.start()
    try:
        assert _wait_until(lambda: client2.connected, timeout=5.0), (
            "테스트갭 #4 회귀 — 같은 peer_id 재연결이 self-heal 되지 않음 "
            "(영구 거부라면 H1 트레이드오프 문서화와 다른 회귀)"
        )
        with server.clients_lock:
            assert len(server.clients) == 1
            info = next(iter(server.clients.values()))
            assert info["peer_id"] == peer_id
    finally:
        client2.stop()
        server.stop()


# ── 갭 #5 (중요도 7): dedup hash-injection 배선 통합 레벨 ────────────────

def test_dedup_hash_injection_enables_second_transfer_skip(tmp_path):
    """main.py 의 실제 핸들러 체인(_handle_file_end)이 계산한 SHA-256 을
    metadata.files 에 제대로 주입해야, 동일 파일의 재전송이 dedup short-circuit
    으로 실제 skip 된다 (CLAUDE.md 함정 #24 회귀).

    2026-07-10 갱신: staging 이 transfer_id 별 하위 폴더로 격리되면서(별개의
    "(1)" 접미어 버그 수정) 서로 다른 offer_id 는 애초에 staging 안에서 충돌할
    수 없어졌다 — 이 시나리오의 실제 회귀 지점은 이제 "받기" 버튼 흐름
    (`_receive_offer` → `config.download_path`)으로 옮겨졌다. `_receive_offer`
    가 다운로드 폴더의 실제 충돌을 발견하면 그때 hash 를 계산해 dedup 을
    시도하므로, 여기서 그 경로를 직접 검증한다."""
    unique_name = f"dedup-wiring-{uuid.uuid4().hex}.bin"
    src = tmp_path / "src"
    src.mkdir()
    content = b"dedup wiring content " * 5000
    f1 = src / unique_name
    f1.write_bytes(content)

    server_app, client_app, _server_stub, stub = _setup_pair(tmp_path)
    try:
        dest_path = Path(client_app.config.download_path) / unique_name

        # 1차 전송 — "받기" 버튼(_receive_offer)으로 다운로드 폴더에 수신
        server_app._announce_offer([str(f1)])
        assert _wait_until(lambda: stub.captured is not None, timeout=4.0)
        offer, _fetch_cb = stub.captured
        client_app._receive_offer(offer["offer_id"])
        assert dest_path.exists(), "1차 전송 후 목적지 파일이 없음"
        assert dest_path.read_bytes() == content

        # 2차 전송(동일 내용, 새 offer_id) — hash 주입이 되면 dedup 이 발동해
        # "dup (1).bin" 같은 새 파일이 생기지 않고 기존 경로 그대로여야 함
        stub.captured = None
        server_app._announce_offer([str(f1)])
        assert _wait_until(lambda: stub.captured is not None, timeout=4.0)
        offer2, _fetch_cb2 = stub.captured
        assert offer2["offer_id"] != offer["offer_id"]
        client_app._receive_offer(offer2["offer_id"])

        duplicated = Path(client_app.config.download_path) / f"{dest_path.stem} (1){dest_path.suffix}"
        assert not duplicated.exists(), (
            "테스트갭 #5 회귀 — dedup hash 주입이 깨져 재수신이 새 파일로 복제됨"
        )
        assert dest_path.read_bytes() == content
    finally:
        client_app.stop()
        server_app.stop()


# ── 갭 #7 (중요도 6): 이어받기(resume) — MSG_CLIP_FETCH.resume 실경로 ─────
# 2026-07-04 재설계: 예전엔 MSG_FILE_REQUEST/MSG_FILE_RESUME + 직접 호출로만
# 검증되던 죽은 코드였음(송신부 자체가 없어 실제로 한 번도 안 불림). 이제는
# _fetch_offer 가 CheckpointManager 를 직접 읽어 MSG_CLIP_FETCH.resume 필드에
# 실어 보내고, source 가 그 힌트로 완료 파일 skip + 진행 중 파일 이어보내기를
# 수행한다(main.py `_load_resume_for_offer`/`_serve_fetch`/`_send_files`,
# core/file_transfer.py `FileTransferManager.send_file(start_chunk_index=...)`).


def test_resume_skips_completed_file_and_resumes_partial_file(tmp_path, monkeypatch):
    """이전 시도에서 한 파일은 완전히 받았고 다른 파일은 일부 청크만 받은
    상태(체크포인트로 기록됨)에서 같은 offer 를 다시 fetch 하면: 완료 파일은
    재전송하지 않고, 진행 중이던 파일은 저장된 last_chunk_index 다음부터만
    전송돼야 한다."""
    # staging(_staging_dir())은 tempfile.gettempdir()/ic_clipboard 고정 경로라
    # 테스트 간 격리가 안 됨 — 다른 테스트들처럼 uuid 접미사로 이름 충돌(dedup/
    # conflict-rename)을 피한다.
    uid = uuid.uuid4().hex
    done_name = f"resume-done-{uid}.bin"
    partial_name = f"resume-partial-{uid}.bin"
    src = tmp_path / "src"
    src.mkdir()
    done_content = b"already-fully-received-content"
    (src / done_name).write_bytes(done_content)
    partial_content = os.urandom(CHUNK_SIZE * 3 + CHUNK_SIZE // 2)
    (src / partial_name).write_bytes(partial_content)

    server_app, client_app, _server_stub, client_stub = _setup_pair(tmp_path)
    try:
        server_app._announce_offer([str(src / done_name), str(src / partial_name)])
        assert _wait_until(lambda: client_stub.captured is not None)
        offer, fetch_cb = client_stub.captured
        offer_id = offer["offer_id"]

        rel_paths = [f["path"] for f in server_app.current_offer["metadata"].files]
        rel_done = next(p for p in rel_paths if os.path.basename(p) == done_name)
        rel_partial = next(p for p in rel_paths if os.path.basename(p) == partial_name)

        # 이전 시도 시뮬레이션 — done.bin 은 전부(단일 청크), partial.bin 은
        # 앞 2청크(index 0, 1)만 실제 수신됨. restore_folder 는 완료 처리 시
        # assembled 임시 파일의 실존 여부로 옮길지 판단하므로, done.bin 이
        # "완료됐다"고 하려면 그 assembled 임시 파일이 실제로 있어야 한다.
        assert client_app.file_manager.receive_chunk(
            offer_id, rel_done, 0, done_content, xxhash.xxh64(done_content).hexdigest()
        )
        for idx in range(2):
            chunk = partial_content[idx * CHUNK_SIZE:(idx + 1) * CHUNK_SIZE]
            assert client_app.file_manager.receive_chunk(
                offer_id, rel_partial, idx, chunk, xxhash.xxh64(chunk).hexdigest()
            )
        client_app.checkpoint_manager.save(Checkpoint(
            transfer_id=offer_id,
            completed_files=[rel_done],
            current_file=rel_partial,
            last_chunk_index=1,
        ))

        calls = []
        original_send_file = server_app.file_manager.send_file

        def spy_send_file(filepath, cb, start_chunk_index=0):
            calls.append((os.path.basename(filepath), start_chunk_index))
            return original_send_file(filepath, cb, start_chunk_index=start_chunk_index)

        monkeypatch.setattr(server_app.file_manager, "send_file", spy_send_file)

        fetched = fetch_cb(offer_id)

        names_called = {name for name, _ in calls}
        assert done_name not in names_called, "완료 파일을 재전송함 — 재개 skip 실패"
        assert dict(calls).get(partial_name) == 2, (
            f"이어받기 시작 위치가 last_chunk_index+1(=2) 이 아님: {calls}"
        )

        contents = {os.path.basename(p): open(p, "rb").read() for p in fetched.paths}
        assert contents[done_name] == done_content
        assert contents[partial_name] == partial_content
        assert client_app.checkpoint_manager.load(offer_id) is None, (
            "성공 후 체크포인트가 정리되지 않음"
        )
    finally:
        client_app.stop()
        server_app.stop()


def test_resume_ignored_when_checkpoint_does_not_match_current_offer(tmp_path):
    """체크포인트가 가리키는 파일이 현재 offer 의 파일 목록에 없으면(오퍼가
    그 사이 바뀌었거나 stale) 이어받기를 시도하지 않고 처음부터 정상
    전송돼야 한다 — 부분 적용으로 인한 오조립을 막는 안전장치."""
    new_name = f"resume-new-{uuid.uuid4().hex}.bin"
    src = tmp_path / "src"
    src.mkdir()
    content = b"fresh-offer-unrelated-to-old-checkpoint"
    (src / new_name).write_bytes(content)

    server_app, client_app, _server_stub, client_stub = _setup_pair(tmp_path)
    try:
        server_app._announce_offer([str(src / new_name)])
        assert _wait_until(lambda: client_stub.captured is not None)
        offer, fetch_cb = client_stub.captured
        offer_id = offer["offer_id"]

        # new.bin 과 무관한 stale 체크포인트 (예: 이전 오퍼가 다른 파일을 담았음).
        client_app.checkpoint_manager.save(Checkpoint(
            transfer_id=offer_id,
            completed_files=["gone.bin"],
            current_file="",
            last_chunk_index=-1,
        ))

        fetched = fetch_cb(offer_id)
        assert open(fetched.paths[0], "rb").read() == content
    finally:
        client_app.stop()
        server_app.stop()


# ── 갭 #8 (중요도 5): conflict policy 4종 종단(socket) ───────────────────

@pytest.mark.parametrize(
    "policy", ["overwrite", "skip", "rename_with_timestamp", "rename_with_counter"],
)
def test_conflict_policy_end_to_end_socket(tmp_path, policy):
    """4가지 file_conflict_policy 가 실제 소켓 기반 전송 종단에서 기대한
    대로 동작하는지 (기존엔 _resolve_conflict 단위 테스트만 존재).

    2026-07-10 갱신: staging 이 transfer_id 별 하위 폴더로 격리되면서(별개의
    "(1)" 접미어 버그 수정) 서로 다른 offer_id 는 staging 안에서 애초에 충돌할
    수 없어졌다 — 사용자에게 실제로 보이는 충돌 지점은 "받기" 버튼 흐름
    (`_receive_offer` → `config.download_path`)이므로 거기서 검증한다."""
    unique_name = f"conflict-{uuid.uuid4().hex}.bin"
    src = tmp_path / "src"
    src.mkdir()
    content_v1 = b"version-one-content " * 3000
    content_v2 = b"version-TWO-different-content " * 3000  # 다른 내용 → dedup 안 탐
    f1 = src / unique_name
    f1.write_bytes(content_v1)

    server_app, client_app, _server_stub, stub = _setup_pair(tmp_path)
    try:
        client_app.config.file_conflict_policy = policy
        download_dir = Path(client_app.config.download_path)
        dest_path = download_dir / unique_name

        server_app._announce_offer([str(f1)])
        assert _wait_until(lambda: stub.captured is not None, timeout=4.0)
        offer, _fetch_cb = stub.captured
        client_app._receive_offer(offer["offer_id"])
        assert dest_path.read_bytes() == content_v1

        f1.write_bytes(content_v2)
        stub.captured = None
        server_app._announce_offer([str(f1)])
        assert _wait_until(lambda: stub.captured is not None, timeout=4.0)
        offer2, _fetch_cb2 = stub.captured
        client_app._receive_offer(offer2["offer_id"])

        if policy == "overwrite":
            assert dest_path.read_bytes() == content_v2
        elif policy == "skip":
            assert dest_path.read_bytes() == content_v1
        elif policy == "rename_with_counter":
            counter_path = download_dir / f"{dest_path.stem} (1){dest_path.suffix}"
            assert counter_path.exists(), "rename_with_counter 파일이 생성되지 않음"
            assert counter_path.read_bytes() == content_v2
            assert dest_path.read_bytes() == content_v1
        elif policy == "rename_with_timestamp":
            matches = [
                p for p in download_dir.glob(f"{dest_path.stem}.*{dest_path.suffix}")
                if p != dest_path
            ]
            assert matches, "rename_with_timestamp 파일이 생성되지 않음"
            assert matches[0].read_bytes() == content_v2
            assert dest_path.read_bytes() == content_v1
    finally:
        client_app.stop()
        server_app.stop()
