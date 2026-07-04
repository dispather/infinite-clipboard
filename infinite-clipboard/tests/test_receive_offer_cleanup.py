"""M6 회귀 — "받기" 버튼 실패 후 receivable_offers 미정리로 인한 영구 고장.

2026-07-03 감사 M6: `_receive_offer` 가 fetch 실패/로컬 저장 실패 시 알림만
띄우고 `_clear_receivable`을 호출하지 않았다. 전송창(별도 프로세스)은 같은
offer_id 위젯을 재사용하므로, 최초 클릭 때 세운 "requested" 플래그가 리셋되지
않아 받기 버튼이 영구적으로 고장(재시도 불가)됐다. 수정: 두 실패 경로 모두
`_clear_receivable` 호출.
"""

from config import AppConfig
from core.protocol import generate_peer_id
from main import InfiniteClipboard


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


def test_receive_offer_clears_receivable_on_fetch_failure():
    app = _make_app()
    offer_id = "offer-1"
    _register_receivable(app, offer_id, generate_peer_id())

    def _boom(oid):
        raise RuntimeError("fetch 실패 시뮬레이션")
    app._fetch_offer = _boom

    app._receive_offer(offer_id)

    with app._offer_lock:
        assert offer_id not in app.receivable_offers, (
            "M6 회귀 — fetch 실패 후에도 receivable_offers 에 남아있어 "
            "받기 버튼이 영구 고장됨"
        )


def test_receive_offer_clears_receivable_on_local_save_failure(tmp_path, monkeypatch):
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
        assert offer_id not in app.receivable_offers, (
            "M6 회귀 — 로컬 저장 실패 후에도 receivable_offers 에 남아있어 "
            "받기 버튼이 영구 고장됨"
        )


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
