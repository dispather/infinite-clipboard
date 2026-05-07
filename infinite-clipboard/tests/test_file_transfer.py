"""파일 전송 회귀 테스트 — offset write, assemble, path traversal 방어."""

import hashlib
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
import xxhash

from core.file_transfer import (
    FileTransferManager, get_temp_dir, CheckpointManager, Checkpoint,
    FileMetadata, ActiveOutgoing, ActiveIncoming,
    _is_safe_rel_path, _resolve_within,
    cleanup_staging_dir,
)


@pytest.fixture
def download_dir():
    """각 테스트마다 격리된 다운로드 디렉토리."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def manager(download_dir):
    return FileTransferManager(download_path=str(download_dir), max_file_size_gb=1)


# ─── 1. Path Traversal 방어 ────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "../etc/passwd",           # POSIX 상위
    "..\\windows\\sys.ini",    # Windows 역슬래시
    "/etc/passwd",              # POSIX 절대
    "C:\\Windows\\a.ini",      # Windows 드라이브
    "\\\\server\\share\\x",    # UNC
    "foo/../../bar",            # 중간 ..
    "a/./b",                    # 중간 .
    "",                         # 빈 문자열
    "x\x00y",                  # 널 바이트
    "./x",                      # 선행 .
])
def test_unsafe_rel_path_rejected(bad):
    assert not _is_safe_rel_path(bad), f"악성 경로 통과: {bad!r}"


@pytest.mark.parametrize("good", [
    "a.txt",
    "dir/sub/file.txt",
    "한글파일 이름.png",
    "deep/nested/folder/data.bin",
])
def test_safe_rel_path_accepted(good):
    assert _is_safe_rel_path(good), f"정상 경로 거부: {good!r}"


def test_resolve_within_blocks_escape(tmp_path):
    assert _resolve_within(tmp_path, "a/b.txt") is not None
    assert _resolve_within(tmp_path, "../evil.txt") is None


def test_receive_chunk_rejects_evil_path(manager):
    data = b"X" * 128
    h = xxhash.xxh64(data).hexdigest()
    ok = manager.receive_chunk(str(uuid.uuid4()), "../evil.txt", 0, data, h)
    assert ok is False


# ─── 2. 청크 offset write 라운드트립 ───────────────────────────────────

def _split_chunks(data: bytes, size: int):
    for i in range(0, len(data), size):
        yield i // size, data[i:i + size]


def test_offset_write_single_file(manager):
    """2MB + 512B → 3청크. 순서대로 write → assemble → SHA-256 일치."""
    data = b"A" * (2 * 1024 * 1024) + b"B" * 512
    tid = str(uuid.uuid4())
    rel = "hello.bin"
    for idx, chunk in _split_chunks(data, 1024 * 1024):
        ok = manager.receive_chunk(tid, rel, idx, chunk,
                                    xxhash.xxh64(chunk).hexdigest())
        assert ok
    full_sha = hashlib.sha256(data).hexdigest()
    assert manager.assemble_file(tid, rel, full_sha)
    # 파일 크기 확인
    assembled = get_temp_dir(tid) / "assembled_hello.bin"
    assert assembled.stat().st_size == len(data)
    shutil.rmtree(get_temp_dir(tid), ignore_errors=True)


def test_offset_write_out_of_order(manager):
    """청크가 역순으로 도착해도 offset write로 올바른 파일 조립."""
    data = b"1" * 512 + b"2" * 1024 * 1024 + b"3" * 1024
    tid = str(uuid.uuid4())
    rel = "ooo.bin"
    chunks = list(_split_chunks(data, 1024 * 1024))
    # 역순 전송
    for idx, chunk in reversed(chunks):
        manager.receive_chunk(tid, rel, idx, chunk,
                              xxhash.xxh64(chunk).hexdigest())
    assert manager.assemble_file(tid, rel, hashlib.sha256(data).hexdigest())
    shutil.rmtree(get_temp_dir(tid), ignore_errors=True)


def test_chunk_hash_mismatch_rejected(manager):
    data = b"Z" * 1024
    ok = manager.receive_chunk(str(uuid.uuid4()), "x.bin", 0, data,
                                "0000000000000000")
    assert ok is False


def test_assemble_fails_on_sha256_mismatch(manager):
    data = b"content"
    tid = str(uuid.uuid4())
    rel = "x.bin"
    manager.receive_chunk(tid, rel, 0, data, xxhash.xxh64(data).hexdigest())
    # 엉뚱한 SHA-256
    assert manager.assemble_file(tid, rel, "0" * 64) is False
    # 실패 시 조립 파일이 삭제되어야 함
    assembled = get_temp_dir(tid) / "assembled_x.bin"
    assert not assembled.exists()


# ─── 3. 이어받기 체크포인트 ────────────────────────────────────────────

def test_checkpoint_save_load(tmp_path, monkeypatch):
    """체크포인트 저장/로드 라운드트립."""
    # _get_app_config_dir를 tmp_path로 리디렉트
    from core import file_transfer as ft
    monkeypatch.setattr(ft, "_get_app_config_dir", lambda: tmp_path)

    cm = CheckpointManager()
    tid = str(uuid.uuid4())
    cp = Checkpoint(
        transfer_id=tid,
        completed_files=["a.txt", "dir/b.bin"],
        current_file="dir/b.bin",
        last_chunk_index=5,
        completed_hashes={"a.txt": "hex1", "dir/b.bin": "hex2"},
    )
    cm.save(cp)
    loaded = cm.load(tid)
    assert loaded is not None
    assert loaded.completed_files == ["a.txt", "dir/b.bin"]
    assert loaded.completed_hashes["a.txt"] == "hex1"
    assert loaded.last_chunk_index == 5

    # 삭제
    cm.delete(tid)
    assert cm.load(tid) is None


# ─── 4. 메타데이터 수집 및 전송 타입 ───────────────────────────────────

def test_collect_metadata_single_file(manager, tmp_path):
    f = tmp_path / "one.txt"
    f.write_bytes(b"x" * 100)
    md = manager.collect_metadata([str(f)])
    assert md.transfer_type == "file"
    assert md.file_count == 1
    assert md.total_size == 100


def test_collect_metadata_folder(manager, tmp_path):
    d = tmp_path / "folder"
    d.mkdir()
    (d / "a.txt").write_bytes(b"AAA")
    sub = d / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"BBBB")

    md = manager.collect_metadata([str(d)])
    assert md.transfer_type == "folder"
    assert md.file_count == 2
    # 경로 구분자가 / 로 정규화되어 있어야 함 (크로스플랫폼 규약)
    paths = {e["path"] for e in md.files}
    assert all("\\" not in p for p in paths)


# ═══════════════════════════════════════════════════════════════════════
# 7. Active transfer 큐 (v2.1 단일 active invariant)
# ═══════════════════════════════════════════════════════════════════════


def _make_metadata(transfer_id: str = None, total_size: int = 100) -> FileMetadata:
    """테스트용 metadata fixture 생성."""
    import uuid
    tid = transfer_id or str(uuid.uuid4())
    return FileMetadata(
        transfer_id=tid,
        transfer_type="file",
        root_name="x.bin",
        files=[{"path": "x.bin", "size": total_size, "hash": ""}],
        total_size=total_size,
        file_count=1,
    )


def test_begin_outgoing_first_returns_no_superseded(manager):
    md = _make_metadata()
    new_active, superseded = manager.begin_outgoing(md, ["/tmp/x.bin"])
    assert isinstance(new_active, ActiveOutgoing)
    assert new_active.transfer_id == md.transfer_id
    assert superseded is None
    assert manager.get_active_outgoing() is new_active


def test_begin_outgoing_second_returns_superseded(manager):
    md1 = _make_metadata()
    md2 = _make_metadata()
    first, _ = manager.begin_outgoing(md1, ["/tmp/a"])
    second, superseded = manager.begin_outgoing(md2, ["/tmp/b"])

    assert superseded is first
    assert first.stop_event.is_set()
    assert first.cancel_reason == "superseded"
    # 새 active 가 매니저에 등록됨
    assert manager.get_active_outgoing() is second
    assert not second.stop_event.is_set()


def test_end_outgoing_only_clears_matching_id(manager):
    md1 = _make_metadata()
    md2 = _make_metadata()
    first, _ = manager.begin_outgoing(md1, [])
    # 다른 transfer_id 로 end 호출 → 정리되지 않음
    manager.end_outgoing("99999999-9999-4999-8999-999999999999")
    assert manager.get_active_outgoing() is first
    # 올바른 id 로 end → 정리
    manager.end_outgoing(md1.transfer_id)
    assert manager.get_active_outgoing() is None

    # superseded race: begin → begin → end(old_id) 시 새 active 가 보호됨
    a, _ = manager.begin_outgoing(md1, [])
    b, _ = manager.begin_outgoing(md2, [])
    manager.end_outgoing(md1.transfer_id)  # 이미 superseded 됐지만 호출됨
    assert manager.get_active_outgoing() is b


def test_cancel_outgoing_matching_id(manager):
    md = _make_metadata()
    active, _ = manager.begin_outgoing(md, [])
    cancelled = manager.cancel_outgoing(md.transfer_id, reason="user")
    assert cancelled is active
    assert active.stop_event.is_set()
    assert active.cancel_reason == "user"


def test_cancel_outgoing_unknown_id_returns_none(manager):
    md = _make_metadata()
    manager.begin_outgoing(md, [])
    result = manager.cancel_outgoing(
        "deadbeef-dead-4eef-8aaa-deadbeefdead", reason="user",
    )
    assert result is None


def test_begin_incoming_supersedes_existing(manager):
    md1 = _make_metadata()
    md2 = _make_metadata()
    first, _ = manager.begin_incoming(md1)
    second, superseded = manager.begin_incoming(md2)

    assert superseded is first
    assert first.stop_event.is_set()
    assert first.cancel_reason == "superseded"
    assert manager.get_active_incoming() is second


def test_cancel_incoming_matching_id(manager):
    md = _make_metadata()
    active, _ = manager.begin_incoming(md)
    cancelled = manager.cancel_incoming(md.transfer_id, reason="error")
    assert cancelled is active
    assert active.stop_event.is_set()
    assert active.cancel_reason == "error"


def test_cleanup_incoming_artifacts_removes_temp_dir(manager, tmp_path, monkeypatch):
    """cleanup_incoming_artifacts 가 임시 디렉토리를 실제로 삭제하는지 확인."""
    import uuid
    fake_tid = str(uuid.uuid4())

    # get_temp_dir 가 가리키는 위치에 가짜 디렉토리 생성
    temp = get_temp_dir(fake_tid)
    temp.mkdir(parents=True, exist_ok=True)
    (temp / "assembled_x").write_bytes(b"junk")
    assert temp.exists()

    manager.cleanup_incoming_artifacts(fake_tid)
    assert not temp.exists()


def test_cleanup_incoming_artifacts_no_temp_dir_silent(manager):
    """임시 디렉토리가 없어도 cleanup 이 예외 없이 종료."""
    import uuid
    manager.cleanup_incoming_artifacts(str(uuid.uuid4()))  # 그냥 통과


def test_active_transfer_locks_are_independent(manager):
    """outgoing 과 incoming 큐는 독립적으로 동작."""
    md_out = _make_metadata()
    md_in = _make_metadata()
    out, _ = manager.begin_outgoing(md_out, [])
    inn, _ = manager.begin_incoming(md_in)
    # 두 active 동시 존재
    assert manager.get_active_outgoing() is out
    assert manager.get_active_incoming() is inn
    # outgoing cancel 이 incoming 에 영향 안 줌
    manager.cancel_outgoing(md_out.transfer_id, reason="user")
    assert not inn.stop_event.is_set()


# ═══════════════════════════════════════════════════════════════════════
# 8. v2.3 audit #10 — 충돌 정책 + dedup short-circuit
# ═══════════════════════════════════════════════════════════════════════


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_compute_file_hash_streaming(tmp_path):
    """streaming SHA-256 이 hashlib 기본 결과와 일치."""
    p = tmp_path / "x.bin"
    data = b"hello world" * 1000  # 11 KB
    p.write_bytes(data)
    assert FileTransferManager._compute_file_hash(p) == _hash(data)


def test_resolve_conflict_no_destination(manager, tmp_path):
    """destination 없으면 final_path 그대로 반환."""
    target = manager._resolve_conflict(
        tmp_path / "new.txt", 100, _hash(b"x"),
        "rename_with_counter", "tx-1",
    )
    assert target == tmp_path / "new.txt"


def test_resolve_conflict_dedup_short_circuit(manager, tmp_path):
    """size + SHA-256 일치 → None (정책 무관 자동 skip).

    사용자 사고 (numbering 누적) 의 직접 차단. 모든 정책에 우선 적용.
    """
    p = tmp_path / "same.txt"
    p.write_bytes(b"identical")
    h = _hash(b"identical")
    for policy in ("rename_with_counter", "overwrite", "skip", "rename_with_timestamp"):
        target = manager._resolve_conflict(p, 9, h, policy, "tx-1")
        assert target is None, f"dedup must skip under policy={policy}"
    # 파일은 그대로 (overwrite 도 unlink 안 함 — dedup 가 먼저 가로챔)
    assert p.exists()
    assert p.read_bytes() == b"identical"


def test_resolve_conflict_size_diff_falls_through(manager, tmp_path):
    """size 다르면 dedup 안 탐 → 정책 적용."""
    p = tmp_path / "x.txt"
    p.write_bytes(b"local")
    target = manager._resolve_conflict(
        p, expected_size=999, expected_hash=_hash(b"local"),
        policy="rename_with_counter", transfer_id="tx-1",
    )
    assert target == tmp_path / "x (1).txt"


def test_resolve_conflict_hash_diff_falls_through(manager, tmp_path):
    """size 같고 hash 다르면 dedup 안 탐 (다른 콘텐츠) → 정책 적용."""
    p = tmp_path / "x.txt"
    p.write_bytes(b"localx")
    target = manager._resolve_conflict(
        p, expected_size=6, expected_hash="deadbeef" * 8,
        policy="rename_with_counter", transfer_id="tx-1",
    )
    assert target == tmp_path / "x (1).txt"


def test_resolve_conflict_overwrite_unlinks_existing(manager, tmp_path):
    """overwrite 정책: 기존 파일 unlink 후 final_path 반환."""
    p = tmp_path / "x.txt"
    p.write_bytes(b"old")
    target = manager._resolve_conflict(p, 999, None, "overwrite", "tx-1")
    assert target == p
    assert not p.exists()


def test_resolve_conflict_skip_returns_none(manager, tmp_path):
    """skip 정책: None + 기존 파일 보존."""
    p = tmp_path / "x.txt"
    p.write_bytes(b"keep")
    target = manager._resolve_conflict(p, 999, None, "skip", "tx-1")
    assert target is None
    assert p.exists()
    assert p.read_bytes() == b"keep"


def test_resolve_conflict_timestamp_format(manager, tmp_path):
    """rename_with_timestamp: name.YYYYMMDD-HHMMSS.ext 형식."""
    p = tmp_path / "x.txt"
    p.write_bytes(b"old")
    target = manager._resolve_conflict(p, 999, None, "rename_with_timestamp", "tx-1")
    assert target is not None
    assert target.parent == p.parent
    assert target.suffix == ".txt"
    # stem = "x.YYYYMMDD-HHMMSS"
    parts = target.stem.split(".")
    assert len(parts) == 2 and parts[0] == "x"
    ts = parts[1]
    assert len(ts) == 15 and ts[8] == "-"  # 8자 날짜 + "-" + 6자 시각


def test_resolve_conflict_unknown_policy_falls_back(manager, tmp_path):
    """모르는 정책 → counter 폴백 (config validation 외 직접 호출 대비)."""
    p = tmp_path / "x.txt"
    p.write_bytes(b"old")
    target = manager._resolve_conflict(p, 999, None, "garbage_policy", "tx-1")
    assert target == tmp_path / "x (1).txt"


# ═══════════════════════════════════════════════════════════════════════
# 9. v2.3 audit P2 — staging cleanup TTL
# ═══════════════════════════════════════════════════════════════════════


def test_cleanup_staging_removes_expired_files(tmp_path):
    """mtime > TTL 인 파일 삭제, 신선 파일 보존."""
    import os, time
    staging = tmp_path / "ic_clipboard"
    staging.mkdir()
    old = staging / "old.txt"; old.write_bytes(b"A" * 100)
    new = staging / "new.txt"; new.write_bytes(b"B" * 200)
    past = time.time() - 30 * 3600  # 30 hours ago
    os.utime(old, (past, past))
    deleted, freed = cleanup_staging_dir(staging, ttl_hours=24)
    assert deleted == 1
    assert freed == 100
    assert not old.exists()
    assert new.exists()


def test_cleanup_staging_removes_expired_directory(tmp_path):
    """오래된 폴더 (재귀 size 합산) 삭제."""
    import os, time
    staging = tmp_path / "ic_clipboard"
    staging.mkdir()
    old_dir = staging / "myfolder"
    old_dir.mkdir()
    (old_dir / "a.txt").write_bytes(b"a" * 50)
    (old_dir / "b.txt").write_bytes(b"b" * 50)
    past = time.time() - 30 * 3600
    for entry in [old_dir / "a.txt", old_dir / "b.txt", old_dir]:
        os.utime(entry, (past, past))
    deleted, freed = cleanup_staging_dir(staging, ttl_hours=24)
    assert deleted == 1  # 폴더 1개
    assert freed == 100  # 두 파일 합산
    assert not old_dir.exists()


def test_cleanup_staging_protects_active_paths(tmp_path):
    """active_paths set 안에 있는 항목은 만료돼도 보존."""
    import os, time
    staging = tmp_path / "ic_clipboard"
    staging.mkdir()
    p = staging / "active.txt"
    p.write_bytes(b"in-flight")
    past = time.time() - 30 * 3600
    os.utime(p, (past, past))
    deleted, freed = cleanup_staging_dir(
        staging, ttl_hours=24, active_paths={str(p)},
    )
    assert deleted == 0
    assert p.exists()


def test_cleanup_staging_missing_dir_is_noop(tmp_path):
    """존재 안 하는 dir 는 (0, 0) — 예외 없음."""
    deleted, freed = cleanup_staging_dir(tmp_path / "nonexistent", ttl_hours=24)
    assert (deleted, freed) == (0, 0)


def test_cleanup_staging_zero_or_negative_ttl_clamped(tmp_path):
    """ttl_hours=0 또는 음수는 max(1, ...) 로 클램프 — 즉시 모두 삭제 위험 차단."""
    import os, time
    staging = tmp_path / "ic_clipboard"
    staging.mkdir()
    fresh = staging / "fresh.txt"
    fresh.write_bytes(b"x")
    # 신선 파일 (mtime=now). ttl 0/음수도 cutoff = now - 1h 가 되므로 보존돼야 함.
    deleted, _ = cleanup_staging_dir(staging, ttl_hours=0)
    assert deleted == 0
    assert fresh.exists()
    deleted, _ = cleanup_staging_dir(staging, ttl_hours=-100)
    assert deleted == 0
    assert fresh.exists()
