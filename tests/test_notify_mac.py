"""macOS 액션 버튼 알림 백엔드(core/notify_mac.py) 회귀 (2026-07-10).

macOS 전용 — 다른 OS 에서는 skip. `UNUserNotificationCenter`는 코드 서명된
앱 번들에게만 알림 권한을 준다(core/notify_mac.py 모듈 docstring 참조) —
CI(macos-14 러너, 서명 안 된 `python3` 프로세스로 실행)에서는 `is_supported()`
가 False 로 저하하는 게 정상이고, 이건 실패가 아니라 이 백엔드의 graceful
degradation 계약 자체를 검증하는 것이다. 액션 디스패치 로직(`_handle_action`)
은 OS 권한/서명과 무관한 순수 Python 로직이라 `is_supported()` 값과
상관없이 항상 결정적으로 검증한다.
"""

import platform

import pytest

pytestmark = pytest.mark.skipif(platform.system() != "Darwin", reason="macOS 전용 백엔드")


def _make_notifier():
    from core.notify_mac import MacActionableNotifier
    return MacActionableNotifier()


def test_construction_does_not_raise():
    """pyobjc 임포트/브리징 호출 자체가 깨지지 않는지 — 서명 여부와 무관하게
    생성자는 예외 없이 끝나야 한다(권한 거부/미서명은 is_supported()=False 로
    표현되지, raise 되지 않는다)."""
    notifier = _make_notifier()
    try:
        assert notifier.is_supported() in (True, False)
    finally:
        notifier.stop()


def test_notify_receivable_returns_false_when_unsupported():
    """CI 는 서명 안 된 python3 프로세스로 실행되므로 is_supported() 가 False 일
    가능성이 높다 — 그 경우 notify_receivable 도 예외 없이 False 를 반환해야
    한다(호출부가 plyer 폴백으로 우회하는 계약)."""
    notifier = _make_notifier()
    try:
        if notifier.is_supported():
            pytest.skip("이 환경에선 알림 권한이 승인됨 — 이 테스트의 전제(미승인)가 성립 안 함")
        calls = []
        ok = notifier.notify_receivable(
            "test-offer-abc", "파일 받기", "smoke.zip (1.2 MB)",
            "받기", "무시", lambda: calls.append("received"),
        )
        assert ok is False
        assert calls == []
    finally:
        notifier.stop()


def test_notify_receivable_succeeds_when_authorized():
    """실기기(또는 서명된 앱 번들 내부)에서 권한이 승인된 경우의 실제 경로."""
    notifier = _make_notifier()
    try:
        if not notifier.is_supported():
            pytest.skip("이 환경에선 알림 권한 미승인 — 실제 표시 경로는 서명된 실기기에서 확인 필요")
        calls = []
        ok = notifier.notify_receivable(
            "test-offer-authorized", "파일 받기", "smoke.zip (1.2 MB)",
            "받기", "무시", lambda: calls.append("received"),
        )
        assert ok is True
    finally:
        notifier.stop()


def test_handle_action_receive_fires_callback_regardless_of_authorization():
    """디스패치 로직(_handle_action)은 OS 권한/서명과 무관한 순수 Python 로직 —
    is_supported() 값과 상관없이 항상 결정적으로 검증 가능."""
    notifier = _make_notifier()
    calls = []
    notifier._pending["req-1"] = lambda: calls.append("received")
    notifier._handle_action("req-1", "receive")
    assert calls == ["received"]
    assert "req-1" not in notifier._pending


def test_handle_action_dismiss_does_not_fire_callback():
    notifier = _make_notifier()
    calls = []
    notifier._pending["req-2"] = lambda: calls.append("received")
    notifier._handle_action("req-2", "dismiss")
    assert calls == []
    assert "req-2" not in notifier._pending


def test_handle_action_unknown_request_id_is_ignored():
    notifier = _make_notifier()
    notifier._handle_action("no-such-request", "receive")  # no raise
