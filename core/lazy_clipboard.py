"""
core/lazy_clipboard.py — v3.0 Pure lazy clipboard provider (S2b).

OS 가 **paste 시점**에 호출하는 provide 콜백 안에서, main 이 주입한
`fetch_callback` (네트워크 fetch) 으로 받은 콘텐츠를 OS 클립보드에 내준다.
복사 시점엔 경로/ref 만 보관(offer broadcast)하고, 실제 데이터는 다른 PC 가
Ctrl+V 하는 그 순간에만 흐른다 (pure lazy — 스파이크 `lazy_proven`).

이 모듈은 **추상 인터페이스 + OS/세션 감지 + 팩토리** 다 (S2b 파운데이션).
OS별 구체 백엔드(X11/Wayland/Windows/macOS)는 후속 단계에서 슬롯-인 한다.

── 규칙 (infinite-clipboard/CLAUDE.md) ──────────────────────────────────
- 규칙 #1: **core/ 는 UI 무관** — tkinter/pystray/customtkinter import 금지.
- 규칙 #9: 종료 race silent — 백엔드 stop() 은 shutdown 의식적 종료를 silent 처리.

── ⚠️ fetch 동기성 (spec-review Rec 2) ─────────────────────────────────
모든 OS 의 provide 콜백은 **OS 이벤트 루프 안에서 동기로 실행**되며
(X11 SelectionRequest / Wayland data_source.send / Win WM_RENDERFORMAT /
macOS provideDataForType), `fetch_callback` 이 반환할 때까지 그 루프를 블록한다.
A→서버→B 더블홉 + 대용량 chunk 조립이 길어지면 paste 가 그동안 멈춘다.
→ `fetch_callback` 은 **하드 타임아웃**을 자체 적용해야 하고(main 책임),
  초과/실패 시 예외를 던진다. provider 는 예외를 잡아 빈 데이터/실패로 처리하고,
  main 이 받기 fallback (Task 8) 로 우회한다. provider 별 블로킹 한계는
  실측·문서화 대상 (Task 5 step 4/6).

── 생산 paste 타깃 (스파이크 대비 추가 작업) ───────────────────────────
스파이크는 *메커니즘*(콜백 발화 + 바이트 왕복)만 증명했다. 실제 파일 매니저
paste 는 raw 바이트가 아니라 다음을 요구한다 (백엔드 구현 시 처리):
- 이미지: `image/png` (X11/Wayland) / `CF_*`(Win) / `public.png`(mac) — 바이트 직접.
- 파일: **스테이징된 임시 파일** + URI 목록 —
  X11/Wayland `text/uri-list` (+ GNOME `x-special/gnome-copied-files`,
  KDE `application/x-kde-cutselection`), Windows `CF_HDROP`,
  macOS `NSFilePromiseProvider` (또는 file-url). 따라서 file 의 `FetchedContent`
  는 bytes 가 아니라 **스테이징 경로 목록**이다 (아래 FetchedContent 참조).
"""

import logging
import os
import platform
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


# ── offer kind (protocol._CLIP_OFFER_KINDS 와 동기) ─────────────────────
KIND_FILE = "file"
KIND_IMAGE = "image"
_VALID_KINDS = frozenset({KIND_FILE, KIND_IMAGE})

# ── 백엔드 식별자 ────────────────────────────────────────────────────────
BACKEND_X11 = "x11"
BACKEND_WAYLAND = "wayland"
BACKEND_WINDOWS = "windows"
BACKEND_MACOS = "macos"
BACKEND_NONE = "none"


@dataclass
class FetchedContent:
    """`fetch_callback` 의 반환 — provider 가 OS 클립보드에 내줄 콘텐츠.

    - 이미지(kind=image): `data` 에 raw PNG 바이트. `paths` 비움.
    - 파일(kind=file): `paths` 에 **로컬 스테이징된 절대경로 목록** (provider 가
      OS 별 uri-list / CF_HDROP / file-promise 로 변환해 내준다). `data` 비움.

    main 의 fetch 로직(Task 6)이 네트워크 chunk 를 조립해 이 형태로 반환한다.
    """
    kind: str
    data: bytes = b""
    paths: List[str] = field(default_factory=list)


# fetch_callback(offer_id) -> FetchedContent. 동기 호출(OS 콜백 안에서 블록).
# 타임아웃/실패 시 예외를 던지는 것이 계약 (provider 가 잡아 fallback 신호).
FetchCallback = Callable[[str], FetchedContent]


class OwnerThreadBusy(Exception):
    """OwnerThreadCall 이 제한 시간 안에 시작되지 못해 취소됨 (소유 스레드가 바쁨)."""


class OwnerThreadCall:
    """OS 이벤트 루프를 소유한 스레드에 맡긴 호출 1건 — 제한 대기 + 취소 (함정 #46).

    register_offer 는 main 의 네트워크 수신 스레드에서 불리고, 실제 등록은 OS 이벤트
    루프 소유 스레드(macOS=메인 run loop)가 한다. 그 스레드는 paste 콜백 안에서 네트워크
    fetch 를 동기로 기다릴 수 있다(Rec 2). 호출 스레드가 결과를 무기한 기다리면
    «네트워크 스레드 → 소유 스레드 → 네트워크 스레드» 순환 대기가 되고, fetch 가 기다리는
    청크를 읽을 스레드가 없어 fetch 타임아웃(최대 600s)까지 교착한다.

    그래서 `wait()` 는 timeout 까지만 기다리고, 그 안에 소유 스레드가 집지 못한 호출은
    취소한다 — 취소하지 않으면 나중에 실행돼 «실패 보고(받기 모드) + 뒤늦은 클립보드
    가로채기» 이중 상태가 된다. 이미 실행 중(running)이면 끝날 때까지 기다려 실제 결과를
    돌려준다(진행 중인 등록을 실패로 보고해도 같은 이중 상태).

    상태: pending → running → done, 또는 pending → cancelled.
    사용: 호출 스레드가 만들고 → 소유 스레드에 `run` 을 게시 → 호출 스레드가 `wait`.
    """

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"

    # running 상태에서 완료를 기다리는 상한. fn 은 짧은 클립보드 등록이라 보통 ms 단위 —
    # 이 상한은 fn 자체가 멈춘 경우에도 호출 스레드를 무기한 잡지 않기 위한 안전망이다.
    RUNNING_GRACE = 10.0

    def __init__(self, fn: Callable[[], object]):
        self._fn = fn
        self._lock = threading.Lock()
        self._finished = threading.Event()
        self.state = self.PENDING
        self._result = None
        self._error: Optional[BaseException] = None

    def run(self) -> None:
        """소유 스레드에서 호출. 이미 취소됐으면 아무것도 하지 않는다."""
        with self._lock:
            if self.state != self.PENDING:
                return
            self.state = self.RUNNING
        try:
            self._result = self._fn()
        except Exception as e:  # noqa: BLE001 — 호출 스레드로 전파
            self._error = e
        finally:
            with self._lock:
                self.state = self.DONE
            self._finished.set()

    def wait(self, timeout: float):
        """호출 스레드에서. fn 의 결과를 반환(예외는 그대로 전파).

        timeout 안에 소유 스레드가 시작하지 못하면 취소하고 OwnerThreadBusy.
        """
        if not self._finished.wait(timeout):
            with self._lock:
                if self.state == self.PENDING:
                    self.state = self.CANCELLED
                    raise OwnerThreadBusy(f"소유 스레드가 {timeout:.1f}s 안에 시작 못 함")
            # running — 등록이 이미 진행 중. 끝까지 기다려 실제 결과를 돌려준다.
            if not self._finished.wait(self.RUNNING_GRACE):
                raise OwnerThreadBusy(
                    f"소유 스레드 작업이 {timeout + self.RUNNING_GRACE:.1f}s 안에 안 끝남"
                )
        if self._error is not None:
            raise self._error
        return self._result


class LazyClipboardProvider(ABC):
    """OS별 lazy provide 백엔드의 공통 인터페이스.

    수명주기: `register_offer()` 로 OS 클립보드 소유권 획득 + provide 콜백 등록
    → paste 시 OS 가 콜백 호출 → `fetch_callback` 발화 → 바이트 반환.
    `clear()` 로 현재 offer 해제(supersede), `stop()` 으로 백엔드 완전 종료.
    """

    @abstractmethod
    def is_supported(self, kind: str) -> bool:
        """이 백엔드가 해당 kind(file/image)의 lazy provide 를 지원하는지.

        False 면 main 이 등록하지 않고 받기 fallback 으로 우회한다.
        """
        raise NotImplementedError

    @abstractmethod
    def register_offer(self, offer: dict, fetch_callback: FetchCallback) -> bool:
        """offer 를 OS 클립보드에 lazy 등록 (placeholder).

        Args:
            offer: protocol.parse_clip_offer 결과
                ({offer_id, source_peer, kind, items, total_size, created_at}).
            fetch_callback: paste 시점에 호출될 동기 fetch (offer_id → FetchedContent).

        Returns:
            bool: 등록 성공 여부. False 면 main 이 fallback 으로 우회.

        ⚠️ main 의 네트워크 수신 스레드에서 불린다 — 이벤트 루프 스레드를 **무기한**
        기다리지 말 것(그 스레드가 paste 콜백 안에서 fetch 청크를 기다리면 교착, 함정 #46).
        제한 시간 안에 등록 못 하면 False 를 반환하고, 그 등록은 나중에 실행되지 않아야
        한다(`OwnerThreadCall`).
        """
        raise NotImplementedError

    @abstractmethod
    def clear(self) -> None:
        """현재 등록된 offer 를 해제 (supersede / 로컬 복사 발생 시)."""
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        """백엔드 완전 종료 (스레드/이벤트 루프 정리). 규칙 #9 silent 종료."""
        raise NotImplementedError

    def owns_clipboard(self) -> bool:
        """이 provider 가 현재 OS 클립보드(또는 selection)를 lazy offer 로 소유 중인지.

        받는 PC 의 클립보드 모니터(main._monitor_clipboard)는 이 값이 True 면 클립보드
        읽기를 건너뛴다. 이유 — 우리가 등록한 lazy placeholder 를 "로컬 복사" 로 오인해
        ① 자기 자신의 paste-render(WM_RENDERFORMAT / SelectionRequest / send)를 유발해
        paste 도 안 했는데 네트워크 fetch 하거나, ② 그 staging 경로로 offer 를 재
        broadcast 하는 self-loop 를 막기 위함. 소유권을 잃으면(사용자가 로컬 복사 →
        OS 가 소유권을 회수) 다시 False 가 되어 모니터가 정상 재개한다.

        기본 False (소유 추적을 안 하는 백엔드/더미). 각 OS 백엔드는 활성 offer 유무로
        override 한다 — abstractmethod 가 아니라 concrete 기본값이라, 일부 메서드만 구현한
        구식 더미/테스트 서브클래스는 영향 없음. macOS 는 예외로 «마지막으로 우리가 쓴 뒤
        pasteboard 가 그대로인가»로 판정한다(소유 상실 콜백이 없고, 해제한 뒤에도 이미 내준
        항목이 pasteboard 에 데이터로 남는다 — core/lazy_mac.py owns_clipboard).
        """
        return False


def detect_backend() -> str:
    """현재 OS/세션에 맞는 백엔드 식별자를 반환 (순수 — env/platform 만 참조).

    Linux 는 Wayland 우선(WAYLAND_DISPLAY 또는 XDG_SESSION_TYPE=wayland),
    아니면 DISPLAY 있으면 X11, 둘 다 없으면 none(헤드리스).
    """
    system = platform.system()
    if system == "Windows":
        return BACKEND_WINDOWS
    if system == "Darwin":
        return BACKEND_MACOS
    if system == "Linux":
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
            return BACKEND_WAYLAND
        if session == "x11" or os.environ.get("DISPLAY"):
            return BACKEND_X11
        return BACKEND_NONE
    return BACKEND_NONE


def get_lazy_provider() -> Optional[LazyClipboardProvider]:
    """OS/세션을 감지해 적절한 lazy provider 인스턴스를 반환.

    백엔드 미구현 / 의존성 부재 / 헤드리스(none) / 생성 실패 시 **None** 을
    반환하고, main 은 이를 "lazy 불가" 신호로 받아 받기 fallback 으로 우회한다
    (graceful — 크래시 없음).

    NOTE: S2b 파운데이션 단계 — OS별 구체 백엔드는 후속 슬롯-인. 현재는 감지
    결과만 로깅하고 None 을 반환한다. 백엔드가 추가되면 해당 분기에서 생성.
    """
    backend = detect_backend()
    if backend == BACKEND_NONE:
        logger.debug("lazy provider: 헤드리스/미지원 세션 — fallback 사용")
        return None

    # 백엔드별 lazy import (의존성 부재 시 ImportError → None graceful).
    # 후속 단계에서 각 분기에 구체 provider 생성 추가.
    try:
        if backend == BACKEND_X11:
            from core.lazy_x11 import X11LazyProvider
            return X11LazyProvider()
        if backend == BACKEND_WAYLAND:
            from core.lazy_wayland import WaylandLazyProvider
            return WaylandLazyProvider()
        if backend == BACKEND_WINDOWS:
            from core.lazy_win import WindowsLazyProvider
            return WindowsLazyProvider()
        if backend == BACKEND_MACOS:
            from core.lazy_mac import MacLazyProvider
            return MacLazyProvider()
    except Exception as e:
        # 의존성 부재/초기화 실패 — lazy 불가, fallback (크래시 금지)
        logger.warning(f"lazy provider({backend}) 생성 실패 — fallback: {e}")
        return None

    return None
