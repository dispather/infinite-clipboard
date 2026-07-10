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
import threading
import customtkinter

from config import AppConfig, save_config
from ui import theme as t
from ui.components import (
    SectionCard, SectionHeader, FormRow,
    PrimaryButton, SecondaryButton, IconButton, Badge,
    load_icon, apply_window_icon, enable_mousewheel_scroll, bind_focus_ring,
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
    # v2.3 audit #10: 충돌 정책 매핑. 한국어 라벨은 사용자가 직관적으로 선택.
    # 영어 키는 config.py 의 VALID_FILE_CONFLICT_POLICIES 와 동일.
    _POLICY_TO_KR = {
        "overwrite": "덮어쓰기",
        "skip": "건너뛰기",
        "rename_with_timestamp": "시점 추가",
        "rename_with_counter": "번호 추가",
    }
    _POLICY_TO_EN = {v: k for k, v in _POLICY_TO_KR.items()}
    _POLICY_ORDER = ["덮어쓰기", "건너뛰기", "시점 추가", "번호 추가"]

    def __init__(self, config: AppConfig, on_save_callback=None):
        super().__init__()

        self._config = config
        self._on_save_callback = on_save_callback
        # H3: Tailscale CLI 조회는 후보가 여러 개면 각 5초 timeout 이 누적돼 최대
        # 10초(macOS)까지 걸릴 수 있다. 창 생성을 막지 않도록 백그라운드 스레드로
        # 조회하고, 메인 스레드는 after() 폴링으로 결과를 받아 위젯을 갱신한다
        # (Tk 는 스레드 안전하지 않음 — transfer_window.py 의 상태파일 폴링과
        # 동일하게, 백그라운드 스레드는 위젯을 직접 건드리지 않는다).
        self._tailscale_ip = ""
        self._tailscale_ip_ready = False
        threading.Thread(target=self._detect_tailscale_bg, daemon=True).start()

        self.title("Infinite Clipboard · 설정")
        # Critical 감사(2026-07-10): 이전엔 950px 고정 + resizable(False,False) +
        # 스크롤 없는 CTkFrame 이라, 세로 해상도가 낮은 화면(예: 1366x768 노트북)
        # 에서 저장 버튼까지 도달 불가능했다. 컨테이너를 CTkScrollableFrame 으로
        # 바꾸고 창을 리사이즈 가능하게 열어, 화면이 작아도 스크롤로 전 섹션에
        # 도달 가능하게 한다. 버튼 바는 여전히 스크롤 밖(하단 고정)이라 항상 보임.
        self.geometry("500x700")
        self.minsize(420, 320)
        self.resizable(True, True)
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
        # Critical 감사(2026-07-10): CTkScrollableFrame 으로 전환 — 낮은 세로
        # 해상도 화면에서도 4섹션 전부에 스크롤로 도달 가능. 버튼 바는 위에서
        # 이미 self 에 직접 pack(side="bottom") 했으므로 스크롤 영역 밖에 고정.
        container = customtkinter.CTkScrollableFrame(self, fg_color="transparent")
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

        # H3: 초기엔 "확인 중" — 조회가 끝나면 _poll_tailscale_result 가 갱신
        self._ts_badge = Badge(header, text="Tailscale 확인 중…", variant="muted")
        self._ts_badge.pack(side="right")

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

        # 함정 #10: CTkScrollableFrame 은 canvas 위에서만 기본으로 휠이 먹으므로
        # 자식 위젯 트리 전체에 재귀 바인딩 필요 (창이 작아져 스크롤이 실제로
        # 필요해졌을 때 휠로도 이동 가능하도록).
        enable_mousewheel_scroll(container)

        # H3: 위젯 구성이 끝난 뒤 폴링 시작 (백그라운드 스레드는 이미 위에서 시작됨)
        self.after(100, self._poll_tailscale_result)

    def _detect_tailscale_bg(self) -> None:
        """백그라운드 스레드 — Tk 위젯을 직접 건드리지 않고 결과만 저장."""
        self._tailscale_ip = _detect_tailscale_ip()
        self._tailscale_ip_ready = True

    def _poll_tailscale_result(self) -> None:
        """메인 스레드 폴링 — 백그라운드 조회가 끝나면 배지/버튼을 갱신."""
        if not self._tailscale_ip_ready:
            self.after(150, self._poll_tailscale_result)
            return

        ip = self._tailscale_ip
        # 리뷰 발견: Badge._VARIANTS(private) 를 직접 읽는 대신 공개 메서드 사용
        # — Badge 내부 색상표가 바뀌어도 이 호출부는 영향받지 않음.
        self._ts_badge.set_variant(
            "ok" if ip else "muted",
            text=f"● Tailscale {ip}" if ip else "Tailscale 미연결",
        )
        # 자동 버튼 — 서버 모드로 전환돼 disabled 상태면 그대로 둔다
        # (_on_mode_changed 의 disabled 색을 덮어쓰지 않기 위함)
        if self._detect_btn.cget("state") != "disabled":
            self._detect_btn.configure(
                fg_color=t.signal_ok if ip else t.relay_raised,
                hover_color=t.signal_ok_hi if ip else t.whisper_line_hi,
                text_color=t.tray_bg if ip else t.spool_label,
            )

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
            command=self._update_bind_warning,
        )
        self._bind_seg.pack(side="left", fill="x", expand=True)
        row_bind.pack(fill="x")

        # M1: "모든 인터페이스" 선택 시 평문 프로토콜 스니핑 위험을 opt-in
        # 시점에 바로 보여준다 (로그만으로는 사용자가 결정 전에 못 봄).
        self._bind_warning = customtkinter.CTkLabel(
            inner, text="⚠ 프로토콜은 평문 — 같은 물리 LAN 의 제3자가 스니핑 가능",
            font=t.FONT_META, text_color=t.signal_wait, anchor="w",
        )
        self._update_bind_warning(initial_bind)

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

        # M4: 과거 라벨 "Tailscale 자동 인증"은 이 설정이 실제로는 인증을
        # 우회/완화하지 않는데도(HMAC 은 항상 필수) 그렇게 암시해 혼동을 줬다.
        # 실제 효과는 접속 peer 가 Tailscale IP 대역이면 로그 한 줄을 남기는
        # 것뿐 — 라벨/캡션으로 정확히 표기.
        self._trust_var = customtkinter.BooleanVar(value=config.tailscale_trust)
        trust_row = customtkinter.CTkFrame(inner, fg_color="transparent")
        icon_lbl = load_icon("shield-check", size=16, color="dim")
        if icon_lbl is not None:
            customtkinter.CTkLabel(trust_row, text="", image=icon_lbl).pack(side="left", padx=(0, t.SP[2] - 2))
        customtkinter.CTkSwitch(
            trust_row,
            text="Tailscale 피어 로그 표시",
            variable=self._trust_var, onvalue=True, offvalue=False,
            text_color=t.terminal_text,
            font=t.FONT_BODY,
            progress_color=t.signal_ok,
            button_color=t.terminal_text,
            button_hover_color="#fafafa",
            fg_color=t.relay_raised,
        ).pack(side="left", fill="x", expand=True)
        trust_row.pack(fill="x")
        customtkinter.CTkLabel(
            inner, text="인증에는 영향 없음 — HMAC 은 항상 필수",
            font=t.FONT_META, text_color=t.spool_dim, anchor="w",
        ).pack(fill="x", pady=(0, t.SP[2]))

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
        row_path.pack(fill="x", pady=(0, t.SP[2]))

        # v2.3 audit #10: 충돌 처리 정책 — 동일 파일명 재수신 시 동작.
        # SHA-256 dedup 이 우선 적용되어 같은 파일은 정책 무관 자동 skip.
        initial_policy = self._POLICY_TO_KR.get(config.file_conflict_policy, "번호 추가")
        self._policy_var = customtkinter.StringVar(value=initial_policy)
        row_policy = FormRow(inner, "충돌 처리")
        self._policy_seg = customtkinter.CTkSegmentedButton(
            row_policy,
            values=self._POLICY_ORDER,
            variable=self._policy_var,
            height=30,
            selected_color=t.signal_ok,
            selected_hover_color=t.signal_ok_hi,
            unselected_color=t.relay_raised,
            unselected_hover_color=t.whisper_line_hi,
            text_color=t.terminal_text,
            text_color_disabled=t.spool_mute,
            font=(t.FAMILY, 11, "bold"),
        )
        self._policy_seg.pack(side="left", fill="x", expand=True)
        row_policy.pack(fill="x", pady=(0, t.SP[2]))

        # v2.3 audit P2: staging TTL + 즉시 정리 버튼.
        row_ttl = FormRow(inner, "임시 정리")
        self._ttl_entry = self._make_entry(row_ttl, font=t.FONT_MONO)
        self._ttl_entry.configure(width=70)
        self._ttl_entry.pack(side="left", padx=(0, t.SP[1]))
        self._ttl_entry.insert(0, str(config.staging_ttl_hours))
        customtkinter.CTkLabel(
            row_ttl, text="시간 후",
            font=t.FONT_LABEL,
            text_color=t.spool_label,
        ).pack(side="left", padx=(0, t.SP[3]))
        SecondaryButton(
            row_ttl, text="지금 정리",
            command=self._cleanup_staging_now,
        ).pack(side="right")
        row_ttl.pack(fill="x")

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

    def _cleanup_staging_now(self) -> None:
        """v2.3 audit P2: "지금 정리" 버튼 콜백.

        settings 창은 별도 프로세스이므로 main 프로세스 상태와 무관하게 직접
        cleanup_staging_dir 호출. 사용자가 막 입력한 TTL 값을 즉시 반영
        (저장 안 했어도 dry-run 처럼 동작). 잘못된 값은 24 폴백.
        """
        from tkinter import messagebox
        try:
            from core.file_transfer import cleanup_staging_dir, format_size
            import tempfile
            from pathlib import Path
            ttl_text = self._ttl_entry.get().strip()
            if ttl_text.isdigit() and 1 <= int(ttl_text) <= 720:
                ttl = int(ttl_text)
            else:
                ttl = 24
            staging = Path(tempfile.gettempdir()) / "ic_clipboard"
            deleted, freed = cleanup_staging_dir(staging, ttl)
            if deleted == 0:
                messagebox.showinfo("임시 파일 정리", "정리할 항목이 없습니다.")
            else:
                messagebox.showinfo(
                    "임시 파일 정리",
                    f"{deleted}개 항목 삭제\n{format_size(freed)} 회수",
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"cleanup 실패: {e}")
            messagebox.showerror("임시 파일 정리", f"정리 실패: {e}")

    def _section_inner(self, card):
        """SectionCard 내부에 여백 있는 프레임을 만들고 반환."""
        inner = customtkinter.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True,
                   padx=t.CARD["padding"][0], pady=t.CARD["padding"][1])
        return inner

    def _make_entry(self, parent, font=None):
        entry = customtkinter.CTkEntry(
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
        # High #3 (2026-07-10 감사): border_color_focus 토큰이 정의만 되고
        # 미배선이라 Tab 이동 시 포커스 위치가 안 보였다 — 여기서 실제 배선.
        bind_focus_ring(entry, t.INPUT["border_color"], t.INPUT["border_color_focus"])
        return entry

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

    def _update_bind_warning(self, selected: str) -> None:
        """M1: "모든 인터페이스" 선택 시에만 평문 프로토콜 경고 캡션 표시."""
        if selected == "모든 인터페이스":
            self._bind_warning.pack(fill="x", pady=(t.SP[1], 0))
        else:
            self._bind_warning.pack_forget()

    def _auto_detect_ip(self) -> None:
        """"자동" 버튼 클릭 — H3: 같은 CLI 조회라 최대 10초 걸릴 수 있어
        버튼을 잠그고 백그라운드 스레드 + 폴링으로 비동기 처리."""
        self._detect_btn.configure(state="disabled")
        self._auto_detect_ip_ready = False
        threading.Thread(target=self._auto_detect_ip_bg, daemon=True).start()
        self.after(100, self._poll_auto_detect_ip)

    def _auto_detect_ip_bg(self) -> None:
        self._auto_detect_ip_result = _detect_tailscale_ip()
        self._auto_detect_ip_ready = True

    def _poll_auto_detect_ip(self) -> None:
        if not self._auto_detect_ip_ready:
            self.after(150, self._poll_auto_detect_ip)
            return

        ip = self._auto_detect_ip_result
        self._detect_btn.configure(state="normal")
        if ip:
            self._host_entry.delete(0, "end")
            self._host_entry.insert(0, ip)
            self._detect_btn.configure(fg_color=t.signal_ok)
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
        # v2.3 audit #10: 충돌 정책. 잘못된 라벨이면 default 유지.
        self._config.file_conflict_policy = self._POLICY_TO_EN.get(
            self._policy_var.get(), "rename_with_counter",
        )
        # v2.3 audit P2: staging TTL. 잘못된 입력이면 _validate_and_clamp 가 보정.
        ttl_text = self._ttl_entry.get().strip()
        if ttl_text.isdigit():
            self._config.staging_ttl_hours = int(ttl_text)

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

    from config import load_config
    config = load_config()
    win = SettingsWindow(config)
    win.after(150, win.focus_force)
    win.protocol("WM_DELETE_WINDOW", lambda: _close_all(win))
    root.mainloop()
