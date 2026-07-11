"""M11 회귀 — tray.py 가 UI 창을 별도 프로세스로 띄울 때 start_new_session
없이 실행되던 문제.

2026-07-03 감사 M11 (함정 #8 부분 회귀): `_launch_window`의 `subprocess.Popen(cmd)`
가 `start_new_session=True` 없이 호출됐다. 이러면 새 창 프로세스가 tray 앱과
같은 프로세스 그룹/세션에 묶여, 개발 모드에서 터미널의 Ctrl+C(SIGINT) 가
그룹 전체에 전달되면 "독립 프로세스"여야 할 창도 함께 죽는다. main.py 의
자기 재시작(함정 #8) 은 이미 이 플래그를 쓰고 있었는데 tray.py 만 빠져 있었다.
"""

import os
import subprocess
import sys
import time

import pytest

_SKIP = None
if sys.platform == "linux" and not os.environ.get("DISPLAY"):
    _SKIP = "DISPLAY 없음 (헤드리스) — pystray 가 import 시점에 X11 접속을 시도함"

pytestmark = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")

if _SKIP is None:
    from ui import tray as tray_mod


class _DummyApp:
    on_state_changed = None


def _make_tray():
    return tray_mod.TrayApp(_DummyApp())


def test_launch_window_uses_start_new_session(monkeypatch):
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    tray = _make_tray()
    tray._launch_window("settings")

    deadline = time.time() + 2.0
    while time.time() < deadline and not calls:
        time.sleep(0.02)

    assert len(calls) == 1, "subprocess.Popen 이 호출되지 않음"
    _, kwargs = calls[0]
    assert kwargs.get("start_new_session") is True, (
        f"M11 회귀 — start_new_session=True 가 전달되지 않음: {kwargs}"
    )


def test_launch_window_rejects_unknown_window_type(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))

    tray = _make_tray()
    tray._launch_window("not-a-real-window-type")

    time.sleep(0.1)
    assert not calls, "알 수 없는 window_type 인데 subprocess 가 호출됨"


class _FakeRunningProcess:
    """subprocess.Popen 대역 — poll() 이 항상 None(아직 실행 중)을 반환."""

    def poll(self):
        return None


def test_launch_window_skips_duplicate_spawn_while_already_running(monkeypatch):
    """2026-07-12 mac-studio 오딧 #1 회귀 — 대용량 수신 자동 팝업과 트레이
    메뉴 클릭이 겹쳐도 같은 window_type 은 한 번만 spawn 돼야 한다."""
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _FakeRunningProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    tray = _make_tray()
    tray._launch_window("transfers")

    deadline = time.time() + 2.0
    while time.time() < deadline and not calls:
        time.sleep(0.02)
    assert len(calls) == 1, "첫 spawn 이 발생하지 않음"

    # 창이 아직 떠있는(poll() is None) 상태에서 재요청 — 재실행 생략돼야 함
    tray._launch_window("transfers")
    time.sleep(0.2)
    assert len(calls) == 1, "이미 떠있는 window_type 인데 subprocess 가 다시 호출됨"


def test_launch_window_concurrent_calls_spawn_only_once(monkeypatch):
    """자동 팝업 스레드와 트레이 메뉴 클릭 스레드가 거의 동시에 같은
    window_type 을 요청하는 실제 repro 시나리오 — race 없이 1회만 spawn."""
    import threading as _threading

    calls = []
    call_lock = _threading.Lock()

    def fake_popen(cmd, **kwargs):
        with call_lock:
            calls.append((cmd, kwargs))
        return _FakeRunningProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    tray = _make_tray()
    threads = [
        _threading.Thread(target=lambda: tray._launch_window("transfers"))
        for _ in range(5)
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=2.0)

    deadline = time.time() + 2.0
    while time.time() < deadline and not calls:
        time.sleep(0.02)

    assert len(calls) == 1, f"동시 호출 5회인데 spawn 이 {len(calls)}회 발생함"
