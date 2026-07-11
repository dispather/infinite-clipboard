"""2026-07-12 mac-studio 기능 요청 — 히스토리 창 삭제/전체지우기.

HistoryWindow 는 별도 프로세스의 스냅샷 뷰라 clipboard_history.json 을 직접
고치면 메인 프로세스의 인메모리 clipboard_history 가 다음 클립보드 변경 시
덮어써서 삭제가 무효화된다. cancel_requests.json/receive_requests.json 과
동일한 IPC(history_delete_requests.json)로 메인 프로세스에 위임하는
main.py:_watch_history_delete_requests 를 검증한다.
"""

import json
import threading
import time

from config import AppConfig
from core.protocol import generate_peer_id
from main import InfiniteClipboard


def _make_app(tmp_path, monkeypatch):
    cfg = AppConfig(mode="client", auth_key="x" * 32, peer_id=generate_peer_id())
    app = InfiniteClipboard(cfg)
    history_file = tmp_path / "clipboard_history.json"
    delete_file = tmp_path / "history_delete_requests.json"
    monkeypatch.setattr(app, "_get_history_file", lambda: str(history_file))
    monkeypatch.setattr(app, "_get_history_delete_request_file", lambda: str(delete_file))
    return app, history_file, delete_file


def test_delete_request_removes_matching_entry(tmp_path, monkeypatch):
    app, history_file, delete_file = _make_app(tmp_path, monkeypatch)
    app.clipboard_history = [
        {"type": "text", "content": "a", "preview": "a", "timestamp": 111.0},
        {"type": "text", "content": "b", "preview": "b", "timestamp": 222.0},
        {"type": "text", "content": "c", "preview": "c", "timestamp": 333.0},
    ]

    with open(delete_file, "w", encoding="utf-8") as f:
        json.dump([222.0], f)

    app.running = True
    th = threading.Thread(target=app._watch_history_delete_requests, daemon=True)
    th.start()
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline and len(app.clipboard_history) == 3:
            time.sleep(0.05)
    finally:
        app.running = False
        th.join(timeout=2.0)

    remaining = [e["timestamp"] for e in app.clipboard_history]
    assert remaining == [111.0, 333.0], f"삭제 대상만 제거돼야 함, 실제: {remaining}"

    # 파일에도 반영됐어야 함 (_save_history_file 호출 확인)
    with open(history_file, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert [e["timestamp"] for e in on_disk] == [111.0, 333.0]

    # 처리된 요청 파일은 비워짐(또는 삭제됨) — 재처리 방지
    if delete_file.exists():
        with open(delete_file, "r", encoding="utf-8") as f:
            assert json.load(f) == []


def test_delete_request_for_unknown_timestamp_is_noop(tmp_path, monkeypatch):
    """존재하지 않는 timestamp 를 요청해도 예외 없이, 다른 항목은 그대로."""
    app, history_file, delete_file = _make_app(tmp_path, monkeypatch)
    app.clipboard_history = [
        {"type": "text", "content": "a", "preview": "a", "timestamp": 111.0},
    ]

    with open(delete_file, "w", encoding="utf-8") as f:
        json.dump([999.0], f)

    app.running = True
    th = threading.Thread(target=app._watch_history_delete_requests, daemon=True)
    th.start()
    try:
        time.sleep(1.0)
    finally:
        app.running = False
        th.join(timeout=2.0)

    assert len(app.clipboard_history) == 1
    assert app.clipboard_history[0]["timestamp"] == 111.0


def test_clear_all_requests_removes_every_entry(tmp_path, monkeypatch):
    """전체 지우기 — HistoryWindow 가 모든 timestamp 를 한 번에 append."""
    app, history_file, delete_file = _make_app(tmp_path, monkeypatch)
    app.clipboard_history = [
        {"type": "text", "content": str(i), "preview": str(i), "timestamp": float(i)}
        for i in range(5)
    ]

    with open(delete_file, "w", encoding="utf-8") as f:
        json.dump([0.0, 1.0, 2.0, 3.0, 4.0], f)

    app.running = True
    th = threading.Thread(target=app._watch_history_delete_requests, daemon=True)
    th.start()
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline and app.clipboard_history:
            time.sleep(0.05)
    finally:
        app.running = False
        th.join(timeout=2.0)

    assert app.clipboard_history == []
