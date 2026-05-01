"""
시스템 트레이 아이콘 + 메뉴 구현

pystray 기반 트레이 앱. InfiniteClipboard 인스턴스를 참조하여
상태 표시, 아이콘 색상 변경, OS 토스트 알림 등을 수행한다.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import sys
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pystray
from PIL import Image, ImageDraw

if TYPE_CHECKING:
    from main import InfiniteClipboard

logger = logging.getLogger("infinite-clipboard.tray")


# ── 아이콘 이미지 로드 ─────────────────────────────────────────────────

# 상태 → PNG 파일명 매핑. build/generate_icons.sh가 만드는 파일명과 동기화.
# "yellow"는 기존 호환용 별명 (amber와 같음).
_STATE_TO_FILE = {
    "green":  "tray-green.png",
    "amber":  "tray-amber.png",
    "yellow": "tray-amber.png",
    "red":    "tray-red.png",
    "gray":   "tray-gray.png",
}

# 프로세스 생명주기 동안 로드된 이미지를 캐시 (디스크 I/O 절감)
_icon_cache: dict[str, Image.Image] = {}


def _assets_dir() -> Path:
    """아이콘 PNG가 있는 디렉토리.

    - 개발 환경: `ui/tray.py` 기준 상위 폴더의 `assets/generated/`
    - PyInstaller 번들: `sys._MEIPASS/assets/generated/` (spec 파일이 --add-data로 포함)
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "assets" / "generated"
    return Path(__file__).resolve().parent.parent / "assets" / "generated"


def _fallback_icon(color_hex: str) -> Image.Image:
    """PNG를 찾지 못했을 때 최소한의 단색 squircle 아이콘을 즉석 생성."""
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([4, 4, 60, 60], radius=14, fill=color_hex)
    return image


def create_icon_image(color: str = "green") -> Image.Image:
    """상태 색상에 해당하는 트레이 아이콘을 로드한다.

    Args:
        color: "green" / "amber"(또는 "yellow") / "red" / "gray" 중 하나.
                미지정·오타 시 "gray"로 폴백.
    """
    cached = _icon_cache.get(color)
    if cached is not None:
        return cached

    filename = _STATE_TO_FILE.get(color, _STATE_TO_FILE["gray"])
    path = _assets_dir() / filename
    try:
        image = Image.open(path).convert("RGBA")
    except Exception as e:
        logger.warning(f"아이콘 로드 실패 ({path}): {e} — 폴백 단색 아이콘 사용")
        fallbacks = {
            "green": "#10b981", "amber": "#f59e0b", "yellow": "#f59e0b",
            "red": "#ef4444", "gray": "#9ca3af",
        }
        image = _fallback_icon(fallbacks.get(color, fallbacks["gray"]))

    _icon_cache[color] = image
    return image


# ── TrayApp 클래스 ──────────────────────────────────────────────────────


class TrayApp:
    """시스템 트레이 앱"""

    def __init__(self, app: "InfiniteClipboard") -> None:
        self.app = app
        self.icon: pystray.Icon | None = None
        # 앱 상태 변경 시 아이콘 자동 갱신
        self.app.on_state_changed = self.update_icon

    def run(self) -> None:
        """트레이 아이콘 생성 및 실행 (블로킹)"""
        menu = pystray.Menu(
            pystray.MenuItem("Clipboard History", self._show_history),
            pystray.MenuItem("Transfers", self._show_transfers),
            pystray.MenuItem("Settings", self._show_settings),
            pystray.MenuItem("View Log", self._view_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("About", self._show_about),
            pystray.MenuItem("Quit", self._quit),
        )

        self.icon = pystray.Icon(
            name="infinite-clipboard",
            icon=create_icon_image("gray"),
            title="Infinite Clipboard",
            menu=menu,
        )

        # setup 콜백: Windows에서 네이티브 아이콘 생성 후 호출됨
        # setup 없이 run()하면 Windows에서 메시지 루프 진입 전 종료될 수 있음
        self.icon.run(setup=self._on_tray_ready)

    def _on_tray_ready(self, icon: pystray.Icon) -> None:
        """트레이 아이콘이 OS에 등록된 후 호출되는 콜백"""
        icon.visible = True
        self.update_icon()
        logger.info("[트레이] 아이콘 활성화 완료")

    def stop(self) -> None:
        """트레이 아이콘 중지"""
        if self.icon is not None:
            self.icon.stop()

    def update_icon(self) -> None:
        """앱 상태에 따라 트레이 아이콘 색상 변경.

        v2.2.1 B3: active transfer 가 있으면 amber 우선 — connection 상태와 무관하게
        "지금 뭔가 진행 중" 신호. 완료/취소 시 connection 기반 색상으로 복귀.
        """
        if self.icon is None:
            return

        # v2.2.1 B3: active transfer 우선
        if hasattr(self.app, "_has_active_transfer") and self.app._has_active_transfer():
            self.icon.icon = create_icon_image("amber")
            return

        if self.app.config.mode == "server":
            if self.app.connected_clients > 0:
                self.icon.icon = create_icon_image("green")
            else:
                self.icon.icon = create_icon_image("yellow")
        else:
            if self.app.connected:
                self.icon.icon = create_icon_image("green")
            else:
                self.icon.icon = create_icon_image("red")

    def notify(self, title: str, message: str) -> None:
        """OS 토스트 알림"""
        try:
            from plyer import notification
            notification.notify(
                title=title,
                message=message,
                app_name="Infinite Clipboard",
                timeout=5,
            )
        except Exception as e:
            logger.warning(f"알림 전송 실패: {e}")

    # ── 메뉴 콜백 — 별도 프로세스로 UI 창 실행 ────────────────────────

    def _launch_window(self, window_type: str) -> None:
        """UI 창을 별도 프로세스로 띄운다 (tkinter/Gtk 메인루프 충돌 방지).

        - PyInstaller 번들: 자기 자신(InfiniteClipboard 바이너리)을 --window 인자로 재호출.
          번들에는 ui/*.py 원본이 없으므로 subprocess로 .py 를 가리킬 수 없다.
        - 개발 모드 (python3 main.py): 같은 해석기로 main.py 를 --window 인자와 함께 재호출.
        """
        if window_type not in ("settings", "history", "transfers"):
            return

        if getattr(sys, "frozen", False):
            # PyInstaller 번들 — sys.executable 이 자체 바이너리
            cmd = [sys.executable, "--window", window_type]
        else:
            # 개발 모드 — python + main.py
            main_py = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "main.py",
            )
            cmd = [sys.executable, main_py, "--window", window_type]

        threading.Thread(
            target=lambda: subprocess.Popen(cmd),
            daemon=True,
        ).start()

    def _show_history(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._launch_window("history")

    def _show_transfers(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._launch_window("transfers")

    def _show_settings(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._launch_window("settings")

    def _show_about(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """About 정보를 OS 알림으로 표시 (트레이에서 모달 창은 복잡하니 알림으로)"""
        try:
            from version import __version__, __app_name__, __author__, __license__
        except ImportError:
            __version__ = "2.0.0"
            __app_name__ = "Infinite Clipboard"
            __author__ = "dispather"
            __license__ = "MIT"
        self.notify(
            f"{__app_name__} v{__version__}",
            f"© {__author__} · {__license__} License\n"
            f"Tailscale LAN 클립보드/파일 공유",
        )

    def _view_log(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """로그 파일을 OS 기본 텍스트 편집기로 열기"""
        from config import LOG_FILE
        log_path = str(LOG_FILE)

        try:
            system = platform.system()
            if system == "Linux":
                # KDE → kate, GNOME → xdg-open
                if shutil.which("kate"):
                    subprocess.Popen(["kate", log_path])
                elif shutil.which("xdg-open"):
                    subprocess.Popen(["xdg-open", log_path])
            elif system == "Darwin":
                subprocess.Popen(["open", log_path])
            elif system == "Windows":
                os.startfile(log_path)
        except Exception as e:
            logger.error(f"로그 파일 열기 실패: {e}")

    def _quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        logger.info("[트레이] 종료 요청")
        self.app.stop()
        self.stop()
