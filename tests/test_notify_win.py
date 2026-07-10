"""Windows 액션 버튼 알림 백엔드(core/notify_win.py) 회귀 (2026-07-10).

Windows 전용 — 다른 OS 에서는 skip. CI(windows-latest)와 실기기 모두에서
`Windows-Toasts` 임포트/구성 자체가 깨지지 않는지, 실제 토스트 표시 API
호출이 예외 없이 성공하는지, 그리고 "받기"/"무시" 액션 디스패치 로직이
올바른지 검증한다. 실제 사람이 토스트 버튼을 눌러야만 발화하는
`on_activated` 콜백은 여기선 `show_toast`를 가로채 캡처한 실제 클로저를
직접 호출해 디스패치만 결정적으로 검증한다(tests/test_notify_linux.py 와
동일 접근 — 사람이 클릭하는 부분만 실기기가 필요, 그 이후 로직은 CI 로 확정).
"""

import platform

import pytest

pytestmark = pytest.mark.skipif(platform.system() != "Windows", reason="Windows 전용 백엔드")


def _make_notifier():
    from core.notify_win import WindowsActionableNotifier
    return WindowsActionableNotifier()


def _notify_and_capture_toast(notifier, monkeypatch, offer_id, on_receive):
    """실제 notify_receivable 이 만드는 Toast 객체(진짜 on_activated 클로저 포함)를
    show_toast 호출 직전에 가로채 반환 — 실제 화면 표시 없이 디스패치 로직만 검증."""
    captured = {}
    monkeypatch.setattr(notifier._toaster, "show_toast", lambda toast: captured.update(toast=toast))
    ok = notifier.notify_receivable(offer_id, "t", "m", "받기", "무시", on_receive)
    assert ok is True
    return captured["toast"]


def test_is_supported_true_with_windows_toasts_installed():
    notifier = _make_notifier()
    assert notifier.is_supported() is True


def test_notify_receivable_shows_real_toast():
    """실제 InteractableWindowsToaster.show_toast 를 그대로 호출 — CI windows-latest
    러너에서 예외 없이 성공하는지 확인(화면에 실제로 보이는지는 사람이 봐야
    확정할 수 있지만, API 호출·의존성 배선이 깨지지 않았다는 강한 신호)."""
    notifier = _make_notifier()
    try:
        calls = []
        ok = notifier.notify_receivable(
            "test-offer-abc", "파일 받기", "smoke.zip (1.2 MB)",
            "받기", "무시", lambda: calls.append("received"),
        )
        assert ok is True
    finally:
        notifier.stop()


def test_on_activated_receive_action_fires_callback(monkeypatch):
    notifier = _make_notifier()
    calls = []
    toast = _notify_and_capture_toast(
        notifier, monkeypatch, "test-offer-receive", lambda: calls.append("received"),
    )

    class _ReceiveArgs:
        arguments = "receive"

    toast.on_activated(_ReceiveArgs())
    assert calls == ["received"]


def test_on_activated_dismiss_action_does_not_fire_callback(monkeypatch):
    notifier = _make_notifier()
    calls = []
    toast = _notify_and_capture_toast(
        notifier, monkeypatch, "test-offer-dismiss", lambda: calls.append("received"),
    )

    class _DismissArgs:
        arguments = "dismiss"

    toast.on_activated(_DismissArgs())
    assert calls == []


def test_on_activated_handles_none_args_without_raising(monkeypatch):
    """토스트 본문(버튼이 아닌 곳) 클릭 시 args 가 None 일 수 있다는 방어 로직 확인."""
    notifier = _make_notifier()
    calls = []
    toast = _notify_and_capture_toast(
        notifier, monkeypatch, "test-offer-none-args", lambda: calls.append("received"),
    )
    toast.on_activated(None)  # no raise
    assert calls == []
