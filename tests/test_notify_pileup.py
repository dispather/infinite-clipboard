"""Linux «파일 받기» 알림 쌓임 회귀 (2026-11-27, linux-notify-pileup).

실제 `LinuxActionableNotifier` 의 수명 관리 로직(발신자별 replaces_id 슬롯, withdraw,
NotificationClosed 정리)을 **가짜 D-Bus 알림 데몬**에 붙여 결정적으로 검증한다 —
실 세션 버스가 없어도 돈다(tests/test_notify_linux.py 는 실 데몬 전용이라 헤드리스에선 skip).

가짜 데몬은 freedesktop Notifications 명세대로 동작한다: Notify 의 replaces_id 가
열린 알림이면 그 id 를 그대로 돌려주며 교체, 0 이거나 없는 id 면 새 id 발급.
CloseNotification 은 열린 집합에서 제거. «열린 알림 수» = 화면/이력에 남은 수.
"""

import logging
import threading
import time
import uuid

from config import AppConfig
from core.notify_linux import LinuxActionableNotifier
from core.protocol import generate_peer_id
from main import InfiniteClipboard


class _Variant:
    def __init__(self, _sig, value):
        self._value = value

    def unpack(self):
        return self._value


class _FakeGLib:
    Variant = _Variant

    class VariantType:
        @staticmethod
        def new(_sig):
            return None


class _FakeGio:
    class DBusCallFlags:
        NONE = 0


class _FakeDaemon:
    """org.freedesktop.Notifications 의 최소 모형."""

    def __init__(self, reject_notify=False):
        self.open: set = set()
        self.next_id = 1
        self.notify_calls = 0
        self.reject_notify = reject_notify

    def call_sync(self, _bus, _path, _iface, method, params, *_rest):
        if method == "Notify":
            self.notify_calls += 1
            if self.reject_notify:
                raise RuntimeError("GDBus.Error:org.freedesktop.Notifications.Error."
                                   "ExcessNotificationGeneration")
            replaces_id = params.unpack()[1]
            if replaces_id and replaces_id in self.open:
                return _Variant("(u)", (replaces_id,))
            nid = self.next_id
            self.next_id += 1
            self.open.add(nid)
            return _Variant("(u)", (nid,))
        if method == "CloseNotification":
            self.open.discard(params.unpack()[0])
            return None
        raise AssertionError(f"unexpected D-Bus method {method}")

    def signal_unsubscribe(self, _sub):
        pass


def _make_notifier(daemon):
    n = LinuxActionableNotifier.__new__(LinuxActionableNotifier)
    n._lock = threading.Lock()
    n._pending = {}
    n._by_offer = {}
    n._slots = {}
    n._supported = True
    n._conn = daemon
    n._sub_id = None
    n._closed_sub_id = None
    n._ctx = None
    n._glib = _FakeGLib
    n._gio = _FakeGio
    return n


def _notify(n, offer_id, slot, calls=None):
    return n.notify_receivable(
        offer_id, "파일 받기", "f.bin (10 B)", "받기", "무시",
        lambda: (calls if calls is not None else []).append(offer_id), slot=slot,
    )


class _Params:
    def __init__(self, *v):
        self._v = v

    def unpack(self):
        return self._v


# ── 백엔드 단위 ─────────────────────────────────────────────────────────────

def test_same_sender_three_offers_leave_one_open_notification():
    d = _FakeDaemon()
    n = _make_notifier(d)
    for _ in range(3):
        assert _notify(n, str(uuid.uuid4()), slot="peer-a")
    assert len(d.open) == 1
    assert n.open_count() == 1
    assert len(n._pending) == 1


def test_two_senders_keep_two_notifications():
    """🔴 전역 단일 슬롯 금지 — 다른 발신자의 «받기» 버튼을 지우면 안 된다."""
    d = _FakeDaemon()
    n = _make_notifier(d)
    _notify(n, "a1", slot="peer-a")
    _notify(n, "b1", slot="peer-b")
    _notify(n, "a2", slot="peer-a")
    assert len(d.open) == 2


def test_withdraw_closes_notification():
    d = _FakeDaemon()
    n = _make_notifier(d)
    _notify(n, "a1", slot="peer-a")
    n.withdraw("a1")
    assert d.open == set()
    assert n.open_count() == 0 and n._pending == {}
    n.withdraw("a1")  # 두 번째는 no-op
    n.withdraw("unknown")


def test_replaced_offer_callback_is_dropped():
    """교체된 옛 offer 의 콜백이 새 알림 클릭에 섞여 발화하면 안 된다."""
    d = _FakeDaemon()
    n = _make_notifier(d)
    calls = []
    _notify(n, "a1", slot="peer-a", calls=calls)
    _notify(n, "a2", slot="peer-a", calls=calls)
    (nid,) = d.open
    n._on_action_invoked(None, None, None, None, None, _Params(nid, "receive"))
    assert calls == ["a2"]
    n.withdraw("a1")  # 이미 교체됨 — 새 알림을 닫으면 안 된다
    assert d.open == {nid}


def test_notification_closed_signal_clears_pending():
    d = _FakeDaemon()
    n = _make_notifier(d)
    _notify(n, "a1", slot="peer-a")
    (nid,) = d.open
    d.open.discard(nid)  # 데몬 쪽 만료
    n._on_notification_closed(None, None, None, None, None, _Params(nid, 1))
    assert n._pending == {}
    # 만료 뒤 같은 발신자 새 offer — 교체 대상이 없어 새 id, 여전히 열린 것은 1개.
    _notify(n, "a2", slot="peer-a")
    assert len(d.open) == 1


def test_notify_rejected_closes_old_slot_notification():
    """교체 Notify 가 거부되면(ExcessNotificationGeneration) 옛 알림은 대체된 offer 를
    가리키는 죽은 버튼 — 닫아 둔다."""
    d = _FakeDaemon()
    n = _make_notifier(d)
    _notify(n, "a1", slot="peer-a")
    d.reject_notify = True
    assert _notify(n, "a2", slot="peer-a") is False
    assert d.open == set()
    assert n._pending == {}


def test_stop_closes_all():
    d = _FakeDaemon()
    n = _make_notifier(d)
    _notify(n, "a1", slot="peer-a")
    _notify(n, "b1", slot="peer-b")
    n.stop()
    assert d.open == set()


# ── main 오케스트레이션 (실 백엔드 + 가짜 데몬) ─────────────────────────────

def _make_app(notifier):
    app = InfiniteClipboard(AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
    ))
    app.actionable_notifier = notifier
    app._actionable_notifier_inited = True
    app._save_transfer_state = lambda *a, **k: None
    return app


def _offer(source_peer):
    return {
        "offer_id": str(uuid.uuid4()), "source_peer": source_peer, "kind": "file",
        "items": [{"name": "f.bin", "size": 10, "hash": ""}],
        "total_size": 10, "created_at": time.time(),
    }


def test_app_same_sender_three_offers_then_receive_leaves_zero():
    d = _FakeDaemon()
    app = _make_app(_make_notifier(d))
    app._notify = lambda title, message: None
    peer = generate_peer_id()
    offers = [_offer(peer) for _ in range(3)]
    for o in offers:
        app._handle_clip_offer(o)
    assert len(d.open) == 1
    assert list(app.receivable_offers) == [offers[-1]["offer_id"]]
    # 받음(알림·전송창 공통 종착점) → 알림 회수
    app._clear_receivable(offers[-1]["offer_id"])
    assert d.open == set()


def test_app_two_senders_two_notifications():
    d = _FakeDaemon()
    app = _make_app(_make_notifier(d))
    app._notify = lambda title, message: None
    pa, pb = generate_peer_id(), generate_peer_id()
    app._handle_clip_offer(_offer(pa))
    app._handle_clip_offer(_offer(pb))
    app._handle_clip_offer(_offer(pa))
    assert len(d.open) == 2


def test_app_fallback_notify_once_per_sender_while_unreceived():
    """D-Bus 가 거부해 plyer 폴백으로 갈 때 같은 발신자 연속은 1건만."""
    d = _FakeDaemon(reject_notify=True)
    app = _make_app(_make_notifier(d))
    plain = []
    app._notify = lambda title, message: plain.append(title)
    pa, pb = generate_peer_id(), generate_peer_id()
    offers = [_offer(pa) for _ in range(3)]
    for o in offers:
        app._handle_clip_offer(o)
    assert len(plain) == 1
    app._handle_clip_offer(_offer(pb))
    assert len(plain) == 2, "다른 발신자는 따로 알려야 한다"
    # 받고 나면 그 발신자의 다음 offer 는 다시 알린다
    app._clear_receivable(offers[-1]["offer_id"])
    app._handle_clip_offer(_offer(pa))
    assert len(plain) == 3


def test_app_replace_logs_one_line(caplog):
    """stale-receivable-clear-log: 같은 발신자 offer 2연속 → «이전 받기 항목 대체» 1줄, 파일명 없음."""
    d = _FakeDaemon()
    app = _make_app(_make_notifier(d))
    app._notify = lambda title, message: None
    peer = generate_peer_id()
    with caplog.at_level(logging.INFO, logger="infinite-clipboard"):
        app._handle_clip_offer(_offer(peer))
        app._handle_clip_offer(_offer(peer))
    lines = [r.getMessage() for r in caplog.records if "이전 받기 항목 대체" in r.getMessage()]
    assert len(lines) == 1
    assert "f.bin" not in lines[0]
