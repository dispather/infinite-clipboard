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

── 알림 수명 (2026-11-27, linux-notify-pileup) ──────────────────────────
예전엔 offer 마다 `replaces_id=0` 으로 새 알림을 띄우고 `stop()` 에서만 닫아,
받기 모드 offer 가 하루 수십 건 오면 알림이 쌓이고 KDE 가
`Error.ExcessNotificationGeneration` 으로 Notify 를 거부했다(거부되면 plyer
폴백 알림이 또 뜬다). 지금은:
- 발신자(`slot`)별로 마지막 알림 id 를 기억해 같은 발신자의 새 offer 는
  `replaces_id` 로 **그 자리를 교체**한다. 🔴 전역 단일 슬롯 금지 — 다른 발신자의
  «받기» 버튼을 지운다.
- `withdraw(offer_id)` — 받음·대체(lazy 경로)·terminal 실패 때 main 이 불러
  `CloseNotification` 으로 화면/이력에서 지운다.
- `NotificationClosed` 시그널 구독 — 만료·사용자 닫기로 닫힌 알림의 콜백을
  `_pending` 에서 뺀다(예전엔 ActionInvoked 없이는 영영 안 줄었다). 슬롯·offer
  매핑은 남겨 둔다 — 다음 offer 가 같은 id 를 replaces_id 로 써서 이력 항목까지
  교체할 수 있게, 그리고 withdraw 가 이력에서 지울 수 있게.
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
        self._pending: dict = {}   # notif_id(uint32) -> on_receive (아직 누를 수 있는 알림)
        self._by_offer: dict = {}  # offer_id -> notif_id (닫기 대상)
        self._slots: dict = {}     # slot(발신자) -> notif_id (replaces_id 원천)
        self._supported = False
        self._conn = None
        self._sub_id = None
        self._closed_sub_id = None
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
            self._closed_sub_id = self._conn.signal_subscribe(
                "org.freedesktop.Notifications", "org.freedesktop.Notifications",
                "NotificationClosed", "/org/freedesktop/Notifications", None,
                Gio.DBusSignalFlags.NONE, self._on_notification_closed,
            )
            self._supported = True
        except Exception as e:
            logger.warning(f"Linux 알림(D-Bus) 초기화 실패 — plyer 폴백: {e}")
            self._conn = None
        finally:
            self._ctx.pop_thread_default()

    def is_supported(self) -> bool:
        return self._supported

    def _close_ids(self, notif_ids) -> None:
        """CloseNotification — 이미 닫힌 id 여도 데몬은 무시하거나 에러를 돌려줄 뿐(silent)."""
        if self._conn is None:
            return
        for notif_id in notif_ids:
            try:
                self._conn.call_sync(
                    "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                    "org.freedesktop.Notifications", "CloseNotification",
                    self._glib.Variant("(u)", (notif_id,)),
                    None, self._gio.DBusCallFlags.NONE, 2000, None,
                )
            except Exception as e:
                logger.debug(f"CloseNotification({notif_id}) 실패(무시): {e}")

    def _forget_id_locked(self, notif_id) -> None:
        """notif_id 를 가리키는 모든 매핑 제거 — self._lock 보유 상태에서 호출."""
        self._pending.pop(notif_id, None)
        for oid in [o for o, n in self._by_offer.items() if n == notif_id]:
            del self._by_offer[oid]
        for slot in [k for k, n in self._slots.items() if n == notif_id]:
            del self._slots[slot]

    def notify_receivable(
        self, offer_id, title, message, accept_label, dismiss_label, on_receive: OnReceive,
        slot=None,
    ) -> bool:
        if not self._supported or self._conn is None:
            return False
        with self._lock:
            replaces_id = self._slots.get(slot, 0) if slot is not None else 0
        try:
            reply = self._conn.call_sync(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", "Notify",
                self._glib.Variant(
                    "(susssasa{sv}i)",
                    (
                        _APP_NAME, replaces_id, "", title, message,
                        [_ACTION_RECEIVE, accept_label, _ACTION_DISMISS, dismiss_label],
                        {}, _EXPIRE_MS,
                    ),
                ),
                self._glib.VariantType.new("(u)"), self._gio.DBusCallFlags.NONE, 2000, None,
            )
        except Exception as e:
            logger.warning(f"D-Bus Notify 실패 — plyer 폴백: {e}")
            # 교체에 실패했으면 그 발신자의 옛 알림은 이미 대체된(받을 수 없는) offer 를
            # 가리킨다 — 남겨 두면 눌러도 superseded 로 실패하는 버튼이 된다.
            if replaces_id:
                with self._lock:
                    self._forget_id_locked(replaces_id)
                self._close_ids([replaces_id])
            return False
        notif_id = reply.unpack()[0]
        stale_ids = []
        with self._lock:
            if replaces_id and replaces_id != notif_id:
                # 데몬이 교체 대신 새 id 를 줬다(옛 알림이 이미 사라졌거나 데몬 정책) —
                # 옛 id 는 닫아 두 개가 남지 않게 한다.
                self._forget_id_locked(replaces_id)
                stale_ids.append(replaces_id)
            # 같은 id 를 가리키던 옛 offer(교체된 것)의 매핑·콜백은 버린다.
            self._forget_id_locked(notif_id)
            self._pending[notif_id] = on_receive
            self._by_offer[offer_id] = notif_id
            if slot is not None:
                self._slots[slot] = notif_id
        self._close_ids(stale_ids)
        return True

    def withdraw(self, offer_id) -> None:
        with self._lock:
            notif_id = self._by_offer.get(offer_id)
            if notif_id is None:
                return
            self._forget_id_locked(notif_id)
        self._close_ids([notif_id])

    def open_count(self) -> int:
        """추적 중인(닫기 대상) 알림 수 — 진단·테스트용."""
        with self._lock:
            return len(set(self._by_offer.values()))

    def _on_action_invoked(self, _conn, _sender, _path, _iface, _signal, params):
        notif_id, action_key = params.unpack()
        with self._lock:
            on_receive = self._pending.pop(notif_id, None)
        if on_receive is not None and action_key == _ACTION_RECEIVE:
            on_receive()

    def _on_notification_closed(self, _conn, _sender, _path, _iface, _signal, params):
        # (id, reason) — reason 1=만료 2=사용자 닫음 3=CloseNotification 4=기타.
        # 닫힌 알림의 버튼은 더 못 누르므로 콜백만 버린다(_by_offer·_slots 는 유지 — 모듈 docstring).
        notif_id = params.unpack()[0]
        with self._lock:
            self._pending.pop(notif_id, None)

    def pump(self) -> None:
        if not self._supported or self._ctx is None:
            return
        # 무한 루프 방지 — 클립보드 감지 스레드 한 tick 당 최대 N회만 드레인.
        for _ in range(10):
            if not self._ctx.iteration(False):
                break

    def stop(self) -> None:
        if self._conn is not None:
            for sub in (self._sub_id, self._closed_sub_id):
                if sub is None:
                    continue
                try:
                    self._conn.signal_unsubscribe(sub)
                except Exception:
                    pass
        with self._lock:
            ids = set(self._pending.keys()) | set(self._by_offer.values())
            self._pending.clear()
            self._by_offer.clear()
            self._slots.clear()
        # 종료 시 남은 알림을 화면/이력에서 지운다 — 안 지우면 이미
        # 죽은 프로세스로 이어지는, 눌러도 반응 없는 "받기" 버튼만 남는다.
        self._close_ids(sorted(ids))
