"""
core/notify_win.py — Windows 액션 버튼 알림 백엔드 (`Windows-Toasts`, WinRT 기반).

의존성: `Windows-Toasts`(requirements_win.txt) — WinRT 토스트 액션 버튼을
지원하는 활발히 유지되는 라이브러리(`win10toast` 계열은 단일 클릭 콜백만
지원해 버튼 2개 구분이 안 됨).

⚠️ AUMID 미등록 — `InteractableWindowsToaster`는 커스텀 AUMID
(`notifierAUMID`)를 안 주면 **Command Prompt 의 AUMID 로 폴백**한다
(라이브러리 자체 문서: "provides the command prompt as the default").
토스트 자체는 정상 동작하고 `applicationText`("Infinite Clipboard")가
attribution text 로 표시되지만, 아이콘/기본 타이틀은 cmd.exe 것으로 보일
수 있다 — 기능엔 영향 없는 화장품 수준 이슈라 v1 은 기본값 그대로 출시.
제대로 고치려면 `register_hkey_aumid`(패키지 동봉 스크립트)로 설치 시
레지스트리에 커스텀 AUMID 를 등록해야 하는데, 이건 `build/installer.iss`
(Inno Setup) 쪽 작업이라 별도 후속 과제로 남김(실 Windows 없이 레지스트리
등록 스크립트를 검증 없이 바꾸는 게 더 위험 판단).

`on_activated` 콜백은 WinRT 알림 서브시스템 내부 스레드에서 발화한다 —
lazy_win.py 의 lazy-paste 콜백(WM_RENDERFORMAT, OS 에 데이터를 동기로
돌려줘야 함)과 달리, 이 콜백은 "눌렸다"는 신호일 뿐이라 OS 에 뭘 돌려줄
필요가 없다. 그래도 `on_receive`(main.py 가 이미 스레드로 감쌈)를 그
콜백 스택 안에서 직접 실행하면 WinRT 내부 디스패치 스레드를 오래 잡아두게
되므로, main.py 의 계약(즉시 새 스레드로 위임 후 반환)을 그대로 따른다 —
이 백엔드는 별도 스레드 처리를 하지 않고 그 계약에 의존한다.
"""

import logging

from core.actionable_notify import ActionableNotifier, OnReceive

logger = logging.getLogger(__name__)

_ACTION_RECEIVE = "receive"
_ACTION_DISMISS = "dismiss"


class WindowsActionableNotifier(ActionableNotifier):
    def __init__(self):
        self._toaster = None
        self._supported = False
        try:
            from windows_toasts import InteractableWindowsToaster
            self._toaster = InteractableWindowsToaster("Infinite Clipboard")
            self._supported = True
        except Exception as e:
            logger.debug(f"Windows-Toasts 사용 불가 — plyer 폴백: {e}")
            self._toaster = None

    def is_supported(self) -> bool:
        return self._supported

    def notify_receivable(
        self, offer_id, title, message, accept_label, dismiss_label, on_receive: OnReceive,
    ) -> bool:
        if not self._supported or self._toaster is None:
            return False
        try:
            from windows_toasts import Toast, ToastButton, ToastActivatedEventArgs

            toast = Toast([title, message])
            toast.AddAction(ToastButton(accept_label, _ACTION_RECEIVE))
            toast.AddAction(ToastButton(dismiss_label, _ACTION_DISMISS))

            def _on_activated(args: "ToastActivatedEventArgs" = None) -> None:
                # args 가 None 이거나 arguments 가 비면(토스트 본문 클릭 등)
                # "받기" 로 취급하지 않는다 — 명시적으로 그 버튼일 때만.
                if args is not None and getattr(args, "arguments", None) == _ACTION_RECEIVE:
                    on_receive()

            toast.on_activated = _on_activated
            self._toaster.show_toast(toast)
        except Exception as e:
            logger.warning(f"Windows 알림 실패 — plyer 폴백: {e}")
            return False
        return True

    def stop(self) -> None:
        if self._toaster is None:
            return
        try:
            self._toaster.clear_toasts()
        except Exception:
            pass
