"""
core/lazy_mac.py — macOS lazy clipboard 백엔드 (NSPasteboard data provider).

`core.lazy_clipboard.LazyClipboardProvider` 의 macOS 구현. spikes/lazy-clipboard/
macos_spike.py 의 검증된 메커니즘(NSPasteboardItem 에 `setDataProvider:forTypes:` 로
지연 등록 → paste 시 `pasteboard:item:provideDataForType:` 콜백 발화 → fetch 한 바이트
제공)을 프로덕션화한다. `lazy_x11.py`/`lazy_win.py` 와 동형 인터페이스.

── ⚠️ 콜백은 메인 스레드 런루프에서 발화 (CI run 26632380131 에서 실증) ───────
NSPasteboard data provider 콜백(`provideDataForType:`)은 **앱 메인 스레드의 run loop**
로 전달된다. 백그라운드 스레드에서 자체 run loop 를 펌핑해도 콜백이 오지 않는다(초기
구현이 그래서 paste 타임아웃). 따라서:
  - pasteboard 등록(clearContents/writeObjects)은 **메인 스레드에서** 수행한다
    (register_offer 가 worker 스레드에서 불리면 `performSelectorOnMainThread:` 로 위임,
    메인 스레드면 직접). AppKit thread-affinity 도 만족.
  - ⚠️ 위임은 **기다림 상한이 있다**(`_main_wait_timeout`, 함정 #46). 메인 스레드는 paste
    콜백 안에서 fetch 청크를 기다리는데, 그 청크를 읽는 네트워크 스레드가 register_offer 에서
    메인을 무기한 기다리면 교착한다(2026-09-28 실기: 64 MiB 두 번째 offer → 256s 타임아웃 +
    서버 송신 timeout 으로 연결 끊김). 시간 안에 시작 못 한 등록은 취소되고 False → main 이
    받기 모드로 우회. 메인이 fetch 중이면(`_providing`) 기다리지 않고 바로 False.
  - 콜백을 받으려면 **호스트가 메인 스레드 run loop 를 펌핑**해야 한다. 실제 앱은 Tk
    mainloop(macOS 에선 Cocoa run loop) 가 이를 제공한다 → main 오케스트레이션(Task 6)
    /`.app` 검증(Task 9)에서 통합. 테스트는 NSRunLoop 를 직접 펌핑해 메커니즘을 검증한다.

── 서빙 타깃 (S0 검증) ──────────────────────────────────────────────────
data lazy provide 는 대용량(512KB)+이미지(public.png 1MB) PASS. 파일은 file promise
대신 **`public.file-url` data-lazy**(사용자 결정 2026-05-29) — 다른 OS 의 uri-list/CF_HDROP
와 같은 "스테이징 경로를 paste 시점에 제공" 모델.
  - 이미지(kind=image): `public.png` 1개 item ← FetchedContent.data
  - 파일(kind=file):   파일 수(offer.items)만큼 item, 각 `public.file-url` ←
                       FetchedContent.paths[i] 의 file:// URL (경로는 fetch 시점 확정 →
                       item 은 offer.items 수로 만들고 콜백에서 index 매핑).

── fetch 동기성 (Rec 2) ────────────────────────────────────────────────
provideDataForType 콜백 안에서 fetch_callback 을 동기 호출 → 그동안 메인 run loop 블록.
실패/타임아웃이면 setData 를 안 해 dataForType=None → 붙여넣는 앱은 빈 결과 → main 이
받기 fallback 으로 우회.

규칙 #1: UI 무관(AppKit 은 OS 클립보드용). 규칙 #9: stop() silent.
함정 #2 류: pyobjc 는 macOS 전용 — 모듈 top import 가 비-macOS 에서 실패하고 팩토리가
잡아 None 반환 → fallback. py_compile 은 영향 없음.
"""

import logging
import threading
from typing import Optional

from core.lazy_clipboard import (
    LazyClipboardProvider, FetchedContent, FetchCallback,
    KIND_FILE, KIND_IMAGE, OwnerThreadCall, OwnerThreadBusy,
)

# pyobjc — 부재/비-macOS 면 ImportError → 팩토리가 잡아 None (fallback)
import objc
from Foundation import NSObject, NSData, NSURL, NSThread
from AppKit import NSPasteboard, NSPasteboardItem, NSApplicationLoad

logger = logging.getLogger(__name__)

# 서빙 UTI
_TYPE_PNG = "public.png"
_TYPE_FILE_URL = "public.file-url"  # = NSPasteboardTypeFileURL


# ─── ObjC 클래스는 모듈 레벨 1회 정의 (스파이크 하니스 버그 교훈) ───
try:
    _PROTO = objc.protocolNamed("NSPasteboardItemDataProvider")
    _BASES, _KW = (NSObject,), {"protocols": [_PROTO]}
except Exception:  # 프로토콜 미발견 — 비공식 conform 폴백
    _BASES, _KW = (NSObject,), {}


class _Provider(*_BASES, **_KW):
    """paste 시점에 backend._provide 를 호출하는 NSPasteboardItemDataProvider.

    backend(역참조는 backend.self._providers 로 유지)/key(이미지=0, 파일=경로 index)/
    _reg(이 항목이 속한 등록 1건 — offer·fetch_cb·fetch 결과)는 인스턴스 속성.
    콜백은 메인 스레드 run loop 에서 발화.
    """

    def initWithBackend_key_(self, backend, key):
        self = objc.super(_Provider, self).init()
        if self is None:
            return None
        self._backend = backend
        self._key = key
        return self

    def pasteboard_item_provideDataForType_(self, pasteboard, item, type_):
        self._backend._provide(item, type_, self._key, getattr(self, "_reg", None))


class _MainThreadRunner(NSObject):
    """performSelectorOnMainThread 로 OwnerThreadCall 을 메인 스레드에서 실행.

    호출 측은 waitUntilDone=False 로 게시하고 call.wait(timeout) 으로 기다린다 —
    이미 취소된 call 은 run() 이 아무것도 하지 않는다.
    """

    def runCall_(self, call):
        call.run()


class MacLazyProvider(LazyClipboardProvider):
    """NSPasteboard 를 지연 소유하고 paste(provideDataForType)에 lazy 응답."""

    # 워커 스레드의 등록이 메인 스레드를 기다리는 상한 (X11/Wayland 백엔드와 같은 2.0s).
    _main_wait_timeout = 2.0
    # 붙여넣기 중 미뤄 둔 clear() 를 나머지 항목 요청이 안 와도 적용하는 상한(fetch 완료 기준).
    _drain_timeout = 2.0

    def __init__(self):
        NSApplicationLoad()
        self._runner = _MainThreadRunner.alloc().init()
        self._lock = threading.Lock()
        self._offer: Optional[dict] = None
        self._fetch_cb: Optional[FetchCallback] = None
        self._cache: Optional[FetchedContent] = None
        # 현재 등록 1건 {offer, cb, cache} — 항목(_Provider)도 같은 dict 를 쥔다. fetch 결과를
        # 등록 단위로 묶어, 붙여넣기 도중 clear()/supersede 가 와도 같은 등록의 나머지 항목
        # (여러 파일 offer 의 2번째 이후)은 이미 받은 결과를 내준다(부분 붙여넣기 방지).
        self._reg: Optional[dict] = None
        self._providers = []  # GC 방지 ref 유지 (콜백 발화까지 살아있어야)
        self._items = []
        # 등록 시점의 NSPasteboard changeCount. macOS 는 소유권 상실 콜백이 없어,
        # owns_clipboard 는 현재 changeCount 와 비교해 "그 뒤 아무도 안 썼는지"로 판단한다
        # (사용자가 로컬 복사하면 changeCount 가 올라가 자동으로 소유 아님 처리).
        self._change_count: Optional[int] = None
        # 메인 스레드가 provideDataForType 콜백 안에서 fetch 중인지 — 그동안 워커의 등록은
        # 메인을 기다리지 않고 바로 실패(→받기 모드)한다(함정 #46).
        self._providing = False
        self._fetching_reg: Optional[dict] = None

    # ── LazyClipboardProvider 인터페이스 ──────────────────────────────

    def is_supported(self, kind: str) -> bool:
        return kind in (KIND_FILE, KIND_IMAGE)

    def owns_clipboard(self) -> bool:
        # 마지막으로 우리가 쓴 뒤 pasteboard 가 그대로(changeCount 불변)면 지금 내용은 우리 것.
        # 등록을 해제(clear)한 뒤에도 마찬가지다 — 이미 내준 항목은 pasteboard 에 데이터로 남아,
        # 여기서 False 를 주면 클립보드 모니터가 방금 받은 파일을 로컬 복사로 읽어 재broadcast
        # 한다(붙여넣기 도중 둘째 offer → 미뤄 둔 해제, 2026-10-01 mac 실기 관찰 A).
        # changeCount 가 올라갔으면 다른 곳(로컬 복사 등)이 덮어쓴 것 → 소유 아님.
        with self._lock:
            expected = self._change_count
            if expected is None:
                return False
        try:
            return NSPasteboard.generalPasteboard().changeCount() == expected
        except Exception:
            return False

    def register_offer(self, offer: dict, fetch_callback: FetchCallback) -> bool:
        kind = offer.get("kind") if isinstance(offer, dict) else None
        if not self.is_supported(kind):
            return False
        # 상태(_offer/_fetch_cb/_cache)는 여기서 바꾸지 않는다 — _do_register 가 메인
        # 스레드에서 pasteboard 교체와 함께 바꾼다. 여기서 먼저 바꾸면 등록이 늦어지거나
        # 취소될 때 아직 pasteboard 에 있는 이전 offer 의 항목이 새 offer 를 fetch 한다.
        if not NSThread.isMainThread():
            with self._lock:
                providing = self._providing
            if providing:
                logger.info("macOS lazy: 메인 스레드가 붙여넣기 수신 중 — 등록 생략(→받기 모드)")
                return False
        try:
            return bool(self._run_on_main(lambda: self._do_register(offer, fetch_callback)))
        except OwnerThreadBusy as e:
            logger.info(f"macOS lazy: 메인 스레드 바쁨 — 등록 취소(→받기 모드): {e}")
            return False
        except Exception as e:  # noqa: BLE001
            logger.warning(f"macOS lazy: 등록 실패 — fallback: {e}")
            return False

    def clear(self) -> None:
        # 소유는 유지하되 offer=None → provideDataForType 가 아무것도 안 줘 dataForType=None.
        # (소유 판정은 해제와 무관하게 changeCount 로 한다 — owns_clipboard 참조)
        # 단 지금 이 등록을 붙여넣는 중(fetch 진행)이면 해제를 미룬다: 같은 붙여넣기의 나머지
        # 항목이 그 결과를 받는다(여러 파일 offer 의 부분 붙여넣기 방지). 모든 항목을
        # 내줬거나 fetch 완료 후 _drain_timeout 이 지나면 해제된다(_finish_drain_locked).
        with self._lock:
            reg = self._reg
            if reg is not None and self._providing and self._fetching_reg is reg:
                reg["clear_pending"] = True
                return
            self._clear_locked()

    def _clear_locked(self) -> None:
        # _change_count 는 남긴다 — pasteboard 에는 여전히 우리가 쓴 항목이 있다(owns_clipboard)
        self._reg = None
        self._offer = None
        self._fetch_cb = None
        self._cache = None

    def _finish_drain_locked(self, reg: dict) -> None:
        """미뤄 둔 clear 를 적용 (self._lock 보유 상태로 호출). 이미 교체된 등록이면 무시."""
        if reg.get("clear_pending"):
            reg["clear_pending"] = False
            if self._reg is reg:
                self._clear_locked()

    def _finish_drain(self, reg: dict) -> None:
        with self._lock:
            self._finish_drain_locked(reg)

    def stop(self) -> None:
        with self._lock:
            self._reg = None
            self._offer = None
            self._fetch_cb = None
            self._cache = None
            self._change_count = None
            self._providers = []
            self._items = []

    # ── 메인 스레드 디스패치 ───────────────────────────────────────────

    def _run_on_main(self, fn):
        """fn 을 메인 스레드에서 실행(이미 메인이면 직접). 결과 반환/예외 전파.

        worker 스레드에서 호출 시 메인 run loop 가 펌핑돼야 완료된다(실앱=Tk mainloop).
        `_main_wait_timeout` 안에 메인이 시작하지 못하면 취소하고 OwnerThreadBusy —
        무기한 기다리면 메인이 네트워크 fetch 를 기다리는 동안 교착한다(함정 #46).
        """
        if NSThread.isMainThread():
            return fn()
        call = OwnerThreadCall(fn)
        self._runner.performSelectorOnMainThread_withObject_waitUntilDone_(
            "runCall:", call, False,
        )
        return call.wait(self._main_wait_timeout)

    def _do_register(self, offer: dict, fetch_callback: FetchCallback) -> bool:
        """NSPasteboardItem(들)에 data provider 지연 등록 (메인 스레드에서)."""
        kind = offer.get("kind")
        n = 1 if kind == KIND_IMAGE else max(1, len(offer.get("items") or []))
        types = self._types_for_kind(kind)
        reg = {"offer": offer, "cb": fetch_callback, "cache": None,
               "n": n, "served": set(), "clear_pending": False}
        providers, items = [], []
        for i in range(n):
            prov = _Provider.alloc().initWithBackend_key_(self, i)
            prov._reg = reg
            item = NSPasteboardItem.alloc().init()
            if not item.setDataProvider_forTypes_(prov, types):
                logger.warning("macOS lazy: setDataProvider_forTypes_ 실패")
                return False
            providers.append(prov)
            items.append(item)
        # 상태 교체는 pasteboard 교체와 같은 메인 스레드 구간에서 — _provide 도 메인에서
        # 돌므로 둘 사이에 이전 항목의 콜백이 끼어 새 offer 를 읽는 일이 없다.
        with self._lock:
            self._reg = reg
            self._offer = offer
            self._fetch_cb = fetch_callback
            self._cache = None
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        wrote = pb.writeObjects_(items)
        self._providers = providers  # GC 방지
        self._items = items
        # 등록 직후 changeCount 기록 (owns_clipboard 비교 기준)
        with self._lock:
            self._change_count = pb.changeCount()
        return bool(wrote)

    def _types_for_kind(self, kind: str) -> list:
        if kind == KIND_IMAGE:
            return [_TYPE_PNG]
        return [_TYPE_FILE_URL]  # KIND_FILE

    def _provide(self, item, type_, key, reg=None) -> None:
        """provideDataForType 콜백 본체 (메인 스레드). fetch→직렬화→setData.

        reg = 이 항목이 속한 등록. 현재 등록일 때만 내준다 — 해제(clear)·교체된 등록의 항목은
        아무것도 안 준다. 붙여넣기 도중 온 clear() 는 미뤄지므로(clear 참조) 한 번의
        붙여넣기(항목별 콜백 여러 번)는 끝까지 같은 fetch 결과로 채워진다.
        """
        with self._lock:
            if reg is None:
                reg = self._reg
            if reg is None or reg is not self._reg:
                return
            cache = reg["cache"]
        offer, cb = reg["offer"], reg["cb"]
        if cache is None and cb is None:
            return
        kind = offer.get("kind")

        try:
            if cache is None:
                with self._lock:
                    self._providing = True
                    self._fetching_reg = reg
                fetched = None
                try:
                    fetched = cb(offer.get("offer_id"))
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"macOS lazy fetch 실패 — 미제공(→fallback): {e}")
                finally:
                    with self._lock:
                        self._providing = False
                        self._fetching_reg = None
                        if fetched is not None:
                            reg["cache"] = fetched
                            self._cache = fetched
                        drain_pending = reg.get("clear_pending", False)
                        if drain_pending and fetched is None:
                            self._finish_drain_locked(reg)  # 내줄 결과가 없으니 바로 해제
                if fetched is None:
                    return
                if drain_pending:
                    # 나머지 항목 요청이 안 와도(붙여넣는 앱이 첫 항목만 읽음) 해제되게
                    t = threading.Timer(self._drain_timeout, self._finish_drain, args=(reg,))
                    t.daemon = True
                    t.start()
                cache = fetched
            self._serve(item, type_, key, kind, cache)
        finally:
            with self._lock:
                reg["served"].add(key)
                if reg.get("clear_pending") and len(reg["served"]) >= reg["n"]:
                    self._finish_drain_locked(reg)

    def _serve(self, item, type_, key, kind, cache) -> None:
        """fetch 결과 → 이 항목의 UTI 데이터로 setData."""
        if not isinstance(cache, FetchedContent):
            return
        data = None
        if kind == KIND_IMAGE and type_ == _TYPE_PNG:
            data = cache.data
        elif kind == KIND_FILE and type_ == _TYPE_FILE_URL:
            paths = cache.paths
            if isinstance(key, int) and 0 <= key < len(paths):
                url = NSURL.fileURLWithPath_(paths[key])
                s = url.absoluteString()
                if s is not None:
                    data = s.encode("utf-8")
        if not data:
            return
        try:
            ns = NSData.dataWithBytes_length_(data, len(data))
            item.setData_forType_(ns, type_)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"macOS lazy setData 실패: {e}")
