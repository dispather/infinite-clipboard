"""core/lazy_mac.py 의 파이썬 로직을 가짜 pyobjc 로 헤드리스 검증 (함정 #46).

tests/test_lazy_mac.py 는 실제 NSPasteboard round-trip 이라 macOS(CI/실기)에서만 돈다.
이 파일은 objc/Foundation/AppKit 을 최소 가짜 모듈로 바꿔 끼워, 브리지가 아니라
**lazy_mac 의 스레딩·상태 로직**만 Linux 에서 돌린다:
  - 워커 등록이 바쁜 메인 스레드를 무기한 기다리지 않는다(제한 대기 + 취소)
  - 메인이 fetch 중(_providing)이면 기다리지 않고 바로 False
  - 취소된 등록은 나중에도 pasteboard·상태를 바꾸지 않는다
  - 붙여넣기 도중 clear() 가 와도 같은 등록의 나머지 항목이 비지 않는다
가짜 NSThread.isMainThread 는 «가짜 메인 루프 스레드»에서만 True 다.
ObjC 브리지 자체(performSelectorOnMainThread 에 파이썬 객체 전달 등)는 여기서 검증되지 않는다.
"""

import importlib
import queue
import sys
import threading
import time
import types

import pytest


class _FakeMain:
    """메인 run loop 흉내 — 게시된 콜러블을 순서대로 실행하는 전용 스레드."""

    def __init__(self):
        self._q = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def post(self, fn):
        self._q.put(fn)

    def call(self, fn, timeout=5.0):
        """메인에서 fn 을 돌리고 결과를 기다린다(테스트용)."""
        box, done = {}, threading.Event()

        def _run():
            try:
                box["r"] = fn()
            except Exception as e:  # noqa: BLE001
                box["e"] = e
            done.set()

        self.post(_run)
        assert done.wait(timeout), "가짜 메인 루프 응답 없음"
        if "e" in box:
            raise box["e"]
        return box.get("r")

    def _run(self):
        while True:
            fn = self._q.get()
            if fn is None:
                return
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    def stop(self):
        self._q.put(None)


def _build_fake_pyobjc(main: _FakeMain):
    class NSObject:
        @classmethod
        def alloc(cls):
            return cls.__new__(cls)

        def init(self):
            return self

        def performSelectorOnMainThread_withObject_waitUntilDone_(self, sel, obj, wait):
            method = getattr(self, sel.replace(":", "_"))
            if wait:
                main.call(lambda: method(obj), timeout=60.0)
            else:
                main.post(lambda: method(obj))

    class NSThread:
        @staticmethod
        def isMainThread():
            return threading.current_thread() is main.thread

    class NSData:
        @staticmethod
        def dataWithBytes_length_(data, n):
            return bytes(data[:n])

    class _URL:
        def __init__(self, p):
            self._p = p

        def absoluteString(self):
            return "file://" + self._p

    class NSURL:
        @staticmethod
        def fileURLWithPath_(p):
            return _URL(p)

    class NSPasteboardItem(NSObject):
        def init(self):
            self.provider, self.types, self.data = None, [], {}
            return self

        def setDataProvider_forTypes_(self, prov, types_):
            self.provider, self.types = prov, list(types_)
            return True

        def setData_forType_(self, data, type_):
            self.data[type_] = data
            return True

    class _Pasteboard:
        def __init__(self):
            self.count = 0
            self.items = []

        def changeCount(self):
            return self.count

        def clearContents(self):
            self.count += 1
            self.items = []
            return self.count

        def writeObjects_(self, items):
            self.items = list(items)
            return True

    board = _Pasteboard()

    class NSPasteboard:
        @staticmethod
        def generalPasteboard():
            return board

    objc = types.ModuleType("objc")

    def protocolNamed(name):
        raise LookupError(name)  # 비공식 conform 폴백 경로

    objc.protocolNamed = protocolNamed
    objc.super = super
    foundation = types.ModuleType("Foundation")
    foundation.NSObject, foundation.NSData = NSObject, NSData
    foundation.NSURL, foundation.NSThread = NSURL, NSThread
    appkit = types.ModuleType("AppKit")
    appkit.NSPasteboard, appkit.NSPasteboardItem = NSPasteboard, NSPasteboardItem
    appkit.NSApplicationLoad = lambda: True
    return {"objc": objc, "Foundation": foundation, "AppKit": appkit}, board


@pytest.fixture
def mac(monkeypatch):
    """가짜 pyobjc 위에 core.lazy_mac 을 새로 import. 끝나면 가짜 모듈·import 캐시 원복."""
    main = _FakeMain()
    mods, board = _build_fake_pyobjc(main)
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "core.lazy_mac", raising=False)
    lazy_mac = importlib.import_module("core.lazy_mac")
    prov = lazy_mac.MacLazyProvider()
    prov._main_wait_timeout = 0.3
    yield types.SimpleNamespace(prov=prov, main=main, board=board)
    prov.stop()
    main.stop()
    sys.modules.pop("core.lazy_mac", None)  # 가짜 위에 import 된 모듈을 남기지 않는다


def _offer(kind, n_items=1):
    items = [{"name": f"f{i}.bin", "size": 1, "hash": ""} for i in range(n_items)]
    return {"offer_id": f"offer-{kind}-{n_items}-{time.monotonic_ns()}", "kind": kind,
            "items": items, "total_size": n_items}


def _paste(mac, type_="public.file-url"):
    """메인에서 pasteboard 의 모든 항목에 provideDataForType 콜백 → 항목별 데이터."""
    def _do():
        out = []
        for item in mac.board.items:
            item.provider.pasteboard_item_provideDataForType_(mac.board, item, type_)
            out.append(item.data.get(type_))
        return out
    return mac.main.call(_do, timeout=10.0)


def _register_from_worker(prov, offer, cb):
    holder = {"done": threading.Event()}

    def _run():
        t0 = time.monotonic()
        holder["ok"] = prov.register_offer(offer, cb)
        holder["elapsed"] = time.monotonic() - t0
        holder["done"].set()

    threading.Thread(target=_run, daemon=True).start()
    return holder


def _file_cb(paths):
    from core.lazy_clipboard import FetchedContent, KIND_FILE
    return lambda oid: FetchedContent(kind=KIND_FILE, paths=list(paths))


def test_register_from_worker_when_main_idle(mac):
    h = _register_from_worker(mac.prov, _offer("file"), _file_cb(["/tmp/a"]))
    assert h["done"].wait(2.0) and h["ok"] is True
    assert len(mac.board.items) == 1
    assert _paste(mac) == [b"file:///tmp/a"]


def test_worker_register_gives_up_when_main_busy_and_never_applies(mac):
    prov = mac.prov
    offer_a = _offer("file")
    assert _register_from_worker(prov, offer_a, _file_cb(["/tmp/a"]))["done"].wait(2.0)
    before = mac.board.changeCount()

    release = threading.Event()
    mac.main.post(lambda: release.wait(5.0))  # 메인이 다른 일로 막힘(run loop 못 돎)
    h = _register_from_worker(prov, _offer("file"), _file_cb(["/tmp/b"]))
    try:
        assert h["done"].wait(2.0), "워커 등록이 막힌 메인을 무기한 기다림(교착)"
        assert h["ok"] is False
        assert h["elapsed"] < 1.5
    finally:
        release.set()
    mac.main.call(lambda: None)  # 큐에 남은 (취소된) 등록까지 처리되게
    assert mac.board.changeCount() == before, "취소된 등록이 pasteboard 를 바꿈"
    assert prov._offer is offer_a, "취소된 등록이 provider 상태를 바꿈"
    assert _paste(mac) == [b"file:///tmp/a"], "기존 항목이 새 offer 를 fetch 함"


def test_worker_register_skips_immediately_while_providing(mac):
    prov = mac.prov
    fetch_started, fetch_release = threading.Event(), threading.Event()

    from core.lazy_clipboard import FetchedContent, KIND_FILE

    def _slow_cb(oid):
        fetch_started.set()
        fetch_release.wait(5.0)
        return FetchedContent(kind=KIND_FILE, paths=["/tmp/a"])

    assert _register_from_worker(prov, _offer("file"), _slow_cb)["done"].wait(2.0)
    before = mac.board.changeCount()
    mac.main.post(lambda: _paste_items_now(mac))  # 메인이 붙여넣기 콜백 안에서 fetch 대기
    assert fetch_started.wait(2.0)
    h = _register_from_worker(prov, _offer("file"), _file_cb(["/tmp/b"]))
    try:
        assert h["done"].wait(1.0)
        assert h["ok"] is False
        assert h["elapsed"] < 0.2, f"fetch 중인데 메인을 기다림: {h['elapsed']:.2f}s"
    finally:
        fetch_release.set()
    mac.main.call(lambda: None)
    assert mac.board.changeCount() == before


def _paste_items_now(mac, type_="public.file-url"):
    for item in mac.board.items:
        item.provider.pasteboard_item_provideDataForType_(mac.board, item, type_)


def test_clear_mid_fetch_still_fills_remaining_items_of_same_registration(mac):
    """붙여넣기 도중(첫 항목 fetch 중) 네트워크 스레드가 clear() 해도 같은 등록의 나머지
    항목은 받은 결과를 내준다 — 안 그러면 여러 파일 중 첫 파일만 붙여넣어진다."""
    prov = mac.prov
    from core.lazy_clipboard import FetchedContent, KIND_FILE

    def _cb_that_sees_clear(oid):
        prov.clear()  # 둘째 offer 도착 → main._handle_clip_offer 가 먼저 clear()
        return FetchedContent(kind=KIND_FILE, paths=["/tmp/a", "/tmp/b"])

    assert _register_from_worker(prov, _offer("file", 2), _cb_that_sees_clear)["done"].wait(2.0)
    assert _paste(mac) == [b"file:///tmp/a", b"file:///tmp/b"]


def test_clear_after_completed_paste_serves_nothing(mac):
    """붙여넣기가 끝난 뒤의 clear() 는 즉시 해제 — 나중 Cmd+V 에 옛 offer 가 붙지 않는다.

    (등록 단위 결과를 해제 뒤에도 내주면 옛 파일이 붙고, 소유가 풀린 클립보드 모니터가
    그 경로를 로컬 복사로 읽어 재broadcast 할 수 있다)
    """
    prov = mac.prov
    assert _register_from_worker(prov, _offer("file"), _file_cb(["/tmp/a"]))["done"].wait(2.0)
    assert _paste(mac) == [b"file:///tmp/a"]
    prov.clear()
    assert prov.owns_clipboard() is False
    for item in mac.board.items:
        item.data.clear()
    assert _paste(mac) == [None], "해제된 등록이 옛 결과를 계속 내줌"


def test_clear_mid_fetch_keeps_ownership_until_all_items_served(mac):
    """붙여넣기 도중의 clear() 는 미뤄진다 — 그동안 소유 유지(모니터가 안 읽음),
    같은 등록의 항목을 다 내주면 해제되고 이후 붙여넣기는 아무것도 안 준다."""
    prov = mac.prov
    seen = {}
    from core.lazy_clipboard import FetchedContent, KIND_FILE

    def _cb(oid):
        prov.clear()
        seen["owns_during_fetch"] = prov.owns_clipboard()
        return FetchedContent(kind=KIND_FILE, paths=["/tmp/a", "/tmp/b"])

    assert _register_from_worker(prov, _offer("file", 2), _cb)["done"].wait(2.0)
    assert _paste(mac) == [b"file:///tmp/a", b"file:///tmp/b"]
    assert seen["owns_during_fetch"] is True, "미뤄 둔 해제 동안 소유가 풀림"
    assert prov.owns_clipboard() is False, "모든 항목을 내준 뒤에도 해제 안 됨"
    for item in mac.board.items:
        item.data.clear()
    assert _paste(mac) == [None, None]


def test_deferred_clear_applies_after_timeout_if_rest_never_requested(mac):
    """붙여넣는 앱이 첫 항목만 읽어도 미뤄 둔 해제는 _drain_timeout 뒤 적용된다."""
    prov = mac.prov
    prov._drain_timeout = 0.2
    from core.lazy_clipboard import FetchedContent, KIND_FILE

    def _cb(oid):
        prov.clear()
        return FetchedContent(kind=KIND_FILE, paths=["/tmp/a", "/tmp/b"])

    assert _register_from_worker(prov, _offer("file", 2), _cb)["done"].wait(2.0)
    first = mac.board.items[0]
    mac.main.call(lambda: first.provider.pasteboard_item_provideDataForType_(
        mac.board, first, "public.file-url"))
    assert first.data.get("public.file-url") == b"file:///tmp/a"
    assert prov.owns_clipboard() is True  # 아직 둘째 항목 대기 중
    deadline = time.monotonic() + 2.0
    while prov.owns_clipboard() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert prov.owns_clipboard() is False, "미뤄 둔 해제가 시간 상한 뒤에도 적용 안 됨"


def test_deferred_clear_applies_immediately_when_fetch_fails(mac):
    prov = mac.prov

    def _cb(oid):
        prov.clear()
        raise RuntimeError("fetch 타임아웃")

    assert _register_from_worker(prov, _offer("file", 2), _cb)["done"].wait(2.0)
    assert _paste(mac) == [None, None]
    assert prov.owns_clipboard() is False


def test_clear_before_paste_serves_nothing(mac):
    """clear 의 원래 의미 유지 — 붙여넣기 전에 해제된 등록은 아무것도 안 준다(fetch 도 안 함)."""
    prov = mac.prov
    fetched = []
    from core.lazy_clipboard import FetchedContent, KIND_FILE

    def _cb(oid):
        fetched.append(oid)
        return FetchedContent(kind=KIND_FILE, paths=["/tmp/a"])

    assert _register_from_worker(prov, _offer("file"), _cb)["done"].wait(2.0)
    prov.clear()
    assert _paste(mac) == [None]
    assert fetched == []


def test_fake_modules_do_not_leak():
    """이 파일의 가짜 pyobjc 가 다른 테스트(test_lazy_mac.py 의 skip 판정)로 새지 않는다."""
    assert "core.lazy_mac" not in sys.modules or sys.platform == "darwin"
    try:
        import objc  # noqa: F401
        leaked = not hasattr(objc, "__file__") and sys.platform != "darwin"
    except ImportError:
        leaked = False
    assert not leaked, "가짜 objc 모듈이 sys.modules 에 남음"
