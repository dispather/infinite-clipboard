"""v3.0 S4: transfer_window 받기 섹션 UI smoke 테스트 (Xvfb/실 디스플레이).

헤드리스 로컬에선 skip, CI(test.yml Xvfb)에서 실행. 시각적 정확성이 아니라
**구성/렌더가 예외 없이 되는가 + 받기 위젯/섹션 토글 로직** 을 검증한다 (헤드리스라
시각 확인 불가한 blind-UI 의 크래시·로직 버그 차단). cancel 버튼 패턴 미러라 안전망.
"""

import json
import os
import sys
import uuid

import pytest

_SKIP = None
if sys.platform != "linux" and sys.platform != "darwin" and sys.platform != "win32":
    _SKIP = "지원 OS 아님"
elif sys.platform == "linux" and not os.environ.get("DISPLAY"):
    _SKIP = "DISPLAY 없음 (헤드리스) — CI Xvfb/실 세션에서만"
else:
    try:
        import customtkinter  # noqa: F401
    except Exception:
        _SKIP = "customtkinter 미설치"

pytestmark = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")


def _gui():
    """CTk 루트 생성 — 디스플레이/Tk 문제 시 skip (graceful)."""
    import customtkinter
    try:
        root = customtkinter.CTk()
        root.withdraw()
        return root
    except Exception as e:  # Tk/디스플레이 초기화 실패
        pytest.skip(f"GUI 초기화 불가: {e}")


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


def test_receivable_renders_and_section_visible(tmp_path):
    """받을 항목 있는 상태 → __init__ 의 _poll_state 가 받기 위젯 생성 + 섹션 표시."""
    from ui.transfer_window import TransferWindow

    state_file = tmp_path / "transfer_state.json"
    _write_state(state_file, [_make_receivable("doc.txt", 999, "file")])

    root = _gui()
    win = None
    try:
        win = TransferWindow(str(state_file))  # __init__ 가 _poll_state 1회 호출
        win.update()
        assert len(win._receivable_widgets) == 1, "받기 위젯이 생성되지 않음"
        assert win._recv_visible is True, "받기 섹션이 표시되지 않음"
    finally:
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        root.destroy()


def test_no_receivable_section_hidden(tmp_path):
    """받을 항목 없음 → 받기 섹션 숨김 (happy-path 에서 공간 차지 안 함)."""
    from ui.transfer_window import TransferWindow

    state_file = tmp_path / "transfer_state.json"
    _write_state(state_file, [])

    root = _gui()
    win = None
    try:
        win = TransferWindow(str(state_file))
        win.update()
        assert len(win._receivable_widgets) == 0
        assert win._recv_visible is False, "받을 항목 없는데 받기 섹션이 보임"
    finally:
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        root.destroy()


def test_receive_click_writes_ipc(tmp_path, monkeypatch):
    """받기 버튼 클릭 → receive_requests.json 에 offer_id 기록 (main 폴링 대상)."""
    import config as config_mod
    from ui.transfer_window import TransferWindow

    # _get_config_dir 를 tmp 로 격리 (실 설정 디렉토리 오염 방지)
    monkeypatch.setattr(config_mod, "_get_config_dir", lambda: tmp_path)

    state_file = tmp_path / "transfer_state.json"
    entry = _make_receivable("clip.png", 555, "image")
    _write_state(state_file, [entry])

    root = _gui()
    win = None
    try:
        win = TransferWindow(str(state_file))
        win.update()
        oid = entry["offer_id"]
        assert oid in win._receivable_widgets
        win._on_receive_click(oid)

        req_file = tmp_path / "receive_requests.json"
        assert req_file.exists(), "receive_requests.json 미생성"
        with open(req_file, encoding="utf-8") as f:
            reqs = json.load(f)
        assert oid in reqs, f"offer_id 미기록: {reqs}"
        # UI 즉시 피드백 (버튼 비활성)
        assert win._receivable_widgets[oid]["requested"] is True
    finally:
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        root.destroy()
