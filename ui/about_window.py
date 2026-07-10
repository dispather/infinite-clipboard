"""
Infinite Clipboard · 정보 (About) 창.

OS 알림(toast)에 의존하면 Windows Focus Assist / macOS 알림 권한 등
환경별로 표시 안 될 수 있어, 3 OS 모두 동일하게 보장하기 위해
모달 창으로 분리. 트레이의 About 메뉴가 `--window about` 으로 재호출.
"""

import os
import sys
import webbrowser

# 별도 프로세스로 실행될 때 부모 디렉토리(infinite-clipboard/)를 모듈 경로에 추가
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from pathlib import Path

import customtkinter

from version import __app_name__, __author__, __license__, __url__, __version__
from ui import theme as t
from ui.components import PrimaryButton, SecondaryButton, apply_window_icon


def _resolve_icon_path() -> Path:
    """앱 아이콘 PNG 경로 — PyInstaller 번들/소스 양쪽에서 동작."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "assets" / "generated" / "icon-512.png"
    return (Path(__file__).resolve().parent.parent
            / "assets" / "generated" / "icon-512.png")


class AboutWindow(customtkinter.CTkToplevel):
    """About 창 — 정적 정보(이름/버전/저자/라이선스/URL) 표시."""

    def __init__(self):
        super().__init__()

        self.title(f"{__app_name__} · 정보")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.configure(fg_color=t.tray_bg)
        apply_window_icon(self)

        # geometry 는 콘텐츠 pack 후 update_idletasks → 자동 산출 (하단 참조).
        # 너비는 380 으로 고정 (좁아져 텍스트 줄바꿈되는 것 방지).
        container = customtkinter.CTkFrame(self, fg_color="transparent")
        container.pack(fill="x", padx=t.SP[5], pady=t.SP[5])

        # ── 앱 아이콘 (128x128) ────────────────────────────────────
        icon_path = _resolve_icon_path()
        if icon_path.is_file():
            try:
                from PIL import Image
                pil_img = Image.open(str(icon_path))
                ctk_img = customtkinter.CTkImage(
                    light_image=pil_img, dark_image=pil_img, size=(128, 128)
                )
                customtkinter.CTkLabel(
                    container, image=ctk_img, text=""
                ).pack(pady=(0, t.SP[4]))
                # CTkImage 가 GC 되지 않도록 보존
                self._icon_ref = ctk_img
            except Exception:
                pass  # 아이콘 실패해도 텍스트는 보여야 함

        # ── 앱 이름 ──────────────────────────────────────────────
        customtkinter.CTkLabel(
            container, text=__app_name__,
            font=t.FONT_HEADING,
            text_color=t.terminal_text,
        ).pack(pady=(0, t.SP[1]))

        # ── 버전 (강조) ──────────────────────────────────────────
        customtkinter.CTkLabel(
            container, text=f"v{__version__}",
            font=(t.FAMILY, 14, "bold"),
            text_color=t.signal_ok,
        ).pack(pady=(0, t.SP[3]))

        # ── 한 줄 설명 ──────────────────────────────────────────
        customtkinter.CTkLabel(
            container,
            text="Tailscale LAN 클립보드/파일 공유",
            font=t.FONT_BODY,
            text_color=t.spool_label,
        ).pack(pady=(0, t.SP[4]))

        # ── © 저자 · 라이선스 ────────────────────────────────────
        customtkinter.CTkLabel(
            container,
            text=f"© {__author__}  ·  {__license__} License",
            font=t.FONT_LABEL,
            text_color=t.spool_dim,
        ).pack(pady=(0, t.SP[2]))

        # ── 버튼 바 (GitHub 링크 + 닫기) ─────────────────────────
        btn_bar = customtkinter.CTkFrame(container, fg_color="transparent")
        btn_bar.pack(fill="x", pady=(t.SP[4], 0))

        PrimaryButton(
            btn_bar, text="닫기", command=self.destroy
        ).pack(side="right")
        SecondaryButton(
            btn_bar, text="GitHub", command=self._open_url
        ).pack(side="right", padx=(0, t.SP[2]))

        # ── 자동 크기 (피드백: tkinter 다이얼로그는 고정 geometry 대신 자동) ──
        self.update_idletasks()
        w = max(380, self.winfo_reqwidth())
        h = self.winfo_reqheight()
        self.geometry(f"{w}x{h}")

    def _open_url(self) -> None:
        try:
            webbrowser.open(__url__)
        except Exception:
            pass  # 브라우저 열기 실패는 무시 — About 창은 계속 보여야 함
