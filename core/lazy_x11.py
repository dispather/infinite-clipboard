"""
core/lazy_x11.py — X11 lazy clipboard 백엔드 (python-xlib selection owner + INCR).

`core.lazy_clipboard.LazyClipboardProvider` 의 X11 구현. spikes/lazy-clipboard/
x11_spike.py 의 검증된 메커니즘(CLIPBOARD selection 소유 + SelectionRequest 시 lazy
fetch + 대용량 INCR 청크)을 프로덕션화한다.

스파이크 대비 추가: 실제 파일 매니저 paste 가 요구하는 타깃을 서빙한다.
  - 이미지(kind=image): `image/png` ← FetchedContent.data (PNG 바이트)
  - 파일(kind=file):    `text/uri-list` + `x-special/gnome-copied-files`
                        ← FetchedContent.paths 의 file:// URI

── 스레딩 (python-xlib 는 thread-safe 아님) ────────────────────────────
**모든 Display I/O 는 단일 이벤트 스레드(_loop)에서만** 수행한다. register_offer 는
메인 스레드에서 호출되므로 직접 Display 를 만지지 않고 커맨드를 큐(_pending)에 넣고,
이벤트 스레드가 그것을 집어 selection 소유권을 잡는다(결과는 Event 로 동기 반환).
이로써 set_selection_owner(메인) ↔ next_event(이벤트 스레드) 동시 접근을 차단한다.

── fetch 동기성 (Rec 2) ────────────────────────────────────────────────
SelectionRequest 핸들러 안에서 fetch_callback 을 동기 호출하므로 그동안 X 이벤트
처리가 블록된다. fetch_callback 이 예외/타임아웃이면 요청을 NONE 으로 거부 →
requestor(붙여넣는 앱)는 빈 결과 → main 이 받기 fallback 으로 우회.

규칙 #1: UI 무관. 규칙 #9: stop() 은 종료 race 를 silent 처리.
"""

import logging
import os
import threading
import time
from typing import Optional
from urllib.parse import quote

from core.lazy_clipboard import (
    LazyClipboardProvider, FetchedContent, FetchCallback,
    KIND_FILE, KIND_IMAGE,
)

# python-xlib — 부재 시 ImportError → 팩토리가 잡아 None (fallback)
from Xlib import X, display, error, Xatom
from Xlib.protocol import event

logger = logging.getLogger(__name__)

# 한 번에 보낼 INCR 청크 (서버 max-request-size 보다 충분히 작게)
_INCR_CHUNK = 64 * 1024


def _path_to_uri(path: str) -> str:
    """절대경로 → file:// URI (공백/한글 등 percent-encoding)."""
    return "file://" + quote(os.path.abspath(path))


class X11LazyProvider(LazyClipboardProvider):
    """CLIPBOARD selection 을 소유하고 paste(ConvertSelection) 에 lazy 응답."""

    def __init__(self):
        # Display 연결 — DISPLAY 미설정/연결 실패 시 예외 (팩토리가 잡음).
        # 직전 연결이 방금 닫힌 직후라 X 서버가 정리 중이면 최초 handshake 에서
        # 드물게 연결이 리셋된다 (CI Xvfb 관찰) — OpenClipboard 재시도
        # (core/lazy_win.py _open_clipboard_retry) 와 동일한 이유로 짧게 재시도.
        self._dpy = self._connect_retry()
        self._screen = self._dpy.screen()
        self._win = self._screen.root.create_window(
            0, 0, 1, 1, 0, self._screen.root_depth,
            event_mask=(X.PropertyChangeMask | X.StructureNotifyMask),
        )
        # atoms
        self._CLIPBOARD = self._dpy.intern_atom("CLIPBOARD")
        self._TARGETS = self._dpy.intern_atom("TARGETS")
        self._INCR = self._dpy.intern_atom("INCR")
        self._A_PNG = self._dpy.intern_atom("image/png")
        self._A_URILIST = self._dpy.intern_atom("text/uri-list")
        self._A_GNOME = self._dpy.intern_atom("x-special/gnome-copied-files")

        self._lock = threading.Lock()
        self._offer: Optional[dict] = None
        self._fetch_cb: Optional[FetchCallback] = None
        self._cache: Optional[FetchedContent] = None
        # register_offer 커맨드 큐 (메인 → 이벤트 스레드): (offer, cb, done_event, result)
        self._pending = None
        # 진행 중 INCR: {(requestor_id, prop): (data, offset, target_atom)}
        self._incr = {}

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def _connect_retry(retries: int = 5, delay: float = 0.1):
        last: Optional[Exception] = None
        for _ in range(retries):
            try:
                return display.Display()
            except (OSError, error.ConnectionClosedError, error.DisplayConnectionError) as e:
                last = e
                time.sleep(delay)
        raise last if last is not None else OSError("X11 Display 연결 실패")

    # ── LazyClipboardProvider 인터페이스 ──────────────────────────────

    def is_supported(self, kind: str) -> bool:
        return kind in (KIND_FILE, KIND_IMAGE)

    def owns_clipboard(self) -> bool:
        # 활성 offer 가 있으면 우리가 CLIPBOARD selection 을 소유 중. SelectionClear
        # (다른 owner 가 가져감 = 로컬 복사)가 _offer 를 None 으로 비우면 자동 False.
        with self._lock:
            return self._offer is not None

    def register_offer(self, offer: dict, fetch_callback: FetchCallback) -> bool:
        kind = offer.get("kind") if isinstance(offer, dict) else None
        if not self.is_supported(kind):
            return False
        done = threading.Event()
        result = {"ok": False}
        with self._lock:
            self._pending = (offer, fetch_callback, done, result)
        self._ensure_thread()
        # 이벤트 스레드가 소유권을 잡고 결과를 채울 때까지 대기 (graceful 타임아웃)
        done.wait(timeout=2.0)
        return result["ok"]

    def clear(self) -> None:
        # 이벤트 스레드는 계속 돌되 offer=None 이면 모든 요청을 NONE 으로 거부.
        # (소유권은 유지 — paste 는 빈 결과를 즉시 받아 hang 하지 않음)
        with self._lock:
            self._offer = None
            self._fetch_cb = None
            self._cache = None
            self._incr = {}

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=1.5)
        try:
            self._dpy.close()
        except Exception:
            pass  # 규칙 #9: 종료 race silent

    # ── 이벤트 스레드 (유일한 Display 사용자) ──────────────────────────

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="x11-lazy", daemon=True,
        )
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._drain_pending()
                if self._dpy.pending_events():
                    ev = self._dpy.next_event()
                    if ev.type == X.SelectionRequest:
                        self._handle_request(ev)
                    elif ev.type == X.PropertyNotify:
                        self._handle_property_notify(ev)
                    elif ev.type == X.SelectionClear:
                        # 다른 owner 가 CLIPBOARD 를 가져감 (로컬 복사 등) → offer 비활성
                        with self._lock:
                            self._offer = None
                            self._cache = None
                else:
                    time.sleep(0.005)
            except Exception as e:
                if not self._stop.is_set():
                    logger.debug(f"X11 lazy 이벤트 처리 무시: {e}")

    def _drain_pending(self) -> None:
        """register_offer 커맨드를 이벤트 스레드에서 처리 (Display I/O 격리)."""
        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is None:
            return
        offer, cb, done, result = pending
        with self._lock:
            self._offer = offer
            self._fetch_cb = cb
            self._cache = None
            self._incr = {}
        try:
            self._win.set_selection_owner(self._CLIPBOARD, X.CurrentTime)
            self._dpy.flush()
            owned = self._dpy.get_selection_owner(self._CLIPBOARD) == self._win
        except Exception as e:
            logger.warning(f"X11 lazy: CLIPBOARD 소유 실패: {e}")
            owned = False
        result["ok"] = owned
        done.set()

    def _targets_for_kind(self, kind: str) -> list:
        if kind == KIND_IMAGE:
            return [self._A_PNG]
        return [self._A_URILIST, self._A_GNOME]  # KIND_FILE

    def _serialized_for_target(self, target_atom: int) -> Optional[bytes]:
        """요청 target 에 맞는 바이트 생성 (fetch 1회 캐시). 미지원/실패 시 None."""
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
                logger.warning(f"X11 lazy fetch 실패 — 요청 거부(→fallback): {e}")
                return None
            with self._lock:
                if self._offer is offer:  # fetch 도중 supersede 안 됐으면 캐시
                    self._cache = fetched
            cache = fetched

        if not isinstance(cache, FetchedContent):
            return None
        if kind == KIND_IMAGE and target_atom == self._A_PNG:
            return cache.data
        if kind == KIND_FILE and target_atom in (self._A_URILIST, self._A_GNOME):
            uris = [_path_to_uri(p) for p in cache.paths]
            if target_atom == self._A_URILIST:
                return ("\r\n".join(uris) + "\r\n").encode("utf-8")
            # x-special/gnome-copied-files: "copy\n<uri>\n<uri>"
            return ("copy\n" + "\n".join(uris)).encode("utf-8")
        return None

    def _log_requestor(self, req) -> None:
        """진단(3.0.2): SelectionRequest(=누군가 CLIPBOARD 를 읽음) 요청자를 식별.

        외부 reader(plasmashell/클립보드 매니저 등)가 등록 직후 읽으면 그게 곧 전체
        fetch 를 강제 — WM_CLASS/PID 로 그 주체를 특정해 추측을 끝내기 위함. 동작 변경 없음.
        """
        try:
            r = req.requestor
            wm_class = None
            try:
                wm_class = r.get_wm_class()
            except Exception:
                pass
            pid = None
            try:
                p = r.get_full_property(self._dpy.intern_atom("_NET_WM_PID"), Xatom.CARDINAL)
                if p is not None and p.value is not None and len(p.value):
                    pid = int(p.value[0])
            except Exception:
                pass
            try:
                target_name = self._dpy.get_atom_name(req.target)
            except Exception:
                target_name = req.target
            logger.debug(
                f"[lazy-diag] selection requestor: our_pid={os.getpid()} "
                f"target={target_name} wm_class={wm_class} pid={pid}"
            )
        except Exception as e:
            logger.debug(f"[lazy-diag] requestor 식별 실패: {e}")

    def _handle_request(self, req) -> None:
        self._log_requestor(req)  # 진단(3.0.2): 누가 selection 을 읽었나
        requestor = req.requestor
        target = req.target
        prop = req.property if req.property != X.NONE else req.target

        with self._lock:
            offer = self._offer
        if offer is None:
            self._send_notify(req, X.NONE)
            return

        if target == self._TARGETS:
            targets = [self._TARGETS] + self._targets_for_kind(offer.get("kind"))
            requestor.change_property(prop, Xatom.ATOM, 32, targets)
            self._send_notify(req, prop)
            return

        data = self._serialized_for_target(target)
        if data is None:
            self._send_notify(req, X.NONE)
            return

        max_len = self._dpy.display.info.max_request_length * 4
        if len(data) <= max_len - 1024:
            # 단발 전송
            requestor.change_property(prop, target, 8, data)
            self._send_notify(req, prop)
        else:
            # INCR 시작 — 총 길이(하한)를 INCR property 로 먼저
            requestor.change_attributes(event_mask=X.PropertyChangeMask)
            requestor.change_property(prop, self._INCR, 32, [len(data)])
            self._incr[(requestor.id, prop)] = (data, 0, target)
            self._send_notify(req, prop)

    def _handle_property_notify(self, ev) -> None:
        # INCR: requestor 가 property 를 지웠으면(Delete) 다음 청크 전송
        if ev.state != X.PropertyDelete:
            return
        key = (ev.window.id, ev.atom)
        entry = self._incr.get(key)
        if entry is None:
            return
        data, off, target = entry
        chunk = data[off:off + _INCR_CHUNK]
        ev.window.change_property(ev.atom, target, 8, chunk)
        self._dpy.flush()
        if not chunk:
            del self._incr[key]  # 0바이트 청크 = INCR 종료
        else:
            self._incr[key] = (data, off + len(chunk), target)

    def _send_notify(self, req, prop) -> None:
        ev = event.SelectionNotify(
            time=req.time, requestor=req.requestor, selection=req.selection,
            target=req.target, property=prop,
        )
        req.requestor.send_event(ev)
        self._dpy.flush()
