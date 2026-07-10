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
import time
import uuid

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


def _write_state(path, receivable, completed=None):
    state = {"active": {}, "completed": completed or [], "receivable": receivable}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)


def _make_receivable(name="photo.png", size=12345, kind="image"):
    return {
        "offer_id": str(uuid.uuid4()),
        "source_peer": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "name": name, "kind": kind, "total_size": size, "created_at": 1.0,
    }


def _make_window(gui_root, state_file):
    """공유 루트 위에 TransferWindow(Toplevel) 생성 — __init__ 이 _poll_state 1회 호출.

    language="ko" 로 고정 — CI 러너 로케일에 따라 자동감지되면 이 파일의
    한국어 문자열 assert(재시도/폴더 열기 등)가 로케일에 따라 깨진다.
    """
    from ui.transfer_window import TransferWindow
    win = TransferWindow(str(state_file), config=AppConfig(language="ko", auth_key="x" * 32))
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


def test_state_reader_thread_stops_on_destroy(gui_root, tmp_path):
    """M15: state 읽기 백그라운드 스레드가 destroy() 이후 계속 돌면 안 된다
    (창을 닫아도 파일을 계속 폴링하는 유령 스레드가 남으면 안 됨)."""
    state_file = tmp_path / "transfer_state.json"
    _write_state(state_file, [])
    win = _make_window(gui_root, state_file)
    assert win._reader_running is True
    win.destroy()

    deadline = time.time() + 2.0
    while time.time() < deadline and win._reader_running:
        time.sleep(0.05)
    assert win._reader_running is False, "M15 회귀 — destroy() 후에도 reader 스레드가 안 멈춤"


def test_poll_state_does_not_perform_file_io_directly(gui_root, tmp_path, monkeypatch):
    """M15: _poll_state 자체는 파일을 열지 않고 백그라운드가 채운
    self._latest_state 만 읽어야 한다 — 메인스레드(GUI) I/O 회귀 가드."""
    state_file = tmp_path / "transfer_state.json"
    _write_state(state_file, [])
    win = _make_window(gui_root, state_file)
    try:
        # 백그라운드 스레드는 잠시 멈추고, _read_state 호출 여부만 관찰
        win._reader_running = False
        time.sleep(0.6)  # 진행 중이던 마지막 sleep(0.5) 루프가 빠져나갈 시간

        calls = []
        original_read_state = win._read_state
        win._read_state = lambda: (calls.append(1) or original_read_state())

        win._poll_state()

        assert not calls, (
            f"M15 회귀 — _poll_state 가 메인스레드에서 직접 파일을 읽음 (호출 {len(calls)}회)"
        )
    finally:
        win.destroy()


def test_receivable_widget_becomes_retryable_on_last_failure(gui_root, tmp_path):
    """2026-07-04: retryable 실패(last_failure)가 entry 에 실리면 위젯이 "재시도"
    상태로 전환되고 requested 가 명시적으로 리셋되는지 (M6 회귀 방지 핵심 동작)."""
    state_file = tmp_path / "transfer_state.json"
    entry = _make_receivable("clip.png", 555, "image")
    oid = entry["offer_id"]
    _write_state(state_file, [entry])
    win = _make_window(gui_root, state_file)
    try:
        # 사용자가 받기 클릭 → 응답 대기 중 상태를 시뮬레이션.
        w = win._receivable_widgets[oid]
        w["requested"] = True
        w["btn"].configure(state="disabled", text="받는 중")

        # main.py 가 retryable 실패로 last_failure 를 채운 상태를 흉내.
        entry_failed = dict(entry)
        entry_failed["last_failure"] = {
            "reason": "offline", "message": "원본 PC 연결 끊김", "failed_at": 123.0,
        }
        with win._state_lock:
            win._latest_state = {"active": {}, "completed": [], "receivable": [entry_failed]}
        win._poll_state()
        win.update()

        assert w["requested"] is False, "M6 회귀 — last_failure 갱신 후 requested 가 리셋 안 됨"
        assert w["btn"].cget("text") == "재시도"
        assert w["btn"].cget("state") == "normal"
        assert w["reason_label"].cget("text") == "원본 PC 연결 끊김"
        assert w["reason_label"].winfo_ismapped()
    finally:
        win.destroy()


def _find_completed_row(container, filename):
    """완료 섹션(CTkScrollableFrame) 안에서 filename 라벨을 가진 행 프레임을 찾는다."""
    import customtkinter
    for frame in container.winfo_children():
        for child in frame.winfo_children():
            if isinstance(child, customtkinter.CTkLabel) and child.cget("text") == filename:
                return frame
    return None


def _has_open_folder_button(frame):
    import customtkinter
    return any(
        isinstance(child, customtkinter.CTkButton) and child.cget("text") == "폴더 열기"
        for child in frame.winfo_children()
    )


def test_completed_widget_shows_open_folder_button_only_for_receive_flow(gui_root, tmp_path):
    """2026-07-04: via_receive_button+path 가 있는 완료 항목만 '폴더 열기' 버튼이
    떠야 한다 — lazy-paste 완료 항목(그 필드 없음)엔 버튼이 없어야 한다."""
    state_file = tmp_path / "transfer_state.json"
    receive_entry = {
        "transfer_id": "t-receive", "filename": "photo.png", "total_size": 100,
        "direction": "receive", "completed_at": 1.0,
        "path": str(tmp_path / "downloads"), "via_receive_button": True,
    }
    lazy_entry = {
        "transfer_id": "t-lazy", "filename": "clip.png", "total_size": 50,
        "direction": "receive", "completed_at": 2.0,
    }
    _write_state(state_file, [], completed=[receive_entry, lazy_entry])
    win = _make_window(gui_root, state_file)
    try:
        receive_frame = _find_completed_row(win._completed_frame, "photo.png")
        lazy_frame = _find_completed_row(win._completed_frame, "clip.png")
        assert receive_frame is not None and lazy_frame is not None

        assert _has_open_folder_button(receive_frame), (
            "받기 완료 항목에 '폴더 열기' 버튼이 없음"
        )
        assert not _has_open_folder_button(lazy_frame), (
            "lazy-paste 완료 항목에 '폴더 열기' 버튼이 잘못 뜸"
        )
    finally:
        win.destroy()
