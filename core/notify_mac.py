"""
core/notify_mac.py — macOS 액션 버튼 알림 백엔드 (`UNUserNotificationCenter`).

⚠️ 알려진 제약(실기기 검증 필요, 이 리포 개발 환경엔 macOS 가 없어 코드
리뷰로만 확정 불가): `UNUserNotificationCenter` 는 **코드 서명된 앱 번들**
에게만 알림 권한을 준다. 이 프로젝트는 현재 macOS 빌드를 무서명으로
배포한다(`build/build_mac.sh`) — `.app` 빌드 직후 애드혹 서명
(`codesign --force --deep --sign -`) 단계를 추가해야 이 백엔드가 실제로
동작한다(유료 Apple Developer 계정 불필요). 서명이 없거나 권한이 거부되면
`is_supported()` 가 False 를 반환해 기존 plyer 기반 `_notify()` 로 안전하게
저하한다 — 크래시 없음.

권한 요청(`requestAuthorizationWithOptions:completionHandler:`)은 비동기라
결과를 짧게 폴링해서 기다린다(최대 `_AUTH_WAIT_TIMEOUT`=5초). 이 생성자
자체가 최대 5초 블로킹할 수 있다는 뜻이므로, **호출자(main.py)가 반드시
별도 스레드에서 생성해야 한다** — `_ensure_actionable_notifier`가 네트워크
메시지 처리 스레드에서 직접 이 생성자를 기다리면 같은 피어의 후속 메시지
처리가 그만큼 지연된다(2026-07-10 리뷰에서 실제로 발견된 회귀 — 이 파일이
아니라 main.py 쪽에서 백그라운드 스레드로 고쳤다).

`UNNotificationCategory`의 액션 제목은 카테고리 등록 시점에 고정된다(알림
인스턴스마다 다르게 줄 수 없음) — 그래서 accept_label/dismiss_label 이
바뀌면(언어 전환 등) `notify_receivable` 이 카테고리를 다시 등록해 항상
최신 라벨을 반영한다(등록 자체는 저비용, 매 호출 재등록 안 함 — 마지막
라벨과 같으면 skip).
"""

import logging
import threading
import time

from core.actionable_notify import ActionableNotifier, OnReceive

logger = logging.getLogger(__name__)

_CATEGORY_ID = "IC_RECEIVABLE"
_ACTION_RECEIVE = "receive"
_ACTION_DISMISS = "dismiss"
_AUTH_WAIT_TIMEOUT = 5.0


class MacActionableNotifier(ActionableNotifier):
    def __init__(self):
        self._center = None
        self._delegate = None
        self._authorized = False
        self._lock = threading.Lock()
        self._pending: dict = {}  # request identifier(str) -> on_receive
        self._category_labels = None  # (accept_label, dismiss_label) 마지막 등록값
        try:
            self._init_center()
        except Exception as e:
            logger.debug(f"macOS 알림 센터 사용 불가 — plyer 폴백: {e}")
            self._center = None

    def _init_center(self) -> None:
        from UserNotifications import UNUserNotificationCenter, UNAuthorizationOptionAlert

        self._center = UNUserNotificationCenter.currentNotificationCenter()
        if self._center is None:
            # 코드 서명/번들 아이덴티티가 없으면 여기서 이미 nil 인 경우가 있다.
            return

        self._delegate = _NotificationDelegate.alloc().initWithNotifier_(self)
        self._center.setDelegate_(self._delegate)

        granted_box = {"done": False, "granted": False}

        def _completion(granted, error):
            granted_box["granted"] = bool(granted)
            granted_box["done"] = True

        self._center.requestAuthorizationWithOptions_completionHandler_(
            UNAuthorizationOptionAlert, _completion,
        )
        deadline = time.time() + _AUTH_WAIT_TIMEOUT
        while not granted_box["done"] and time.time() < deadline:
            time.sleep(0.05)
        self._authorized = granted_box["granted"]
        if not self._authorized:
            logger.info("macOS 알림 권한 미승인(무서명 빌드 가능성) — plyer 폴백")

    def is_supported(self) -> bool:
        return self._center is not None and self._authorized

    def _ensure_category(self, accept_label: str, dismiss_label: str) -> None:
        if self._category_labels == (accept_label, dismiss_label):
            return
        from UserNotifications import (
            UNNotificationAction, UNNotificationActionOptionForeground,
            UNNotificationCategory, UNNotificationCategoryOptionNone,
        )
        accept = UNNotificationAction.actionWithIdentifier_title_options_(
            _ACTION_RECEIVE, accept_label, UNNotificationActionOptionForeground,
        )
        dismiss = UNNotificationAction.actionWithIdentifier_title_options_(
            _ACTION_DISMISS, dismiss_label, 0,
        )
        category = UNNotificationCategory.categoryWithIdentifier_actions_intentIdentifiers_options_(
            _CATEGORY_ID, [accept, dismiss], [], UNNotificationCategoryOptionNone,
        )
        self._center.setNotificationCategories_({category})
        self._category_labels = (accept_label, dismiss_label)

    def notify_receivable(
        self, offer_id, title, message, accept_label, dismiss_label, on_receive: OnReceive,
    ) -> bool:
        if not self.is_supported():
            return False
        request_id = f"ic-receivable-{offer_id}"
        try:
            from UserNotifications import (
                UNMutableNotificationContent, UNNotificationRequest,
            )
            self._ensure_category(accept_label, dismiss_label)

            content = UNMutableNotificationContent.alloc().init()
            content.setTitle_(title)
            content.setBody_(message)
            content.setCategoryIdentifier_(_CATEGORY_ID)

            request = UNNotificationRequest.requestWithIdentifier_content_trigger_(
                request_id, content, None,
            )
            with self._lock:
                self._pending[request_id] = on_receive
            self._center.addNotificationRequest_withCompletionHandler_(request, None)
        except Exception as e:
            logger.warning(f"macOS 알림 실패 — plyer 폴백: {e}")
            with self._lock:
                self._pending.pop(request_id, None)
            return False
        return True

    def _handle_action(self, request_id: str, action_id: str) -> None:
        with self._lock:
            on_receive = self._pending.pop(request_id, None)
        if on_receive is not None and action_id == _ACTION_RECEIVE:
            on_receive()

    def stop(self) -> None:
        if self._center is not None:
            try:
                self._center.removeAllDeliveredNotifications()
            except Exception:
                pass
        with self._lock:
            self._pending.clear()


try:
    from Foundation import NSObject

    class _NotificationDelegate(NSObject):
        """UNUserNotificationCenterDelegate — 액션 클릭을 MacActionableNotifier
        로 위임. 이 콜백은 시스템이 별도 스레드/큐에서 호출하므로(메인 스레드
        보장 없음), main.py 의 on_receive 스레드 위임 계약에 그대로 의존한다."""

        def initWithNotifier_(self, notifier):
            self = super().init()
            if self is None:
                return None
            self._notifier = notifier
            return self

        def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
            self, _center, response, completion_handler,
        ):
            try:
                request_id = response.notification().request().identifier()
                action_id = response.actionIdentifier()
                self._notifier._handle_action(request_id, action_id)
            finally:
                completion_handler()
except Exception:
    # pyobjc-framework-Cocoa 부재 등 — is_supported() 가 False 이므로 이 정의
    # 자체는 실제로 쓰이지 않는다(위 _init_center 가 먼저 ImportError 로 실패).
    pass
