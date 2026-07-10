"""
Infinite Clipboard — 디자인 토큰 (3 레이어).

Refactoring UI 7원칙 + Design Craft 방향 "Terminal Calm" 기반:
- Archetype: Utility & Function + Precision (개발자 도구)
- 깊이 전략: Borders-only (whisper borders, rgba alpha 0.06)
- Signature: 상태색(mint/amber/red/gray)이 앱 전체를 물들인다

3 레이어:
  1. PRIMITIVE  — 원시 값 (#hex, px)
  2. SEMANTIC   — 용도 별칭 (relay_surface, signal_ok)
  3. COMPONENT  — 컴포넌트별 기본값 (button / input / card)

네이밍이 도메인을 환기: tray_bg / relay_surface / spool_dim / signal_ok /
whisper_line — "네트워크 릴레이가 클립보드를 스풀에 담아 신호를 보낸다"
"""

from __future__ import annotations

import sys
from typing import Tuple


# ═══════════════════════════════════════════════════════════════════════
# LAYER 1 — PRIMITIVE (원시 값)
# ═══════════════════════════════════════════════════════════════════════

# Zinc 9단계 — Refactoring UI 권장 (cool neutral, 순수 회색보다 깊이감)
ZINC = {
    50:  "#fafafa",
    100: "#f4f4f5",
    200: "#e4e4e7",
    300: "#d4d4d8",
    400: "#a1a1aa",
    500: "#71717a",
    600: "#52525b",
    700: "#3f3f46",
    800: "#27272a",
    900: "#18181b",
    950: "#09090b",
}

# 상태색 팔레트 — tray 아이콘과 완전 동기화
MINT = {
    400: "#34d399",  # hover/hi
    500: "#10b981",  # 기본 accent (연결됨)
    600: "#059669",  # active/pressed
    900: "#064e3b",  # dim/muted variant
}
AMBER = {500: "#f59e0b", 600: "#d97706"}
RED   = {500: "#ef4444", 600: "#dc2626"}


# ═══════════════════════════════════════════════════════════════════════
# LAYER 2 — SEMANTIC (도메인 네이밍)
# ═══════════════════════════════════════════════════════════════════════

# 배경·표면 — Whisper Elevation: 2-3% 명도 차이로만 레이어링
tray_bg         = ZINC[950]      # 앱 캔버스 (가장 어두움)
relay_surface   = ZINC[900]      # 카드·섹션 (+2~3%)
relay_raised    = ZINC[800]      # 입력 필드·강조 표면 (+4~5%)
whisper_line    = "#26262e"      # rgba(255,255,255,0.06) 근사값
whisper_line_hi = "#3a3a42"      # hover 시

# 텍스트 — 3단계 dim
terminal_text   = ZINC[100]      # primary body
spool_label     = ZINC[400]      # secondary / label
# High #5 (2026-07-10 감사): ZINC[500](#71717a) 그대로였을 땐 실사용 배경
# (relay_surface/relay_raised) 기준 WCAG AA 대비율 3.08~3.67:1로 미달(4.5:1 기준).
# zinc-400/500 사이 커스텀 값으로 상향해 4.5:1 이상 확보(relay_raised 4.71:1,
# relay_surface 5.60:1) 하면서도 spool_label 과는 구분되는 3단계 위계 유지.
spool_dim       = "#909099"      # muted metadata (timestamp 등)
spool_mute      = ZINC[600]      # disabled

# 신호 (상태색) — Signature 의 핵심
signal_ok       = MINT[500]      # 연결됨 / 성공 / accent primary
signal_ok_hi    = MINT[400]      # hover
signal_ok_lo    = MINT[600]      # active
signal_wait     = AMBER[500]     # 대기 / 경고
signal_fail     = RED[500]       # 끊김 / 위험
signal_idle     = ZINC[500]      # 초기

# 인터랙티브 상태
focus_ring      = MINT[500]      # focus 외곽선 (2px)
hover_surface   = ZINC[800]      # 리스트 아이템 hover
press_surface  = ZINC[700]


# ═══════════════════════════════════════════════════════════════════════
# LAYER 3 — COMPONENT 기본값
# ═══════════════════════════════════════════════════════════════════════

# 버튼 크기 — 36 primary / 32 secondary / 28 icon
BTN = {
    "primary": dict(
        height=36,
        radius=8,
        fg_color=signal_ok,
        hover_color=signal_ok_hi,
        text_color=ZINC[950],     # mint 위에 어두운 텍스트 (대비↑)
        font_weight="bold",
    ),
    "secondary": dict(
        height=32,
        radius=8,
        fg_color="transparent",
        border_color=whisper_line_hi,
        border_width=1,
        hover_color=relay_raised,
        text_color=terminal_text,
    ),
    "ghost": dict(
        height=28,
        radius=6,
        fg_color="transparent",
        hover_color=relay_raised,
        text_color=spool_label,
    ),
    "icon": dict(
        width=32,
        height=32,
        radius=6,
        fg_color="transparent",
        hover_color=relay_raised,
    ),
}

# 입력 필드
INPUT = dict(
    height=34,
    radius=8,
    fg_color=relay_raised,       # 주변보다 어두움 ("들어간" 느낌)
    border_color=whisper_line,
    border_color_focus=focus_ring,
    border_width=1,
    text_color=terminal_text,
    placeholder_color=spool_dim,
)

# 섹션 카드
CARD = dict(
    radius=12,
    fg_color=relay_surface,
    border_color=whisper_line,
    border_width=1,
    padding=(20, 16),            # (x, y)
)

# Settings 윈도우
WINDOW_BG = tray_bg


# ═══════════════════════════════════════════════════════════════════════
# SPACING — 4px 기반 제약 스케일
# ═══════════════════════════════════════════════════════════════════════

SP = {
    0: 0, 1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24, 8: 32, 10: 40, 12: 48, 16: 64,
}


# ═══════════════════════════════════════════════════════════════════════
# RADIUS — 동심 공식 적용 (outer = inner + padding)
# ═══════════════════════════════════════════════════════════════════════

RADIUS = dict(sm=6, md=8, lg=12, xl=16)


# ═══════════════════════════════════════════════════════════════════════
# TYPOGRAPHY — 시스템 네이티브 스택
# ═══════════════════════════════════════════════════════════════════════

def _system_font_family() -> str:
    """플랫폼별 시스템 네이티브 산세리프 폰트를 반환."""
    if sys.platform == "darwin":
        return "SF Pro Text"
    if sys.platform == "win32":
        # Segoe UI 는 한글 글리프 미보유 → Tk 폰트 폴백이 baseline/굵기를 망가뜨림.
        # Malgun Gothic 은 Windows 7+ 기본 탑재, 한글+라틴 글리프 모두 보유.
        return "Malgun Gothic"
    # Linux: Noto Sans 가 CJK 폴백까지 처리. 없으면 Inter, 그 다음 DejaVu
    return "Noto Sans"


def _system_mono_family() -> str:
    if sys.platform == "darwin":
        return "Menlo"
    if sys.platform == "win32":
        return "Consolas"
    return "JetBrains Mono"


FAMILY = _system_font_family()
FAMILY_MONO = _system_mono_family()

# 제약 폰트 스케일 (tkinter 는 pt 단위 권장)
FONT_HEADING    = (FAMILY, 16, "bold")     # 창 제목급
FONT_SECTION    = (FAMILY, 11, "bold")     # 섹션 헤더 (UPPERCASE + tracking)
FONT_BODY       = (FAMILY, 12, "normal")
FONT_BODY_BOLD  = (FAMILY, 12, "bold")
FONT_LABEL      = (FAMILY, 11, "normal")   # 폼 라벨
FONT_META       = (FAMILY, 10, "normal")   # 타임스탬프·보조 메타
FONT_MONO       = (FAMILY_MONO, 11, "normal")  # 키·IP 표시


# ═══════════════════════════════════════════════════════════════════════
# ICON 로딩 헬퍼
# ═══════════════════════════════════════════════════════════════════════

def icon_path(name: str, size: int = 20, color: str = "text") -> str:
    """렌더된 Lucide PNG 아이콘 경로.

    Args:
        name:  Lucide 아이콘 이름 (예: "server", "file-text")
        size:  16 / 20 / 24 / 32
        color: "text" / "dim" / "accent"
    """
    from pathlib import Path
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = Path(meipass) / "ui" / "assets" / "icons" / "png"
    else:
        base = Path(__file__).resolve().parent / "assets" / "icons" / "png"
    return str(base / str(size) / color / f"{name}.png")


# ═══════════════════════════════════════════════════════════════════════
# 상태 → 색상 매핑 (Signature)
# ═══════════════════════════════════════════════════════════════════════

def signal_color(state: str) -> str:
    """앱 상태에 해당하는 신호 색상. 트레이 아이콘 상태와 동일 키 사용."""
    return {
        "connected":    signal_ok,
        "ok":           signal_ok,
        "green":        signal_ok,
        "waiting":      signal_wait,
        "amber":        signal_wait,
        "yellow":       signal_wait,
        "disconnected": signal_fail,
        "red":          signal_fail,
        "idle":         signal_idle,
        "gray":         signal_idle,
    }.get(state, signal_idle)
