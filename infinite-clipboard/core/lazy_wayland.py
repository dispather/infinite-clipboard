"""
core/lazy_wayland.py — Wayland lazy clipboard 백엔드 (ext-data-control-v1 data source).

`core.lazy_clipboard.LazyClipboardProvider` 의 Wayland 구현. spikes/lazy-clipboard/
wayland_spike.py 의 검증된 메커니즘(ext_data_control_manager_v1 으로 selection 소유 +
paste 시 컴포지터가 data source 의 `send(mime, fd)` 호출 → 그 안에서 lazy fetch 한
바이트를 fd 로 write)을 프로덕션화한다. `lazy_x11.py` 와 동형 인터페이스.

ext-data-control-v1 은 클립보드 매니저용 프로토콜이라 일반 wl_data_device 와 달리
입력 focus serial 이 필요 없다 — 백그라운드(트레이) 프로세스가 selection 을 소유할 수
있는 lazy clipboard 의 전제다. wl_data_device 였다면 키보드/포인터 focus 가 없는 우리
프로세스는 set_selection 이 거부됐을 것.

스파이크 대비 추가: 실제 파일 매니저 paste 가 요구하는 타깃을 서빙한다.
  - 이미지(kind=image): `image/png` ← FetchedContent.data (PNG 바이트)
  - 파일(kind=file):    `text/uri-list` + `x-special/gnome-copied-files`
                        ← FetchedContent.paths 의 file:// URI

── 스레딩 (pywayland Display 는 thread-safe 아님) ──────────────────────
**모든 Display I/O 는 단일 이벤트 스레드(_loop)에서만** 수행한다 (X11 백엔드와 동일
원칙). register_offer 는 메인 스레드에서 호출되므로 직접 Display 를 만지지 않고 커맨드를
큐(_pending)에 넣고, 이벤트 스레드가 그것을 집어 data source 생성 + set_selection 한다
(결과는 Event 로 동기 반환).

── fetch 동기성 (Rec 2) ────────────────────────────────────────────────
`send` 핸들러 안에서 fetch_callback 을 동기 호출하므로 그동안 이벤트 dispatch 가
블록된다 (스파이크 §5-2 가 경고한 더블홉 hang 위험 지점). fetch 가 예외/타임아웃이면
빈 바이트를 fd 로 내주고 → 붙여넣는 앱은 빈 결과 → main 이 받기 fallback 으로 우회.
대용량 fd write 도 reader(paste 앱)가 드레인할 때까지 이 스레드를 블록한다(파이프
backpressure) — provider 별 블로킹 한계는 실측·문서화 대상 (Task 5 step 4/6).

규칙 #1: UI 무관. 규칙 #9: stop() 은 종료 race 를 silent 처리.
"""

import logging
import os
import select
import threading
from typing import Optional
from urllib.parse import quote

from core.lazy_clipboard import (
    LazyClipboardProvider, FetchedContent, FetchCallback,
    KIND_FILE, KIND_IMAGE,
)

# pywayland + vendored 바인딩 — 부재 시 ImportError → 팩토리가 잡아 None (fallback)
from pywayland.client import Display
from core._wayland_proto.wayland import WlSeat
from core._wayland_proto.ext_data_control_v1 import ExtDataControlManagerV1

logger = logging.getLogger(__name__)

# 서빙 MIME (X11 atom 과 달리 Wayland 는 문자열 그대로)
_MIME_PNG = "image/png"
_MIME_URILIST = "text/uri-list"
_MIME_GNOME = "x-special/gnome-copied-files"


def _path_to_uri(path: str) -> str:
    """절대경로 → file:// URI (공백/한글 등 percent-encoding)."""
    return "file://" + quote(os.path.abspath(path))


class WaylandLazyProvider(LazyClipboardProvider):
    """ext-data-control selection 을 소유하고 paste(send) 에 lazy 응답."""

    def __init__(self):
        # Display 연결 — WAYLAND_DISPLAY 미설정/연결 실패 시 예외 (팩토리가 잡음)
        self._dpy = Display()
        self._dpy.connect()

        self._lock = threading.Lock()
        self._offer: Optional[dict] = None
        self._fetch_cb: Optional[FetchCallback] = None
        self._cache: Optional[FetchedContent] = None
        # register_offer 커맨드 큐 (메인 → 이벤트 스레드): (offer, cb, done_event, result)
        self._pending = None
        self._source = None  # 현재 ext_data_control_source_v1 (이벤트 스레드 소유)

        # globals (이벤트 스레드가 바인딩)
        self._seat = None
        self._manager = None
        self._device = None
        self._bind_ok = False

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
        # 이벤트 스레드가 selection 을 잡고 결과를 채울 때까지 대기 (graceful 타임아웃)
        done.wait(timeout=2.0)
        return result["ok"]

    def clear(self) -> None:
        # 이벤트 스레드는 계속 돌되 offer=None 이면 send 를 빈 바이트로 응답.
        # (selection 소유는 유지 — paste 는 빈 결과를 즉시 받아 hang 하지 않음)
        with self._lock:
            self._offer = None
            self._fetch_cb = None
            self._cache = None

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=1.5)
        try:
            self._dpy.disconnect()
        except Exception:
            pass  # 규칙 #9: 종료 race silent

    # ── 이벤트 스레드 (유일한 Display 사용자) ──────────────────────────

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="wayland-lazy", daemon=True,
        )
        self._thread.start()

    def _bind_globals(self) -> None:
        """registry roundtrip 으로 wl_seat + ext_data_control_manager_v1 바인딩.

        manager 미광고(구버전/zwlr 전용 컴포지터)면 _bind_ok=False → register_offer
        가 False 반환 → main fallback (크래시 없음).
        """
        state = {"seat": None, "manager": None}
        registry = self._dpy.get_registry()

        def on_global(reg, name, iface, version):
            if iface == "wl_seat":
                state["seat"] = reg.bind(name, WlSeat, min(version, 1))
            elif iface == "ext_data_control_manager_v1":
                state["manager"] = reg.bind(name, ExtDataControlManagerV1, min(version, 1))

        registry.dispatcher["global"] = on_global
        self._dpy.roundtrip()
        self._seat = state["seat"]
        self._manager = state["manager"]
        self._bind_ok = self._seat is not None and self._manager is not None
        if self._bind_ok:
            self._device = self._manager.get_data_device(self._seat)
        else:
            logger.info(
                "Wayland lazy: ext_data_control_manager_v1 미광고 — fallback "
                "(manager=%s seat=%s)",
                self._manager is not None, self._seat is not None,
            )

    def _loop(self) -> None:
        try:
            self._bind_globals()
        except Exception as e:
            logger.warning(f"Wayland lazy: 바인딩 실패 — fallback: {e}")
            # 대기 중인 register_offer 가 타임아웃 없이 풀리도록 결과 False 로 응답
            self._fail_pending()
            return
        fd = self._dpy.get_fd()
        while not self._stop.is_set():
            try:
                self._drain_pending()
                self._dpy.flush()
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    self._dpy.dispatch(block=True)  # 데이터 준비됨 → 읽고 dispatch
            except Exception as e:
                if not self._stop.is_set():
                    logger.debug(f"Wayland lazy 이벤트 처리 무시: {e}")
                else:
                    return

    def _fail_pending(self) -> None:
        """바인딩 실패 시 대기 중 register_offer 커맨드를 ok=False 로 즉시 응답."""
        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is not None:
            _, _, done, result = pending
            result["ok"] = False
            done.set()

    def _drain_pending(self) -> None:
        """register_offer 커맨드를 이벤트 스레드에서 처리 (Display I/O 격리).

        새 data source 를 만들어 set_selection 한다. 직전 source 는 컴포지터가
        `cancelled` 로 회수 → `_on_cancelled` 가 destroy. supersede race 가드:
        set_selection/roundtrip **전에** self._source 를 새 source 로 교체해, 회수되는
        옛 source 의 cancelled 가 방금 등록한 offer 를 지우지 않게 한다.
        """
        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is None:
            return
        offer, cb, done, result = pending
        if not self._bind_ok:
            result["ok"] = False
            done.set()
            return
        with self._lock:
            self._offer = offer
            self._fetch_cb = cb
            self._cache = None
        try:
            source = self._manager.create_data_source()
            source.dispatcher["send"] = self._on_send
            source.dispatcher["cancelled"] = self._on_cancelled
            for mime in self._mimes_for_kind(offer.get("kind")):
                source.offer(mime)
            # set_selection 전에 현재 source 교체 (cancelled race 가드)
            with self._lock:
                self._source = source
            self._device.set_selection(source)
            self._dpy.roundtrip()
            result["ok"] = True
        except Exception as e:
            logger.warning(f"Wayland lazy: selection 설정 실패: {e}")
            result["ok"] = False
        done.set()

    def _mimes_for_kind(self, kind: str) -> list:
        if kind == KIND_IMAGE:
            return [_MIME_PNG]
        return [_MIME_URILIST, _MIME_GNOME]  # KIND_FILE

    def _on_cancelled(self, source) -> None:
        # 다른 source 가 selection 을 가져감 (로컬 복사 / 새 offer 가 우리 옛 source 회수).
        # 현재 source 가 회수된 경우에만 offer 상태를 비운다 (새 offer 보호).
        with self._lock:
            if source is self._source:
                self._offer = None
                self._cache = None
                self._source = None
        try:
            source.destroy()
        except Exception:
            pass  # 규칙 #9

    def _serialized_for_mime(self, mime: str) -> Optional[bytes]:
        """요청 mime 에 맞는 바이트 생성 (fetch 1회 캐시). 미지원/실패 시 None."""
        with self._lock:
            offer = self._offer
            cb = self._fetch_cb
            cache = self._cache
        if offer is None or cb is None:
            return None
        kind = offer.get("kind")

        if cache is None:
            # fetch 는 lock 밖에서 (네트워크 — clear() 를 막지 않도록)
            try:
                fetched = cb(offer.get("offer_id"))
            except Exception as e:
                logger.warning(f"Wayland lazy fetch 실패 — 빈 결과(→fallback): {e}")
                return None
            with self._lock:
                if self._offer is offer:  # fetch 도중 supersede 안 됐으면 캐시
                    self._cache = fetched
            cache = fetched

        if not isinstance(cache, FetchedContent):
            return None
        if kind == KIND_IMAGE and mime == _MIME_PNG:
            return cache.data
        if kind == KIND_FILE and mime in (_MIME_URILIST, _MIME_GNOME):
            uris = [_path_to_uri(p) for p in cache.paths]
            if mime == _MIME_URILIST:
                return ("\r\n".join(uris) + "\r\n").encode("utf-8")
            # x-special/gnome-copied-files: "copy\n<uri>\n<uri>"
            return ("copy\n" + "\n".join(uris)).encode("utf-8")
        return None

    def _on_send(self, _source, mime, fd) -> None:
        # ← paste 시점에 컴포지터가 호출 (이벤트 스레드 안에서 동기). fetch→fd write.
        # 실패/미지원이면 빈 바이트를 내주고 fd 를 닫는다 → 붙여넣는 앱은 빈 결과
        # → main 이 받기 fallback 으로 우회 (Wayland 는 X11 처럼 요청 거부가 없으므로
        #    "빈 데이터 + close" 가 graceful 동치).
        try:
            data = self._serialized_for_mime(mime)
            if data is None:
                data = b""
            with os.fdopen(fd, "wb") as f:
                f.write(data)
        except Exception as e:
            logger.warning(f"Wayland lazy send 처리 실패: {e}")
            try:
                os.close(fd)
            except OSError:
                pass  # 규칙 #9
