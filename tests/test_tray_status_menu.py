"""2026-09-28 UX 검토 B1 — TrayApp 이 상태 스냅샷을 아이콘/툴팁/메뉴로 반영하는지.

pystray 는 import 시점에 X11 접속을 시도하므로 헤드리스에선 skip(CI Xvfb 에서 실행).
실제 트레이 아이콘을 띄우지 않고 icon 객체만 가짜로 꽂아 update_icon 배선을 본다.
"""

import os
import sys

import pytest

_SKIP = None
if sys.platform == "linux" and not os.environ.get("DISPLAY"):
    _SKIP = "DISPLAY 없음 (헤드리스) — pystray 가 import 시점에 X11 접속을 시도함"

pytestmark = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")

if _SKIP is None:
    from ui import tray as tray_mod


class _FakeIcon:
    def __init__(self):
        self.icon = None
        self.title = ""
        self.menu_updates = 0

    def update_menu(self):
        self.menu_updates += 1


class _App:
    on_state_changed = None
    _lang = "en"

    def __init__(self):
        self.snap = {"mode": "server", "connected": True, "server_host": "", "port": 9999,
                     "peers": [], "startup_error": False, "client_error": "", "active": [],
                     "last_received": None}

    def status_snapshot(self):
        return dict(self.snap)


def _make():
    app = _App()
    tray = tray_mod.TrayApp(app)
    tray.icon = _FakeIcon()
    return app, tray


def test_update_icon_sets_tooltip_and_menu_only_on_change():
    app, tray = _make()
    tray.update_icon()
    assert tray.icon.title == "Infinite Clipboard — Server running — no devices connected"
    assert tray.icon.menu_updates == 1
    tray.update_icon()   # 상태 그대로 → 메뉴 재생성 안 함
    assert tray.icon.menu_updates == 1
    app.snap["peers"] = ["mac-studio"]
    tray.update_icon()
    assert tray.icon.menu_updates == 2
    assert tray._status_lines[1].strip() == "· mac-studio"


def test_busy_badge_image_differs_from_plain_connection_icon():
    app, tray = _make()
    app.snap["peers"] = ["pc"]
    tray.update_icon()
    plain = tray.icon.icon
    app.snap["active"] = [{"filename": "a", "direction": "receive"}]
    tray.update_icon()
    assert tray.icon.icon is not plain
    assert tray.icon.icon is tray_mod.create_icon_image("green", "busy")


def test_menu_items_are_translated_and_status_first():
    app, tray = _make()
    tray.update_icon()
    items = list(tray._menu_items())
    texts = [getattr(i, "text", None) for i in items]
    assert texts[0].startswith("● Server running")
    assert items[0].enabled is False
    assert "Clipboard History" in texts and "Quit" in texts


def test_update_menu_items_and_refresh_on_update_change():
    """2026-09-29 자동 업데이트: «업데이트 확인» 은 항상, «업데이트 설치 (vX)» 는 새 버전이 있을 때만.
    업데이트 상태가 바뀌면(상태 줄은 그대로여도) 메뉴를 다시 만든다."""
    app, tray = _make()
    app.snap["update"] = {"version": None, "phase": "idle", "confirm_pending": False,
                          "pending_receivables": 0}
    tray.update_icon()
    texts = [getattr(i, "text", None) for i in tray._menu_items()]
    assert "Check for Updates" in texts
    assert not any(isinstance(x, str) and x.startswith("Install Update") for x in texts)
    before = tray.icon.menu_updates

    app.snap["update"] = dict(app.snap["update"], version="3.0.99")
    tray.update_icon()
    assert tray.icon.menu_updates == before + 1
    items = list(tray._menu_items())
    install = [i for i in items if getattr(i, "text", "") == "Install Update (v3.0.99)"]
    assert len(install) == 1 and install[0].enabled

    app.snap["update"] = dict(app.snap["update"], phase="downloading")
    tray.update_icon()
    items = list(tray._menu_items())
    dl = [i for i in items if getattr(i, "text", "") == "Downloading Update…"]
    assert len(dl) == 1 and dl[0].enabled is False
