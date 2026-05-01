"""
Infinite Clipboard — 재사용 UI 컴포넌트.

Settings/History/Transfers 에서 일관되게 쓰이는 조각들.
모든 색·폰트·간격은 `ui.theme` 토큰을 참조 — 컴포넌트에 하드코딩 금지.

컴포넌트 목록:
- load_icon()         : 사전 렌더된 PNG 아이콘을 CTkImage 로 로드 (캐시)
- SectionCard         : 섹션 컨테이너 (whisper border + 12px radius)
- SectionHeader       : 섹션 제목 (uppercase + tracking + optional count/status)
- FormRow             : 좌측 라벨 + 우측 위젯 (그리드 정렬)
- PrimaryButton       : 민트 accent 주 액션 버튼
- SecondaryButton     : 투명 + 테두리 보조 버튼
- IconButton          : 정사각 아이콘 전용
- Badge               : 상태 뱃지 (색상 variant 4종)
- EmptyState          : 빈 상태 (아이콘 + 제목 + 설명)
- IconLabel           : 아이콘 + 텍스트 한 줄 (광학 정렬)
"""

from __future__ import annotations

import os
import sys

# ui/ 가 별도 프로세스로 실행될 때 프로젝트 루트 보장
_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from typing import Callable, Optional
import customtkinter as ctk
from PIL import Image

from ui import theme as t


# ═══════════════════════════════════════════════════════════════════════
# 아이콘 로딩 (캐시)
# ═══════════════════════════════════════════════════════════════════════

_icon_cache: dict[tuple[str, int, str], ctk.CTkImage] = {}


def enable_mac_clipboard_shortcuts(root) -> None:
    """macOS 에서 클립보드 단축키 + 우클릭 컨텍스트 메뉴 활성화.

    macOS Tk 는 Cmd-* 를 메뉴바가 먼저 가로채기 때문에 bind_class 만으로는
    이벤트가 위젯에 도달 못 한다. 두 가지를 같이 적용:

    1) **빈 메뉴바 장착** — root['menu'] 에 tk.Menu 를 달면 macOS 가 앱을
       "full app" 으로 인식해 Cmd-키 이벤트를 포커스 위젯으로 전달. 추가로
       표준 Edit 메뉴를 등록해 Cmd+C/V/X/A 가 확실히 동작.
    2) **우클릭 컨텍스트 메뉴** — 한/영 IME 영향 없이 항상 동작하는 fallback.
       Entry/Text 위젯 class 전체에 바인딩.

    bind_class 는 전역이므로 인터프리터 당 한 번만 호출.
    """
    if sys.platform != "darwin":
        return
    if getattr(root, "_mac_clipboard_bound", False):
        return

    import tkinter as tk

    # ── 표준 Edit 메뉴바 등록 (Cmd 키 라우팅 개선) ─────────────────────
    try:
        menubar = tk.Menu(root)
        edit_menu = tk.Menu(menubar, tearoff=0)

        def _focused_event(name):
            w = root.focus_get()
            if w is not None:
                try:
                    w.event_generate(f"<<{name}>>")
                except Exception:
                    pass

        edit_menu.add_command(label="잘라내기", accelerator="Cmd+X",
                              command=lambda: _focused_event("Cut"))
        edit_menu.add_command(label="복사", accelerator="Cmd+C",
                              command=lambda: _focused_event("Copy"))
        edit_menu.add_command(label="붙여넣기", accelerator="Cmd+V",
                              command=lambda: _focused_event("Paste"))
        edit_menu.add_separator()
        edit_menu.add_command(label="전체 선택", accelerator="Cmd+A",
                              command=lambda: _focused_event("SelectAll"))
        menubar.add_cascade(label="편집", menu=edit_menu)
        root.configure(menu=menubar)
    except Exception:
        pass  # 메뉴바 실패해도 우클릭은 살리기

    # ── 우클릭 컨텍스트 메뉴 (IME/레이아웃 무관 fallback) ─────────────
    def _show_context_menu(event):
        widget = event.widget
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="잘라내기",
                         command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(label="복사",
                         command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="붙여넣기",
                         command=lambda: widget.event_generate("<<Paste>>"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _select_all_entry(e):
        try:
            e.widget.select_range(0, "end")
            e.widget.icursor("end")
        except Exception:
            pass
        return "break"

    def _select_all_text(e):
        try:
            e.widget.tag_add("sel", "1.0", "end-1c")
        except Exception:
            pass
        return "break"

    # macOS: 일반적으로 Button-2 가 보조 클릭. Ctrl+Button-1 도 대응.
    # 일부 외장 마우스에서 Button-3 을 쏘므로 함께 바인딩.
    for cls in ("Entry", "TEntry", "Text", "TText"):
        root.bind_class(cls, "<Button-2>", _show_context_menu)
        root.bind_class(cls, "<Button-3>", _show_context_menu)
        root.bind_class(cls, "<Control-Button-1>", _show_context_menu)
        # Cmd-* 도 시도 (메뉴바가 라우팅하지 못할 때 대비)
        root.bind_class(cls, "<Command-c>", lambda e: e.widget.event_generate("<<Copy>>"))
        root.bind_class(cls, "<Command-v>", lambda e: e.widget.event_generate("<<Paste>>"))
        root.bind_class(cls, "<Command-x>", lambda e: e.widget.event_generate("<<Cut>>"))

    for cls in ("Entry", "TEntry"):
        root.bind_class(cls, "<Command-a>", _select_all_entry)
    for cls in ("Text", "TText"):
        root.bind_class(cls, "<Command-a>", _select_all_text)

    root._mac_clipboard_bound = True  # type: ignore[attr-defined]


def enable_mousewheel_scroll(scrollable_frame: ctk.CTkScrollableFrame) -> None:
    """CTkScrollableFrame 자식 트리 전체에 마우스 휠 스크롤을 바인딩.

    customtkinter 기본 동작은 내부 canvas 위에 커서가 있을 때만 휠이 먹힘.
    리스트 아이템(CTkFrame/CTkLabel) 위에서도 휠을 받으려면 각 자식 위젯에
    `<MouseWheel>` (Windows/macOS) + `<Button-4>`/`<Button-5>` (Linux X11/Wayland)
    이벤트를 명시적으로 바인딩해야 한다.

    중복 바인딩 방지로 `_mw_bound` 속성 플래그 사용.
    """
    canvas = scrollable_frame._parent_canvas  # type: ignore[attr-defined]

    def on_wheel(event):
        # Linux: Button-4/5 (단위 휠 이벤트, delta 없음)
        if getattr(event, "num", None) == 4:
            canvas.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5:
            canvas.yview_scroll(3, "units")
        # Windows/macOS: MouseWheel delta (일반적으로 ±120 단위)
        elif getattr(event, "delta", 0) != 0:
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def bind_recursive(widget):
        if getattr(widget, "_mw_bound", False):
            return
        widget._mw_bound = True  # type: ignore[attr-defined]
        widget.bind("<MouseWheel>", on_wheel, add="+")
        widget.bind("<Button-4>", on_wheel, add="+")
        widget.bind("<Button-5>", on_wheel, add="+")
        try:
            for child in widget.winfo_children():
                bind_recursive(child)
        except Exception:
            pass

    bind_recursive(scrollable_frame)


def apply_window_icon(toplevel) -> None:
    """Toplevel 창의 제목바 아이콘을 앱 아이콘(squircle ∞)으로 설정.

    설정 안 하면 Tk 기본 로고(회색 X 비슷한 기호)가 제목바에 뜸.
    """
    try:
        import tkinter as tk
        from pathlib import Path
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            icon_file = Path(meipass) / "assets" / "generated" / "icon-512.png"
        else:
            icon_file = (Path(__file__).resolve().parent.parent
                         / "assets" / "generated" / "icon-512.png")
        if icon_file.is_file():
            photo = tk.PhotoImage(file=str(icon_file))
            toplevel.iconphoto(False, photo)
            # PhotoImage 가 GC 되면 깜빡이므로 창에 붙잡아 둠
            toplevel._icon_photo_ref = photo  # type: ignore[attr-defined]
    except Exception:
        pass  # 아이콘은 장식용 — 실패해도 창 기능에 영향 없음


def load_icon(name: str, size: int = 20, color: str = "text") -> Optional[ctk.CTkImage]:
    """사전 렌더된 PNG 아이콘을 CTkImage 로 로드 (프로세스 생애 동안 캐시).

    Args:
        name:  Lucide 이름 (예: "server", "file-text")
        size:  16 / 20 / 24 / 32 — CTkImage 가 HiDPI 자동 처리
        color: "text" / "dim" / "accent"

    Returns:
        CTkImage, 파일 없으면 None (호출측에서 fallback 처리)
    """
    key = (name, size, color)
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached

    path = t.icon_path(name, size=size, color=color)
    if not os.path.isfile(path):
        return None

    image = Image.open(path).convert("RGBA")
    # CTkImage 는 light/dark 모드에 다른 이미지를 줄 수 있지만 우리는 단일 다크 테마라 같은 걸로.
    ctk_img = ctk.CTkImage(light_image=image, dark_image=image, size=(size, size))
    _icon_cache[key] = ctk_img
    return ctk_img


# ═══════════════════════════════════════════════════════════════════════
# 섹션 카드 + 헤더
# ═══════════════════════════════════════════════════════════════════════

class SectionCard(ctk.CTkFrame):
    """섹션 카드 — whisper border + 12px radius + relay_surface 배경.

    내부 여백은 padding 인자로 조절. 기본값은 theme.CARD.
    내부에 SectionHeader 와 FormRow 들을 쌓는 컨테이너.
    """

    def __init__(self, master, **kwargs):
        super().__init__(
            master,
            fg_color=t.CARD["fg_color"],
            corner_radius=t.CARD["radius"],
            border_color=t.CARD["border_color"],
            border_width=t.CARD["border_width"],
            **kwargs,
        )


class SectionHeader(ctk.CTkFrame):
    """섹션 헤더 — UPPERCASE + letter-tracking + optional count badge.

    Refactoring UI 원칙: 라벨은 de-emphasize (작게/가볍게/uppercase).
    count 가 주어지면 우측에 Badge 표시.
    """

    def __init__(self, master, title: str, count: Optional[int] = None,
                 status: Optional[str] = None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        label = ctk.CTkLabel(
            self, text=title.upper() + "   ",  # tkinter 는 letter-spacing 없음 → trailing space 로 대리
            font=t.FONT_SECTION,
            text_color=t.spool_label,
            anchor="w",
        )
        label.pack(side="left")

        if count is not None:
            Badge(self, text=str(count), variant="muted").pack(side="left", padx=(t.SP[2], 0))
        if status is not None:
            # "연결됨" / "대기" 등 상태 라벨 — 색상으로 강조
            color_map = {
                "ok": t.signal_ok, "wait": t.signal_wait,
                "fail": t.signal_fail, "idle": t.signal_idle,
            }
            dot = ctk.CTkLabel(
                self, text="●",
                font=t.FONT_BODY,
                text_color=color_map.get(status, t.signal_idle),
            )
            dot.pack(side="right")


# ═══════════════════════════════════════════════════════════════════════
# 폼 행
# ═══════════════════════════════════════════════════════════════════════

class FormRow(ctk.CTkFrame):
    """라벨 + 위젯 가로 배치 행.

    **사용법** (중요): 위젯은 반드시 이 row 를 parent 로 생성해야 한다.
    tkinter 의 `in_=` 은 같은 레벨 형제 위젯을 안정적으로 re-parent 하지 못하므로,
    위젯 자체가 FormRow 의 자식이어야 렌더된다.

        row = FormRow(parent, "호스트")
        entry = ctk.CTkEntry(row, ...)         # parent = row ✓
        entry.pack(side="left", fill="x", expand=True)
        row.pack(fill="x", pady=...)
    """

    LABEL_WIDTH = 80

    def __init__(self, master, label: str, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        ctk.CTkLabel(
            self, text=label,
            font=t.FONT_LABEL,
            text_color=t.spool_label,
            anchor="w",
            width=self.LABEL_WIDTH,
        ).pack(side="left", padx=(0, t.SP[3]))


# ═══════════════════════════════════════════════════════════════════════
# 버튼
# ═══════════════════════════════════════════════════════════════════════

class PrimaryButton(ctk.CTkButton):
    """주요 액션 (저장·시작·확인)."""

    def __init__(self, master, text: str, command: Optional[Callable] = None, **kwargs):
        cfg = dict(t.BTN["primary"])
        font_w = cfg.pop("font_weight", "bold")
        super().__init__(
            master, text=text, command=command,
            corner_radius=cfg["radius"],
            height=cfg["height"],
            fg_color=cfg["fg_color"],
            hover_color=cfg["hover_color"],
            text_color=cfg["text_color"],
            font=(t.FAMILY, 13, font_w),
            **kwargs,
        )


class SecondaryButton(ctk.CTkButton):
    """보조 액션 (취소·닫기)."""

    def __init__(self, master, text: str, command: Optional[Callable] = None, **kwargs):
        cfg = t.BTN["secondary"]
        super().__init__(
            master, text=text, command=command,
            corner_radius=cfg["radius"],
            height=cfg["height"],
            fg_color=cfg["fg_color"],
            border_color=cfg["border_color"],
            border_width=cfg["border_width"],
            hover_color=cfg["hover_color"],
            text_color=cfg["text_color"],
            font=(t.FAMILY, 12, "normal"),
            **kwargs,
        )


class IconButton(ctk.CTkButton):
    """정사각 아이콘 전용 버튼 (32x32). tooltip 은 아직 미구현."""

    def __init__(self, master, icon_name: str, command: Optional[Callable] = None,
                 icon_color: str = "text", **kwargs):
        cfg = t.BTN["icon"]
        img = load_icon(icon_name, size=20, color=icon_color)
        super().__init__(
            master, text="", image=img, command=command,
            corner_radius=cfg["radius"],
            width=cfg["width"], height=cfg["height"],
            fg_color=cfg["fg_color"],
            hover_color=cfg["hover_color"],
            **kwargs,
        )


# ═══════════════════════════════════════════════════════════════════════
# Badge
# ═══════════════════════════════════════════════════════════════════════

class Badge(ctk.CTkLabel):
    """상태/카운트 뱃지. variant: 'muted' | 'ok' | 'wait' | 'fail' | 'info'."""

    _VARIANTS = {
        "muted": (t.spool_label, t.relay_raised),
        "ok":    (t.signal_ok,   "#0a3d2b"),  # dim mint bg
        "wait":  (t.signal_wait, "#3d2a0a"),
        "fail":  (t.signal_fail, "#3d0a0a"),
        "info":  (t.terminal_text, t.relay_raised),
    }

    def __init__(self, master, text: str, variant: str = "muted", **kwargs):
        fg, bg = self._VARIANTS.get(variant, self._VARIANTS["muted"])
        super().__init__(
            master, text=text,
            font=t.FONT_META,
            text_color=fg,
            fg_color=bg,
            corner_radius=6,
            padx=6, pady=2,
            **kwargs,
        )


# ═══════════════════════════════════════════════════════════════════════
# EmptyState
# ═══════════════════════════════════════════════════════════════════════

class EmptyState(ctk.CTkFrame):
    """빈 상태 — 아이콘(dim) + 제목 + 설명. 리스트 영역에 중앙 배치."""

    def __init__(self, master, icon_name: str, title: str, desc: str = "", **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        img = load_icon(icon_name, size=32, color="dim")
        if img is not None:
            ctk.CTkLabel(self, text="", image=img).pack(pady=(t.SP[6], t.SP[3]))

        ctk.CTkLabel(
            self, text=title,
            font=t.FONT_BODY_BOLD,
            text_color=t.spool_label,
        ).pack()

        if desc:
            ctk.CTkLabel(
                self, text=desc,
                font=t.FONT_META,
                text_color=t.spool_dim,
                justify="center",
            ).pack(pady=(t.SP[1], t.SP[6]))


# ═══════════════════════════════════════════════════════════════════════
# IconLabel — 아이콘 + 텍스트 (광학 정렬 적용)
# ═══════════════════════════════════════════════════════════════════════

class IconLabel(ctk.CTkFrame):
    """아이콘 + 라벨 가로 배치. interface-micro-details §1.2 광학 정렬 적용.

    padding_adjust=2 로 아이콘 쪽 여백을 텍스트 쪽보다 2px 덜 줌.
    """

    def __init__(self, master, icon_name: str, text: str,
                 icon_size: int = 16, icon_color: str = "dim",
                 text_color: Optional[str] = None, font=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        img = load_icon(icon_name, size=icon_size, color=icon_color)
        if img is not None:
            ctk.CTkLabel(self, text="", image=img).pack(
                side="left", padx=(0, t.SP[2] - 2)  # 광학 정렬: 2px 덜
            )

        ctk.CTkLabel(
            self, text=text,
            font=font or t.FONT_BODY,
            text_color=text_color or t.terminal_text,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
