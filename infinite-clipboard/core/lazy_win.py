"""
core/lazy_win.py — Windows lazy clipboard 백엔드 (classic delayed rendering).

`core.lazy_clipboard.LazyClipboardProvider` 의 Windows 구현. spikes/lazy-clipboard/
windows_spike.py 의 delayed-render 메커니즘(SetClipboardData(fmt, NULL) 로 지연 등록 →
paste 시 OS 가 owner 에게 WM_RENDERFORMAT 송신 → 핸들러에서 fetch 한 바이트 제공)을
프로덕션화한다. `lazy_x11.py`/`lazy_wayland.py` 와 동형 인터페이스.

── 스파이크 대비 두 가지 프로덕션 전환 (S0 미결 = 대용량 NULL 대응) ──────
1) **메시지 루프**: 스파이크는 daemon 스레드에서 `PumpWaitingMessages()` + sleep **폴링**
   이었다. `WM_RENDERFORMAT` 는 *SENT* 메시지라 owner 스레드가 메시지 검색 상태(GetMessage)
   에 있어야 즉시 처리된다. 폴링 루프는 sent 메시지 처리 타이밍이 불안정해 대용량에서
   `GetClipboardData NULL` 을 유발한 것으로 의심됐다(S0 thin 하니스 아티팩트). →
   프로덕션은 **블로킹 `PumpMessages()`** 로 항상 메시지 검색 상태를 유지한다.
2) **실제 포맷**: 스파이크는 round-trip 정확도를 위해 등록 커스텀 포맷 + 4바이트 길이
   프리픽스를 썼다. 실제 소비자(Explorer/이미지 앱)는 진짜 포맷을 기대하므로:
   - 파일: **CF_HDROP** (DROPFILES 구조 + UTF-16LE 더블널 경로 목록) + 즉시 등록
     "Preferred DropEffect"=copy. ← FetchedContent.paths
   - 이미지: 등록 **"PNG"** (raw PNG 바이트, opaque HGLOBAL). ← FetchedContent.data
   프리픽스 없음 — GlobalSize 라운딩 패딩은 PNG 디코더/DROPFILES 더블널이 무시.

── 스레딩 ───────────────────────────────────────────────────────────────
**창 + 클립보드 소유 + 메시지 루프는 단일 메시지 스레드(_loop)에서만**. 창은 그 스레드에서
생성돼야 한다(스레드 affinity). register_offer(메인 스레드)는 `_pending` 에 커맨드를 넣고
`WM_APP` 를 PostMessage 해 메시지 스레드를 깨운다 → 메시지 스레드가 OpenClipboard +
SetClipboardData(NULL) 로 지연 등록(결과는 Event 동기 반환). WM_RENDERFORMAT 핸들러는
클립보드를 열지 않는다(이미 system 이 열어둠).

── fetch 동기성 (Rec 2) ────────────────────────────────────────────────
WM_RENDERFORMAT 안에서 fetch_callback 을 동기 호출 → 그동안 메시지 처리 블록.
실패/타임아웃이면 아무 데이터도 제공하지 않아 GetClipboardData 가 NULL → 붙여넣는 앱은
빈 결과 → main 이 받기 fallback 으로 우회.

규칙 #1: UI 무관. 규칙 #9: stop() 은 종료 race 를 silent 처리.
규칙: 함정 #2 (pywin32 는 Windows 전용) — 모듈 top import 는 비-Windows 에서 실패하고,
팩토리(get_lazy_provider)가 잡아 None 반환 → fallback. py_compile 은 영향 없음.
"""

import ctypes
import itertools
import logging
import os
import struct
import threading
import time
from typing import Optional

from core.lazy_clipboard import (
    LazyClipboardProvider, FetchedContent, FetchCallback,
    KIND_FILE, KIND_IMAGE,
)
# 모니터(WindowsClipboardHandler)와 OpenClipboard 경합을 직렬화하는 프로세스 전역 락.
# clipboard_manager 는 비-Windows 안전하게 import 되고 lazy_win 보다 먼저 로드돼 있다.
from core.clipboard_manager import WINDOWS_CLIPBOARD_LOCK

# pywin32 + Win32 API — 부재/비-Windows 면 ImportError/AttributeError → 팩토리가 잡아 None
import win32clipboard
import win32con
import win32gui

logger = logging.getLogger(__name__)

# ── 64-bit 안전 HGLOBAL ctypes 설정 (스파이크와 동일 계약) ──────────────
# 핸들은 64-bit 라 restype/argtypes 명시 필수 — 미설정 시 c_int truncate 로 깨진다.
_k32 = ctypes.windll.kernel32
_u32 = ctypes.windll.user32
_GMEM_MOVEABLE = 0x0002
_k32.GlobalAlloc.restype = ctypes.c_void_p
_k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalLock.argtypes = [ctypes.c_void_p]
_k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_u32.SetClipboardData.restype = ctypes.c_void_p
_u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

# 등록 포맷 (RegisterClipboardFormat 은 같은 이름이면 프로세스 간 동일 ID)
_FMT_PNG = win32clipboard.RegisterClipboardFormat("PNG")
_FMT_DROPEFFECT = win32clipboard.RegisterClipboardFormat("Preferred DropEffect")
_DROPEFFECT_COPY = 1  # DROPEFFECT_COPY

_WM_APP_DRAIN = win32con.WM_APP + 1  # register_offer 커맨드 드레인 트리거

# 윈도우 클래스명 고유화 카운터 — RegisterClass 는 프로세스 전역이라 같은 이름을 재사용하면
# 첫 인스턴스의 WndProc 에 묶인다. provider 재생성/다중 인스턴스 시 새 창이 옛(죽은) WndProc
# 로 메시지를 보내 WM_APP 드레인이 안 돼 register_offer 가 타임아웃(=False). 인스턴스별 고유
# 이름으로 회피.
_CLASS_COUNTER = itertools.count()


def build_dropfiles(paths) -> bytes:
    """절대경로 목록 → CF_HDROP 의 DROPFILES 바이트 (UTF-16LE 더블널 종료).

    DROPFILES 헤더(20B): pFiles(=20) + POINT(x,y) + fNC(0) + fWide(1=wide).
    이어서 각 경로 UTF-16LE + 널(\\x00\\x00), 마지막에 추가 널(목록 종료).
    """
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)  # pFiles, pt.x, pt.y, fNC, fWide
    body = b""
    for p in paths:
        body += str(p).encode("utf-16-le") + b"\x00\x00"
    body += b"\x00\x00"  # 목록 종료 널
    return header + body


def _hglobal(data: bytes) -> int:
    """data 를 GMEM_MOVEABLE HGLOBAL 로 복사해 핸들 반환 (프리픽스 없음 — 진짜 포맷).

    SetClipboardData 성공 시 소유권이 시스템으로 넘어가므로 호출자는 free 하지 않는다.
    """
    size = len(data)
    h = _k32.GlobalAlloc(_GMEM_MOVEABLE, size)
    if not h:
        raise OSError(f"GlobalAlloc 실패 (size={size})")
    p = _k32.GlobalLock(h)
    if not p:
        raise OSError("GlobalLock 실패")
    if size:
        ctypes.memmove(p, data, size)
    _k32.GlobalUnlock(h)
    return h


class WindowsLazyProvider(LazyClipboardProvider):
    """delayed render 로 클립보드를 지연 소유하고 paste(WM_RENDERFORMAT)에 lazy 응답."""

    def __init__(self):
        # 인스턴스별 고유 클래스명 (위 _CLASS_COUNTER 주석 참조)
        self._class_name = f"InfiniteClipboardLazyWnd_{next(_CLASS_COUNTER)}"
        self._atom = None
        self._lock = threading.Lock()
        self._offer: Optional[dict] = None
        self._fetch_cb: Optional[FetchCallback] = None
        self._cache: Optional[FetchedContent] = None
        self._pending = None  # (offer, cb, done_event, result)

        self._hwnd = None
        self._hwnd_ready = threading.Event()
        self._wndproc_ref = None  # WndProc 콜백 GC 방지 참조 유지
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── LazyClipboardProvider 인터페이스 ──────────────────────────────

    def is_supported(self, kind: str) -> bool:
        return kind in (KIND_FILE, KIND_IMAGE)

    def owns_clipboard(self) -> bool:
        # 활성 offer 가 등록돼 있으면 우리가 클립보드를 소유 중. WM_DESTROYCLIPBOARD
        # (다른 앱/로컬 복사가 소유권 회수)가 _offer 를 None 으로 비우면 자동 False.
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
        # 창 생성 완료 후 메시지 스레드를 깨워 지연 등록 수행
        if self._hwnd_ready.wait(timeout=2.0) and self._hwnd:
            win32gui.PostMessage(self._hwnd, _WM_APP_DRAIN, 0, 0)
        # _drain_pending 의 OpenClipboard 재시도(최대 ~1s)를 포함하도록 넉넉히 대기
        done.wait(timeout=4.0)
        return result["ok"]

    def clear(self) -> None:
        # 소유는 유지하되 offer=None → WM_RENDERFORMAT 가 아무것도 제공 안 함
        # (paste 는 NULL = 빈 결과를 즉시 받아 hang 없이 fallback)
        with self._lock:
            self._offer = None
            self._fetch_cb = None
            self._cache = None

    def stop(self) -> None:
        self._stop.set()
        hwnd = self._hwnd
        if hwnd:
            try:
                # WM_CLOSE → DefWindowProc → DestroyWindow → WM_DESTROY → PostQuitMessage
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass  # 규칙 #9
        t = self._thread
        if t is not None:
            t.join(timeout=1.5)
        # 고유 클래스 등록 해제 (인스턴스별 누수 방지). 창 파괴 후라야 성공.
        if self._atom is not None:
            try:
                win32gui.UnregisterClass(self._class_name, None)
            except Exception:
                pass  # 규칙 #9 (창 잔존/이미 해제 — 무해)

    # ── 메시지 스레드 (창 + 클립보드 소유 + 블로킹 루프) ────────────────

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._hwnd_ready.clear()
        self._thread = threading.Thread(
            target=self._loop, name="win-lazy", daemon=True,
        )
        self._thread.start()

    def _loop(self) -> None:
        try:
            self._create_window()
        except Exception as e:
            logger.warning(f"Windows lazy: 창 생성 실패 — fallback: {e}")
            self._fail_pending()
            self._hwnd_ready.set()  # register_offer 대기 해제
            return
        self._hwnd_ready.set()
        try:
            win32gui.PumpMessages()  # 블로킹 — WM_QUIT 까지 (sent 메시지 즉시 처리)
        except Exception as e:
            if not self._stop.is_set():
                logger.debug(f"Windows lazy 메시지 루프 종료: {e}")

    def _create_window(self) -> None:
        wc = win32gui.WNDCLASS()
        wc.lpszClassName = self._class_name
        self._wndproc_ref = self._wndproc  # 참조 유지 (GC 방지)
        wc.lpfnWndProc = self._wndproc_ref
        try:
            self._atom = win32gui.RegisterClass(wc)
            atom = self._atom
        except Exception:
            atom = self._class_name  # 이미 등록됨(고유명이라 정상 경로선 미발생)
        self._hwnd = win32gui.CreateWindowEx(
            0, atom, "infinite-clipboard-lazy", 0, 0, 0, 0, 0,
            win32con.HWND_MESSAGE, 0, 0, None,
        )

    def _fail_pending(self) -> None:
        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is not None:
            _, _, done, result = pending
            result["ok"] = False
            done.set()

    # ── WndProc (메시지 스레드에서만 실행) ─────────────────────────────

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == _WM_APP_DRAIN:
            self._drain_pending()
            return 0
        if msg == win32con.WM_RENDERFORMAT:
            self._render(wparam)  # wparam = 요청된 포맷
            return 0
        if msg == win32con.WM_RENDERALLFORMATS:
            # 앱 종료 시 클립보드 영속용 — lazy 는 종료 시 offer 소멸이 자연스러워 skip.
            return 0
        if msg == win32con.WM_DESTROYCLIPBOARD:
            # 소유권 상실(다른 앱 복사 / 우리 EmptyClipboard) → offer 비활성
            with self._lock:
                self._offer = None
                self._cache = None
            return 0
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _open_clipboard_retry(self, retries: int = 8, delay: float = 0.1) -> None:
        """OpenClipboard 를 재시도로 감싼다 (실패 시 마지막 예외 re-raise).

        클립보드는 프로세스 단일 소유라 모니터/다른 앱과 일시 경합 시 OpenClipboard 가
        ERROR_ACCESS_DENIED(5) 로 실패한다. WINDOWS_CLIPBOARD_LOCK 으로 앱 내부(모니터)
        경합은 직렬화하지만, 외부 앱 경합은 backoff 재시도로 견딘다. 단 한 번의 일시적
        거부가 offer 를 받기-fallback 으로 떨어뜨리던 버그(2026-05-31 로그)의 핵심 수정.
        clipboard_manager.WindowsClipboardHandler._open_clipboard 와 동일 정책.
        """
        last = None
        for i in range(retries):
            try:
                win32clipboard.OpenClipboard(self._hwnd)
                return
            except Exception as e:
                last = e
                if i < retries - 1:
                    time.sleep(delay)
        raise last if last is not None else OSError("OpenClipboard 실패")

    def _drain_pending(self) -> None:
        """register_offer 커맨드 처리: 지연 포맷 등록 (메시지 스레드 = 클립보드 소유자)."""
        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is None:
            return
        offer, cb, done, result = pending
        try:
            # 모니터와의 내부 경합 직렬화 — 락을 쥔 채 재시도로 OpenClipboard.
            with WINDOWS_CLIPBOARD_LOCK:
                self._open_clipboard_retry()  # 전 재시도 실패 시 예외 → 아래 except → fallback
                try:
                    win32clipboard.EmptyClipboard()  # WM_DESTROYCLIPBOARD(옛 offer) 동기 발생
                    # EmptyClipboard 이후 새 offer 설정 (옛 WM_DESTROYCLIPBOARD 가 새 offer 안 지우게)
                    with self._lock:
                        self._offer = offer
                        self._fetch_cb = cb
                        self._cache = None
                    for fmt in self._formats_for_kind(offer.get("kind")):
                        _u32.SetClipboardData(fmt, None)  # 지연 등록 (NULL = 정상)
                    if offer.get("kind") == KIND_FILE:
                        # 즉시 작은 포맷: copy 의도 (Explorer move/copy 결정)
                        _u32.SetClipboardData(
                            _FMT_DROPEFFECT, _hglobal(struct.pack("<I", _DROPEFFECT_COPY)),
                        )
                    result["ok"] = True
                finally:
                    win32clipboard.CloseClipboard()
        except Exception as e:
            logger.warning(f"Windows lazy: 지연 등록 실패: {e}")
            result["ok"] = False
        done.set()

    def _formats_for_kind(self, kind: str) -> list:
        if kind == KIND_IMAGE:
            return [_FMT_PNG]
        return [win32con.CF_HDROP]  # KIND_FILE

    @staticmethod
    def _exe_for_pid(pid) -> str:
        """진단(3.0.2): PID → 실행 파일 경로 (실패해도 raise 안 함)."""
        try:
            import win32api
            import win32process
            h = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid,
            )
            try:
                return win32process.GetModuleFileNameEx(h, 0)
            finally:
                win32api.CloseHandle(h)
        except Exception as e:
            return f"<exe 조회 실패: {e}>"

    def _log_requester(self, fmt) -> None:
        """진단(3.0.2): WM_RENDERFORMAT(=누군가 클립보드 파일/이미지를 읽음)를 유발한
        프로세스를 로깅한다. 외부 reader(Phone Link/MWB/클립보드 매니저 등)가 등록 직후
        읽으면 그게 곧 전체 fetch 를 강제 — 그 주체를 특정해 추측을 끝내기 위함.
        opener_pid == our_pid 면 우리 자신(모니터), 다르면 외부. 동작 변경 없음.
        """
        info = f"fmt={fmt} our_pid={os.getpid()}"
        try:
            # GetOpenClipboardWindow: paste 요청자가 OpenClipboard 한 윈도우 (NULL 핸들로
            # 열었으면 0). win32gui 위치가 불확실해 ctypes user32 로 직접 호출.
            _u32.GetOpenClipboardWindow.restype = ctypes.c_void_p
            hwnd = _u32.GetOpenClipboardWindow()
            info += f" opener_hwnd={hwnd}"
            if hwnd:
                try:
                    info += f" class={win32gui.GetClassName(hwnd)!r}"
                except Exception:
                    pass
                try:
                    import win32process
                    _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
                    info += f" opener_pid={pid} exe={self._exe_for_pid(pid)}"
                except Exception as e:
                    info += f" pid_err={e}"
        except Exception as e:
            info += f" opener_err={e}"
        logger.debug(f"[lazy-diag] render requester: {info}")

    def _render(self, fmt) -> None:
        """WM_RENDERFORMAT: 요청 포맷 바이트를 생성해 제공 (클립보드 열지 않음)."""
        self._log_requester(fmt)  # 진단(3.0.2): 누가 paste-render 를 유발했나
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
                logger.warning(f"Windows lazy fetch 실패 — 미제공(→fallback): {e}")
                return
            with self._lock:
                if self._offer is offer:  # fetch 중 supersede 안 됐으면 캐시
                    self._cache = fetched
            cache = fetched

        if not isinstance(cache, FetchedContent):
            return
        try:
            if kind == KIND_IMAGE and fmt == _FMT_PNG:
                _u32.SetClipboardData(fmt, _hglobal(cache.data))
            elif kind == KIND_FILE and fmt == win32con.CF_HDROP:
                _u32.SetClipboardData(fmt, _hglobal(build_dropfiles(cache.paths)))
        except Exception as e:
            logger.warning(f"Windows lazy render 실패: {e}")
