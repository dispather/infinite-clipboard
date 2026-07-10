"""H7 회귀 — MSG_FILE_ERROR 가 broadcast 아닌 targeted 전송이어야 한다.

2026-07-03 감사 H7: 전송 실패(청크 해시 불일치/조립 실패/송신 중 예외) 를 보고하는
MSG_FILE_ERROR 가 무조건 broadcast 돼 무관한 피어에게 전송 실패 상세(파일 경로 등)
가 노출됐다. source/receiver peer 를 알 수 있으면 그 peer 에게만 targeted 전송해야
하고, 알 수 없는 극단적 edge case(offer 가 이미 superseded 로 사라짐)에서만 기존
broadcast 로 graceful degrade 해야 한다.
"""

import uuid

from config import AppConfig
from core.protocol import MSG_FILE_ERROR, generate_peer_id
from main import InfiniteClipboard


def _make_app():
    return InfiniteClipboard(AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
    ))


def _patch_senders(app):
    """_send_msg_to/_send_msg 호출을 가로채 기록."""
    targeted_calls = []
    broadcast_calls = []
    app._send_msg_to = lambda msg_type, data, receiver_peer: targeted_calls.append(
        (msg_type, data, receiver_peer)
    )
    app._send_msg = lambda msg_type, data=None: broadcast_calls.append((msg_type, data))
    return targeted_calls, broadcast_calls


# ── 수신측: 청크 해시 불일치 ──────────────────────────────────────────────

def test_chunk_hash_mismatch_error_targeted_when_source_known():
    app = _make_app()
    transfer_id = str(uuid.uuid4())
    source_peer = generate_peer_id()
    with app._transfers_lock:
        app.pending_transfers[transfer_id] = object()
    with app._offer_lock:
        app.received_offers[transfer_id] = {"source_peer": source_peer}

    targeted, broadcast = _patch_senders(app)

    app._handle_file_chunk({
        "transfer_id": transfer_id, "file_path": "x.bin", "chunk_index": 0,
        "binary_data": b"corrupted-bytes", "hash": "0" * 16,
    })

    assert not broadcast, f"H7 회귀 — MSG_FILE_ERROR 가 broadcast 됨: {broadcast}"
    assert len(targeted) == 1
    msg_type, data, receiver_peer = targeted[0]
    assert msg_type == MSG_FILE_ERROR
    assert receiver_peer == source_peer
    assert data["receiver_peer"] == source_peer
    assert data["transfer_id"] == transfer_id


def test_chunk_hash_mismatch_error_falls_back_to_broadcast_when_source_unknown():
    """offer 가 이미 supersede 로 사라진 edge case — 기존 broadcast 로 graceful degrade."""
    app = _make_app()
    transfer_id = str(uuid.uuid4())
    with app._transfers_lock:
        app.pending_transfers[transfer_id] = object()
    # received_offers 에 없음 (edge case)

    targeted, broadcast = _patch_senders(app)

    app._handle_file_chunk({
        "transfer_id": transfer_id, "file_path": "x.bin", "chunk_index": 0,
        "binary_data": b"corrupted-bytes", "hash": "0" * 16,
    })

    assert not targeted
    assert len(broadcast) == 1
    assert broadcast[0][0] == MSG_FILE_ERROR


# ── 수신측: 조립(SHA-256) 실패 ────────────────────────────────────────────

def test_assemble_failure_error_targeted_when_source_known():
    app = _make_app()
    transfer_id = str(uuid.uuid4())
    source_peer = generate_peer_id()
    with app._transfers_lock:
        app.pending_transfers[transfer_id] = object()
    with app._offer_lock:
        app.received_offers[transfer_id] = {"source_peer": source_peer}

    targeted, broadcast = _patch_senders(app)

    app._handle_file_end({
        "transfer_id": transfer_id,
        "file_path": "never-received.bin",
        "sha256": "0" * 64,
    })

    assert not broadcast, f"H7 회귀 — MSG_FILE_ERROR 가 broadcast 됨: {broadcast}"
    assert len(targeted) == 1
    msg_type, data, receiver_peer = targeted[0]
    assert msg_type == MSG_FILE_ERROR
    assert receiver_peer == source_peer
    assert data["receiver_peer"] == source_peer


# ── 송신측: 전송 중 예외 ──────────────────────────────────────────────────

def test_send_files_error_targeted_when_receiver_known(tmp_path):
    app = _make_app()
    f = tmp_path / "one.txt"
    f.write_bytes(b"x" * 100)
    metadata = app.file_manager.collect_metadata([str(f)])
    receiver_peer = generate_peer_id()

    def _boom(*a, **k):
        raise RuntimeError("simulated send failure")
    app.file_manager.send_file = _boom

    targeted, broadcast = _patch_senders(app)

    app._send_files(
        metadata.transfer_id, metadata, [str(f)], receiver_peer=receiver_peer,
    )

    # FILE_START 도 targeted 로 먼저 나가므로, FILE_ERROR 만 걸러서 검사
    error_broadcasts = [c for c in broadcast if c[0] == MSG_FILE_ERROR]
    error_targeted = [c for c in targeted if c[0] == MSG_FILE_ERROR]
    assert not error_broadcasts, f"H7 회귀 — MSG_FILE_ERROR 가 broadcast 됨: {error_broadcasts}"
    assert len(error_targeted) == 1
    msg_type, data, rcv = error_targeted[0]
    assert rcv == receiver_peer
    assert data["receiver_peer"] == receiver_peer
    assert data["transfer_id"] == metadata.transfer_id


def test_send_files_error_is_broadcast_when_eager(tmp_path):
    """receiver_peer 없음(eager 호환 경로)이면 기존처럼 broadcast 유지."""
    app = _make_app()
    f = tmp_path / "one.txt"
    f.write_bytes(b"x" * 100)
    metadata = app.file_manager.collect_metadata([str(f)])

    def _boom(*a, **k):
        raise RuntimeError("simulated send failure")
    app.file_manager.send_file = _boom

    targeted, broadcast = _patch_senders(app)

    app._send_files(metadata.transfer_id, metadata, [str(f)])

    error_targeted = [c for c in targeted if c[0] == MSG_FILE_ERROR]
    error_broadcasts = [c for c in broadcast if c[0] == MSG_FILE_ERROR]
    assert not error_targeted
    assert len(error_broadcasts) == 1


# ── 리뷰 발견: received_offers eviction 이 source_peer 조회를 깨뜨리던 문제 ──

def test_source_peer_survives_unrelated_offer_eviction():
    """리뷰 발견: received_offers 는 offer_id 1개만 유지하는 supersede 캐시라,
    무관한 다른 peer 의 새 offer 가 도착하면(broadcast) 통째로 교체된다. 이
    전송의 source_peer 조회가 그 eviction 에 영향받으면, 진행 중이던 전송이
    실패했을 때 H7 의 targeted 라우팅이 broadcast 로 새 정보가 노출된다.
    _fetch_offer 가 채우는 _transfer_source_peers 는 offer 캐시 lifecycle 과
    무관해야 한다."""
    app = _make_app()
    transfer_id = str(uuid.uuid4())
    source_peer = generate_peer_id()

    with app._transfers_lock:
        app._transfer_source_peers[transfer_id] = source_peer

    # 무관한 다른 peer 의 새 offer 도착 흉내 — received_offers 통째로 교체됨
    with app._offer_lock:
        app.received_offers = {str(uuid.uuid4()): {"source_peer": generate_peer_id()}}

    assert app._source_peer_for_transfer(transfer_id) == source_peer, (
        "리뷰 회귀 — 무관한 offer eviction 으로 source_peer 조회가 깨짐"
    )


def test_source_peer_cleared_after_transfer_complete(tmp_path):
    """_transfer_source_peers 항목이 전송 완료 후 정리되지 않으면 무한정 쌓인다."""
    app = _make_app()
    f = tmp_path / "one.txt"
    f.write_bytes(b"x" * 10)
    metadata = app.file_manager.collect_metadata([str(f)])
    transfer_id = metadata.transfer_id
    source_peer = generate_peer_id()

    with app._transfers_lock:
        app.pending_transfers[transfer_id] = metadata
        app._transfer_source_peers[transfer_id] = source_peer

    app._handle_transfer_complete({"transfer_id": transfer_id})

    with app._transfers_lock:
        assert transfer_id not in app._transfer_source_peers
