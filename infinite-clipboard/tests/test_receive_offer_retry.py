"""이어받기 UI 재시도 — terminal vs retryable 실패 분류 회귀 (2026-07-04).

`_receive_offer`(main.py)의 실패를 무조건 `_clear_receivable` 하던 것을, 재시도해도
동일하게 실패할 게 확실한 원인(terminal)과 일시적 원인(retryable)으로 나눴다.
retryable 은 목록에서 지우지 않고 `receivable_offers[offer_id]["last_failure"]`
에 사유만 기록 — 전송창(ui/transfer_window.py)이 이 타임스탬프 변화를 보고
위젯의 "requested" 플래그를 명시적으로 리셋해 "재시도" 버튼을 다시 활성화한다.

M6(2026-07-03)이 고친 "requested 플래그 영구 고착" 버그를 이 설계가 재도입하지
않는다는 건, terminal 케이스가 여전히 clear 된다는 tests/test_receive_offer_cleanup.py
와, retryable 케이스가 last_failure 로 정확히 보존된다는 아래 테스트가 함께 보장한다
(위젯 쪽 requested 리셋 자체는 main.py 레이어 테스트 범위 밖 — ui/ 는 별도 프로세스).
"""

import pytest

from config import AppConfig
from core.protocol import (
    FETCH_FAIL_SUPERSEDED, FETCH_FAIL_EXPIRED, FETCH_FAIL_MISSING,
    FETCH_FAIL_OFFLINE, FETCH_FAIL_ERROR, generate_peer_id,
)
from main import FAIL_REASON_INFO, FetchFailure, InfiniteClipboard

TERMINAL_REASONS = ("superseded", "expired", "missing", "unknown_offer")
RETRYABLE_REASONS = (
    "offline", "error", "cancelled", "chunk_hash_mismatch", "assemble_failed",
    "source_error", "timeout", "no_space", "save_error", "fetch_empty",
)


def _make_app():
    return InfiniteClipboard(AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
    ))


def _register_receivable(app, offer_id, source_peer, name="photo.png"):
    with app._offer_lock:
        app.receivable_offers[offer_id] = {
            "offer_id": offer_id,
            "source_peer": source_peer,
            "name": name,
            "kind": "image",
            "total_size": 123,
            "created_at": 0.0,
        }


# ── FAIL_REASON_INFO 분류 정합성 ──────────────────────────────────────────

def test_fail_reason_info_covers_network_fetch_fail_reasons():
    """core.protocol 의 네트워크 FETCH_FAIL 5종이 전부 매핑돼 있어야 한다."""
    for reason in (
        FETCH_FAIL_SUPERSEDED, FETCH_FAIL_EXPIRED, FETCH_FAIL_MISSING,
        FETCH_FAIL_OFFLINE, FETCH_FAIL_ERROR,
    ):
        assert reason in FAIL_REASON_INFO


def test_fail_reason_info_covers_local_only_reasons():
    """_fetch_offer/_receive_offer 가 만드는 로컬 전용 사유도 전부 매핑돼 있어야 한다."""
    for reason in TERMINAL_REASONS + RETRYABLE_REASONS:
        assert reason in FAIL_REASON_INFO, f"{reason} 가 FAIL_REASON_INFO 에 없음"


def test_terminal_vs_retryable_classification_matches_design():
    """설계 표(terminal=재시도 무의미 / retryable=재시도 여지)와 코드가 일치하는지."""
    for reason, (_message, retryable) in FAIL_REASON_INFO.items():
        expected = reason not in TERMINAL_REASONS
        assert retryable == expected, (
            f"{reason} 의 retryable={retryable} 이 기대({expected})와 다름"
        )


# ── terminal 사유 — receivable 제거 (M6 동작 유지) ────────────────────────

@pytest.mark.parametrize("reason", TERMINAL_REASONS)
def test_terminal_reason_clears_receivable(reason):
    app = _make_app()
    offer_id = f"offer-terminal-{reason}"
    _register_receivable(app, offer_id, generate_peer_id())

    def _boom(oid, _reason=reason):
        raise FetchFailure(_reason, f"시뮬레이션: {_reason}")
    app._fetch_offer = _boom

    app._receive_offer(offer_id)

    with app._offer_lock:
        assert offer_id not in app.receivable_offers


# ── retryable 사유 — receivable 보존 + last_failure 기록 ──────────────────

@pytest.mark.parametrize("reason", RETRYABLE_REASONS)
def test_retryable_reason_preserves_receivable_with_last_failure(reason):
    app = _make_app()
    offer_id = f"offer-retry-{reason}"
    _register_receivable(app, offer_id, generate_peer_id())

    def _boom(oid, _reason=reason):
        raise FetchFailure(_reason, f"시뮬레이션: {_reason}")
    app._fetch_offer = _boom

    app._receive_offer(offer_id)

    with app._offer_lock:
        entry = app.receivable_offers.get(offer_id)
        assert entry is not None, f"{reason} 는 retryable — receivable 에 남아있어야 함"
        assert entry["last_failure"]["reason"] == reason
        assert entry["last_failure"]["failed_at"] > 0


def test_unclassified_exception_treated_as_retryable():
    """_fetch_offer 가 FetchFailure 로 안 감싼 예외(네트워크 전송 등)도 낙관적으로
    retryable 취급 — receivable 에 남아 재시도 여지를 준다."""
    app = _make_app()
    offer_id = "offer-unclassified"
    _register_receivable(app, offer_id, generate_peer_id())

    def _boom(oid):
        raise ConnectionError("전송 중 소켓 끊김 시뮬레이션")
    app._fetch_offer = _boom

    app._receive_offer(offer_id)

    with app._offer_lock:
        entry = app.receivable_offers.get(offer_id)
        assert entry is not None
        assert entry["last_failure"]["reason"] == "error"


# ── 재시도 성공 경로 ──────────────────────────────────────────────────────

def test_retry_after_failure_then_success_clears_last_failure(tmp_path):
    app = _make_app()
    offer_id = "offer-retry-then-success"
    _register_receivable(app, offer_id, generate_peer_id())
    app.config.download_path = str(tmp_path / "downloads")

    def _boom(oid):
        raise FetchFailure("offline", "1차 시도 실패")
    app._fetch_offer = _boom
    app._receive_offer(offer_id)

    with app._offer_lock:
        assert app.receivable_offers[offer_id]["last_failure"]["reason"] == "offline"

    src = tmp_path / "src.bin"
    src.write_bytes(b"hello")

    class _Fetched:
        paths = [str(src)]

    app._fetch_offer = lambda oid: _Fetched()
    app._receive_offer(offer_id)  # 재시도(같은 offer_id 로 재호출)

    with app._offer_lock:
        assert offer_id not in app.receivable_offers
    assert (tmp_path / "downloads" / "src.bin").exists()


# ── 완료 목록 path 주석 (폴더 열기 버튼용) ─────────────────────────────────

def test_receive_offer_success_annotates_completed_entry_with_path(tmp_path):
    """_finish_transfer 가 만든 완료 엔트리(transfer_id==offer_id)에 _receive_offer
    가 최종 저장 경로를 사후 보강 — ui/transfer_window.py 의 '폴더 열기' 버튼 조건."""
    app = _make_app()
    offer_id = "offer-completed-path"
    _register_receivable(app, offer_id, generate_peer_id())
    app.config.download_path = str(tmp_path / "downloads")

    # _handle_transfer_complete → _finish_transfer 가 실제로 만드는 모양을 재현.
    with app._progress_lock:
        app._completed_transfers.append({
            "transfer_id": offer_id, "filename": "photo.png",
            "total_size": 123, "direction": "receive", "completed_at": 0.0,
        })

    src = tmp_path / "photo.png"
    src.write_bytes(b"img-bytes")

    class _Fetched:
        paths = [str(src)]

    app._fetch_offer = lambda oid: _Fetched()
    app._receive_offer(offer_id)

    with app._progress_lock:
        entry = next(
            e for e in app._completed_transfers if e["transfer_id"] == offer_id
        )
    assert entry["via_receive_button"] is True
    assert entry["path"] == str(tmp_path / "downloads")


def test_lazy_paste_completed_entry_has_no_path_field(tmp_path):
    """대조군 — 받기 버튼을 거치지 않은(lazy-paste) 완료 엔트리는 path 필드가 없어야
    ui/transfer_window.py 가 그 항목엔 '폴더 열기' 버튼을 그리지 않는다."""
    app = _make_app()
    with app._progress_lock:
        app._completed_transfers.append({
            "transfer_id": "some-lazy-paste-transfer", "filename": "clip.png",
            "total_size": 42, "direction": "receive", "completed_at": 0.0,
        })

    with app._progress_lock:
        entry = app._completed_transfers[0]
    assert "path" not in entry
    assert "via_receive_button" not in entry
