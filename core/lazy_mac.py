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
    KIND_FILE, KIND_IMAGE,
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

    backend(역참조는 backend.self._providers 로 유지)/key(이미지=0, 파일=경로 index)는
    인스턴스 속성. 콜백은 메인 스레드 run loop 에서 발화.
    """

    def initWithBackend_key_(self, backend, key):
        self = objc.super(_Provider, self).init()
        if self is None:
            return None
        self._backend = backend
        self._key = key
        return self

    def pasteboard_item_provideDataForType_(self, pasteboard, item, type_):
        self._backend._provide(item, type_, self._key)


class _MainThreadRunner(NSObject):
    """performSelectorOnMainThread 로 임의 콜러블을 메인 스레드에서 동기 실행."""

    def runHolder_(self, holder):
        try:
            holder["result"] = holder["fn"]()
        except Exception as e:  # noqa: BLE001
            holder["err"] = e


class MacLazyProvider(LazyClipboardProvider):
    """NSPasteboard 를 지연 소유하고 paste(provideDataForType)에 lazy 응답."""

    def __init__(self):
        NSApplicationLoad()
        self._runner = _MainThreadRunner.alloc().init()
        self._lock = threading.Lock()
        self._offer: Optional[dict] = None
        self._fetch_cb: Optional[FetchCallback] = None
        self._cache: Optional[FetchedContent] = None
        self._providers = []  # GC 방지 ref 유지 (콜백 발화까지 살아있어야)
        self._items = []
        # 등록 시점의 NSPasteboard changeCount. macOS 는 소유권 상실 콜백이 없어,
        # owns_clipboard 는 현재 changeCount 와 비교해 "그 뒤 아무도 안 썼는지"로 판단한다
        # (사용자가 로컬 복사하면 changeCount 가 올라가 자동으로 소유 아님 처리).
        self._change_count: Optional[int] = None

    # ── LazyClipboardProvider 인터페이스 ──────────────────────────────

    def is_supported(self, kind: str) -> bool:
        return kind in (KIND_FILE, KIND_IMAGE)

    def owns_clipboard(self) -> bool:
        # 활성 offer + 등록 후 pasteboard 가 그대로(changeCount 불변)면 우리가 소유 중.
        # changeCount 가 올라갔으면 다른 곳(로컬 복사 등)이 덮어쓴 것 → 소유 아님.
        with self._lock:
            if self._offer is None or self._change_count is None:
                return False
            expected = self._change_count
        try:
            return NSPasteboard.generalPasteboard().changeCount() == expected
        except Exception:
            return False

    def register_offer(self, offer: dict, fetch_callback: FetchCallback) -> bool:
        kind = offer.get("kind") if isinstance(offer, dict) else None
        if not self.is_supported(kind):
            return False
        with self._lock:
            self._offer = offer
            self._fetch_cb = fetch_callback
            self._cache = None
        try:
            return bool(self._run_on_main(lambda: self._do_register(offer)))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"macOS lazy: 등록 실패 — fallback: {e}")
            return False

    def clear(self) -> None:
        # 소유는 유지하되 offer=None → provideDataForType 가 아무것도 안 줘 dataForType=None
        with self._lock:
            self._offer = None
            self._fetch_cb = None
            self._cache = None
            self._change_count = None

    def stop(self) -> None:
        with self._lock:
            self._offer = None
            self._fetch_cb = None
            self._cache = None
            self._providers = []
            self._items = []

    # ── 메인 스레드 디스패치 ───────────────────────────────────────────

    def _run_on_main(self, fn):
        """fn 을 메인 스레드에서 실행(이미 메인이면 직접). 결과 반환/예외 전파.

        worker 스레드에서 호출 시 메인 run loop 가 펌핑돼야 완료된다(실앱=Tk mainloop).
        """
        if NSThread.isMainThread():
            return fn()
        holder = {"fn": fn, "result": None, "err": None}
        self._runner.performSelectorOnMainThread_withObject_waitUntilDone_(
            "runHolder:", holder, True,
        )
        if holder["err"] is not None:
            raise holder["err"]
        return holder["result"]

    def _do_register(self, offer: dict) -> bool:
        """NSPasteboardItem(들)에 data provider 지연 등록 (메인 스레드에서)."""
        kind = offer.get("kind")
        n = 1 if kind == KIND_IMAGE else max(1, len(offer.get("items") or []))
        types = self._types_for_kind(kind)
        providers, items = [], []
        for i in range(n):
            prov = _Provider.alloc().initWithBackend_key_(self, i)
            item = NSPasteboardItem.alloc().init()
            if not item.setDataProvider_forTypes_(prov, types):
                logger.warning("macOS lazy: setDataProvider_forTypes_ 실패")
                return False
            providers.append(prov)
            items.append(item)
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

    def _provide(self, item, type_, key) -> None:
        """provideDataForType 콜백 본체 (메인 스레드). fetch→직렬화→setData."""
        with self._lock:
            offer = self._offer
            cb = self._fetch_cb
            cache = self._cache
        if offer is None or cb is None:
            return
        kind = offer.get("kind")

        if cache is None:
            try:
                fetched = cb(offer.get("offer_id"))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"macOS lazy fetch 실패 — 미제공(→fallback): {e}")
                return
            with self._lock:
                if self._offer is offer:  # fetch 중 supersede 안 됐으면 캐시
                    self._cache = fetched
            cache = fetched

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
