"""
클립보드 이력 창 — S4 리뉴얼.

- 타입 아이콘 (file-text / image / file) + 광학 정렬
- 카드형 항목, whisper hover (relay_raised 로 밝아짐)
- 우측 타임스탬프 tabular-nums 느낌 (고정폭 배치)
- 빈 상태: inbox 아이콘 + 제목 + 설명
- 타이틀 중복 제거 (윈도우 제목바만 사용)
"""

import os
import sys

_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import time
import customtkinter

from ui import theme as t
from ui.components import load_icon, EmptyState, Badge, apply_window_icon, enable_mousewheel_scroll


# 타입 → (아이콘명, 한국어 라벨, 컬러키)
_TYPE_META = {
    "text":  ("file-text", "텍스트", "dim"),
    "image": ("image",     "이미지", "accent"),
    "files": ("file",      "파일",   "accent"),
}


def _format_elapsed(timestamp: float) -> str:
    diff = time.time() - timestamp
    if diff < 5:
        return "방금"
    if diff < 60:
        return f"{int(diff)}초 전"
    if diff < 3600:
        return f"{int(diff // 60)}분 전"
    if diff < 86400:
        return f"{int(diff // 3600)}시간 전"
    return f"{int(diff // 86400)}일 전"


def _prepare_preview(entry: dict) -> str:
    """이력 항목의 미리보기 문자열을 만든다.

    - 여러 줄이면 첫 줄만 보여주되 "…" 를 덧붙여 더 있음을 암시.
    - 원본 content 가 preview(잘린 값)보다 길면 "…" 를 덧붙임.
    - 이미 "..." 또는 "…" 로 끝나면 중복 추가하지 않음.
    """
    preview = entry.get("preview", "")
    content = entry.get("content")

    first_line = preview.split("\n", 1)[0]

    truncated = False
    # 1) preview 안에 줄바꿈이 있으면 뒷줄이 잘린 것
    if "\n" in preview:
        truncated = True
    # 2) 원본 content 가 preview 첫 줄보다 길면 잘린 것 (텍스트 타입 한정)
    if isinstance(content, str):
        if len(content) > len(first_line) or "\n" in content:
            truncated = True

    # 이미 말줄임 기호가 붙어있으면 그대로 반환
    if first_line.endswith("...") or first_line.endswith("…"):
        return first_line

    return first_line + " …" if truncated else first_line


class HistoryWindow(customtkinter.CTkToplevel):
    def __init__(self, history_list: list, clipboard_manager):
        super().__init__()

        self.history_list = history_list
        self.clipboard_manager = clipboard_manager

        self.title("클립보드 이력")
        # 가로를 넉넉히 — preview 텍스트가 잘리지 않게
        self.geometry("620x560")
        self.attributes("-topmost", True)
        self.resizable(True, True)
        self.configure(fg_color=t.tray_bg)
        apply_window_icon(self)

        # 상단 헤더 (카운트 배지)
        header = customtkinter.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=t.SP[4], pady=(t.SP[4], t.SP[2]))

        customtkinter.CTkLabel(
            header, text="이력",
            font=t.FONT_HEADING,
            text_color=t.terminal_text,
            anchor="w",
        ).pack(side="left")

        self._count_badge = Badge(header, text=str(len(history_list)), variant="muted")
        self._count_badge.pack(side="left", padx=(t.SP[2], 0))

        customtkinter.CTkLabel(
            header, text="항목을 클릭하면 다시 복사됩니다",
            font=t.FONT_META, text_color=t.spool_dim, anchor="e",
        ).pack(side="right")

        # 스크롤 리스트
        self._scroll = customtkinter.CTkScrollableFrame(
            self, fg_color=t.relay_surface,
            corner_radius=t.CARD["radius"],
            border_color=t.whisper_line,
            border_width=1,
            scrollbar_button_color=t.relay_raised,
            scrollbar_button_hover_color=t.whisper_line_hi,
        )
        self._scroll.pack(fill="both", expand=True, padx=t.SP[4], pady=(0, t.SP[4]))

        self._item_widgets: list = []
        self._render()

    def refresh(self, history_list: list) -> None:
        self.history_list = history_list
        self._count_badge.configure(text=str(len(history_list)))
        self._render()

    def _render(self) -> None:
        for w in self._item_widgets:
            w.destroy()
        self._item_widgets.clear()

        if not self.history_list:
            empty = EmptyState(
                self._scroll,
                icon_name="inbox",
                title="이력이 비어 있어요",
                desc="어느 PC에서든 복사하면 여기 쌓입니다",
            )
            empty.pack(expand=True, pady=t.SP[10])
            self._item_widgets.append(empty)
            return

        for entry in self.history_list:
            item = self._create_item(entry)
            item.pack(fill="x", padx=t.SP[2], pady=t.SP[1] // 2)
            self._item_widgets.append(item)

        # 새로 생성된 아이템들에도 휠 스크롤이 먹히도록 재귀 바인딩
        enable_mousewheel_scroll(self._scroll)

    def _create_item(self, entry: dict) -> customtkinter.CTkFrame:
        content_type = entry.get("type", "text")
        preview = entry.get("preview", "")
        timestamp = entry.get("timestamp", time.time())

        icon_name, _type_label, icon_color = _TYPE_META.get(
            content_type, ("file-text", "텍스트", "dim")
        )

        # 전체 행에 cursor=hand2 로 "클릭 가능" 신호
        frame = customtkinter.CTkFrame(
            self._scroll,
            fg_color="transparent",
            corner_radius=t.RADIUS["md"],
            height=40,
            cursor="hand2",
        )
        frame.pack_propagate(False)

        # 좌: 타입 아이콘
        icon_img = load_icon(icon_name, size=16, color=icon_color)
        icon_lbl = None
        if icon_img is not None:
            icon_lbl = customtkinter.CTkLabel(
                frame, text="", image=icon_img, cursor="hand2",
            )
            icon_lbl.pack(side="left", padx=(t.SP[3], t.SP[2] - 2))

        # 우: 타임스탬프
        time_lbl = customtkinter.CTkLabel(
            frame, text=_format_elapsed(timestamp),
            font=t.FONT_META,
            text_color=t.spool_dim,
            width=64, anchor="e",
            cursor="hand2",
        )
        time_lbl.pack(side="right", padx=(t.SP[2], t.SP[3]))

        # 중: 미리보기 (원본이 더 긴 경우 " …" 덧붙여 잘림을 표시)
        preview_text = _prepare_preview(entry)
        preview_lbl = customtkinter.CTkLabel(
            frame, text=preview_text,
            font=t.FONT_BODY,
            text_color=t.terminal_text,
            anchor="w",
            cursor="hand2",
        )
        preview_lbl.pack(side="left", fill="x", expand=True)

        # 클릭: 재복사 + 짧은 시각 피드백 (배경 잠깐 민트 계열로)
        def _on_click(_e=None, _entry=entry, _f=frame):
            self._recopy(_entry)
            try:
                _f.configure(fg_color=t.signal_ok_lo)
                _f.after(140, lambda: _f.configure(fg_color=t.hover_surface))
            except Exception:
                pass

        # hover: 배경 밝아짐
        def _on_enter(_e=None, _f=frame):
            _f.configure(fg_color=t.hover_surface)

        def _on_leave(_e=None, _f=frame):
            _f.configure(fg_color="transparent")

        # 모든 자식 위젯에 동일 바인딩 (아이콘 Label 포함)
        widgets = [frame, time_lbl, preview_lbl]
        if icon_lbl is not None:
            widgets.append(icon_lbl)
        for w in widgets:
            w.bind("<Button-1>", _on_click)
            w.bind("<Enter>", _on_enter)
            w.bind("<Leave>", _on_leave)

        return frame

    def _recopy(self, entry: dict) -> None:
        import logging
        log = logging.getLogger(__name__)
        content_type = entry.get("type", "text")
        content = entry.get("content")
        preview = entry.get("preview", "")[:40]

        if content is None:
            log.warning(f"[history] 재복사 불가: content=None (type={content_type}, preview={preview!r})")
            return

        # 비텍스트 타입의 경우 _add_to_history 가 "[image]" 같은 표식을
        # content 로 저장했으므로 실제로는 복사 불가. 텍스트만 지원.
        if content_type != "text":
            log.warning(f"[history] 재복사 미지원 타입: {content_type} (preview={preview!r})")
            return

        try:
            ok = self.clipboard_manager.set_clipboard_content("text", content)
            log.info(f"[history] 재복사 성공={ok}, 길이={len(content)}, preview={preview!r}")
        except Exception as e:
            log.error(f"[history] 재복사 실패: {e}")


if __name__ == "__main__":
    import json

    customtkinter.set_appearance_mode("System")
    root = customtkinter.CTk()
    root.withdraw()
    root.after(50, root.deiconify)
    root.after(100, root.withdraw)

    def _close_all(win=None):
        try:
            if win:
                win.destroy()
        except Exception:
            pass
        try:
            root.quit()
            root.destroy()
        except Exception:
            pass
        sys.exit(0)

    from config import _get_config_dir
    from core.clipboard_manager import ClipboardManager

    history = []
    history_file = _get_config_dir() / "clipboard_history.json"
    if history_file.exists():
        try:
            with open(history_file, "r", encoding="utf-8") as hf:
                history = json.load(hf)
        except Exception:
            pass

    cm = ClipboardManager()
    win = HistoryWindow(history, cm)
    win.after(150, win.focus_force)
    win.protocol("WM_DELETE_WINDOW", lambda: _close_all(win))
    root.mainloop()
