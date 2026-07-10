"""
파일 전송 상태 창 — S4 리뉴얼.

- 섹션 헤더에 카운트 배지 (진행 중 · N / 완료 · N)
- 진행 중: 방향 아이콘(arrow-up/down) + 파일명 + progress bar + 속도/ETA
- 완료:   check 아이콘 (accent) + 파일명 + 크기 + 시간 + 방향
- 빈 상태: inbox 아이콘 + 제목 + 설명

별도 프로세스에서 실행되므로 transfer_state.json 을 500ms 마다 폴링.
"""

import os
import sys
import platform
import subprocess

_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import json
import threading
import time
import customtkinter

from core.file_transfer import format_size as _format_size
from ui import theme as t
from ui.components import (
    load_icon, EmptyState, Badge, apply_window_icon,
    enable_mousewheel_scroll, add_tooltip,
)


def _format_time(timestamp: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(timestamp))


def _format_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}초 남음"
    if seconds < 3600:
        return f"{int(seconds // 60)}분 {int(seconds % 60)}초 남음"
    return f"{int(seconds // 3600)}시간 남음"


def _open_folder(path: str) -> None:
    """다운로드 폴더를 OS 파일 매니저로 연다 (ui/tray.py:_view_log 의 OS 분기 차용,
    대상이 파일이 아닌 디렉토리라는 점만 다름 — Linux 는 kate 필요 없이 xdg-open 하나면 됨).
    """
    try:
        system = platform.system()
        if system == "Linux":
            subprocess.Popen(["xdg-open", path])
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        elif system == "Windows":
            os.startfile(path)
    except Exception:
        pass


class TransferWindow(customtkinter.CTkToplevel):
    def __init__(self, state_file: str):
        super().__init__()

        self._state_file = state_file

        # M15: state_file 읽기(json.load)는 GUI 메인스레드(after 콜백)에서
        # 500ms 마다 동기 I/O 로 실행돼, 느린 디스크/대용량 completed 목록/
        # 백신 스캔 등에서 창 전체가 잠깐씩 멎을 수 있었다. 백그라운드 스레드가
        # 파일을 읽어 공유 상태만 갱신하고, 메인스레드는 그 결과를 (I/O 없이)
        # 위젯에 반영만 한다.
        self._state_lock = threading.Lock()
        # 최초 1회는 동기 읽기 — 창이 뜨자마자 현재 상태를 정확히 반영해야
        # 하므로(빈 기본값으로 그렸다가 배경 스레드 첫 결과를 기다리면 초기
        # 렌더가 늦거나 비결정적이 됨). 이후 갱신은 배경 스레드가 전담.
        self._latest_state: dict = self._read_state()
        self._reader_running = True
        threading.Thread(target=self._state_reader_loop, daemon=True).start()

        self.title("파일 전송")
        # 세로를 넉넉히 — 완료 항목 여러 개가 한눈에 보이도록
        self.geometry("540x640")
        self.attributes("-topmost", True)
        self.resizable(True, True)
        self.configure(fg_color=t.tray_bg)
        apply_window_icon(self)

        self._active_widgets: dict[str, dict] = {}
        self._completed_ids: set[str] = set()
        self._receivable_widgets: dict[str, dict] = {}

        # ── 받기 섹션 (v3.0 S4: lazy 미지원/실패 시 수동 수신) ──
        # 받을 항목이 있을 때만 표시. happy-path(paste 로 바로 가져옴)에선 비어 숨김.
        recv_bar = customtkinter.CTkFrame(self, fg_color="transparent")
        recv_bar.pack(fill="x", padx=t.SP[4], pady=(t.SP[4], t.SP[2]))
        customtkinter.CTkLabel(
            recv_bar, text="받기".upper(),
            font=t.FONT_SECTION, text_color=t.spool_label,
        ).pack(side="left")
        self._recv_count = Badge(recv_bar, text="0", variant="muted")
        self._recv_count.pack(side="left", padx=(t.SP[2], 0))
        self._recv_bar = recv_bar

        self._receive_frame = customtkinter.CTkScrollableFrame(
            self, fg_color=t.relay_surface,
            corner_radius=t.CARD["radius"],
            border_color=t.whisper_line, border_width=1,
            scrollbar_button_color=t.relay_raised,
            height=120,
        )
        self._receive_frame.pack(fill="x", padx=t.SP[4], pady=(0, t.SP[3]))
        # 받을 항목 0 이면 섹션 자체를 숨긴다 (아래 _poll_state 가 토글)
        self._recv_bar.pack_forget()
        self._receive_frame.pack_forget()
        self._recv_visible = False

        # ── 진행 중 섹션 ──
        active_bar = customtkinter.CTkFrame(self, fg_color="transparent")
        active_bar.pack(fill="x", padx=t.SP[4], pady=(t.SP[4], t.SP[2]))
        self._active_bar = active_bar  # 받기 섹션을 이 앞에 pack 하기 위한 참조

        customtkinter.CTkLabel(
            active_bar, text="진행 중".upper(),
            font=t.FONT_SECTION, text_color=t.spool_label,
        ).pack(side="left")
        self._active_count = Badge(active_bar, text="0", variant="muted")
        self._active_count.pack(side="left", padx=(t.SP[2], 0))

        self._active_frame = customtkinter.CTkScrollableFrame(
            self, fg_color=t.relay_surface,
            corner_radius=t.CARD["radius"],
            border_color=t.whisper_line, border_width=1,
            scrollbar_button_color=t.relay_raised,
            height=160,
        )
        self._active_frame.pack(fill="x", padx=t.SP[4], pady=(0, t.SP[3]))

        self._active_empty = EmptyState(
            self._active_frame,
            icon_name="inbox",
            title="진행 중인 전송이 없어요",
            desc="파일을 다른 PC에서 복사하면 여기 표시됩니다",
        )
        self._active_empty.pack(pady=t.SP[4])

        # ── 완료 섹션 ──
        comp_bar = customtkinter.CTkFrame(self, fg_color="transparent")
        comp_bar.pack(fill="x", padx=t.SP[4], pady=(t.SP[2], t.SP[2]))

        customtkinter.CTkLabel(
            comp_bar, text="완료".upper(),
            font=t.FONT_SECTION, text_color=t.spool_label,
        ).pack(side="left")
        self._comp_count = Badge(comp_bar, text="0", variant="muted")
        self._comp_count.pack(side="left", padx=(t.SP[2], 0))

        self._completed_frame = customtkinter.CTkScrollableFrame(
            self, fg_color=t.relay_surface,
            corner_radius=t.CARD["radius"],
            border_color=t.whisper_line, border_width=1,
            scrollbar_button_color=t.relay_raised,
        )
        self._completed_frame.pack(fill="both", expand=True, padx=t.SP[4], pady=(0, t.SP[4]))

        self._completed_empty = EmptyState(
            self._completed_frame,
            icon_name="circle-check",
            title="완료된 전송이 아직 없어요",
            desc="성공한 전송 기록이 여기 쌓입니다",
        )
        self._completed_empty.pack(pady=t.SP[4])

        self._poll_state()

    # ── JSON 폴링 ──────────────────────────────────────────────────────

    def _read_state(self) -> dict:
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"active": {}, "completed": []}

    def _state_reader_loop(self) -> None:
        """M15: 상태 파일 읽기(동기 I/O)를 GUI 메인스레드 밖에서 수행.

        메인스레드는 self._latest_state 를 락으로 보호된 채 읽기만 하므로
        파일 I/O 지연이 창 반응성에 영향을 주지 않는다.
        """
        while self._reader_running:
            state = self._read_state()
            with self._state_lock:
                self._latest_state = state
            time.sleep(0.5)

    def destroy(self):
        self._reader_running = False
        super().destroy()

    def _poll_state(self):
        with self._state_lock:
            state = self._latest_state
        active = state.get("active", {})
        completed = state.get("completed", [])
        receivable = state.get("receivable", [])

        # ── 받기 섹션 동기화 ──
        recv_ids = set()
        for entry in receivable:
            oid = entry.get("offer_id", "")
            if not oid:
                continue
            recv_ids.add(oid)
            if oid not in self._receivable_widgets:
                self._add_receivable_widget(entry)
            else:
                self._sync_receivable_widget(oid, entry)
        for oid in list(self._receivable_widgets):
            if oid not in recv_ids:
                self._remove_receivable_widget(oid)
        self._recv_count.configure(text=str(len(self._receivable_widgets)))
        self._toggle_receive_section(bool(self._receivable_widgets))

        for tid, info in active.items():
            if tid not in self._active_widgets:
                self._add_active_widget(tid, info)
            self._update_active_widget(tid, info)

        for tid in list(self._active_widgets):
            if tid not in active:
                self._remove_active_widget(tid)

        for entry in completed:
            tid = entry.get("transfer_id", "")
            if tid and tid not in self._completed_ids:
                self._add_completed_widget(entry)
                self._completed_ids.add(tid)

        # 카운트 배지 갱신
        self._active_count.configure(text=str(len(self._active_widgets)))
        self._comp_count.configure(text=str(len(self._completed_ids)))

        # 새로 추가된 자식 위젯에도 휠 스크롤 바인딩 (중복 바인딩 방지 플래그 내부 처리)
        enable_mousewheel_scroll(self._active_frame)
        enable_mousewheel_scroll(self._completed_frame)

        self.after(500, self._poll_state)

    # ── 진행 중 위젯 ───────────────────────────────────────────────────

    def _add_active_widget(self, transfer_id: str, info: dict) -> None:
        # 빈 상태 숨김
        try:
            self._active_empty.pack_forget()
        except Exception:
            pass

        filename = info.get("filename", "")
        total_size = info.get("total_size", 0)
        direction = info.get("direction", "")
        arrow_icon = "arrow-up" if direction == "send" else "arrow-down"

        frame = customtkinter.CTkFrame(
            self._active_frame,
            corner_radius=t.RADIUS["md"],
            fg_color=t.relay_raised,
            border_width=1, border_color=t.whisper_line,
        )
        frame.pack(fill="x", padx=t.SP[2], pady=t.SP[1])

        # 1행: 방향 아이콘 + 파일명 + 크기 + [v2.2.1 B2] 취소 버튼
        row1 = customtkinter.CTkFrame(frame, fg_color="transparent")
        row1.pack(fill="x", padx=t.SP[3], pady=(t.SP[2], 0))

        dir_img = load_icon(arrow_icon, size=16, color="accent")
        if dir_img is not None:
            customtkinter.CTkLabel(row1, text="", image=dir_img).pack(
                side="left", padx=(0, t.SP[2] - 2)
            )
        customtkinter.CTkLabel(
            row1, text=filename,
            font=t.FONT_BODY_BOLD, text_color=t.terminal_text, anchor="w",
        ).pack(side="left", fill="x", expand=True)

        # v2.2.1 B2: 취소 버튼 (오른쪽 끝)
        # Medium #7 (2026-07-10 감사): 24x24 는 앱 내 다른 아이콘 버튼(32x32,
        # theme.BTN["icon"])보다도 작았다 — 클릭 영역을 그 규격에 맞춤.
        cancel_btn = customtkinter.CTkButton(
            row1, text="X", width=32, height=32,
            corner_radius=t.RADIUS["sm"],
            fg_color="transparent",
            hover_color=t.signal_fail,
            text_color=t.spool_dim,
            font=(t.FAMILY, 11, "bold"),
            command=lambda tid=transfer_id: self._on_cancel_click(tid),
        )
        add_tooltip(cancel_btn, "전송 취소")
        cancel_btn.pack(side="right", padx=(t.SP[1], 0))

        customtkinter.CTkLabel(
            row1, text=_format_size(total_size),
            font=t.FONT_META, text_color=t.spool_dim,
        ).pack(side="right")

        # 2행: progress bar
        progress_bar = customtkinter.CTkProgressBar(
            frame, height=6,
            fg_color=t.tray_bg,
            progress_color=t.signal_ok,
        )
        progress_bar.pack(fill="x", padx=t.SP[3], pady=(t.SP[2], 0))
        progress_bar.set(0)

        # 3행: 속도 / ETA
        row3 = customtkinter.CTkFrame(frame, fg_color="transparent")
        row3.pack(fill="x", padx=t.SP[3], pady=(t.SP[1], t.SP[2]))

        speed_label = customtkinter.CTkLabel(
            row3, text="준비 중...",
            font=t.FONT_META, text_color=t.spool_dim, anchor="w",
        )
        speed_label.pack(side="left")
        eta_label = customtkinter.CTkLabel(
            row3, text="",
            font=t.FONT_META, text_color=t.spool_dim, anchor="e",
        )
        eta_label.pack(side="right")

        self._active_widgets[transfer_id] = {
            "frame": frame,
            "progress_bar": progress_bar,
            "speed_label": speed_label,
            "eta_label": eta_label,
            "cancel_btn": cancel_btn,
            "cancelling": False,
        }

    # v2.2.1 B2: 취소 IPC ────────────────────────────────────────────────

    def _get_cancel_request_file(self) -> str:
        """main.py 의 _get_cancel_request_file 와 동일 경로."""
        from config import _get_config_dir
        return str(_get_config_dir() / "cancel_requests.json")

    def _on_cancel_click(self, transfer_id: str) -> None:
        """취소 버튼 클릭 → cancel_requests.json 에 transfer_id append.

        main.py 의 _watch_cancel_requests thread 가 0.5초 안에 처리.
        UI 는 즉시 "취소 중..." 표시로 사용자 피드백.
        """
        w = self._active_widgets.get(transfer_id)
        if not w or w.get("cancelling"):
            return

        # cancel_requests.json 에 append
        cancel_file = self._get_cancel_request_file()
        try:
            requests = []
            try:
                with open(cancel_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    requests = loaded
            except (FileNotFoundError, json.JSONDecodeError):
                pass

            if transfer_id not in requests:
                requests.append(transfer_id)
            with open(cancel_file, "w", encoding="utf-8") as f:
                json.dump(requests, f)
        except OSError:
            return

        # UI 즉시 피드백
        w["cancelling"] = True
        w["cancel_btn"].configure(state="disabled", text="...")
        w["speed_label"].configure(text="취소 중...")
        w["eta_label"].configure(text="")

    # ── v3.0 S4: 받기 섹션 ──────────────────────────────────────────────

    def _get_receive_request_file(self) -> str:
        """main.py 의 _get_receive_request_file 와 동일 경로."""
        from config import _get_config_dir
        return str(_get_config_dir() / "receive_requests.json")

    def _toggle_receive_section(self, show: bool) -> None:
        """받을 항목 유무에 따라 받기 섹션을 보이거나 숨긴다 (진행중 섹션 앞)."""
        if show and not self._recv_visible:
            self._recv_bar.pack(fill="x", padx=t.SP[4], pady=(t.SP[4], t.SP[2]),
                                before=self._active_bar)
            self._receive_frame.pack(fill="x", padx=t.SP[4], pady=(0, t.SP[3]),
                                     before=self._active_bar)
            self._recv_visible = True
        elif not show and self._recv_visible:
            self._receive_frame.pack_forget()
            self._recv_bar.pack_forget()
            self._recv_visible = False

    def _add_receivable_widget(self, entry: dict) -> None:
        oid = entry.get("offer_id", "")
        name = entry.get("name", "파일")
        total_size = entry.get("total_size", 0)

        frame = customtkinter.CTkFrame(
            self._receive_frame, corner_radius=t.RADIUS["md"],
            fg_color=t.relay_raised, border_width=1, border_color=t.whisper_line,
        )
        frame.pack(fill="x", padx=t.SP[2], pady=t.SP[1])

        row = customtkinter.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=t.SP[3], pady=t.SP[2])

        dl_img = load_icon("arrow-down", size=16, color="accent")
        if dl_img is not None:
            customtkinter.CTkLabel(row, text="", image=dl_img).pack(
                side="left", padx=(0, t.SP[2] - 2)
            )
        customtkinter.CTkLabel(
            row, text=name, font=t.FONT_BODY_BOLD,
            text_color=t.terminal_text, anchor="w",
        ).pack(side="left", fill="x", expand=True)

        recv_btn = customtkinter.CTkButton(
            row, text="받기", width=52, height=26,
            corner_radius=t.RADIUS["sm"], fg_color=t.relay_raised,
            hover_color=t.signal_ok, text_color=t.terminal_text, font=t.FONT_META,
            command=lambda o=oid: self._on_receive_click(o),
        )
        recv_btn.pack(side="right", padx=(t.SP[1], 0))
        customtkinter.CTkLabel(
            row, text=_format_size(total_size),
            font=t.FONT_META, text_color=t.spool_dim,
        ).pack(side="right", padx=(0, t.SP[2]))

        reason_label = customtkinter.CTkLabel(
            frame, text="", font=t.FONT_META, text_color=t.signal_fail, anchor="w",
        )

        w = {
            "frame": frame, "btn": recv_btn, "requested": False,
            "reason_label": reason_label, "last_failure_at": None,
        }
        self._receivable_widgets[oid] = w

        # 앱 재시작 등으로 이미 last_failure 가 실려 도착한 entry — 바로 재시도 상태로.
        failure = entry.get("last_failure")
        if failure:
            self._set_receivable_retry_state(w, failure)

    def _set_receivable_retry_state(self, w: dict, failure: dict) -> None:
        """받기 실패(재시도 가능) 상태로 위젯 갱신.

        M6: 실패해도 위젯을 재사용하는 한 "requested" 플래그가 리셋 안 되면
        버튼이 영구 고장된다. 여기선 main.py 가 보낸 last_failure 타임스탬프
        (서버 쪽 실제 상태 변화)를 근거로만 리셋하므로 그 회귀를 재도입하지 않는다.
        """
        w["requested"] = False
        w["last_failure_at"] = failure.get("failed_at")
        try:
            w["btn"].configure(text="재시도", state="normal")
        except Exception:
            pass
        label = w.get("reason_label")
        if label is not None:
            try:
                label.configure(text=failure.get("message", "실패 — 재시도 가능"))
                if not label.winfo_ismapped():
                    label.pack(fill="x", padx=t.SP[3], pady=(0, t.SP[2]))
            except Exception:
                pass

    def _sync_receivable_widget(self, offer_id: str, entry: dict) -> None:
        """이미 있는 받기 위젯을 최신 entry 와 맞춘다 — last_failure 가 새로 생기면
        (또는 다른 사유로 갱신되면) 재시도 상태로 전환."""
        w = self._receivable_widgets.get(offer_id)
        if not w:
            return
        failure = entry.get("last_failure")
        if not failure:
            return
        failed_at = failure.get("failed_at")
        if failed_at and failed_at != w.get("last_failure_at"):
            self._set_receivable_retry_state(w, failure)

    def _remove_receivable_widget(self, offer_id: str) -> None:
        w = self._receivable_widgets.pop(offer_id, None)
        if w:
            try:
                w["frame"].destroy()
            except Exception:
                pass

    def _on_receive_click(self, offer_id: str) -> None:
        """받기/재시도 버튼 → receive_requests.json 에 offer_id append (main 폴링 처리)."""
        w = self._receivable_widgets.get(offer_id)
        if not w or w.get("requested"):
            return
        req_file = self._get_receive_request_file()
        try:
            requests = []
            try:
                with open(req_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    requests = loaded
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            if offer_id not in requests:
                requests.append(offer_id)
            with open(req_file, "w", encoding="utf-8") as f:
                json.dump(requests, f)
        except OSError:
            return
        # 즉시 UI 피드백 (완료되면 main 이 state 에서 제거 → 위젯 사라짐)
        w["requested"] = True
        w["btn"].configure(state="disabled", text="받는 중")
        label = w.get("reason_label")
        if label is not None and label.winfo_ismapped():
            try:
                label.pack_forget()
            except Exception:
                pass

    def _update_active_widget(self, transfer_id: str, info: dict) -> None:
        w = self._active_widgets.get(transfer_id)
        if not w:
            return

        total = info.get("total_size", 1) or 1
        transferred = info.get("bytes_transferred", 0)
        start_time = info.get("start_time", time.time())

        percent = min(transferred / total, 1.0)
        w["progress_bar"].set(percent)

        elapsed = time.time() - start_time
        if elapsed > 0 and transferred > 0:
            speed = transferred / elapsed
            speed_text = f"{_format_size(int(speed))}/s  ·  {int(percent * 100)}%"
            remaining = (total - transferred) / speed if speed > 0 else 0
            eta_text = _format_eta(remaining) if remaining > 0 else "완료 중..."
        else:
            speed_text = "준비 중..."
            eta_text = ""

        w["speed_label"].configure(text=speed_text)
        w["eta_label"].configure(text=eta_text)

    def _remove_active_widget(self, transfer_id: str) -> None:
        w = self._active_widgets.pop(transfer_id, None)
        if w:
            w["frame"].destroy()
        if not self._active_widgets:
            self._active_empty.pack(pady=t.SP[4])

    # ── 완료 위젯 ──────────────────────────────────────────────────────

    def _add_completed_widget(self, entry: dict) -> None:
        try:
            self._completed_empty.pack_forget()
        except Exception:
            pass

        filename = entry.get("filename", "")
        total_size = entry.get("total_size", 0)
        direction = entry.get("direction", "")
        completed_at = entry.get("completed_at", time.time())
        dir_arrow = "arrow-up" if direction == "send" else "arrow-down"

        frame = customtkinter.CTkFrame(
            self._completed_frame,
            corner_radius=t.RADIUS["md"],
            fg_color="transparent",
            height=36,
        )
        frame.pack(fill="x", padx=t.SP[2], pady=t.SP[1] // 2)
        frame.pack_propagate(False)

        # 왼쪽: check 아이콘 (accent) + 방향 아이콘 (dim)
        check_img = load_icon("circle-check", size=16, color="accent")
        if check_img is not None:
            customtkinter.CTkLabel(frame, text="", image=check_img).pack(
                side="left", padx=(t.SP[3], t.SP[1])
            )
        arrow_img = load_icon(dir_arrow, size=14, color="dim")
        if arrow_img is not None:
            customtkinter.CTkLabel(frame, text="", image=arrow_img).pack(
                side="left", padx=(0, t.SP[2] - 2)
            )

        # 오른쪽: 크기 · 시간
        customtkinter.CTkLabel(
            frame, text=f"{_format_size(total_size)}  ·  {_format_time(completed_at)}",
            font=t.FONT_META, text_color=t.spool_dim,
        ).pack(side="right", padx=(t.SP[2], t.SP[3]))

        # 받기 버튼으로 저장된 항목만 최종 위치가 있음(config.download_path) — lazy-paste
        # 는 임시 스테이징으로 가서 "폴더 열기" 개념 자체가 없다.
        saved_dir = entry.get("path")
        if entry.get("via_receive_button") and saved_dir:
            customtkinter.CTkButton(
                frame, text="폴더 열기", width=68, height=24,
                corner_radius=t.RADIUS["sm"], fg_color=t.relay_raised,
                hover_color=t.signal_ok, text_color=t.terminal_text, font=t.FONT_META,
                command=lambda p=saved_dir: _open_folder(p),
            ).pack(side="right", padx=(t.SP[1], 0))

        # 중앙: 파일명
        customtkinter.CTkLabel(
            frame, text=filename,
            font=t.FONT_BODY, text_color=t.terminal_text, anchor="w",
        ).pack(side="left", fill="x", expand=True)


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

    from config import _get_config_dir
    state_file = str(_get_config_dir() / "transfer_state.json")
    win = TransferWindow(state_file)
    win.after(150, win.focus_force)
    win.protocol("WM_DELETE_WINDOW", lambda: _close_all(win))
    root.mainloop()
