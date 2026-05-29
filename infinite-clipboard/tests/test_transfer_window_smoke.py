"""v3.0 S4: transfer_window 받기 섹션 UI smoke 테스트 (Xvfb/실 디스플레이).

헤드리스 로컬에선 skip, CI(test.yml Xvfb)에서 실행. 시각적 정확성이 아니라
**구성/렌더가 예외 없이 되는가 + 받기 위젯/섹션 토글 로직** 을 검증한다 (헤드리스라
시각 확인 불가한 blind-UI 의 크래시·로직 버그 차단). cancel 버튼 패턴 미러라 안전망.

⚠️ 한 프로세스에서 customtkinter.CTk() 루트를 여러 번 만들면 CTkImage 캐시가 깨져
('pyimage doesn't exist') 두 번째 테스트부터 실패한다(실앱은 루트 1개라 무관). →
**모듈 스코프 단일 루트 fixture** 로 공유하고, 각 테스트는 TransferWindow(Toplevel)만 생성·파괴.
"""

import json
import os
import sys
import uuid

import pytest

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
    """모듈 전체가 공유하는 단일 CTk 루트 (다중 루트 pyimage 깨짐 회피)."""
    import customtkinter
    customtkinter.set_appearance_mode("System")
    try:
        root = customtkinter.CTk()
    except Exception as e:  # Tk/디스플레이 초기화 실패
        pytest.skip(f"GUI 초기화 불가: {e}")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def _write_state(path, receivable):
    state = {"active": {}, "completed": [], "receivable": receivable}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)


def _make_receivable(name="photo.png", size=12345, kind="image"):
    return {
        "offer_id": str(uuid.uuid4()),
        "source_peer": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "name": name, "kind": kind, "total_size": size, "created_at": 1.0,
    }


def _make_window(gui_root, state_file):
    """공유 루트 위에 TransferWindow(Toplevel) 생성 — __init__ 이 _poll_state 1회 호출."""
    from ui.transfer_window import TransferWindow
    win = TransferWindow(str(state_file))
    win.update()
    return win


def test_receivable_renders_and_section_visible(gui_root, tmp_path):
    """받을 항목 있는 상태 → 받기 위젯 생성 + 섹션 표시 (크래시 없음)."""
    state_file = tmp_path / "transfer_state.json"
    _write_state(state_file, [_make_receivable("doc.txt", 999, "file")])
    win = _make_window(gui_root, state_file)
    try:
        assert len(win._receivable_widgets) == 1, "받기 위젯이 생성되지 않음"
        assert win._recv_visible is True, "받기 섹션이 표시되지 않음"
    finally:
        win.destroy()


def test_no_receivable_section_hidden(gui_root, tmp_path):
    """받을 항목 없음 → 받기 섹션 숨김 (happy-path 공간 차지 안 함)."""
    state_file = tmp_path / "transfer_state.json"
    _write_state(state_file, [])
    win = _make_window(gui_root, state_file)
    try:
        assert len(win._receivable_widgets) == 0
        assert win._recv_visible is False, "받을 항목 없는데 받기 섹션이 보임"
    finally:
        win.destroy()


def test_receive_click_writes_ipc(gui_root, tmp_path, monkeypatch):
    """받기 버튼 클릭 → receive_requests.json 에 offer_id 기록 (main 폴링 대상)."""
    import config as config_mod
    monkeypatch.setattr(config_mod, "_get_config_dir", lambda: tmp_path)

    state_file = tmp_path / "transfer_state.json"
    entry = _make_receivable("clip.png", 555, "image")
    _write_state(state_file, [entry])
    win = _make_window(gui_root, state_file)
    try:
        oid = entry["offer_id"]
        assert oid in win._receivable_widgets
        win._on_receive_click(oid)

        req_file = tmp_path / "receive_requests.json"
        assert req_file.exists(), "receive_requests.json 미생성"
        with open(req_file, encoding="utf-8") as f:
            reqs = json.load(f)
        assert oid in reqs, f"offer_id 미기록: {reqs}"
        assert win._receivable_widgets[oid]["requested"] is True
    finally:
        win.destroy()
