"""
core/actionable_notify.py — 받기(수신) 알림에 네이티브 "받기"/"무시" 액션 버튼을
붙이는 크로스 OS 인터페이스 + 팩토리 (2026-07-10 제보 UX 개선).

기존 `_notify()`(plyer 기반, main.py)는 "전송 창에서 [받기]" 라고만 알리고
실제 액션이 없다 — 사용자가 알림을 놓치거나 전송 창을 따로 열어야 한다.
이 모듈은 OS 알림 자체에 클릭 가능한 액션 버튼을 붙여, 클릭 즉시
`on_receive` 콜백(main.py 가 `_receive_offer` 를 별도 스레드로 기동)을 트리거한다.

`core/lazy_clipboard.py` 와는 다른 문제를 다룬다 — lazy 는 "클립보드 선택
소유권"(디스플레이 서버 수준)이라 X11/Wayland 가 갈리지만, 알림 전달은
"데스크톱 세션 수준"(Linux 는 D-Bus 세션 버스 하나) 이라 Linux 백엔드는
단일 파일(core/notify_linux.py)이고 X11/Wayland 구분이 없다.

── 규칙 (infinite-clipboard/CLAUDE.md) ──────────────────────────────────
- 규칙 #5: core/ 는 UI 무관 — tkinter/pystray/customtkinter import 금지.
- 규칙 #14: 종료 race silent — 백엔드 stop() 은 의식적 종료를 silent 처리.

── 스레딩 계약 (중요) ───────────────────────────────────────────────────
lazy_clipboard 의 provide 콜백과 달리, 알림 액션 클릭은 **OS 에 데이터를
동기로 돌려줄 필요가 없다** — 그냥 "받기 눌렸다"는 신호일 뿐이다. 따라서
`on_receive` 는 반드시 그 자리에서 새 스레드를 띄워 처리하고 즉시 반환해야
한다(호출부인 main.py 가 이미 그렇게 감싼다) — 어떤 백엔드는 이 콜백을
자신이 의존하는 이벤트 루프 스레드에서 직접 호출하므로(예: Linux 는
`main._monitor_clipboard` 스레드), 블로킹하면 그 스레드가 담당하는 다른
작업(클립보드 감지 등)이 멎는다.
"""

import platform
from abc import ABC, abstractmethod
from typing import Callable, Optional

# on_receive 는 인자 없이 호출된다 — main.py 가 이미 offer_id 를 클로저로 감싸
# 전달하므로 백엔드는 "그냥 눌렸다"만 알면 된다.
OnReceive = Callable[[], None]


class ActionableNotifier(ABC):
    """OS별 액션 버튼 알림 백엔드의 공통 인터페이스.

    수명주기: `notify_receivable()` 로 알림 표시(성공 시 True) → 사용자가
    "받기" 클릭 시 `on_receive()` 발화 → `stop()` 으로 백엔드 완전 종료.
    """

    @abstractmethod
    def is_supported(self) -> bool:
        """이 백엔드가 실제로 액션 버튼 알림을 띄울 수 있는지.

        False 면 main 이 등록하지 않고 기존 plyer 기반 `_notify()` 로 우회한다.
        """
        raise NotImplementedError

    @abstractmethod
    def notify_receivable(
        self, offer_id: str, title: str, message: str,
        accept_label: str, dismiss_label: str, on_receive: OnReceive,
    ) -> bool:
        """"받기"/"무시" 액션 버튼이 있는 알림을 표시.

        Returns:
            bool: 알림 표시 성공 여부. False 면 main 이 plyer 폴백으로 우회.
                반드시 예외를 던지지 말고 False 를 반환할 것(호출부도 방어하지만
                백엔드 자체가 계약을 지키는 편이 안전).
        """
        raise NotImplementedError

    def pump(self) -> None:
        """백엔드가 자체 이벤트 루프를 수동으로 돌려야 할 때만 override.

        기본은 no-op — `LazyClipboardProvider.owns_clipboard()`와 동일하게
        concrete 기본값이라 일부 메서드만 구현한 백엔드에도 안전.
        """
        return

    @abstractmethod
    def stop(self) -> None:
        """백엔드 완전 종료 (구독/리소스 정리). 규칙 #14 silent 종료."""
        raise NotImplementedError


def get_actionable_notifier() -> Optional[ActionableNotifier]:
    """현재 OS 에 맞는 액션 알림 백엔드 인스턴스를 반환.

    미구현/의존성 부재/생성 실패 시 **None** 반환 — main 은 이를 "액션 알림
    불가" 신호로 받아 기존 plyer 기반 `_notify()` 로 graceful 하게 우회한다.
    """
    system = platform.system()
    try:
        if system == "Linux":
            from core.notify_linux import LinuxActionableNotifier
            notifier = LinuxActionableNotifier()
        elif system == "Windows":
            from core.notify_win import WindowsActionableNotifier
            notifier = WindowsActionableNotifier()
        elif system == "Darwin":
            from core.notify_mac import MacActionableNotifier
            notifier = MacActionableNotifier()
        else:
            return None
    except Exception:
        # 의존성 부재/초기화 실패 — 액션 알림 불가, plyer 폴백 (크래시 금지)
        return None
    return notifier if notifier.is_supported() else None
