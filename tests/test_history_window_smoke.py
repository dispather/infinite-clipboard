"""2026-09-28 UX 검토 A4/A5 — 이력 창 라이브 반영 + 말줄임 smoke (Xvfb/실 디스플레이).

헤드리스 로컬에선 skip, CI(test.yml Xvfb)에서 실행. test_transfer_window_smoke.py 와
같은 이유로 모듈 스코프 단일 CTk 루트를 공유한다(다중 루트 pyimage 깨짐).
"""

import json
import os
import sys
import time

import pytest

from config import AppConfig

_SKIP = None
if sys.platform == "linux" and not os.environ.get("DISPLAY"):
    _SKIP = "DISPLAY 없음 (헤드리스) — CI Xvfb/실 세션에서만"
else:
    try:
        import customtkinter  # noqa: F401
    except Exception:
        _SKIP = "customtkinter 미설치"

pytestmark = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")


@pytest.fixture(scope="module")
def gui_root():
    import customtkinter
    customtkinter.set_appearance_mode("System")
    # 앞 모듈의 (이미 파괴된) 루트에 묶인 CTkImage 가 캐시에 남아 있으면
    # 같은 아이콘을 쓰는 순간 'pyimage doesn't exist' — 모듈마다 비우고 시작.
    from ui import components as _components
    _components._icon_cache.clear()
    try:
        root = customtkinter.CTk()
    except Exception as e:
        pytest.skip(f"GUI 초기화 불가: {e}")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def _entry(text, id_):
    return {"type": "text", "content": text, "preview": text, "timestamp": time.time(), "id": id_}


def _write(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)


def _make(gui_root, history_file, entries):
    from ui.history_window import HistoryWindow
    win = HistoryWindow(
        entries, clipboard_manager=None, config=AppConfig(language="ko", auth_key="x" * 32),
        history_file=history_file,
    )
    win.update()
    return win


def test_new_entry_in_file_appears_without_reopening(gui_root, tmp_path):
    """A4 회귀: 예전엔 연 시점 스냅샷이라 창을 열어둔 채 복사해도 새 항목이 안 떴다."""
    hf = tmp_path / "clipboard_history.json"
    first = [_entry("one", "a")]
    _write(hf, first)
    win = _make(gui_root, hf, list(first))
    try:
        _write(hf, [_entry("two", "b")] + first)
        win._poll_history_file()
        assert [e["content"] for e in win.history_list] == ["two", "one"]
        assert win._count_badge.cget("text") == "2"
    finally:
        win.destroy()


def test_pending_delete_not_resurrected_by_unrelated_reload(gui_root, tmp_path):
    """삭제 요청이 메인에 반영되기 전(최대 0.5초) 다른 이유로 파일을 다시 읽어도
    방금 지운 항목이 되살아나면 안 된다."""
    hf = tmp_path / "clipboard_history.json"
    entries = [_entry("keep", "k"), _entry("gone", "g")]
    _write(hf, entries)
    win = _make(gui_root, hf, [dict(e) for e in entries])
    try:
        target = next(e for e in win.history_list if e["id"] == "g")
        win._on_delete_click(target)
        # 메인이 아직 삭제를 반영 안 했고, 새 항목 추가로 파일만 바뀐 상황
        _write(hf, [_entry("new", "n")] + entries)
        win._poll_history_file()
        assert [e["id"] for e in win.history_list] == ["n", "k"]
        # 메인이 삭제를 반영하면 걸러내기 집합도 비워진다
        _write(hf, [_entry("new", "n"), _entry("keep", "k")])
        win._poll_history_file()
        assert win._pending_delete_keys == set()
    finally:
        win.destroy()


def test_long_preview_is_ellipsized_to_label_width(gui_root, tmp_path):
    """A5 회귀: 긴 줄이 글자 중간에서 잘리고 끝의 "…" 가 화면 밖으로 사라졌다."""
    import customtkinter
    hf = tmp_path / "clipboard_history.json"
    long_text = "가나다라마바사 " * 40
    entries = [_entry(long_text, "l")]
    _write(hf, entries)
    win = _make(gui_root, hf, entries)
    try:
        win.geometry("620x560")
        for _ in range(5):
            win.update()
        labels = [
            c for item in win._item_widgets for c in item.winfo_children()
            if isinstance(c, customtkinter.CTkLabel) and "가나다" in str(c.cget("text"))
        ]
        assert labels, "미리보기 라벨을 못 찾음"
        shown = labels[0].cget("text")
        assert shown.endswith("…") and len(shown) < len(long_text), shown
    finally:
        win.destroy()
