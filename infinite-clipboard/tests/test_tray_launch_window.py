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
