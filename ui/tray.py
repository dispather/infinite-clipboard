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

from ui import theme as t
from ui import tray_status
from ui.i18n import t as tr

if TYPE_CHECKING:
    from main import InfiniteClipboard

logger = logging.getLogger("infinite-clipboard.tray")

# 2026-07-12 mac-studio 오딧: _launch_window 가 spawn 중임을 표시하는 sentinel.
# 실제 Popen 객체와 구분해 "아직 프로세스가 안 떴지만 다른 스레드가 이미
# 요청했다"는 상태를 표현한다 (동시 호출 race 차단용).
_SPAWNING = object()

# macOS 트레이 갱신 경로를 1회만 로그 — 실패해도 조용히 직접 호출로 떨어지므로
# 실기 검증 때 "메인스레드 경유가 실제로 됐는지" 를 로그로만 구분할 수 있다.
_ui_dispatch_logged = False


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


# 2026-09-28 UX 검토 B2/B5: 활동 배지 색 — 연결 색(아이콘 본체)과 겹치지 않는 색
_BADGE_COLORS = {"busy": t.signal_busy, "new": t.signal_new}


def _with_badge(base: Image.Image, badge: str) -> Image.Image:
    """우하단에 어두운 테두리를 두른 원형 배지를 합성한다.

    16~22px 트레이에서도 보이도록 지름을 아이콘의 약 45% 로 크게 잡고, 본체 색과
    섞이지 않게 앱 캔버스색(tray_bg) 테두리로 분리한다.
    """
    img = base.copy()
    w, h = img.size
    d = int(min(w, h) * 0.46)
    ring = max(1, int(min(w, h) * 0.07))
    x0, y0 = w - d, h - d
    draw = ImageDraw.Draw(img)
    draw.ellipse([x0, y0, w - 1, h - 1], fill=t.tray_bg)
    draw.ellipse([x0 + ring, y0 + ring, w - 1 - ring, h - 1 - ring],
                 fill=_BADGE_COLORS.get(badge, t.signal_new))
    return img


def create_icon_image(color: str = "green", badge: str | None = None) -> Image.Image:
    """상태 색상에 해당하는 트레이 아이콘을 로드한다.

    Args:
        color: "green" / "amber"(또는 "yellow") / "red" / "gray" 중 하나.
                미지정·오타 시 "gray"로 폴백.
        badge: None / "busy"(전송 중) / "new"(방금 받음) — 연결 색 위에 겹치는 활동 표시.
    """
    if badge:
        key = f"{color}+{badge}"
        cached = _icon_cache.get(key)
        if cached is None:
            cached = _with_badge(create_icon_image(color), badge)
            _icon_cache[key] = cached
        return cached

    cached = _icon_cache.get(color)
    if cached is not None:
        return cached

    filename = _STATE_TO_FILE.get(color, _STATE_TO_FILE["gray"])
    path = _assets_dir() / filename
    try:
        image = Image.open(path).convert("RGBA")
    except Exception as e:
        logger.warning(f"아이콘 로드 실패 ({path}): {e} — 폴백 단색 아이콘 사용")
        # Medium #6 (2026-07-10 감사): theme.py 값을 손으로 베껴 쓰면 나중에
        # theme 쪽만 바뀌었을 때 조용히 드리프트한다(실제로 "gray" 가 이미
        # signal_idle 과 다른 회색으로 어긋나 있었음) — 토큰을 직접 참조.
        fallbacks = {
            "green": t.signal_ok, "amber": t.signal_wait, "yellow": t.signal_wait,
            "red": t.signal_fail, "gray": t.signal_idle,
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
        # 2026-07-12 mac-studio 오딧 #1: window_type 별 마지막 spawn 결과 추적
        # (중복 창 생성 가드). 값은 subprocess.Popen 객체 또는 _SPAWNING sentinel.
        self._window_procs: dict = {}
        self._window_procs_lock = threading.Lock()
        # B1: 메뉴 맨 위 상태 줄(마지막으로 반영한 값) — 바뀔 때만 update_menu
        self._status_lines: list = []
        # 2026-09-29 자동 업데이트: «업데이트 설치» 항목 라벨(None=항목 없음)·진행 중 여부
        self._update_label: str | None = None
        self._update_busy = False
        self._badge_timer: threading.Timer | None = None

    def run(self) -> None:
        """트레이 아이콘 생성 및 실행 (블로킹)"""
        # B1(2026-09-28): 동적 메뉴 — 맨 위에 연결 상태 줄(비활성 항목)을 두고,
        # update_icon 이 상태가 바뀔 때 update_menu() 로 다시 만든다(AppIndicator 는
        # 메뉴를 보여줄 때가 아니라 update_menu 시점에 생성 — pystray 문서).
        # 메뉴 라벨은 예전엔 영어 고정이었다 — 설정 언어를 따른다.
        self.icon = pystray.Icon(
            name="infinite-clipboard",
            icon=create_icon_image("gray"),
            title="Infinite Clipboard",
            menu=pystray.Menu(self._menu_items),
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
        if self._badge_timer is not None:
            self._badge_timer.cancel()
        if self.icon is not None:
            self.icon.stop()

    def _lang(self) -> str:
        return getattr(self.app, "_lang", "ko") or "ko"

    def _snapshot(self) -> dict:
        getter = getattr(self.app, "status_snapshot", None)
        if getter is None:
            return {}
        try:
            return getter()
        except Exception as e:
            logger.debug(f"[트레이] 상태 스냅샷 실패: {e}")
            return {}

    def _menu_items(self):
        """pystray 동적 메뉴 — update_menu() 때마다 다시 평가된다."""
        lang = self._lang()
        for line in self._status_lines:
            yield pystray.MenuItem(line, None, enabled=False)
        if self._status_lines:
            yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem(tr("클립보드 이력", lang), self._show_history)
        yield pystray.MenuItem(tr("파일 전송", lang), self._show_transfers)
        yield pystray.MenuItem(tr("설정", lang), self._show_settings)
        yield pystray.MenuItem(tr("로그 보기", lang), self._view_log)
        yield pystray.MenuItem(tr("임시 파일 정리", lang), self._cleanup_staging)
        yield pystray.Menu.SEPARATOR
        # 2026-09-29 자동 업데이트 — 새 버전이 있을 때만 «업데이트 설치 (vX)»
        if self._update_label:
            yield pystray.MenuItem(self._update_label, self._install_update,
                                   enabled=not self._update_busy)
        yield pystray.MenuItem(tr("업데이트 확인", lang), self._check_update)
        yield pystray.MenuItem(tr("정보", lang), self._show_about)
        yield pystray.MenuItem(tr("종료", lang), self._quit)

    @staticmethod
    def _on_ui_thread(fn) -> None:
        """macOS 는 pystray 가 AppKit(setMenu_/setToolTip_) 을 호출 스레드에서 바로
        부른다 — 네트워크 스레드에서 오는 갱신을 메인 run loop 로 넘긴다. AppIndicator
        는 pystray 가 이미 GLib idle 로 넘기고(@mainloop), Win32 는 Shell_NotifyIcon/
        HMENU 가 스레드 무관이라 그대로 호출한다."""
        global _ui_dispatch_logged
        if platform.system() == "Darwin":
            try:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(fn)
                if not _ui_dispatch_logged:
                    _ui_dispatch_logged = True
                    logger.info("[트레이] macOS 갱신 경로: AppHelper.callAfter(메인 run loop)")
                return
            except Exception as e:
                if not _ui_dispatch_logged:
                    _ui_dispatch_logged = True
                    logger.warning(f"[트레이] macOS 갱신 경로: 직접 호출 폴백 — AppHelper 사용 불가: {e}")
        fn()

    def update_icon(self) -> None:
        """앱 상태 → 트레이 아이콘(색+배지) · 툴팁 · 메뉴 상태 줄 갱신.

        2026-09-28 UX 검토 B2: 예전엔 전송 중이면 아이콘 전체를 amber 로 바꿔
        "서버: 접속 기기 0대(대기)" 와 같은 색이 됐다. 이제 색은 연결 상태만
        뜻하고, 전송 중(busy)·방금 받음(new) 은 우하단 배지로 겹친다.
        """
        if self.icon is None:
            return
        snap = self._snapshot()
        if not snap:
            return
        lang = self._lang()
        color, badge = tray_status.icon_state(snap)
        lines = tray_status.status_lines(snap, lang)
        title = tray_status.tooltip_text(snap, lang)
        update_label = tray_status.update_menu_label(snap, lang)
        update_busy = (snap.get("update") or {}).get("phase") == "downloading"
        image = create_icon_image(color, badge)

        def _apply() -> None:
            icon = self.icon
            if icon is None:
                return
            icon.icon = image
            if icon.title != title:
                icon.title = title
            # 상태 줄·업데이트 항목이 바뀔 때만 메뉴 재생성(열린 메뉴가 닫히는 빈도를 늘리지 않게)
            if (lines != self._status_lines or update_label != self._update_label
                    or update_busy != self._update_busy):
                self._status_lines = lines
                self._update_label = update_label
                self._update_busy = update_busy
                icon.update_menu()

        self._on_ui_thread(_apply)

        # B5: "방금 받음" 배지는 시간이 지나면 스스로 꺼져야 한다 — 만료 시점에 한 번 더 갱신
        if badge == "new":
            last = snap.get("last_received") or {}
            import time as _time
            remaining = tray_status.RECEIVED_BADGE_SECONDS - (_time.time() - float(last.get("at", 0)))
            if self._badge_timer is not None:
                self._badge_timer.cancel()
            self._badge_timer = threading.Timer(max(0.5, remaining + 0.2), self.update_icon)
            self._badge_timer.daemon = True
            self._badge_timer.start()

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
        if window_type not in ("settings", "history", "transfers", "about"):
            return

        # 2026-07-12 mac-studio 오딧 #1: 대용량 수신 자동 팝업(main.py
        # _launch_transfer_window)과 이 메뉴 클릭이 겹치면 창이 2개 뜨는
        # 문제 — 같은 window_type 이 이미 떠있거나(poll() is None) 다른
        # 스레드가 방금 spawn 을 시작(sentinel)했으면 재실행을 생략한다.
        # check-then-mark 를 lock 안에서 원자적으로 수행해 두 트리거가
        # 거의 동시에 들어와도 race 없이 하나만 spawn 된다.
        with self._window_procs_lock:
            entry = self._window_procs.get(window_type)
            if entry is not None and (entry is _SPAWNING or entry.poll() is None):
                logger.debug(f"[트레이] {window_type} 창이 이미 떠있어(또는 준비 중) 재실행 생략")
                return
            self._window_procs[window_type] = _SPAWNING

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

        def _spawn() -> None:
            try:
                # M11: 함정 #8 과 동일 이유 — start_new_session 없으면 이 창이
                # tray 프로세스와 같은 세션/프로세스 그룹에 묶여, 개발 모드에서
                # 터미널의 Ctrl+C(SIGINT) 가 그룹 전체에 가면 "독립 프로세스"
                # 여야 할 창도 함께 죽는다.
                proc = subprocess.Popen(cmd, start_new_session=True)
            except Exception:
                with self._window_procs_lock:
                    self._window_procs.pop(window_type, None)
                raise
            with self._window_procs_lock:
                self._window_procs[window_type] = proc

        threading.Thread(target=_spawn, daemon=True).start()

    def _show_history(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._launch_window("history")

    def _show_transfers(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._launch_window("transfers")

    def _show_settings(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._launch_window("settings")

    def _cleanup_staging(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """v2.3 audit P2: 사용자가 즉시 staging 정리 트리거.

        TTL 만료 항목만 삭제 (진행 중 transfer 보호). 결과는 OS 알림으로 통지.
        """
        try:
            self.app._cleanup_staging(notify=True)
        except Exception as e:
            logger.warning(f"[트레이] cleanup 트리거 실패: {e}")

    def _check_update(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """2026-09-29: 수동 업데이트 확인 — 네트워크 요청이라 메뉴 스레드를 막지 않게 스레드로."""
        threading.Thread(target=self.app.check_for_update, kwargs={"manual": True},
                         daemon=True).start()

    def _install_update(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """2026-09-29: «업데이트 설치» — 가드·다운로드는 app 이 판정(즉시 반환)."""
        self.app.request_update_install()

    def _show_about(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """About 모달 창 표시 — 별도 프로세스(--window about)로 띄움.

        v2.2.1 까지는 OS 알림(plyer.notification)으로 표시했으나
        Windows Focus Assist / macOS 알림 권한 미부여 시 silent 처리되어
        사용자가 버전 확인 불가. 3 OS 동일 표시 보장을 위해 모달로 전환.
        """
        self._launch_window("about")

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
