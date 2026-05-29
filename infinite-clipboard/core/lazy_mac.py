"""
core/lazy_mac.py — macOS lazy clipboard 백엔드 (NSPasteboard data provider).

`core.lazy_clipboard.LazyClipboardProvider` 의 macOS 구현. spikes/lazy-clipboard/
macos_spike.py 의 검증된 메커니즘(NSPasteboardItem 에 `setDataProvider:forTypes:` 로
지연 등록 → paste 시 `pasteboard:item:provideDataForType:` 콜백 발화 → 그 안에서 fetch 한
바이트 제공)을 프로덕션화한다. `lazy_x11.py`/`lazy_win.py` 와 동형 인터페이스.

── S0 검증 결과 (2026-05-29) ────────────────────────────────────────────
data lazy provide 는 대용량(512KB)+이미지(public.png 1MB) 모두 PASS(파이프 데드락 fix 후).
파일은 file promise(NSFilePromiseProvider)가 Finder drag-drop 필요라 NEEDS-MANUAL 이었으나,
**`public.file-url` data-lazy** 로 우회한다(사용자 결정 2026-05-29) — X11/Wayland 의 uri-list,
Windows 의 CF_HDROP 와 같은 "스테이징 경로를 paste 시점에 제공" 모델.
  - 이미지(kind=image): `public.png` 1개 item ← FetchedContent.data
  - 파일(kind=file):   파일 수(offer.items)만큼 item, 각 `public.file-url` ←
                       FetchedContent.paths[i] 의 file:// URL. (paths 는 fetch 시점에야
                       확정되므로 item 수는 offer.items 로, 경로는 콜백에서 index 매핑.)

── 스레딩 / run loop (spec-review CRITICAL #1) ──────────────────────────
data provider 콜백은 **활성 run loop** 가 있어야 fire 한다. **단일 백그라운드 스레드**가
NSApplicationLoad 후 NSRunLoop 를 펌핑하며(앱 메인 스레드는 Tk 루프라 분리), register_offer
는 `_pending` 에 커맨드를 넣고 그 스레드가 매 iteration 집어 NSPasteboardItem 생성 +
writeObjects 한다. (AppKit 은 thread-affine 하나, 한 스레드에서 생성·소유·펌핑을 일관 수행.)

── fetch 동기성 (Rec 2) ────────────────────────────────────────────────
provideDataForType 콜백 안에서 fetch_callback 을 동기 호출 → 그동안 run loop 블록.
실패/타임아웃이면 setData 를 안 해 dataForType 가 None → 붙여넣는 앱은 빈 결과 →
main 이 받기 fallback 으로 우회.

규칙 #1: UI 무관(AppKit 은 OS 클립보드용이지 우리 UI 아님). 규칙 #9: stop() silent.
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
from Foundation import (
    NSObject, NSData, NSURL, NSRunLoop, NSDate, NSDefaultRunLoopMode,
)
from AppKit import NSPasteboard, NSPasteboardItem, NSApplicationLoad

logger = logging.getLogger(__name__)

# 서빙 UTI
_TYPE_PNG = "public.png"
_TYPE_FILE_URL = "public.file-url"  # = NSPasteboardTypeFileURL


# ─── Provider 클래스는 모듈 레벨에서 단 1회 정의 (스파이크 하니스 버그 교훈) ───
# ObjC 클래스는 이름으로 전역 등록되므로 매 등록마다 재정의하면 두 번째가 실패하거나
# stale 상태를 캡처한다. 1회만 정의하고 backend/key 는 인스턴스 속성으로 주입한다.
# protocolNamed 도 1회 해결 (실패 시 비공식 conform 으로 폴백).
try:
    _PROTO = objc.protocolNamed("NSPasteboardItemDataProvider")
    _BASES, _KW = (NSObject,), {"protocols": [_PROTO]}
except Exception:  # 프로토콜 미발견 — 비공식 conform 으로 폴백
    _BASES, _KW = (NSObject,), {}


class _Provider(*_BASES, **_KW):
    """paste 시점에 backend._provide 를 호출하는 NSPasteboardItemDataProvider.

    setDataProvider:forTypes: 는 provider 가 프로토콜을 '공식' conform 해야 성공(YES).
    backend(약참조 아님 — backend 가 self._providers 로 역참조 유지)/key 는 인스턴스 속성.
    key: 이미지=0, 파일=경로 index.
    """

    def initWithBackend_key_(self, backend, key):
        self = objc.super(_Provider, self).init()
        if self is None:
            return None
        self._backend = backend
        self._key = key
        return self

    # 셀렉터: pasteboard:item:provideDataForType:
    def pasteboard_item_provideDataForType_(self, pasteboard, item, type_):
        self._backend._provide(item, type_, self._key)


class MacLazyProvider(LazyClipboardProvider):
    """NSPasteboard 를 지연 소유하고 paste(provideDataForType)에 lazy 응답."""

    def __init__(self):
        self._lock = threading.Lock()
        self._offer: Optional[dict] = None
        self._fetch_cb: Optional[FetchCallback] = None
        self._cache: Optional[FetchedContent] = None
        self._pending = None  # (offer, cb, done_event, result)
        self._providers = []  # GC 방지 ref 유지 (콜백 발화까지 살아있어야)
        self._items = []

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── LazyClipboardProvider 인터페이스 ──────────────────────────────

    def is_supported(self, kind: str) -> bool:
        return kind in (KIND_FILE, KIND_IMAGE)

    def register_offer(self, offer: dict, fetch_callback: FetchCallback) -> bool:
        kind = offer.get("kind") if isinstance(offer, dict) else None
        if not self.is_supported(kind):
            return False
        done = threading.Event()
        result = {"ok": False}
        with self._lock:
            self._pending = (offer, fetch_callback, done, result)
        self._ensure_thread()
        # 런루프 스레드가 NSPasteboard 에 등록하고 결과를 채울 때까지 대기 (graceful 타임아웃)
        done.wait(timeout=2.0)
        return result["ok"]

    def clear(self) -> None:
        # 소유는 유지하되 offer=None → provideDataForType 가 아무것도 안 줘 dataForType=None
        with self._lock:
            self._offer = None
            self._fetch_cb = None
            self._cache = None

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=2.0)

    # ── 런루프 스레드 (NSPasteboard 소유 + 콜백 펌핑) ──────────────────

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="mac-lazy", daemon=True,
        )
        self._thread.start()

    def _loop(self) -> None:
        try:
            NSApplicationLoad()
            rl = NSRunLoop.currentRunLoop()
        except Exception as e:
            logger.warning(f"macOS lazy: run loop 초기화 실패 — fallback: {e}")
            self._fail_pending()
            return
        while not self._stop.is_set():
            try:
                self._drain_pending()
                rl.runMode_beforeDate_(
                    NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05),
                )
            except Exception as e:
                if not self._stop.is_set():
                    logger.debug(f"macOS lazy 런루프 무시: {e}")

    def _fail_pending(self) -> None:
        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is not None:
            _, _, done, result = pending
            result["ok"] = False
            done.set()

    def _drain_pending(self) -> None:
        """register_offer 커맨드 처리: NSPasteboardItem(들)에 data provider 지연 등록."""
        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is None:
            return
        offer, cb, done, result = pending
        kind = offer.get("kind")
        # 파일은 파일 수만큼 item(각 public.file-url), 이미지는 1개(public.png)
        n = 1 if kind == KIND_IMAGE else max(1, len(offer.get("items") or []))
        types = self._types_for_kind(kind)
        with self._lock:
            self._offer = offer
            self._fetch_cb = cb
            self._cache = None
        try:
            providers, items = [], []
            for i in range(n):
                prov = _Provider.alloc().initWithBackend_key_(self, i)
                item = NSPasteboardItem.alloc().init()
                if not item.setDataProvider_forTypes_(prov, types):
                    raise RuntimeError("setDataProvider_forTypes_ 실패")
                providers.append(prov)
                items.append(item)
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            wrote = pb.writeObjects_(items)
            self._providers = providers  # GC 방지
            self._items = items
            result["ok"] = bool(wrote)
        except Exception as e:
            logger.warning(f"macOS lazy: 지연 등록 실패: {e}")
            result["ok"] = False
        done.set()

    def _types_for_kind(self, kind: str) -> list:
        if kind == KIND_IMAGE:
            return [_TYPE_PNG]
        return [_TYPE_FILE_URL]  # KIND_FILE

    def _provide(self, item, type_, key) -> None:
        """provideDataForType 콜백 본체 (런루프 스레드). fetch→직렬화→setData."""
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
            except Exception as e:
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
                # public.file-url 의 바이트 = file:// URL 문자열 (NSURL 이 쓰는 표현)
                url = NSURL.fileURLWithPath_(paths[key])
                s = url.absoluteString()
                if s is not None:
                    data = s.encode("utf-8")
        if not data:
            return
        try:
            ns = NSData.dataWithBytes_length_(data, len(data))
            item.setData_forType_(ns, type_)
        except Exception as e:
            logger.warning(f"macOS lazy setData 실패: {e}")
