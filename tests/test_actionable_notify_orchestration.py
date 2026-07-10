"""main.py 의 액션 알림 오케스트레이션 회귀 (2026-07-10).

core/notify_*.py 백엔드 자체(OS 알림 API)가 아니라, main.py 가 그 백엔드를
어떻게 호출하고 실패/미지원 시 기존 plyer 기반 `_notify()`로 어떻게
폴백하는지, 그리고 알림의 "받기"(이 프로세스 내 콜백)와 전송 창의
"받기"(별도 프로세스, IPC 폴링)가 동시에 같은 offer 를 트리거해도 실제
fetch/저장은 한 번만 일어나는지를 검증한다.

`core/notify_linux.py`(실제 D-Bus) 자체 검증은 tests/test_notify_linux.py.
"""

import threading
import time
import uuid

from config import AppConfig
from core.protocol import generate_peer_id
from main import FetchFailure, InfiniteClipboard


def _make_app():
    return InfiniteClipboard(AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
    ))


def _offer_dict(source_peer, name="file.bin"):
    return {
        "offer_id": str(uuid.uuid4()),
        "source_peer": source_peer,
        "kind": "file",
        "items": [{"name": name, "size": 10, "hash": ""}],
        "total_size": 10,
        "created_at": time.time(),
    }


class _StubNotifier:
    """tests/test_lazy_orchestration.py 의 _StubProvider 와 동일한 주입 패턴."""

    def __init__(self, supported=True):
        self._supported = supported
        self.calls = []

    def is_supported(self):
        return self._supported

    def notify_receivable(self, offer_id, title, message, accept_label, dismiss_label, on_receive):
        self.calls.append({
            "offer_id": offer_id, "title": title, "message": message,
            "accept_label": accept_label, "dismiss_label": dismiss_label,
            "on_receive": on_receive,
        })
        return self._supported

    def pump(self):
        pass

    def stop(self):
        pass


def _install_stub(app, stub):
    app.actionable_notifier = stub
    app._actionable_notifier_inited = True  # 팩토리 재호출(_ensure_actionable_notifier) 방지


def test_ensure_actionable_notifier_does_not_block_caller_on_first_call(monkeypatch):
    """2026-07-10 리뷰 발견: macOS 백엔드(core/notify_mac.py)는 알림 권한 요청이
    비동기라 최대 5초 대기한다 — 이 초기화를 _add_receivable 를 부르는 네트워크
    메시지 처리 스레드에서 직접 기다리면 같은 피어의 후속 메시지가 그만큼 지연된다.
    background 스레드로 위임해 첫 호출은 즉시 None(폴백)을 반환해야 한다."""
    import main as main_module

    app = _make_app()
    started = threading.Event()
    release = threading.Event()

    def _slow_factory():
        started.set()
        release.wait(timeout=3.0)
        return _StubNotifier(supported=True)

    monkeypatch.setattr(main_module, "get_actionable_notifier", _slow_factory)

    t0 = time.time()
    result = app._ensure_actionable_notifier()
    elapsed = time.time() - t0

    assert result is None, "백그라운드 초기화가 끝나기 전엔 None(폴백) 이어야 함"
    assert elapsed < 1.0, f"호출 스레드가 블로킹됨 ({elapsed:.2f}s) — 백그라운드 위임 실패"
    assert started.wait(timeout=2.0), "백그라운드 스레드가 팩토리를 호출하지 않음"

    release.set()  # 팩토리 완료 허용
    deadline = time.time() + 2.0
    while time.time() < deadline and app.actionable_notifier is None:
        time.sleep(0.02)
    assert app.actionable_notifier is not None, "백그라운드 초기화 결과가 반영되지 않음"

    # 초기화 완료 후 재호출하면 준비된 인스턴스를 즉시 반환.
    assert app._ensure_actionable_notifier() is app.actionable_notifier


def test_add_receivable_uses_actionable_notifier_when_available():
    app = _make_app()
    stub = _StubNotifier(supported=True)
    _install_stub(app, stub)

    plain_notify_calls = []
    app._notify = lambda title, message: plain_notify_calls.append((title, message))

    offer = _offer_dict(generate_peer_id(), name="report.pdf")
    app._handle_clip_offer(offer)

    assert len(stub.calls) == 1
    assert stub.calls[0]["offer_id"] == offer["offer_id"]
    assert stub.calls[0]["accept_label"] and stub.calls[0]["dismiss_label"]
    assert not plain_notify_calls, "actionable 알림이 성공했는데 plyer 폴백도 같이 뜸"


def test_add_receivable_falls_back_to_plain_notify_when_notifier_returns_false():
    app = _make_app()
    stub = _StubNotifier(supported=False)  # notify_receivable 이 False 반환(미지원/실패)
    _install_stub(app, stub)

    plain_notify_calls = []
    app._notify = lambda title, message: plain_notify_calls.append((title, message))

    offer = _offer_dict(generate_peer_id())
    app._handle_clip_offer(offer)

    assert len(stub.calls) == 1  # 시도는 함
    assert len(plain_notify_calls) == 1  # 실패해서 기존 알림으로 폴백


def test_add_receivable_falls_back_when_notifier_unavailable():
    app = _make_app()
    app.actionable_notifier = None
    app._actionable_notifier_inited = True  # None 이 곧 "이 세션엔 불가" — 팩토리 재시도 안 함

    plain_notify_calls = []
    app._notify = lambda title, message: plain_notify_calls.append((title, message))

    offer = _offer_dict(generate_peer_id())
    app._handle_clip_offer(offer)

    assert len(plain_notify_calls) == 1


def test_add_receivable_falls_back_when_notifier_raises():
    app = _make_app()

    class _BoomNotifier(_StubNotifier):
        def notify_receivable(self, *a, **kw):
            raise RuntimeError("시뮬레이션: 백엔드 내부 오류")

    _install_stub(app, _BoomNotifier())

    plain_notify_calls = []
    app._notify = lambda title, message: plain_notify_calls.append((title, message))

    offer = _offer_dict(generate_peer_id())
    app._handle_clip_offer(offer)  # 예외를 삼키고 폴백해야 함 — 크래시 금지

    assert len(plain_notify_calls) == 1


def test_on_receive_callback_triggers_receive_offer(tmp_path):
    """알림의 '받기' 클릭 콜백이 실제로 _receive_offer 를 기동해 파일을 저장하는지."""
    app = _make_app()
    app.config.download_path = str(tmp_path / "downloads")
    stub = _StubNotifier(supported=True)
    _install_stub(app, stub)

    offer = _offer_dict(generate_peer_id())
    app._handle_clip_offer(offer)
    assert len(stub.calls) == 1
    on_receive = stub.calls[0]["on_receive"]

    src = tmp_path / "staging" / offer["offer_id"] / "file.bin"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"hello")

    class _Fetched:
        paths = [str(src)]

    app._fetch_offer = lambda oid: _Fetched()

    on_receive()  # main.py 가 이미 threading.Thread 로 감싸 즉시 반환한다

    dest = tmp_path / "downloads" / "file.bin"
    deadline = time.time() + 3.0
    while time.time() < deadline and not dest.exists():
        time.sleep(0.02)
    assert dest.exists()
    assert dest.read_bytes() == b"hello"


def test_receive_offer_dedup_guard_prevents_concurrent_double_fetch():
    """알림의 '받기'와 전송 창의 '받기'가 동시에 같은 offer 를 트리거해도
    실제 fetch 는 한 번만 실행돼야 한다(2026-07-10 — 두 트리거 경로가 별개
    프로세스/콜백이라 서로의 존재를 모른다는 점에서 발견된 회귀)."""
    app = _make_app()
    offer_id = "offer-dedup-guard"
    with app._offer_lock:
        app.receivable_offers[offer_id] = {
            "offer_id": offer_id, "source_peer": generate_peer_id(),
            "name": "f.bin", "kind": "file", "total_size": 1, "created_at": 0.0,
        }

    fetch_calls = []
    started = threading.Event()
    release = threading.Event()

    def _slow_fetch(oid):
        fetch_calls.append(oid)
        started.set()
        release.wait(timeout=3.0)
        raise FetchFailure("offline", "시뮬레이션: 느린 fetch 도중 두 번째 트리거")

    app._fetch_offer = _slow_fetch

    t1 = threading.Thread(target=app._receive_offer, args=(offer_id,))
    t1.start()
    assert started.wait(timeout=2.0), "첫 fetch 가 시작되지 않음"

    # 첫 fetch 가 아직 안 끝난 상태에서 두 번째 트리거 — 가드에 걸려 즉시 반환해야 함.
    app._receive_offer(offer_id)
    release.set()
    t1.join(timeout=3.0)

    assert len(fetch_calls) == 1, "in-flight 가드가 없으면 두 번 fetch 됨"
    with app._offer_lock:
        assert offer_id not in app._receiving_offer_ids, "가드 set 이 finally 에서 정리 안 됨"
