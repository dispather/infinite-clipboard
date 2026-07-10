"""H3 회귀 — 설정창이 Tailscale CLI 조회(최대 10초)로 블로킹되지 않는지
Xvfb/실 디스플레이 스모크 테스트.

헤드리스 로컬에선 skip, CI(test.yml Xvfb)에서 실행. 2026-07-03 감사 H3:
`SettingsWindow.__init__` 이 `_detect_tailscale_ip()` 를 동기 호출해 창이 뜨기도
전에 최대 10초(macOS 후보 2개 × 5초 timeout) 블로킹됐다. 수정: 백그라운드
스레드 + after() 폴링으로 비동기화.
"""

import os
import sys
import time

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
    """모듈 전체가 공유하는 단일 CTk 루트 (다중 루트 pyimage 캐시 깨짐 회피)."""
    import customtkinter
    customtkinter.set_appearance_mode("System")
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


def _slow_ip(delay, ip):
    def _fn():
        time.sleep(delay)
        return ip
    return _fn


def test_init_does_not_block_on_slow_tailscale_detection(gui_root, monkeypatch):
    """H3: __init__ 이 (모의) 느린 Tailscale 조회를 기다리지 않고 즉시 반환해야 한다."""
    import ui.settings_window as sw
    from config import AppConfig

    monkeypatch.setattr(sw, "_detect_tailscale_ip", _slow_ip(delay=1.0, ip="100.64.1.2"))

    start = time.monotonic()
    win = sw.SettingsWindow(AppConfig(auth_key="x" * 32))
    elapsed = time.monotonic() - start
    try:
        win.update()
        assert elapsed < 0.5, f"H3 회귀 — __init__ 이 블로킹됨 ({elapsed:.2f}s)"
        # 백그라운드 조회가 아직 안 끝났을 시점이므로 "확인 중" 상태여야 함
        assert win._ts_badge.cget("text") == "Tailscale 확인 중…"
    finally:
        win.destroy()


def test_badge_and_button_update_after_detection_completes(gui_root, monkeypatch):
    """백그라운드 조회가 끝나면 폴링이 배지 + 자동 버튼 색을 갱신해야 한다."""
    import ui.settings_window as sw
    from config import AppConfig

    monkeypatch.setattr(sw, "_detect_tailscale_ip", _slow_ip(delay=0.2, ip="100.64.9.9"))

    win = sw.SettingsWindow(AppConfig(auth_key="x" * 32))
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            win.update()
            if "100.64.9.9" in win._ts_badge.cget("text"):
                break
            time.sleep(0.05)

        assert "100.64.9.9" in win._ts_badge.cget("text"), (
            f"배지가 갱신되지 않음: {win._ts_badge.cget('text')!r}"
        )
        assert win._tailscale_ip == "100.64.9.9"
    finally:
        win.destroy()


def test_auto_detect_button_click_is_non_blocking(gui_root, monkeypatch):
    """H3: "자동" 버튼 클릭도 비동기 — 클릭 직후 즉시 반환하고, 완료 후 host
    입력창이 채워져야 한다."""
    import ui.settings_window as sw
    from config import AppConfig

    # 초기 조회는 빠르게 끝내 놓고, 버튼 클릭 시의 조회만 느리게 시뮬레이션
    monkeypatch.setattr(sw, "_detect_tailscale_ip", _slow_ip(delay=0.0, ip=""))
    win = sw.SettingsWindow(AppConfig(auth_key="x" * 32, mode="client"))
    try:
        win.update()
        monkeypatch.setattr(sw, "_detect_tailscale_ip", _slow_ip(delay=0.5, ip="100.64.5.5"))

        start = time.monotonic()
        win._auto_detect_ip()
        elapsed = time.monotonic() - start
        win.update()
        assert elapsed < 0.3, f"H3 회귀 — 자동 버튼 클릭이 블로킹됨 ({elapsed:.2f}s)"

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            win.update()
            if win._host_entry.get() == "100.64.5.5":
                break
            time.sleep(0.05)

        assert win._host_entry.get() == "100.64.5.5", (
            f"host 입력창이 갱신되지 않음: {win._host_entry.get()!r}"
        )
    finally:
        win.destroy()
