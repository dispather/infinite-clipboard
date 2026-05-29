"""v3.0 S2b: lazy clipboard 파운데이션 회귀 테스트 (Layer-1 — 헤드리스 검증 가능).

OS/세션 감지(detect_backend), 추상 인터페이스 계약, 팩토리 graceful None,
FetchedContent 계약을 검증한다. 실제 클립보드 round-trip 은 OS 백엔드 슬롯-인
후 CI(Xvfb)/실기기에서 검증 (이 환경은 헤드리스).
"""

import pytest

from core import lazy_clipboard as L
from core.lazy_clipboard import (
    LazyClipboardProvider, FetchedContent, detect_backend, get_lazy_provider,
    BACKEND_X11, BACKEND_WAYLAND, BACKEND_WINDOWS, BACKEND_MACOS, BACKEND_NONE,
    KIND_FILE, KIND_IMAGE,
)


# ─── detect_backend env 매트릭스 ─────────────────────────────────────


def _clear_linux_env(monkeypatch):
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)


def test_detect_windows(monkeypatch):
    monkeypatch.setattr(L.platform, "system", lambda: "Windows")
    assert detect_backend() == BACKEND_WINDOWS


def test_detect_macos(monkeypatch):
    monkeypatch.setattr(L.platform, "system", lambda: "Darwin")
    assert detect_backend() == BACKEND_MACOS


def test_detect_linux_wayland_by_session(monkeypatch):
    monkeypatch.setattr(L.platform, "system", lambda: "Linux")
    _clear_linux_env(monkeypatch)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert detect_backend() == BACKEND_WAYLAND


def test_detect_linux_wayland_by_display(monkeypatch):
    """XDG 미설정이어도 WAYLAND_DISPLAY 있으면 wayland."""
    monkeypatch.setattr(L.platform, "system", lambda: "Linux")
    _clear_linux_env(monkeypatch)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert detect_backend() == BACKEND_WAYLAND


def test_detect_linux_x11_by_session(monkeypatch):
    monkeypatch.setattr(L.platform, "system", lambda: "Linux")
    _clear_linux_env(monkeypatch)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert detect_backend() == BACKEND_X11


def test_detect_linux_x11_by_display(monkeypatch):
    monkeypatch.setattr(L.platform, "system", lambda: "Linux")
    _clear_linux_env(monkeypatch)
    monkeypatch.setenv("DISPLAY", ":0")
    assert detect_backend() == BACKEND_X11


def test_detect_linux_wayland_priority_over_x11(monkeypatch):
    """Wayland + DISPLAY(Xwayland) 동시 → wayland 우선."""
    monkeypatch.setattr(L.platform, "system", lambda: "Linux")
    _clear_linux_env(monkeypatch)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    assert detect_backend() == BACKEND_WAYLAND


def test_detect_linux_headless_none(monkeypatch):
    monkeypatch.setattr(L.platform, "system", lambda: "Linux")
    _clear_linux_env(monkeypatch)
    assert detect_backend() == BACKEND_NONE


def test_detect_unknown_os_none(monkeypatch):
    monkeypatch.setattr(L.platform, "system", lambda: "Plan9")
    assert detect_backend() == BACKEND_NONE


# ─── 추상 인터페이스 계약 ────────────────────────────────────────────


def test_provider_is_abstract():
    """ABC — 직접 인스턴스화 거부."""
    with pytest.raises(TypeError):
        LazyClipboardProvider()


def test_provider_subclass_must_implement_all():
    """추상 메서드 일부만 구현하면 인스턴스화 불가."""
    class Partial(LazyClipboardProvider):
        def is_supported(self, kind):
            return True
        # register_offer/clear/stop 미구현
    with pytest.raises(TypeError):
        Partial()


def test_provider_full_subclass_ok():
    """전 메서드 구현하면 인스턴스화 가능 + 계약대로 동작."""
    class Dummy(LazyClipboardProvider):
        def __init__(self):
            self.cleared = False
            self.stopped = False
        def is_supported(self, kind):
            return kind == KIND_IMAGE
        def register_offer(self, offer, fetch_callback):
            return True
        def clear(self):
            self.cleared = True
        def stop(self):
            self.stopped = True

    d = Dummy()
    assert d.is_supported(KIND_IMAGE) is True
    assert d.is_supported(KIND_FILE) is False
    assert d.register_offer({"offer_id": "x"}, lambda oid: FetchedContent(KIND_IMAGE)) is True
    d.clear(); d.stop()
    assert d.cleared and d.stopped


# ─── 팩토리 graceful ─────────────────────────────────────────────────


def test_factory_headless_returns_none(monkeypatch):
    """헤드리스 세션 → None (main 이 fallback)."""
    monkeypatch.setattr(L.platform, "system", lambda: "Linux")
    _clear_linux_env(monkeypatch)
    assert get_lazy_provider() is None


def test_factory_never_raises(monkeypatch):
    """어떤 OS/세션이어도 예외 없이 provider 또는 None 반환 (크래시 금지)."""
    for sysname, env in [
        ("Windows", {}),
        ("Darwin", {}),
        ("Linux", {"XDG_SESSION_TYPE": "wayland"}),
        ("Linux", {"DISPLAY": ":0"}),
        ("Linux", {}),
    ]:
        monkeypatch.setattr(L.platform, "system", lambda s=sysname: s)
        _clear_linux_env(monkeypatch)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        result = get_lazy_provider()
        assert result is None or isinstance(result, LazyClipboardProvider)


# ─── FetchedContent 계약 ─────────────────────────────────────────────


def test_fetched_content_image():
    fc = FetchedContent(kind=KIND_IMAGE, data=b"\x89PNG...")
    assert fc.kind == KIND_IMAGE
    assert fc.data == b"\x89PNG..."
    assert fc.paths == []


def test_fetched_content_file():
    fc = FetchedContent(kind=KIND_FILE, paths=["/tmp/staged/a.txt", "/tmp/staged/b.txt"])
    assert fc.kind == KIND_FILE
    assert fc.data == b""
    assert fc.paths == ["/tmp/staged/a.txt", "/tmp/staged/b.txt"]
