"""
Infinite Clipboard 설정 창 — S4 리뉴얼 (Terminal Calm 방향성).

Refactoring UI 7원칙 + Design Craft + Interface Micro-Details 적용:
- 4섹션 그룹핑 (연결 / 인증 / 기기 / 자동 실행)
- Whisper borders (rgba alpha 0.06) 로 깊이 표현
- Signature: 상태색이 앱 전체를 물들임 (Tailscale 상태 dot + Auto 버튼 색)
- 한국어 라벨 통일 (Mode→모드, PIN→인증 키 등)
"""

import os
import sys

# 별도 프로세스로 실행될 때 부모 디렉토리(infinite-clipboard/)를 모듈 경로에 추가
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import platform
import shutil
import subprocess
import customtkinter

from config import AppConfig, save_config
from ui import theme as t
from ui.components import (
    SectionCard, SectionHeader, FormRow,
    PrimaryButton, SecondaryButton, IconButton, Badge,
    load_icon, apply_window_icon,
)

customtkinter.set_appearance_mode("dark")
customtkinter.set_default_color_theme("blue")


def _native_directory_dialog(parent=None, initial_dir=None) -> str:
    """OS 네이티브 디렉토리 선택 대화상자."""
    system = platform.system()

    if system == "Linux" and shutil.which("kdialog"):
        cmd = ["kdialog", "--getexistingdirectory", initial_dir or os.path.expanduser("~")]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return ""

    if system == "Linux" and shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory"]
        if initial_dir:
            cmd.append(f"--filename={initial_dir}/")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return ""

    from tkinter import filedialog
    return filedialog.askdirectory(title="저장 경로 선택", initialdir=initial_dir) or ""


def _detect_tailscale_ip() -> str:
    try:
        from core.tailscale import get_tailscale_ip
        return get_tailscale_ip() or ""
    except Exception:
        return ""


class SettingsWindow(customtkinter.CTkToplevel):
    """설정 창 — 4섹션 그룹핑."""

    _MODE_TO_KR = {"server": "서버", "client": "클라이언트"}
    _MODE_TO_EN = {"서버": "server", "클라이언트": "client"}
    # v2.2 R2: bind_address 매핑 (config 값 ↔ UI 라벨)
    _BIND_TO_KR = {"": "Tailscale 자동", "0.0.0.0": "모든 인터페이스"}
    _BIND_TO_EN = {"Tailscale 자동": "", "모든 인터페이스": "0.0.0.0"}

    def __init__(self, config: AppConfig, on_save_callback=None):
        super().__init__()

        self._config = config
        self._on_save_callback = on_save_callback
        self._tailscale_ip = _detect_tailscale_ip()

        self.title("Infinite Clipboard · 설정")
        # 4섹션 + 헤더 + 버튼 바가 한 화면에 모두 들어가는 높이.
        # 일반 CTkFrame 을 쓰므로 스크롤바가 뜨지 않는다.
        self.geometry("500x800")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.configure(fg_color=t.tray_bg)
        apply_window_icon(self)

        # ── 하단 버튼 바 (스크롤 바깥 고정) ─────────────────────────
        # 스크롤 영역 안에 넣으면 사용자가 저장 버튼을 찾으려 스크롤해야 해서 UX 불리.
        # pack(side="bottom") 으로 먼저 배치 → 스크롤 컨테이너가 나머지 공간 차지.
        btn_bar = customtkinter.CTkFrame(
            self, fg_color=t.tray_bg,
            border_color=t.whisper_line, border_width=0,
        )
        btn_bar.pack(side="bottom", fill="x", padx=t.SP[4], pady=(t.SP[2], t.SP[4]))

        PrimaryButton(btn_bar, text="저장하고 재시작", command=self._save).pack(
            side="right"
        )
        SecondaryButton(btn_bar, text="취소", command=self.destroy).pack(
            side="right", padx=(0, t.SP[2])
        )

        # ── 섹션 컨테이너 ────────────────────────────────────────────
        # CTkFrame: 창 크기가 4섹션 + 헤더 전부 수용하므로 스크롤 불필요.
        container = customtkinter.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=t.SP[4], pady=(t.SP[4], 0))

        # ── 헤더: 창 제목 + Tailscale 상태 인디케이터 ──
        header = customtkinter.CTkFrame(container, fg_color="transparent")
        header.pack(fill="x", pady=(0, t.SP[4]))
        customtkinter.CTkLabel(
            header, text="설정",
            font=t.FONT_HEADING,
            text_color=t.terminal_text,
            anchor="w",
        ).pack(side="left")

        if self._tailscale_ip:
            Badge(header, text=f"● Tailscale {self._tailscale_ip}",
                  variant="ok").pack(side="right")
        else:
            Badge(header, text="Tailscale 미연결",
                  variant="muted").pack(side="right")

        # ── 섹션 1. 연결 ──
        sec_conn = SectionCard(container)
        sec_conn.pack(fill="x", pady=(0, t.SP[3]))
        self._build_section_connection(sec_conn, config)

        # ── 섹션 2. 인증 ──
        sec_auth = SectionCard(container)
        sec_auth.pack(fill="x", pady=(0, t.SP[3]))
        self._build_section_auth(sec_auth, config)

        # ── 섹션 3. 기기 ──
        sec_dev = SectionCard(container)
        sec_dev.pack(fill="x", pady=(0, t.SP[3]))
        self._build_section_device(sec_dev, config)

        # ── 섹션 4. 자동 실행 ──
        sec_auto = SectionCard(container)
        sec_auto.pack(fill="x", pady=(0, t.SP[4]))
        self._build_section_autostart(sec_auto)

        # 초기 모드 상태 반영
        self._on_mode_changed(self._MODE_TO_KR.get(config.mode, "클라이언트"))

    # ─── 섹션 빌더 ──────────────────────────────────────────────────

    def _build_section_connection(self, parent, config):
        inner = self._section_inner(parent)
        SectionHeader(inner, title="연결").pack(fill="x", pady=(0, t.SP[3]))

        # 모드
        initial_mode = self._MODE_TO_KR.get(config.mode, "클라이언트")
        self._mode_var = customtkinter.StringVar(value=initial_mode)
        row_mode = FormRow(inner, "모드")
        self._mode_seg = customtkinter.CTkSegmentedButton(
            row_mode, values=["서버", "클라이언트"],
            variable=self._mode_var, command=self._on_mode_changed,
            height=30,
            selected_color=t.signal_ok,
            selected_hover_color=t.signal_ok_hi,
            unselected_color=t.relay_raised,
            unselected_hover_color=t.whisper_line_hi,
            text_color=t.terminal_text,
            text_color_disabled=t.spool_mute,
            font=(t.FAMILY, 12, "bold"),
        )
        self._mode_seg.pack(side="left", fill="x", expand=True)
        row_mode.pack(fill="x", pady=(0, t.SP[2]))

        # 호스트 (클라이언트 모드)
        row_host = FormRow(inner, "호스트")
        self._host_entry = self._make_entry(row_host)
        self._host_entry.pack(side="left", fill="x", expand=True, padx=(0, t.SP[1]))
        self._host_entry.insert(0, config.server_host)
        self._detect_btn = customtkinter.CTkButton(
            row_host, text="자동", width=48, height=32,
            corner_radius=t.RADIUS["md"],
            fg_color=t.signal_ok if self._tailscale_ip else t.relay_raised,
            hover_color=t.signal_ok_hi if self._tailscale_ip else t.whisper_line_hi,
            text_color=t.tray_bg if self._tailscale_ip else t.spool_label,
            font=(t.FAMILY, 11, "bold"),
            command=self._auto_detect_ip,
        )
        self._detect_btn.pack(side="left")
        row_host.pack(fill="x", pady=(0, t.SP[2]))

        # 포트
        row_port = FormRow(inner, "포트")
        self._port_entry = self._make_entry(row_port)
        self._port_entry.pack(side="left", fill="x", expand=True)
        self._port_entry.insert(0, str(config.port))
        self._port_entry.configure(
            validate="key", validatecommand=(self.register(self._validate_port), "%P"),
        )
        row_port.pack(fill="x", pady=(0, t.SP[2]))

        # v2.2 R2: 서버 bind 주소 (Server 모드 전용)
        # "" → Tailscale 자동 (미감지 시 0.0.0.0 fallback) / "0.0.0.0" → 모든 인터페이스
        initial_bind = self._BIND_TO_KR.get(config.bind_address, "Tailscale 자동")
        self._bind_var = customtkinter.StringVar(value=initial_bind)
        row_bind = FormRow(inner, "노출")
        self._bind_seg = customtkinter.CTkSegmentedButton(
            row_bind, values=["Tailscale 자동", "모든 인터페이스"],
            variable=self._bind_var,
            height=30,
            selected_color=t.signal_ok,
            selected_hover_color=t.signal_ok_hi,
            unselected_color=t.relay_raised,
            unselected_hover_color=t.whisper_line_hi,
            text_color=t.terminal_text,
            text_color_disabled=t.spool_mute,
            font=(t.FAMILY, 11, "bold"),
        )
        self._bind_seg.pack(side="left", fill="x", expand=True)
        row_bind.pack(fill="x")

    def _build_section_auth(self, parent, config):
        inner = self._section_inner(parent)
        SectionHeader(inner, title="인증").pack(fill="x", pady=(0, t.SP[3]))

        # 인증 키 + 눈 아이콘 버튼
        row_key = FormRow(inner, "인증 키")
        self._auth_entry = self._make_entry(row_key, font=t.FONT_MONO)
        self._auth_entry.pack(side="left", fill="x", expand=True, padx=(0, t.SP[1]))
        self._auth_entry.insert(0, config.auth_key)
        self._auth_entry.configure(show="•")
        self._pin_visible = False
        self._eye_btn = IconButton(
            row_key, icon_name="eye",
            command=self._toggle_pin_visibility,
        )
        self._eye_btn.pack(side="left")
        row_key.pack(fill="x", pady=(0, t.SP[2]))

        # Tailscale 자동 인증
        self._trust_var = customtkinter.BooleanVar(value=config.tailscale_trust)
        trust_row = customtkinter.CTkFrame(inner, fg_color="transparent")
        icon_lbl = load_icon("shield-check", size=16, color="dim")
        if icon_lbl is not None:
            customtkinter.CTkLabel(trust_row, text="", image=icon_lbl).pack(side="left", padx=(0, t.SP[2] - 2))
        customtkinter.CTkSwitch(
            trust_row,
            text="Tailscale 자동 인증",
            variable=self._trust_var, onvalue=True, offvalue=False,
            text_color=t.terminal_text,
            font=t.FONT_BODY,
            progress_color=t.signal_ok,
            button_color=t.terminal_text,
            button_hover_color="#fafafa",
            fg_color=t.relay_raised,
        ).pack(side="left", fill="x", expand=True)
        trust_row.pack(fill="x")

    def _build_section_device(self, parent, config):
        inner = self._section_inner(parent)
        SectionHeader(inner, title="기기").pack(fill="x", pady=(0, t.SP[3]))

        # 이름
        row_name = FormRow(inner, "이름")
        self._name_entry = self._make_entry(row_name)
        self._name_entry.pack(side="left", fill="x", expand=True)
        self._name_entry.insert(0, config.device_name)
        row_name.pack(fill="x", pady=(0, t.SP[2]))

        # 저장 경로 + 폴더 아이콘 버튼
        row_path = FormRow(inner, "저장 경로")
        self._path_entry = self._make_entry(row_path)
        self._path_entry.pack(side="left", fill="x", expand=True, padx=(0, t.SP[1]))
        self._path_entry.insert(0, config.download_path)
        IconButton(
            row_path, icon_name="folder-open",
            command=self._browse_directory,
        ).pack(side="left")
        row_path.pack(fill="x")

    def _build_section_autostart(self, parent):
        from core.autostart import (
            is_enabled as _autostart_is_enabled,
            is_supported as _autostart_is_supported,
        )
        inner = self._section_inner(parent)
        SectionHeader(inner, title="자동 실행").pack(fill="x", pady=(0, t.SP[3]))

        self._autostart_var = customtkinter.BooleanVar(
            value=_autostart_is_enabled() if _autostart_is_supported() else False,
        )
        row = customtkinter.CTkFrame(inner, fg_color="transparent")
        img = load_icon("power", size=16, color="dim")
        if img is not None:
            customtkinter.CTkLabel(row, text="", image=img).pack(side="left", padx=(0, t.SP[2] - 2))
        sw = customtkinter.CTkSwitch(
            row, text="OS 시작 시 자동 실행",
            variable=self._autostart_var, onvalue=True, offvalue=False,
            text_color=t.terminal_text,
            font=t.FONT_BODY,
            progress_color=t.signal_ok,
            fg_color=t.relay_raised,
        )
        sw.pack(side="left", fill="x", expand=True)
        if not _autostart_is_supported():
            sw.configure(state="disabled")
        row.pack(fill="x")

    # ─── 헬퍼 ──────────────────────────────────────────────────

    def _section_inner(self, card):
        """SectionCard 내부에 여백 있는 프레임을 만들고 반환."""
        inner = customtkinter.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True,
                   padx=t.CARD["padding"][0], pady=t.CARD["padding"][1])
        return inner

    def _make_entry(self, parent, font=None):
        return customtkinter.CTkEntry(
            parent,
            height=t.INPUT["height"],
            corner_radius=t.INPUT["radius"],
            fg_color=t.INPUT["fg_color"],
            border_color=t.INPUT["border_color"],
            border_width=t.INPUT["border_width"],
            text_color=t.INPUT["text_color"],
            placeholder_text_color=t.INPUT["placeholder_color"],
            font=font or t.FONT_BODY,
        )

    # ─── 동작 ──────────────────────────────────────────────────

    def _on_mode_changed(self, selected: str) -> None:
        if selected == "서버":
            self._host_entry.configure(state="disabled", fg_color=t.tray_bg)
            self._detect_btn.configure(state="disabled", fg_color=t.relay_raised)
            # v2.2 R2: server 모드일 때만 bind 옵션 활성
            self._bind_seg.configure(state="normal")
        else:
            self._host_entry.configure(state="normal", fg_color=t.INPUT["fg_color"])
            self._detect_btn.configure(
                state="normal",
                fg_color=t.signal_ok if self._tailscale_ip else t.relay_raised,
            )
            # client 모드: bind 옵션 비활성 (의미 없음)
            self._bind_seg.configure(state="disabled")

    def _auto_detect_ip(self) -> None:
        ip = _detect_tailscale_ip()
        if ip:
            self._host_entry.delete(0, "end")
            self._host_entry.insert(0, ip)
        else:
            self._detect_btn.configure(fg_color=t.signal_fail)

    def _toggle_pin_visibility(self) -> None:
        self._pin_visible = not self._pin_visible
        if self._pin_visible:
            self._auth_entry.configure(show="")
            self._eye_btn.configure(image=load_icon("eye-off", size=20, color="text"))
        else:
            self._auth_entry.configure(show="•")
            self._eye_btn.configure(image=load_icon("eye", size=20, color="text"))

    def _browse_directory(self) -> None:
        self.withdraw()
        directory = _native_directory_dialog(initial_dir=self._path_entry.get())
        self.deiconify()
        self.attributes("-topmost", True)
        self.lift()
        self.focus_force()
        if directory:
            self._path_entry.delete(0, "end")
            self._path_entry.insert(0, directory)

    @staticmethod
    def _validate_port(value: str) -> bool:
        return value == "" or value.isdigit()

    def _save(self) -> None:
        mode_kr = self._mode_var.get()
        self._config.mode = self._MODE_TO_EN.get(mode_kr, "client")

        host_val = self._host_entry.get().strip()
        if host_val:
            self._config.server_host = host_val

        port_text = self._port_entry.get().strip()
        if port_text.isdigit():
            self._config.port = int(port_text)

        self._config.auth_key = self._auth_entry.get()
        self._config.tailscale_trust = self._trust_var.get()
        self._config.device_name = self._name_entry.get().strip()
        self._config.download_path = self._path_entry.get().strip()
        # v2.2 R2: bind 주소 (Server 모드 전용 — client 모드면 사용자 의도 보존)
        if self._config.mode == "server":
            self._config.bind_address = self._BIND_TO_EN.get(self._bind_var.get(), "")

        save_config(self._config)

        # 자동 시작 토글 반영 (실패해도 설정 저장은 진행)
        try:
            from core.autostart import set_enabled as _autostart_set
            _autostart_set(bool(self._autostart_var.get()))
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"자동 시작 설정 실패: {e}")

        if self._on_save_callback:
            self._on_save_callback(self._config)

        self.destroy()


if __name__ == "__main__":
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

    from config import load_config
    config = load_config()
    win = SettingsWindow(config)
    win.after(150, win.focus_force)
    win.protocol("WM_DELETE_WINDOW", lambda: _close_all(win))
    root.mainloop()
