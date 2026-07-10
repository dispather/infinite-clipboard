"""
core/notify_linux.py — Linux 액션 버튼 알림 백엔드 (D-Bus `org.freedesktop.Notifications`).

X11/Wayland 구분 없음 — 알림은 디스플레이 서버가 아니라 데스크톱 세션의
D-Bus 세션 버스 레벨 프로토콜이라 두 세션 타입에서 동일하게 동작한다.
의존성은 `python-gobject`(gi) 하나뿐 — pystray 의 AppIndicator 트레이 백엔드가
이미 시스템 의존성으로 요구하므로 새 pip 패키지가 필요 없다.

── GLib 메인루프 함정 (실측, core/clipboard_manager.py 참조) ────────────
별도 `GLib.MainLoop().run()` 스레드를 새로 띄우면 pystray 의 Gtk 루프와
충돌해 CPU 스핀 + 메모리 손상이 난다(`core/clipboard_manager.py` 의
`_drain_dbus_signals()` 주석 참조 — 이미 검증된 landmine). 이 백엔드는
전용 `GLib.MainContext` 를 직접 만들어 그 컨텍스트에서만 D-Bus 연결/구독을
설정하고, `pump()` 로 그 컨텍스트만 수동 iterate — 어떤 실행 모드(트레이 유무)
에서도 안전하다. `bus_get_sync`/`signal_subscribe` 는 호출 시점의
thread-default 컨텍스트에 귀속되므로, ambient 상태에 의존하지 않도록
`push_thread_default()`/`pop_thread_default()` 로 명시적으로 감싼다.

`ActionInvoked` 시그널은 `pump()` 호출 스택 안에서 동기로 발화한다 — 이
백엔드의 `pump()` 는 main.py 의 `_monitor_clipboard` 루프(클립보드 감지를
담당하는 유일한 스레드)에서 호출되므로, `on_receive` 콜백이 그 자리에서
블로킹하면 클립보드 감지 전체가 멎는다. 그래서 `on_receive` 는 반드시
새 스레드를 띄우고 즉시 반환해야 한다 — 이 계약은 main.py 쪽에서 지킨다
(actionable_notify.py 모듈 docstring 참조).
"""

import logging
import threading

from core.actionable_notify import ActionableNotifier, OnReceive

logger = logging.getLogger(__name__)

_APP_NAME = "Infinite Clipboard"
_ACTION_RECEIVE = "receive"
_ACTION_DISMISS = "dismiss"
_EXPIRE_MS = 15000


class LinuxActionableNotifier(ActionableNotifier):
    def __init__(self):
        self._lock = threading.Lock()
        self._pending: dict = {}  # notif_id(uint32) -> on_receive
        self._supported = False
        self._conn = None
        self._sub_id = None
        self._ctx = None
        self._init_dbus()

    def _init_dbus(self) -> None:
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except Exception as e:
            logger.debug(f"gi/Gio 부재 — 액션 알림 미지원: {e}")
            return

        self._gio = Gio
        self._glib = GLib
        # 전용 컨텍스트 — ambient thread-default 상태에 의존하지 않는다.
        self._ctx = GLib.MainContext.new()
        self._ctx.push_thread_default()
        try:
            self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = self._conn.call_sync(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", "GetCapabilities", None,
                GLib.VariantType.new("(as)"), Gio.DBusCallFlags.NONE, 2000, None,
            )
            caps = reply.unpack()[0]
            if "actions" not in caps:
                logger.info("알림 데몬이 액션 버튼 미지원 — plyer 폴백")
                return
            self._sub_id = self._conn.signal_subscribe(
                "org.freedesktop.Notifications", "org.freedesktop.Notifications",
                "ActionInvoked", "/org/freedesktop/Notifications", None,
                Gio.DBusSignalFlags.NONE, self._on_action_invoked,
            )
            self._supported = True
        except Exception as e:
            logger.warning(f"Linux 알림(D-Bus) 초기화 실패 — plyer 폴백: {e}")
            self._conn = None
        finally:
            self._ctx.pop_thread_default()

    def is_supported(self) -> bool:
        return self._supported

    def notify_receivable(
        self, offer_id, title, message, accept_label, dismiss_label, on_receive: OnReceive,
    ) -> bool:
        if not self._supported or self._conn is None:
            return False
        try:
            reply = self._conn.call_sync(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", "Notify",
                self._glib.Variant(
                    "(susssasa{sv}i)",
                    (
                        _APP_NAME, 0, "", title, message,
                        [_ACTION_RECEIVE, accept_label, _ACTION_DISMISS, dismiss_label],
                        {}, _EXPIRE_MS,
                    ),
                ),
                self._glib.VariantType.new("(u)"), self._gio.DBusCallFlags.NONE, 2000, None,
            )
        except Exception as e:
            logger.warning(f"D-Bus Notify 실패 — plyer 폴백: {e}")
            return False
        notif_id = reply.unpack()[0]
        with self._lock:
            self._pending[notif_id] = on_receive
        return True

    def _on_action_invoked(self, _conn, _sender, _path, _iface, _signal, params):
        notif_id, action_key = params.unpack()
        with self._lock:
            on_receive = self._pending.pop(notif_id, None)
        if on_receive is not None and action_key == _ACTION_RECEIVE:
            on_receive()

    def pump(self) -> None:
        if not self._supported or self._ctx is None:
            return
        # 무한 루프 방지 — 클립보드 감지 스레드 한 tick 당 최대 N회만 드레인.
        for _ in range(10):
            if not self._ctx.iteration(False):
                break

    def stop(self) -> None:
        if self._sub_id is not None and self._conn is not None:
            try:
                self._conn.signal_unsubscribe(self._sub_id)
            except Exception:
                pass
        with self._lock:
            pending_ids = list(self._pending.keys())
            self._pending.clear()
        # 종료 시 아직 응답 안 된 알림을 화면/이력에서 지운다 — 안 지우면 이미
        # 죽은 프로세스로 이어지는, 눌러도 반응 없는 "받기" 버튼만 남는다.
        if self._conn is not None:
            for notif_id in pending_ids:
                try:
                    self._conn.call_sync(
                        "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                        "org.freedesktop.Notifications", "CloseNotification",
                        self._glib.Variant("(u)", (notif_id,)),
                        None, self._gio.DBusCallFlags.NONE, 2000, None,
                    )
                except Exception:
                    pass
