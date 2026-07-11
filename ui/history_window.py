"""
클립보드 이력 창 — S4 리뉴얼.

- 타입 아이콘 (file-text / image / file) + 광학 정렬
- 카드형 항목, whisper hover (relay_raised 로 밝아짐)
- 우측 타임스탬프 tabular-nums 느낌 (고정폭 배치)
- 빈 상태: inbox 아이콘 + 제목 + 설명
- 타이틀 중복 제거 (윈도우 제목바만 사용)
"""

import json
import os
import sys

_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import time
import customtkinter
from tkinter import messagebox

from ui import theme as t
from ui.components import (
    load_icon, EmptyState, Badge, apply_window_icon,
    enable_mousewheel_scroll, bind_focus_ring, enable_tab_focus,
    SecondaryButton,
)
# 주의: 이 파일은 `from ui import theme as t` 로 `t` 를 theme 별칭으로 이미 쓴다.
# i18n 번역 함수는 이름 충돌을 피하려고 `tr` 로 별칭 import 한다.
from ui.i18n import get_language, t as tr


# 타입 → (아이콘명, 한국어 라벨, 컬러키)
_TYPE_META = {
    "text":  ("file-text", "텍스트", "dim"),
    "image": ("image",     "이미지", "accent"),
    "files": ("file",      "파일",   "accent"),
}


def _format_elapsed(timestamp: float, lang: str = "ko") -> str:
    diff = time.time() - timestamp
    if diff < 5:
        return tr("방금", lang)
    if diff < 60:
        return tr("{n}초 전", lang).format(n=int(diff))
    if diff < 3600:
        return tr("{n}분 전", lang).format(n=int(diff // 60))
    if diff < 86400:
        return tr("{n}시간 전", lang).format(n=int(diff // 3600))
    return tr("{n}일 전", lang).format(n=int(diff // 86400))


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
    def __init__(self, history_list: list, clipboard_manager, corrupted: bool = False, config=None):
        super().__init__()

        self.history_list = history_list
        self.clipboard_manager = clipboard_manager
        # M13: 이력 파일이 손상되어 초기화된 경우, "원래 비어있음" 과 구분되는
        # 안내를 보여준다 (과거엔 완전 무음이라 사용자가 구분할 수 없었음).
        self.corrupted = corrupted
        # config 가 None 이어도 get_language 가 getattr 폴백 → detect_os_locale()
        # 로 자연히 처리하므로 이 창을 단독으로 띄워도 안전하다(main.py 호출부가
        # 아직 config 를 안 넘겨도 깨지지 않음).
        self._lang = get_language(config)

        self.title(tr("클립보드 이력", self._lang))
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
            header, text=tr("이력", self._lang),
            font=t.FONT_HEADING,
            text_color=t.terminal_text,
            anchor="w",
        ).pack(side="left")

        self._count_badge = Badge(header, text=str(len(history_list)), variant="muted")
        self._count_badge.pack(side="left", padx=(t.SP[2], 0))

        # 2026-07-12 mac-studio 기능 요청: 개별 삭제 외 전체 지우기.
        SecondaryButton(
            header, text=tr("전체 지우기", self._lang),
            command=self._on_clear_all_click,
        ).pack(side="right")

        customtkinter.CTkLabel(
            header, text=tr("텍스트 항목을 클릭하면 다시 복사됩니다", self._lang),
            font=t.FONT_META, text_color=t.spool_dim, anchor="e",
        ).pack(side="right", padx=(0, t.SP[3]))

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
            if self.corrupted:
                empty = EmptyState(
                    self._scroll,
                    icon_name="inbox",
                    title=tr("이력 파일이 손상되어 초기화됐어요", self._lang),
                    desc=tr("기존 파일은 clipboard_history.json.corrupt-* 로 백업됨", self._lang),
                )
            else:
                empty = EmptyState(
                    self._scroll,
                    icon_name="inbox",
                    title=tr("이력이 비어 있어요", self._lang),
                    desc=tr("어느 PC에서든 복사하면 여기 쌓입니다", self._lang),
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

    # ── 2026-07-12 mac-studio 기능 요청: 삭제 ──────────────────────────

    @staticmethod
    def _get_history_delete_request_file() -> str:
        """main.py 의 _get_history_delete_request_file 와 동일 경로."""
        from config import _get_config_dir
        return str(_get_config_dir() / "history_delete_requests.json")

    def _append_delete_requests(self, keys: list) -> None:
        """history_delete_requests.json 에 삭제 키 목록 append.

        코드 리뷰(2026-07-12, 커밋 49758a0 후속): 각 키는 entry 의 안정적 id
        (uuid4 hex 문자열, `_delete_key_for` 참조) 또는 — id 부여 이전 레거시
        항목 한정 — timestamp(float) 다. main.py 의 _watch_history_delete_requests
        가 id 는 id 로만, timestamp 는 id 없는 레거시 항목에만 매칭해 같은
        timestamp 를 가진 서로 다른 항목이 함께 삭제되지 않는다. 0.5초 안에
        처리되어 self.clipboard_history/clipboard_history.json 에 반영된다.
        cancel_requests.json 과 동일한 read-append-write 패턴(ui/transfer_window.py
        _on_cancel_click 참조).
        """
        req_file = self._get_history_delete_request_file()
        try:
            requests = []
            try:
                with open(req_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    requests = loaded
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            for key in keys:
                if key not in requests:
                    requests.append(key)
            with open(req_file, "w", encoding="utf-8") as f:
                json.dump(requests, f)
        except OSError:
            pass

    @staticmethod
    def _delete_key_for(entry: dict):
        """항목의 삭제 매칭 키 — id 가 있으면 id, 없으면(레거시) timestamp."""
        return entry.get("id") if entry.get("id") is not None else entry.get("timestamp")

    def _on_delete_click(self, entry: dict) -> None:
        """개별 삭제 — 로컬 목록/UI 즉시 갱신 + 메인 프로세스에 IPC 로 위임."""
        key = self._delete_key_for(entry)
        try:
            self.history_list.remove(entry)
        except ValueError:
            pass
        self._count_badge.configure(text=str(len(self.history_list)))
        self._render()
        if key is not None:
            self._append_delete_requests([key])

    def _on_clear_all_click(self) -> None:
        """전체 지우기 — 되돌릴 수 없는 작업이라 확인 다이얼로그를 거친다."""
        if not self.history_list:
            return
        if not messagebox.askyesno(
            tr("전체 지우기", self._lang),
            tr("클립보드 히스토리를 모두 지울까요? 되돌릴 수 없습니다.", self._lang),
        ):
            return
        keys = [self._delete_key_for(e) for e in self.history_list if self._delete_key_for(e) is not None]
        self.history_list.clear()
        self._count_badge.configure(text="0")
        self._render()
        if keys:
            self._append_delete_requests(keys)

    def _create_item(self, entry: dict) -> customtkinter.CTkFrame:
        content_type = entry.get("type", "text")
        timestamp = entry.get("timestamp", time.time())
        # Critical 감사(2026-07-10): image/files 타입은 실제 바이트가 아니라
        # "[image]" 같은 표식만 history 에 저장되어 재복사가 항상 실패한다
        # (`_recopy` 참조). 클릭 후 무음 실패로 사용자를 속이는 대신, 애초에
        # 클릭 가능해 "보이지" 않도록 커서/hover/클릭 바인딩 자체를 생략한다
        # (감지 후 안내보다 예방이 우선 — Nielsen 에러 예방 원칙).
        recopyable = content_type == "text"
        cursor = "hand2" if recopyable else "arrow"

        icon_name, _type_label, icon_color = _TYPE_META.get(
            content_type, ("file-text", "텍스트", "dim")
        )

        frame = customtkinter.CTkFrame(
            self._scroll,
            fg_color="transparent",
            corner_radius=t.RADIUS["md"],
            height=40,
            cursor=cursor,
            border_width=0,
        )
        frame.pack_propagate(False)

        # 좌: 타입 아이콘
        icon_img = load_icon(icon_name, size=16, color=icon_color)
        icon_lbl = None
        if icon_img is not None:
            icon_lbl = customtkinter.CTkLabel(
                frame, text="", image=icon_img, cursor=cursor,
            )
            icon_lbl.pack(side="left", padx=(t.SP[3], t.SP[2] - 2))

        # 최우측: 삭제 버튼 — 2026-07-12 mac-studio 기능 요청. text/image/files
        # 전 타입 공통(재복사 가능 여부와 무관하게 삭제는 항상 가능해야 함)이라
        # 아래 `if not recopyable: return frame` 이전에 추가한다. 클릭 시
        # _on_click(재복사) 바인딩 그룹(widgets 리스트)에 넣지 않고 독립
        # command= 콜백만 쓴다 — 프레임 클릭과 충돌 없음.
        del_img = load_icon("circle-x", size=16, color="dim")
        del_btn = customtkinter.CTkButton(
            frame, text="" if del_img is not None else "×",
            image=del_img, width=24, height=24,
            corner_radius=t.RADIUS["sm"], fg_color="transparent",
            hover_color=t.signal_fail, text_color=t.spool_dim,
            command=lambda _entry=entry: self._on_delete_click(_entry),
        )
        del_btn.pack(side="right", padx=(0, t.SP[2]))

        # 우: 타임스탬프
        time_lbl = customtkinter.CTkLabel(
            frame, text=_format_elapsed(timestamp, self._lang),
            font=t.FONT_META,
            text_color=t.spool_dim,
            width=64, anchor="e",
            cursor=cursor,
        )
        time_lbl.pack(side="right", padx=(t.SP[2], t.SP[3]))

        # 중: 미리보기 (원본이 더 긴 경우 " …" 덧붙여 잘림을 표시)
        # 재복사 불가 타입은 spool_label 로 dim 처리 — "클릭해도 되는 항목"과
        # 시각적으로 구분(Refactoring UI: 비활성 요소는 de-emphasize).
        preview_text = _prepare_preview(entry)
        preview_lbl = customtkinter.CTkLabel(
            frame, text=preview_text,
            font=t.FONT_BODY,
            text_color=t.terminal_text if recopyable else t.spool_label,
            anchor="w",
            cursor=cursor,
        )
        preview_lbl.pack(side="left", fill="x", expand=True)

        if not recopyable:
            return frame

        # High #4 (2026-07-10 감사): hover(마우스)와 focus(키보드)가 같은 배경
        # 강조(hover_surface)를 공유하되, 어느 한쪽이 풀려도 다른 쪽이 살아있으면
        # 강조를 유지해야 한다 — 그래서 상태를 따로 추적한다.
        state = {"hover": False, "focus": False}

        def _sync_visual():
            frame.configure(fg_color=t.hover_surface if (state["hover"] or state["focus"]) else "transparent")

        # 클릭/Enter/Space 공통: 재복사 + 짧은 시각 피드백 (배경 잠깐 민트 계열로)
        def _on_click(_e=None, _entry=entry):
            self._recopy(_entry)
            try:
                frame.configure(fg_color=t.signal_ok_lo)
                frame.after(140, _sync_visual)
            except Exception:
                pass
            return "break"

        # hover: 배경 밝아짐
        def _on_enter(_e=None):
            state["hover"] = True
            _sync_visual()

        def _on_leave(_e=None):
            state["hover"] = False
            _sync_visual()

        def _on_focus_in(_e=None):
            state["focus"] = True
            _sync_visual()

        def _on_focus_out(_e=None):
            state["focus"] = False
            _sync_visual()

        # 모든 자식 위젯에 동일 바인딩 (아이콘 Label 포함)
        widgets = [frame, time_lbl, preview_lbl]
        if icon_lbl is not None:
            widgets.append(icon_lbl)
        for w in widgets:
            w.bind("<Button-1>", _on_click)
            w.bind("<Enter>", _on_enter)
            w.bind("<Leave>", _on_leave)

        # Tab 으로 도달 가능하게 하고 Enter/Space 로 재복사 실행. 포커스 시엔
        # hover 와 동일한 배경 강조 + focus_ring 테두리로 위치를 표시(High #3
        # 과 동일 메커니즘 재사용).
        enable_tab_focus(frame)
        frame.bind("<FocusIn>", _on_focus_in)
        frame.bind("<FocusOut>", _on_focus_out)
        frame.bind("<Return>", _on_click)
        frame.bind("<space>", _on_click)
        bind_focus_ring(
            frame, base_border_color=t.whisper_line, focus_color=t.focus_ring,
            base_border_width=0, focus_border_width=1,
        )

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
    # L5: 신규 CTk 루트 생성 시 CLAUDE.md 규칙 #7 — Cmd+V/우클릭 붙여넣기 활성화.
    # (개발자 단독 실행 전용 블록 — 실사용 경로는 main.py 가 이미 호출)
    from ui.components import enable_mac_clipboard_shortcuts
    enable_mac_clipboard_shortcuts(root)
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
