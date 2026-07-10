"""M5 회귀 — 이미지 offer 임시 스냅샷 삭제가 _outgoing_fetch_lock 없이 실행되던 문제.

2026-07-03 감사 M5: `_cleanup_offer_image`가 진행 중인 `_serve_fetch`(같은 파일을
스트리밍 중) 와 동시에 `os.remove`를 실행할 수 있었다. Windows 는 열려 있는
파일을 삭제하지 못해(OSError 로 조용히 무시됨) temp 파일이 계속 쌓인다. 수정:
`_serve_fetch` 가 잡는 `_outgoing_fetch_lock` 을 `_cleanup_offer_image` 도 잡아
직렬화한다.
"""

import threading
import time

from config import AppConfig
from core.protocol import generate_peer_id
from main import InfiniteClipboard


def _make_app():
    return InfiniteClipboard(AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
    ))


def test_cleanup_offer_image_waits_for_outgoing_fetch_lock(tmp_path):
    app = _make_app()
    img = tmp_path / "snap.png"
    img.write_bytes(b"fake png bytes")
    with app._offer_lock:
        app.current_offer = {
            "offer_id": "x", "kind": "image", "_image_temp": str(img),
        }

    # _serve_fetch 가 파일을 스트리밍 중인 상황을 흉내 — 락을 선점
    app._outgoing_fetch_lock.acquire()
    cleanup_done = threading.Event()

    def run_cleanup():
        app._cleanup_offer_image()
        cleanup_done.set()

    t = threading.Thread(target=run_cleanup, daemon=True)
    t.start()
    time.sleep(0.2)
    try:
        assert not cleanup_done.is_set(), (
            "M5 회귀 — cleanup 이 진행 중인 fetch 의 락을 기다리지 않고 실행됨"
        )
        assert img.exists(), "락 보유 중인데 파일이 이미 삭제됨 (Windows 파일-사용-중 삭제 위험)"
    finally:
        app._outgoing_fetch_lock.release()

    t.join(timeout=2)
    assert cleanup_done.is_set(), "락 해제 후에도 cleanup 이 완료되지 않음"
    assert not img.exists(), "락 해제 후에도 파일이 삭제되지 않음"


def test_cleanup_offer_image_noop_when_no_image_offer():
    """이미지 offer 가 아니면(파일 offer 등) 조용히 아무것도 안 해야 한다."""
    app = _make_app()
    with app._offer_lock:
        app.current_offer = {"offer_id": "y", "kind": "file"}
    app._cleanup_offer_image()  # 예외 없이 통과해야 함


def test_cleanup_offer_image_handles_already_missing_file(tmp_path):
    """파일이 이미 없어도(중복 정리 등) 예외를 전파하지 않아야 한다."""
    app = _make_app()
    missing = tmp_path / "already-gone.png"
    with app._offer_lock:
        app.current_offer = {
            "offer_id": "z", "kind": "image", "_image_temp": str(missing),
        }
    app._cleanup_offer_image()  # OSError(FileNotFoundError) 를 삼켜야 함
