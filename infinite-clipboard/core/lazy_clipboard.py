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
            logger.debug("lazy provider: macOS 백엔드 미구현 (후속) — fallback")
            return None
    except Exception as e:
        # 의존성 부재/초기화 실패 — lazy 불가, fallback (크래시 금지)
        logger.warning(f"lazy provider({backend}) 생성 실패 — fallback: {e}")
        return None

    return None
