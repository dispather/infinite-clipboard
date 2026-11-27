"""Linux plain notifications over explicit-signature D-Bus (2026-11-28,
linux-plain-notify-dbus-signature).

The installed bundle's plyer -> dbus-python path intermittently failed with
«Unable to guess signature from an empty list» for the empty actions/hints of a
plain notification, and TrayApp.notify swallowed the error, so the user saw
nothing. TrayApp.notify on Linux now goes through
core.notify_linux.send_plain_notification (GVariant with the signature spelled
out) and falls back to plyer only when that returns False.

No test here sends a real D-Bus notification: `gi` is replaced by a fake module
and plyer is replaced per test (tests/conftest.py also stubs both by default).
"""

import os
import sys
import types

import pytest

from core import notify_linux
# Captured at import (collection) time, before the conftest autouse stub replaces
# the module attribute — the unit tests below call the real function.
from core.notify_linux import send_plain_notification as _real_send_plain


# ── fake gi ───────────────────────────────────────────────────────────

class _FakeVariant:
    def __init__(self, signature, value):
        self.signature = signature
        self.value = value


class _FakeConn:
    def __init__(self, calls, fail=None):
        self.calls = calls
        self.fail = fail

    def call_sync(self, bus_name, path, iface, method, params, reply_type, flags, timeout, cancellable):
        if self.fail is not None:
            raise self.fail
        self.calls.append({"bus_name": bus_name, "path": path, "iface": iface,
                           "method": method, "params": params, "reply_type": reply_type})
        return _FakeVariant("(u)", (7,))


def _install_fake_gi(monkeypatch, fail=None):
    calls = []
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_a, **_k: None
    repo = types.ModuleType("gi.repository")
    gio = types.SimpleNamespace(
        BusType=types.SimpleNamespace(SESSION="session"),
        DBusCallFlags=types.SimpleNamespace(NONE=0),
        bus_get_sync=lambda _bus, _cancel: _FakeConn(calls, fail),
    )
    glib = types.SimpleNamespace(
        Variant=_FakeVariant,
        VariantType=types.SimpleNamespace(new=lambda s: ("VariantType", s)),
    )
    repo.Gio = gio
    repo.GLib = glib
    gi.repository = repo
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)
    return calls


# ── core.notify_linux.send_plain_notification ─────────────────────────

def test_plain_notify_uses_explicit_signature_and_replaces_id_zero(monkeypatch):
    calls = _install_fake_gi(monkeypatch)

    assert _real_send_plain("제목", "본문") is True

    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "Notify"
    assert call["iface"] == "org.freedesktop.Notifications"
    assert call["params"].signature == "(susssasa{sv}i)"
    # replaces_id 0 — never the receive-notification slot; empty actions/hints are typed.
    assert call["params"].value == (
        "Infinite Clipboard", 0, "", "제목", "본문", [], {}, notify_linux.PLAIN_EXPIRE_MS,
    )
    assert call["reply_type"] == ("VariantType", "(u)")


def test_plain_notify_returns_false_when_daemon_call_fails(monkeypatch):
    _install_fake_gi(monkeypatch, fail=RuntimeError("ExcessNotificationGeneration"))
    assert _real_send_plain("t", "m") is False


def test_plain_notify_returns_false_without_gi(monkeypatch):
    monkeypatch.setitem(sys.modules, "gi", None)  # import gi -> ImportError
    assert _real_send_plain("t", "m") is False


def test_plain_notify_does_not_touch_actionable_slots(monkeypatch):
    """The actionable notifier's per-sender slots stay as they were."""
    calls = _install_fake_gi(monkeypatch)
    notifier = notify_linux.LinuxActionableNotifier.__new__(notify_linux.LinuxActionableNotifier)
    import threading
    notifier._lock = threading.Lock()
    notifier._slots = {"peer-a": 42}
    notifier._pending = {42: lambda: None}
    notifier._by_offer = {"offer-1": 42}

    assert _real_send_plain("t", "m") is True
    assert calls[0]["params"].value[1] == 0
    assert notifier._slots == {"peer-a": 42}
    assert notifier._by_offer == {"offer-1": 42}


# ── ui.tray.TrayApp.notify ────────────────────────────────────────────

_TRAY_SKIP = None
if sys.platform == "linux" and not os.environ.get("DISPLAY"):
    _TRAY_SKIP = "DISPLAY 없음 (헤드리스) — pystray 가 import 시점에 X11 접속을 시도함"

tray_only = pytest.mark.skipif(_TRAY_SKIP is not None, reason=_TRAY_SKIP or "")


def _tray_mod():
    from ui import tray as tray_mod
    return tray_mod


def _record_plyer(monkeypatch):
    plyer = pytest.importorskip("plyer")
    sent = []
    monkeypatch.setattr(plyer.notification, "notify", lambda **kw: sent.append(kw), raising=False)
    return sent


def _record_plain(monkeypatch, result):
    sent = []

    def fake(title, message, *a, **k):
        sent.append((title, message))
        return result

    monkeypatch.setattr(notify_linux, "send_plain_notification", fake)
    return sent


@tray_only
def test_tray_notify_linux_uses_dbus_and_skips_plyer(monkeypatch):
    tray_mod = _tray_mod()
    monkeypatch.setattr(tray_mod.platform, "system", lambda: "Linux")
    plain = _record_plain(monkeypatch, True)
    plyer_sent = _record_plyer(monkeypatch)

    tray_mod.TrayApp.notify(None, "전체 로그 요청", "완료")

    assert plain == [("전체 로그 요청", "완료")]
    assert plyer_sent == []


@tray_only
def test_tray_notify_linux_falls_back_to_plyer_on_dbus_failure(monkeypatch):
    tray_mod = _tray_mod()
    monkeypatch.setattr(tray_mod.platform, "system", lambda: "Linux")
    plain = _record_plain(monkeypatch, False)
    plyer_sent = _record_plyer(monkeypatch)

    tray_mod.TrayApp.notify(None, "t", "m")

    assert plain == [("t", "m")]
    assert len(plyer_sent) == 1
    assert plyer_sent[0]["title"] == "t" and plyer_sent[0]["message"] == "m"


@tray_only
def test_tray_notify_linux_falls_back_when_dbus_path_raises(monkeypatch):
    tray_mod = _tray_mod()
    monkeypatch.setattr(tray_mod.platform, "system", lambda: "Linux")

    def boom(*_a, **_k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(notify_linux, "send_plain_notification", boom)
    plyer_sent = _record_plyer(monkeypatch)

    tray_mod.TrayApp.notify(None, "t", "m")
    assert len(plyer_sent) == 1


@tray_only
@pytest.mark.parametrize("system", ["Windows", "Darwin"])
def test_tray_notify_non_linux_keeps_plyer_path(monkeypatch, system):
    tray_mod = _tray_mod()
    monkeypatch.setattr(tray_mod.platform, "system", lambda: system)
    plain = _record_plain(monkeypatch, True)
    plyer_sent = _record_plyer(monkeypatch)

    tray_mod.TrayApp.notify(None, "t", "m")

    assert plain == []
    assert len(plyer_sent) == 1
