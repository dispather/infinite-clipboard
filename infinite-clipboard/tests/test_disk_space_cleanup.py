"""H5 회귀 — 디스크 공간 부족 시 방치된 전송이 나중에 완료되어 클립보드를
무단으로 덮어쓰는 문제.

2026-07-03 감사 H5: `_handle_file_ready` 가 디스크 공간 부족을 감지하면 로컬
fetch 대기만 깨우고 return 했을 뿐, pending_transfers 등록과 active incoming
슬롯을 정리하지 않았다. source 는 이 실패를 모른 채 chunk 를 계속 보내고, 그
전송이 나중에 조립 완료되면 사용자가 이미 "실패" 알림을 본 뒤에도 클립보드가
조용히 덮어써졌다. 수정: pending_transfers pop + active incoming 정리 +
source 에게 MSG_FILE_CANCEL 통지.
"""

from config import AppConfig
from core.protocol import MSG_FILE_CANCEL, generate_peer_id
from main import InfiniteClipboard


def _make_app():
    return InfiniteClipboard(AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
    ))


def _patch_senders(app):
    targeted_calls = []
    broadcast_calls = []
    app._send_msg_to = lambda msg_type, data, receiver_peer: targeted_calls.append(
        (msg_type, data, receiver_peer)
    )
    app._send_msg = lambda msg_type, data=None: broadcast_calls.append((msg_type, data))
    return targeted_calls, broadcast_calls


def _make_metadata_dict(app, tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 100)
    metadata = app.file_manager.collect_metadata([str(f)])
    return metadata.to_dict()


def test_disk_space_insufficient_pops_pending_transfer(tmp_path):
    app = _make_app()
    app.file_manager.check_disk_space = lambda required_bytes: False
    data = _make_metadata_dict(app, tmp_path)

    app._handle_file_ready(data)

    with app._transfers_lock:
        assert data["transfer_id"] not in app.pending_transfers, (
            "H5 회귀 — 디스크 부족 후에도 pending_transfers 에 남아있으면 이후 "
            "chunk 가 계속 처리돼 방치된 전송이 나중에 완료될 수 있음"
        )


def test_disk_space_insufficient_releases_active_incoming_slot(tmp_path):
    app = _make_app()
    app.file_manager.check_disk_space = lambda required_bytes: False
    data = _make_metadata_dict(app, tmp_path)

    app._handle_file_ready(data)

    assert app.file_manager.get_active_incoming() is None, (
        "H5 회귀 — active incoming 슬롯이 남아있으면 다음 진짜 전송이 "
        "'superseded' 로 오인되거나 취소될 수 있음"
    )


def test_disk_space_insufficient_notifies_source_targeted_when_known(tmp_path):
    app = _make_app()
    app.file_manager.check_disk_space = lambda required_bytes: False
    data = _make_metadata_dict(app, tmp_path)
    source_peer = generate_peer_id()
    with app._offer_lock:
        app.received_offers[data["transfer_id"]] = {"source_peer": source_peer}

    targeted, broadcast = _patch_senders(app)
    app._handle_file_ready(data)

    cancels_targeted = [c for c in targeted if c[0] == MSG_FILE_CANCEL]
    cancels_broadcast = [c for c in broadcast if c[0] == MSG_FILE_CANCEL]
    assert not cancels_broadcast, (
        f"source 를 알 때는 targeted 로 보내야 함: {cancels_broadcast}"
    )
    assert len(cancels_targeted) == 1
    msg_type, cancel_data, receiver_peer = cancels_targeted[0]
    assert receiver_peer == source_peer
    assert cancel_data["transfer_id"] == data["transfer_id"]


def test_disk_space_insufficient_falls_back_to_broadcast_when_source_unknown(tmp_path):
    """offer 가 이미 사라진 edge case — 기존 broadcast 로 graceful degrade."""
    app = _make_app()
    app.file_manager.check_disk_space = lambda required_bytes: False
    data = _make_metadata_dict(app, tmp_path)

    targeted, broadcast = _patch_senders(app)
    app._handle_file_ready(data)

    cancels_targeted = [c for c in targeted if c[0] == MSG_FILE_CANCEL]
    cancels_broadcast = [c for c in broadcast if c[0] == MSG_FILE_CANCEL]
    assert not cancels_targeted
    assert len(cancels_broadcast) == 1
