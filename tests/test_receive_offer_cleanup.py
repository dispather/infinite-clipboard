"""M6 회귀 — "받기" 버튼 실패 후 receivable_offers 미정리로 인한 영구 고장.

2026-07-03 감사 M6: `_receive_offer` 가 fetch 실패/로컬 저장 실패 시 알림만
띄우고 `_clear_receivable`을 호출하지 않았다. 전송창(별도 프로세스)은 같은
offer_id 위젯을 재사용하므로, 최초 클릭 때 세운 "requested" 플래그가 리셋되지
않아 받기 버튼이 영구적으로 고장(재시도 불가)됐다. 당시 수정: 두 실패 경로
모두 `_clear_receivable` 호출.

2026-07-04 재시도 기능 도입으로 이 무조건 clear 정책이 terminal/retryable 로
갈렸다(`main.FAIL_REASON_INFO`, `tests/test_receive_offer_retry.py` 참조).
이 파일은 그 중 **terminal** 케이스(재시도해도 동일 실패가 확실한 원인)만
계속 다룬다 — retryable 케이스가 clear 대신 last_failure 로 보존되면서도
M6 이 고친 "영구 고장"이 재발하지 않는다는 건 test_receive_offer_retry.py 가
검증한다(last_failure 갱신 시 위젯의 requested 가 명시적으로 리셋됨).
"""

from config import AppConfig
from core.protocol import generate_peer_id
from main import FetchFailure, InfiniteClipboard


def _make_app():
    return InfiniteClipboard(AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
    ))


def _register_receivable(app, offer_id, source_peer):
    with app._offer_lock:
        app.receivable_offers[offer_id] = {
            "offer_id": offer_id,
            "source_peer": source_peer,
            "name": "photo.png",
            "kind": "image",
            "total_size": 123,
            "created_at": 0.0,
        }


def test_receive_offer_clears_receivable_on_terminal_fetch_failure():
    """terminal 원인(예: missing)은 여전히 M6 이전처럼 무조건 clear — 같은
    offer_id 로 재시도해도 동일하게 실패할 게 확실하므로 재시도 UI 를 안 남긴다."""
    app = _make_app()
    offer_id = "offer-1"
    _register_receivable(app, offer_id, generate_peer_id())

    def _boom(oid):
        raise FetchFailure("missing", "fetch 실패 시뮬레이션(terminal)")
    app._fetch_offer = _boom

    app._receive_offer(offer_id)

    with app._offer_lock:
        assert offer_id not in app.receivable_offers, (
            "M6 회귀 — terminal 실패 후에도 receivable_offers 에 남아있어 "
            "받기 버튼이 영구 고장됨"
        )


def test_receive_offer_preserves_receivable_on_retryable_local_save_failure(tmp_path):
    """2026-07-04: 로컬 저장 실패(save_error)는 retryable 로 재분류됐다 — M6 이
    막으려 한 "영구 고장"은 clear 가 아니라 last_failure + 위젯 requested 리셋으로
    해결한다(tests/test_receive_offer_retry.py 가 그 리셋 자체를 검증). 여기선
    save_error 가 더 이상 clear 되지 않는다는 것만 회귀 가드로 남긴다."""
    app = _make_app()
    offer_id = "offer-2"
    _register_receivable(app, offer_id, generate_peer_id())

    class _Fetched:
        paths = ["/nonexistent/path/does/not/exist.bin"]

    app._fetch_offer = lambda oid: _Fetched()
    # download_path 를 읽기전용 등으로 만들 필요 없이, 존재하지 않는 소스
    # 경로로 shutil.copy2 가 자연스럽게 실패하도록 유도.
    app.config.download_path = str(tmp_path / "downloads")

    app._receive_offer(offer_id)

    with app._offer_lock:
        entry = app.receivable_offers.get(offer_id)
        assert entry is not None, "save_error 는 retryable — receivable 에 남아있어야 함"
        assert entry["last_failure"]["reason"] == "save_error"


def test_receive_offer_clears_receivable_on_success(tmp_path):
    """정상 경로도 여전히 clear 되는지 (회귀 방지용 대조군)."""
    app = _make_app()
    offer_id = "offer-3"
    _register_receivable(app, offer_id, generate_peer_id())

    src = tmp_path / "src.bin"
    src.write_bytes(b"hello")

    class _Fetched:
        paths = [str(src)]

    app._fetch_offer = lambda oid: _Fetched()
    app.config.download_path = str(tmp_path / "downloads")

    app._receive_offer(offer_id)

    with app._offer_lock:
        assert offer_id not in app.receivable_offers
    assert (tmp_path / "downloads" / "src.bin").exists()
