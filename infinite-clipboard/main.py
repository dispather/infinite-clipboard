"""
Infinite Clipboard v2 — 메인 엔트리포인트

core 모듈 조립: config → Network → ClipboardManager → 이벤트 연결
"""

import sys
import os
import time
import argparse
import logging
import threading
import platform
import base64
import tempfile
from pathlib import Path

# Linux(특히 KDE Plasma Wayland)에서 pystray가 Xorg 백엔드를 선택하면
# StatusNotifierItem과 통합되지 않아 메뉴가 동작하지 않는다.
# GI AppIndicator 바인딩이 있으면 그쪽을 우선한다.
if sys.platform.startswith("linux") and "PYSTRAY_BACKEND" not in os.environ:
    try:
        import gi
        for _ver_name in ("AyatanaAppIndicator3", "AppIndicator3"):
            try:
                gi.require_version(_ver_name, "0.1")
                __import__(f"gi.repository.{_ver_name}")
                os.environ["PYSTRAY_BACKEND"] = "appindicator"
                break
            except (ValueError, ImportError):
                continue
    except ImportError:
        pass

from config import AppConfig, load_config, save_config
from core.protocol import (
    MSG_CLIPBOARD, MSG_PING, MSG_PONG,
    MSG_FILE_READY, MSG_FILE_REQUEST, MSG_FILE_START,
    MSG_FILE_CHUNK, MSG_FILE_END, MSG_FILE_ACK,
    MSG_FILE_RESUME, MSG_FILE_CANCEL, MSG_FILE_CANCEL_ACK, MSG_FILE_ERROR,
    CANCEL_ACK_ROLE_SENDER, CANCEL_ACK_ROLE_RECEIVER, CANCEL_ACK_ROLE_NONE,
    CANCEL_ACK_STATUS_OK, CANCEL_ACK_STATUS_UNKNOWN,
    MSG_TRANSFER_COMPLETE,
)
from core.network import NetworkServer, NetworkClient
from core.protocol import Protocol
from core.clipboard_manager import ClipboardManager
from core.file_transfer import FileTransferManager, FileMetadata, CheckpointManager, Checkpoint, format_size
from core.privacy import detect_sensitive_kind

# 로깅 설정 — 콘솔 + 파일 이중 출력
from config import LOG_FILE

def _setup_logging(debug=False):
    """로깅 초기화: 콘솔(INFO) + 파일(DEBUG)"""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # 파일 핸들러 — 항상 DEBUG 레벨, 최대 5MB 로테이션
    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler(
        str(LOG_FILE), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)

    # v2.2 R1: POSIX 0o600 권한. log 에 클립보드 길이/타입/peer IP 등 metadata 포함.
    # multi-user 시스템에서 다른 사용자의 정보 노출 차단. Windows 는 user ACL 기본.
    if os.name == "posix":
        try:
            import os as _os
            _os.chmod(str(LOG_FILE), 0o600)
        except OSError:
            pass

    # 콘솔 핸들러 — --debug면 DEBUG, 아니면 INFO
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    console_handler.setFormatter(fmt)
    root_logger.addHandler(console_handler)

logger = logging.getLogger("infinite-clipboard")


class InfiniteClipboard:
    """앱 핵심 로직 — 네트워크 + 클립보드 + 파일전송 조립"""

    def __init__(self, config: AppConfig):
        self.config = config
        self.running = False
        self._restart_requested = False

        # 프로토콜 (바이너리 청크 생성용)
        self.protocol = Protocol(config.auth_key)

        # 클립보드 매니저
        self.clipboard = ClipboardManager()

        # 파일 전송 매니저
        self.file_manager = FileTransferManager(
            download_path=config.download_path,
            max_file_size_gb=config.max_file_size_gb,
        )
        self.checkpoint_manager = CheckpointManager()

        # 파일 전송 상태 (스레드 안전: _transfers_lock으로 보호)
        self.pending_transfers = {}   # {transfer_id: FileMetadata} 수신 대기
        self.outgoing_files = {}      # {transfer_id: (metadata, [abs_path])} 발신 대기
        self._transfers_lock = threading.Lock()
        self._cancelled_transfers = set()  # 중단 요청된 transfer_id

        # 전송 진행 상태 (TransferWindow JSON 공유용, _progress_lock으로 보호)
        self._transfer_progress = {}   # {transfer_id: {filename, total_size, bytes_transferred, direction, start_time}}
        self._completed_transfers = [] # [{transfer_id, filename, total_size, direction, completed_at}]
        self._progress_lock = threading.Lock()
        self._last_state_save = 0.0    # 마지막 상태 저장 시각 (스로틀링용)

        # 클립보드 이력 (최근 N개)
        self.clipboard_history = []

        # 네트워크 (모드에 따라 서버 또는 클라이언트)
        self.server = None
        self.client = None

        # 상태
        self.connected = False
        self.connected_clients = 0

        # 상태 변경 콜백 (UI 갱신용, TrayApp에서 설정)
        self.on_state_changed = None
        # 트레이 핸들 (main() 에서 TrayApp 생성 후 주입). v2.3 audit P2 cleanup
        # 알림 송출에 사용. headless / 테스트 환경에선 None 이므로 가드 필요.
        self.tray = None

    def start(self):
        """앱 시작"""
        self.running = True

        # v2.3 audit P2: staging TTL cleanup (시작 시 1회).
        # 진행 중 transfer 는 temp_dir 에 있고 staging 에는 완료 파일만 들어가므로
        # 시작 시 1회로 충분 — 주기 스레드 불필요. 사용자가 즉시 정리하려면
        # 트레이 메뉴 "Cleanup Staging" 사용.
        self._cleanup_staging(notify=False)

        if self.config.mode == "server":
            self._start_server()
        else:
            self._start_client()

        # 클립보드 모니터링 시작
        monitor_thread = threading.Thread(target=self._monitor_clipboard, daemon=True)
        monitor_thread.start()

        # v2.2.1 B2: TransferWindow cancel 버튼 IPC 폴링
        cancel_thread = threading.Thread(target=self._watch_cancel_requests, daemon=True)
        cancel_thread.start()

        logger.info("Infinite Clipboard 동작 시작")

    def _staging_dir(self) -> Path:
        """OS 임시 디렉토리 안의 staging 폴더 경로 (paste 가능 임시 저장소).

        0o700: 다중 사용자 시스템에서 다른 사용자 읽기 차단.
        호출처는 _handle_transfer_complete + _cleanup_staging 두 곳.
        """
        staging = Path(tempfile.gettempdir()) / "ic_clipboard"
        staging.mkdir(parents=True, exist_ok=True, mode=0o700)
        return staging

    def _cleanup_staging(self, notify: bool = False) -> None:
        """v2.3 audit P2: staging 디렉토리 mtime TTL 만료 항목 삭제.

        Args:
            notify: True 면 트레이 OS 알림 표시 (사용자 액션). False 면 silent
                (앱 시작 시 자동 호출).
        """
        from core.file_transfer import cleanup_staging_dir, format_size
        try:
            staging = self._staging_dir()
            deleted, freed = cleanup_staging_dir(
                staging, self.config.staging_ttl_hours,
            )
            if notify and self.tray:
                if deleted == 0:
                    self.tray.notify("임시 파일 정리", "정리할 항목이 없습니다.")
                else:
                    self.tray.notify(
                        "임시 파일 정리",
                        f"{deleted}개 삭제 · {format_size(freed)} 회수",
                    )
        except Exception as e:
            logger.warning(f"staging cleanup 실패: {e}")

    def stop(self):
        """앱 종료"""
        self.running = False
        if hasattr(self.clipboard, 'cleanup'):
            self.clipboard.cleanup()
        if self.server:
            self.server.stop()
        if self.client:
            self.client.stop()
        logger.info("Infinite Clipboard 종료")

    # ── 서버 모드 ──────────────────────────────────────────────────────

    def _start_server(self):
        """서버 모드로 시작"""
        self.server = NetworkServer(
            port=self.config.port,
            auth_key=self.config.auth_key,
            tailscale_trust=self.config.tailscale_trust,
            bind_address=self.config.bind_address,
        )

        self.server.on_client_connected = self._on_server_client_connected
        self.server.on_client_disconnected = self._on_server_client_disconnected
        self.server.on_message_received = self._on_server_message

        self.server.start()
        self.connected = True
        # 실제 bind IP 는 NetworkServer.start() 가 로깅 (Tailscale 자동 / 0.0.0.0)

    def _on_server_client_connected(self, sock, address, name):
        """클라이언트 연결 이벤트"""
        with self.server.clients_lock:
            self.connected_clients = len(self.server.clients)
        logger.info(f"[서버] 클라이언트 연결: {name} ({self.connected_clients}대)")
        self._notify_state_changed()

    def _on_server_client_disconnected(self, sock, address, name):
        """클라이언트 연결 해제 이벤트"""
        with self.server.clients_lock:
            self.connected_clients = len(self.server.clients)
        logger.info(f"[서버] 클라이언트 해제: {name} ({self.connected_clients}대)")
        self._notify_state_changed()

    def _on_server_message(self, sock, message):
        """서버: 클라이언트로부터 메시지 수신"""
        msg_type = message.get("type")
        data = message.get("data")

        if msg_type == MSG_CLIPBOARD:
            self._handle_clipboard_received(data)
            # 다른 클라이언트에 브로드캐스트
            self.server.broadcast(MSG_CLIPBOARD, data, exclude_sock=sock)

        elif msg_type == MSG_FILE_READY:
            self._handle_file_ready(data)
            self.server.broadcast(MSG_FILE_READY, data, exclude_sock=sock)

        elif msg_type == MSG_FILE_REQUEST:
            self._handle_file_request(data)
            self.server.broadcast(MSG_FILE_REQUEST, data, exclude_sock=sock)

        elif msg_type == MSG_FILE_CHUNK:
            self._handle_file_chunk(data)
            # 원본 와이어 바이트로 중계 (재직렬화 방지, 바이너리 프레임 유지)
            raw = message.get("_raw")
            if raw:
                self.server.broadcast_raw(raw, exclude_sock=sock)
            else:
                self.server.broadcast(msg_type, data, exclude_sock=sock)

        elif msg_type == MSG_FILE_END:
            self._handle_file_end(data)
            self.server.broadcast(msg_type, data, exclude_sock=sock)

        elif msg_type == MSG_TRANSFER_COMPLETE:
            self._handle_transfer_complete(data)
            self.server.broadcast(msg_type, data, exclude_sock=sock)

        elif msg_type == MSG_FILE_ERROR:
            self._handle_file_error_received(data)
            self.server.broadcast(msg_type, data, exclude_sock=sock)

        elif msg_type == MSG_FILE_CANCEL:
            # v2.1: 자체 처리 + broadcast (서버도 송수신 당사자일 수 있음)
            self._handle_file_cancel(data)
            self.server.broadcast(msg_type, data, exclude_sock=sock)

        elif msg_type == MSG_FILE_CANCEL_ACK:
            # v2.3.1: cancel ack — 자체 처리 (originator 가 서버일 수 있음) + broadcast
            self._handle_file_cancel_ack(data)
            self.server.broadcast(msg_type, data, exclude_sock=sock)

        elif msg_type in (MSG_FILE_START, MSG_FILE_ACK, MSG_FILE_RESUME):
            self.server.broadcast(msg_type, data, exclude_sock=sock)

        elif msg_type == MSG_PING:
            self.server.send_to(sock, MSG_PONG)

    # ── 클라이언트 모드 ────────────────────────────────────────────────

    def _start_client(self):
        """클라이언트 모드로 시작"""
        self.client = NetworkClient(
            host=self.config.server_host,
            port=self.config.port,
            auth_key=self.config.auth_key,
            device_name=self.config.device_name,
        )

        self.client.on_connected = self._on_client_connected
        self.client.on_disconnected = self._on_client_disconnected
        self.client.on_message_received = self._on_client_message

        self.client.start()

    def _on_client_connected(self):
        """서버 연결 성공"""
        self.connected = True
        logger.info("[클라이언트] 서버 연결 성공")
        self._notify_state_changed()

    def _on_client_disconnected(self):
        """서버 연결 끊김"""
        self.connected = False
        logger.info("[클라이언트] 서버 연결 끊김")
        self._notify_state_changed()

    def _on_client_message(self, message):
        """클라이언트: 서버로부터 메시지 수신"""
        msg_type = message.get("type")
        data = message.get("data")

        if msg_type == MSG_CLIPBOARD:
            self._handle_clipboard_received(data)

        elif msg_type == MSG_FILE_READY:
            self._handle_file_ready(data)

        elif msg_type == MSG_FILE_REQUEST:
            self._handle_file_request(data)

        elif msg_type == MSG_FILE_CHUNK:
            self._handle_file_chunk(data)

        elif msg_type == MSG_FILE_END:
            self._handle_file_end(data)

        elif msg_type == MSG_TRANSFER_COMPLETE:
            self._handle_transfer_complete(data)

        elif msg_type == MSG_FILE_ERROR:
            self._handle_file_error_received(data)

        elif msg_type == MSG_FILE_CANCEL:
            self._handle_file_cancel(data)

        elif msg_type == MSG_FILE_CANCEL_ACK:
            self._handle_file_cancel_ack(data)

        elif msg_type == MSG_PING:
            self.client.send(MSG_PONG)

    # ── 클립보드 처리 ──────────────────────────────────────────────────

    def _monitor_clipboard(self):
        """클립보드 변경 폴링 루프"""
        while self.running:
            try:
                if self._is_network_active():
                    changed, content_type, content = self.clipboard.has_changed()

                    if changed and content is not None:
                        logger.info(f"[클립보드] 변경 감지: {content_type}")
                        if content_type == "files":
                            self._send_file_ready(content)
                        else:
                            self._send_clipboard(content_type, content)
                        self._add_to_history(content_type, content)

            except Exception as e:
                logger.error(f"클립보드 모니터링 오류: {e}")

            time.sleep(self.config.clipboard_check_interval)

    def _send_clipboard(self, content_type, content):
        """클립보드 내용을 네트워크로 전송"""
        data = {"content_type": content_type, "content": content}

        if self.config.mode == "server" and self.server:
            self.server.broadcast(MSG_CLIPBOARD, data)
        elif self.config.mode == "client" and self.client:
            self.client.send(MSG_CLIPBOARD, data)

    def _handle_clipboard_received(self, data):
        """수신된 클립보드 데이터 처리"""
        if not data:
            return

        content_type = data.get("content_type")
        content = data.get("content")

        if content_type and content is not None:
            logger.info(f"[클립보드] 수신: {content_type}")
            self.clipboard.set_clipboard_content(content_type, content)
            self._add_to_history(content_type, content)

    def _add_to_history(self, content_type, content):
        """클립보드 이력에 추가 + 파일에 저장.

        v2.2 R1: history_privacy_mode 가 ON 이고 텍스트가 민감 패턴
        (JWT, AWS key, PEM 등) 을 포함하면 history 저장 skip.
        클립보드 동기화 자체는 영향 없음.
        """
        if (self.config.history_privacy_mode
                and content_type == "text"
                and isinstance(content, str)):
            kind = detect_sensitive_kind(content)
            if kind:
                logger.info(
                    f"[history] 민감 패턴({kind}) 감지로 저장 skip: {len(content)}자"
                )
                return

        entry = {
            "type": content_type,
            "content": content if content_type == "text" else f"[{content_type}]",
            "preview": self._make_preview(content_type, content),
            "timestamp": time.time(),
        }
        self.clipboard_history.insert(0, entry)
        if len(self.clipboard_history) > self.config.clipboard_history_size:
            self.clipboard_history.pop()
        self._save_history_file()

    def _save_history_file(self):
        """이력을 JSON 파일에 저장 (별도 프로세스 History 창 공유용, 원자적 쓰기).

        v2.2 R1: POSIX 0o600 권한 적용. history 에는 텍스트 클립보드 내용이
        그대로 들어갈 수 있어 (multi-user 시스템에서) 다른 사용자 읽기 차단.
        Windows 는 user 폴더 ACL 기본이라 별도 처리 불필요.
        """
        try:
            import json
            import tempfile
            history_file = self._get_history_file()
            dir_name = os.path.dirname(history_file)
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self.clipboard_history, f, ensure_ascii=False)
                # POSIX 권한 (Windows 는 무시)
                if os.name == "posix":
                    try:
                        os.chmod(tmp_path, 0o600)
                    except OSError:
                        pass
                os.replace(tmp_path, history_file)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.error(f"이력 파일 저장 오류: {e}")

    @staticmethod
    def _get_history_file():
        """이력 파일 경로"""
        from config import _get_config_dir
        return str(_get_config_dir() / "clipboard_history.json")

    @staticmethod
    def _make_preview(content_type, content):
        """이력 미리보기 텍스트 생성"""
        if content_type == "text":
            return content[:80] + "..." if len(content) > 80 else content
        elif content_type == "image":
            return "[이미지]"
        elif content_type == "files":
            if isinstance(content, list):
                return f"[파일 {len(content)}개]"
            return "[파일]"
        return "[알 수 없음]"

    # ── 파일 전송 처리 ────────────────────────────────────────────────

    def _handle_file_error_received(self, data):
        """FILE_ERROR 수신 → 송신 중인 전송 중단"""
        transfer_id = data.get("transfer_id")
        if transfer_id:
            self._cancelled_transfers.add(transfer_id)
            # v2.1: manager active state 도 함께 동기화 (transfer_id 일치 시에만 set)
            self.file_manager.cancel_outgoing(transfer_id, reason="error")
            logger.info(f"[파일] 전송 중단 요청 수신: {transfer_id}")

    def _handle_file_cancel(self, data):
        """MSG_FILE_CANCEL 수신 → 송수신 양쪽 cleanup.

        검증 실패 (잘못된 transfer_id 형식 / unknown reason) 시 silent ignore.
        매칭되는 active transfer 가 없어도 silent ignore (이미 정리됨).

        v2.3.1: cleanup 끝나면 MSG_FILE_CANCEL_ACK 송출 — originator UI 가
        "취소 중..." 상태를 정확한 시점에 정리할 수 있게 함. broadcast 라
        originator 가 받으면 의미 있고, 자기 자신이 받으면 silent ignore.
        """
        parsed = Protocol.parse_file_cancel(data)
        if parsed is None:
            return  # 검증 실패 → silent drop
        transfer_id = parsed["transfer_id"]
        reason = parsed["reason"]
        logger.info(f"[파일] CANCEL 수신: {transfer_id} (reason={reason})")

        # 송신 측 정리 — 워커 stop 신호 + state 정리
        cancelled_out = self.file_manager.cancel_outgoing(transfer_id, reason=reason)
        if cancelled_out is not None:
            self._cancelled_transfers.add(transfer_id)
            with self._transfers_lock:
                self.outgoing_files.pop(transfer_id, None)
            self._cancel_transfer_tracking(transfer_id)

        # 수신 측 정리 — 임시 디렉토리 + pending state
        cancelled_in = self.file_manager.cancel_incoming(transfer_id, reason=reason)
        if cancelled_in is not None:
            self.file_manager.cleanup_incoming_artifacts(transfer_id)
            self.file_manager.end_incoming(transfer_id)
            with self._transfers_lock:
                self.pending_transfers.pop(transfer_id, None)
            self._cancel_transfer_tracking(transfer_id)

        # v2.3.1 ack — 어떤 role 로 cleanup 했는지 originator 에게 통지.
        if cancelled_out is not None:
            role, status = CANCEL_ACK_ROLE_SENDER, CANCEL_ACK_STATUS_OK
        elif cancelled_in is not None:
            role, status = CANCEL_ACK_ROLE_RECEIVER, CANCEL_ACK_STATUS_OK
        else:
            role, status = CANCEL_ACK_ROLE_NONE, CANCEL_ACK_STATUS_UNKNOWN
        try:
            ack_wire = self.protocol.create_file_cancel_ack(transfer_id, role, status)
            self._send_raw_msg(ack_wire)
        except ValueError as e:
            # transfer_id 형식 검증은 parse_file_cancel 에서 통과했으므로 여기 도달 안 함
            logger.warning(f"[파일] cancel_ack 송출 실패: {e}")

    def _handle_file_cancel_ack(self, data):
        """MSG_FILE_CANCEL_ACK 수신 — peer 의 cleanup 완료 신호 (v2.3.1).

        UI 는 transfer_state.json 폴링으로 transfer 가 사라지면 행을 자동 제거하므로
        여기서는 logger 만. 향후 timeout/retry 정책 도입 시 확장 지점.
        검증 실패 / 자기 자신이 보낸 ack / unknown transfer_id 모두 silent.
        """
        parsed = Protocol.parse_file_cancel_ack(data)
        if parsed is None:
            return
        logger.info(
            f"[파일] CANCEL_ACK 수신: {parsed['transfer_id']} "
            f"(peer role={parsed['role']} status={parsed['status']})"
        )

    def _send_msg(self, msg_type, data=None):
        """메시지 전송 헬퍼"""
        if self.config.mode == "server" and self.server:
            self.server.broadcast(msg_type, data)
        elif self.config.mode == "client" and self.client:
            self.client.send(msg_type, data)

    def _send_raw_msg(self, raw_bytes):
        """이미 직렬화된 메시지 전송 헬퍼 (바이너리 프레임용)"""
        if self.config.mode == "server" and self.server:
            self.server.broadcast_raw(raw_bytes)
        elif self.config.mode == "client" and self.client:
            self.client.send_raw(raw_bytes)

    def _send_file_ready(self, file_paths):
        """파일 복사 감지 → 메타데이터 수집 → FILE_READY 전송

        v2.1: 단일 active outgoing invariant 강제. 기존 transfer 가 있으면
        cancel 송출 + 워커 stop 신호 후 새 transfer 시작.
        """
        try:
            metadata = self.file_manager.collect_metadata(file_paths)
        except Exception as e:
            logger.error(f"파일 메타데이터 수집 오류: {e}")
            return

        # v2.1 invariant: 기존 active outgoing 이 있으면 supersede
        _new_active, superseded = self.file_manager.begin_outgoing(metadata, file_paths)
        if superseded is not None:
            logger.info(
                f"[파일] 기존 송신 중단 (superseded): {superseded.transfer_id} "
                f"→ {metadata.transfer_id}"
            )
            # 워커 stop 신호 (워커는 _cancelled_transfers polling 중)
            self._cancelled_transfers.add(superseded.transfer_id)
            # 수신측 cleanup 신호 송출 (검증 실패 시 raise → 무시)
            try:
                self._send_msg(MSG_FILE_CANCEL, {
                    "transfer_id": superseded.transfer_id,
                    "reason": "superseded",
                })
            except Exception as e:
                logger.error(f"superseded cancel 송출 실패: {e}")
            # 로컬 outgoing state 정리 — 워커 finally 가 또 정리하지만 idempotent
            with self._transfers_lock:
                self.outgoing_files.pop(superseded.transfer_id, None)
            self._cancel_transfer_tracking(superseded.transfer_id)

        with self._transfers_lock:
            self.outgoing_files[metadata.transfer_id] = (metadata, file_paths)
        self._send_msg(MSG_FILE_READY, metadata.to_dict())

        # 전송 상태 추적 시작 (송신)
        display_name = metadata.root_name or f"{metadata.file_count}개 파일"
        self._track_transfer(metadata.transfer_id, display_name, metadata.total_size, "send")

        logger.info(
            f"[파일] 전송 준비: {metadata.file_count}개, "
            f"{format_size(metadata.total_size)}"
        )

    def _handle_file_ready(self, data):
        """FILE_READY 수신 → 메타데이터 저장 → 자동 전송 요청 (체크포인트 있으면 resume)

        v2.1: 단일 active incoming invariant 강제. 기존 incoming 이 있으면
        임시 디렉토리·pending state 를 정리한 뒤 새 transfer 등록.
        """
        try:
            metadata = FileMetadata.from_dict(data)

            # v2.1 invariant: 기존 active incoming 이 있으면 supersede
            _new_active, superseded = self.file_manager.begin_incoming(metadata)
            if superseded is not None:
                logger.info(
                    f"[파일] 기존 수신 중단 (superseded): {superseded.transfer_id} "
                    f"→ {metadata.transfer_id}"
                )
                self.file_manager.cleanup_incoming_artifacts(superseded.transfer_id)
                with self._transfers_lock:
                    self.pending_transfers.pop(superseded.transfer_id, None)
                self._cancel_transfer_tracking(superseded.transfer_id)

            with self._transfers_lock:
                self.pending_transfers[metadata.transfer_id] = metadata

            if not self.file_manager.check_disk_space(metadata.total_size):
                logger.warning(f"[파일] 디스크 공간 부족: {metadata.transfer_id}")
                return

            # 전송 상태 추적 시작 (수신)
            display_name = metadata.root_name or f"{metadata.file_count}개 파일"
            self._track_transfer(metadata.transfer_id, display_name, metadata.total_size, "receive")

            # 체크포인트 확인 → resume 정보 포함
            request_data = {"transfer_id": metadata.transfer_id}
            checkpoint = self.checkpoint_manager.load(metadata.transfer_id)
            if checkpoint and checkpoint.completed_files:
                request_data["completed_files"] = checkpoint.completed_files
                request_data["completed_hashes"] = checkpoint.completed_hashes
                # 이미 수신한 바이트 계산하여 진행률 보정
                completed_bytes = sum(
                    f["size"] for f in metadata.files
                    if f["path"] in checkpoint.completed_files
                )
                self._update_transfer_bytes(metadata.transfer_id, completed_bytes)
                logger.info(
                    f"[파일] 이어받기 요청: {len(checkpoint.completed_files)}개 파일 완료, "
                    f"{format_size(completed_bytes)} 건너뜀"
                )

            self._send_msg(MSG_FILE_REQUEST, request_data)
            logger.info(
                f"[파일] 수신 요청: {metadata.file_count}개, "
                f"{format_size(metadata.total_size)}"
            )
        except Exception as e:
            logger.error(f"FILE_READY 처리 오류: {e}")

    def _handle_file_request(self, data):
        """FILE_REQUEST 수신 → 파일 전송 시작 (별도 스레드, resume 정보 포함)"""
        transfer_id = data.get("transfer_id")
        with self._transfers_lock:
            entry = self.outgoing_files.get(transfer_id)
        if not entry:
            return

        metadata, file_paths = entry
        # resume 정보 추출 (수신 측이 보낸 완료 파일 목록)
        completed_files = set(data.get("completed_files", []))
        threading.Thread(
            target=self._send_files,
            args=(transfer_id, metadata, file_paths, completed_files),
            daemon=True,
        ).start()

    def _send_files(self, transfer_id, metadata, file_paths, completed_files=None):
        """파일 전송 실행 (별도 스레드에서 호출)"""
        completed_files = completed_files or set()
        bytes_sent = 0  # 전체 전송 바이트 누적

        # 이미 완료된 파일의 바이트를 초기값에 반영
        for f in metadata.files:
            if f["path"] in completed_files:
                bytes_sent += f["size"]

        try:
            for file_idx, file_entry in enumerate(metadata.files):
                # 중단 체크
                if transfer_id in self._cancelled_transfers:
                    logger.info(f"[파일] 전송 중단됨: {transfer_id}")
                    break

                rel_path = file_entry["path"]

                # 이어받기: 이미 완료된 파일은 건너뛰기
                if rel_path in completed_files:
                    logger.info(f"[파일] 이어받기 건너뛰기: {rel_path}")
                    continue

                abs_path = self._resolve_abs_path(metadata, file_paths, rel_path)
                if not abs_path or not os.path.exists(abs_path):
                    logger.error(f"[파일] 경로 없음: {rel_path}")
                    continue

                # FILE_START
                self._send_msg(MSG_FILE_START, {
                    "transfer_id": transfer_id,
                    "file_path": rel_path,
                    "file_index": file_idx,
                    "file_size": file_entry["size"],
                })

                # FILE_CHUNK × N (바이너리 프레임 + 진행률 추적)
                def chunk_callback(chunk_index, chunk_data, chunk_hash,
                                   _tid=transfer_id, _rel=rel_path):
                    nonlocal bytes_sent
                    if _tid in self._cancelled_transfers:
                        raise InterruptedError("전송 중단 요청")
                    raw = self.protocol.create_binary_chunk(
                        _tid, _rel, chunk_index, chunk_data, chunk_hash,
                    )
                    self._send_raw_msg(raw)
                    bytes_sent += len(chunk_data)
                    self._update_transfer_bytes(_tid, bytes_sent)

                sha256 = self.file_manager.send_file(abs_path, chunk_callback)

                # FILE_END
                self._send_msg(MSG_FILE_END, {
                    "transfer_id": transfer_id,
                    "file_path": rel_path,
                    "sha256": sha256,
                })
            else:
                # for 루프가 break 없이 완료된 경우만 TRANSFER_COMPLETE
                self._send_msg(MSG_TRANSFER_COMPLETE, {"transfer_id": transfer_id})
                with self._transfers_lock:
                    self.outgoing_files.pop(transfer_id, None)
                self._finish_transfer(transfer_id)
                logger.info(f"[파일] 전송 완료: {transfer_id}")
                return

            # break로 빠져나온 경우 (중단)
            with self._transfers_lock:
                self.outgoing_files.pop(transfer_id, None)
            self._cancel_transfer_tracking(transfer_id)

        except InterruptedError:
            logger.info(f"[파일] 전송 중단 완료: {transfer_id}")
            with self._transfers_lock:
                self.outgoing_files.pop(transfer_id, None)
            self._cancel_transfer_tracking(transfer_id)
        except Exception as e:
            logger.error(f"파일 전송 오류: {e}")
            with self._transfers_lock:
                self.outgoing_files.pop(transfer_id, None)
            self._cancel_transfer_tracking(transfer_id)
            self._send_msg(MSG_FILE_ERROR, {
                "transfer_id": transfer_id,
                "error": str(e),
            })
        finally:
            self._cancelled_transfers.discard(transfer_id)
            # v2.1: manager active state 정리 (transfer_id 매칭 시에만)
            self.file_manager.end_outgoing(transfer_id)

    def _handle_file_chunk(self, data):
        """FILE_CHUNK 수신 → 청크 저장 및 해시 검증 (불일치 시 전송 중단)"""
        transfer_id = data.get("transfer_id")
        with self._transfers_lock:
            if transfer_id not in self.pending_transfers:
                return

        # 바이너리 프레임이면 binary_data 직접 사용, 아니면 base64 디코딩 폴백
        if "binary_data" in data:
            chunk_data = data["binary_data"]
        else:
            chunk_data = base64.b64decode(data.get("data", ""))
        success = self.file_manager.receive_chunk(
            transfer_id,
            data.get("file_path"),
            data.get("chunk_index"),
            chunk_data,
            data.get("hash", ""),
        )
        if success:
            chunk_index = data.get("chunk_index", 0)
            file_path = data.get("file_path", "")

            # 10청크(~10MB)마다 체크포인트 갱신 (이어받기용)
            if chunk_index > 0 and chunk_index % 10 == 0:
                checkpoint = self.checkpoint_manager.load(transfer_id) or Checkpoint(
                    transfer_id=transfer_id
                )
                checkpoint.current_file = file_path
                checkpoint.last_chunk_index = chunk_index
                self.checkpoint_manager.save(checkpoint)

            # 수신 진행률 업데이트
            with self._progress_lock:
                info = self._transfer_progress.get(transfer_id)
                if info:
                    info["bytes_transferred"] = info.get("bytes_transferred", 0) + len(chunk_data)
            self._save_transfer_state()  # 스로틀링 적용
        else:
            logger.error(
                f"[파일] 청크 해시 불일치 → 전송 중단: "
                f"{data.get('file_path')}#{data.get('chunk_index')}"
            )
            self._cancel_transfer_tracking(transfer_id)
            self._send_msg(MSG_FILE_ERROR, {
                "transfer_id": transfer_id,
                "error": f"청크 해시 불일치: {data.get('file_path')}#{data.get('chunk_index')}",
            })
            with self._transfers_lock:
                self.pending_transfers.pop(transfer_id, None)

    def _handle_file_end(self, data):
        """FILE_END 수신 → 파일 조립 및 SHA-256 검증"""
        transfer_id = data.get("transfer_id")
        with self._transfers_lock:
            if transfer_id not in self.pending_transfers:
                return

        file_path = data.get("file_path")
        sha256 = data.get("sha256", "")

        success = self.file_manager.assemble_file(transfer_id, file_path, sha256)
        if success:
            logger.info(f"[파일] 조립 완료: {file_path}")
            checkpoint = self.checkpoint_manager.load(transfer_id) or Checkpoint(
                transfer_id=transfer_id
            )
            checkpoint.completed_files.append(file_path)
            checkpoint.completed_hashes[file_path] = sha256
            self.checkpoint_manager.save(checkpoint)
            # v2.3 audit #10: metadata 의 file_entry 에 SHA-256 주입.
            # restore_folder 시점에 dedup short-circuit 비교용으로 사용.
            # 송신측 metadata 는 빈 hash 로 오므로 수신측이 FILE_END 시점에 채움.
            with self._transfers_lock:
                md = self.pending_transfers.get(transfer_id)
                if md:
                    for entry in md.files:
                        if entry.get("path") == file_path:
                            entry["hash"] = sha256
                            break
        else:
            logger.error(f"[파일] 조립 실패 → 전송 중단: {file_path}")
            self._cancel_transfer_tracking(transfer_id)
            self._send_msg(MSG_FILE_ERROR, {
                "transfer_id": transfer_id,
                "error": f"파일 조립 실패: {file_path}",
            })
            with self._transfers_lock:
                self.pending_transfers.pop(transfer_id, None)

    def _handle_transfer_complete(self, data):
        """TRANSFER_COMPLETE 수신 → 임시 디렉토리에 복원 → 클립보드에 파일 URI 설정"""
        transfer_id = data.get("transfer_id")
        with self._transfers_lock:
            metadata = self.pending_transfers.get(transfer_id)
        if not metadata:
            return

        try:
            # 임시 디렉토리에 복원 (사용자가 붙여넣기할 때 파일 관리자가 복사/이동).
            # v2.3 audit #10: conflict_policy 전달 — 동일 파일명 재수신 시
            # SHA-256 dedup short-circuit + 4 정책 분기. 사용자 사고 (numbering 누적) 차단.
            staging_dir = self._staging_dir()
            restored_paths = self.file_manager.restore_folder(
                transfer_id, metadata, dest_dir=staging_dir,
                conflict_policy=self.config.file_conflict_policy,
            )
            self._finish_transfer(transfer_id)
            logger.info(f"[파일] 전체 완료, 임시 저장: {len(restored_paths)}개 파일")

            if restored_paths:
                # 폴더 전송이면 폴더 경로를, 파일이면 개별 경로를 클립보드에 설정
                if metadata.transfer_type == "folder" and metadata.root_name:
                    folder_path = str(staging_dir / metadata.root_name)
                    clipboard_paths = [folder_path]
                else:
                    clipboard_paths = restored_paths
                self.clipboard.set_clipboard_content("files", clipboard_paths)
        finally:
            # 예외 발생 시에도 반드시 정리
            self.checkpoint_manager.delete(transfer_id)
            # v2.1: manager active state 정리 (transfer_id 매칭 시에만)
            self.file_manager.end_incoming(transfer_id)
            with self._transfers_lock:
                self.pending_transfers.pop(transfer_id, None)

    @staticmethod
    def _resolve_abs_path(metadata, file_paths, rel_path):
        """메타데이터 상대경로 → 실제 절대경로 변환

        path_map이 있으면 직접 매핑 조회 (basename 충돌 방지).
        없으면 기존 폴백 로직 사용.
        """
        # path_map 우선 조회 (collect_metadata에서 구축)
        if metadata.path_map:
            return metadata.path_map.get(rel_path)

        # 폴백: path_map이 없는 경우 (수신 측에서는 사용되지 않음)
        if metadata.transfer_type == "file":
            return file_paths[0]
        elif metadata.transfer_type == "folder":
            base = os.path.dirname(file_paths[0])
            return os.path.join(base, rel_path)
        else:
            for p in file_paths:
                if os.path.isdir(p):
                    candidate = os.path.join(os.path.dirname(p), rel_path)
                    if os.path.exists(candidate):
                        return candidate
                elif os.path.basename(p) == os.path.basename(rel_path):
                    return p
            return None

    # ── 전송 상태 추적 (TransferWindow 연동) ──────────────────────────

    @staticmethod
    def _get_transfer_state_file():
        """전송 상태 파일 경로"""
        from config import _get_config_dir
        return str(_get_config_dir() / "transfer_state.json")

    def _save_transfer_state(self, force=False):
        """전송 상태를 JSON 파일로 저장 (TransferWindow 폴링용)

        스로틀링: force=False이면 마지막 저장 후 0.5초 이내 호출은 무시.
        시작/완료/취소 등 중요 이벤트에서는 force=True로 즉시 저장.
        원자적 쓰기: 임시 파일에 쓰고 os.replace()로 교체.
        """
        now = time.time()
        if not force and (now - self._last_state_save) < 0.5:
            return

        try:
            import json
            import tempfile
            with self._progress_lock:
                state = {
                    "active": dict(self._transfer_progress),
                    "completed": self._completed_transfers[-20:],
                }

            state_file = self._get_transfer_state_file()
            # 원자적 쓰기: 임시 파일 → os.replace
            dir_name = os.path.dirname(state_file)
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False)
                os.replace(tmp_path, state_file)
            except Exception:
                # 임시 파일 정리
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            self._last_state_save = now
        except Exception as e:
            logger.error(f"전송 상태 저장 오류: {e}")

    def _track_transfer(self, transfer_id, filename, total_size, direction):
        """전송 시작 시 진행 상태 등록"""
        with self._progress_lock:
            self._transfer_progress[transfer_id] = {
                "filename": filename,
                "total_size": total_size,
                "bytes_transferred": 0,
                "direction": direction,
                "start_time": time.time(),
            }
        self._save_transfer_state(force=True)
        # v2.2.1 B1: 큰 파일만 OS 알림
        self._notify_transfer("start", filename, total_size, direction)
        # v2.2.1 B3: 트레이 amber 갱신
        self._notify_state_changed()
        # v2.2.1 B3: 큰 파일 수신 시작 시 transfer_window 자동 표시
        if direction == "receive" and total_size >= self._NOTIFY_SIZE_THRESHOLD:
            self._launch_transfer_window()

    def _has_active_transfer(self) -> bool:
        """v2.2.1 B3: 진행 중 transfer 가 있는지. 트레이 amber 결정."""
        with self._progress_lock:
            return bool(self._transfer_progress)

    def _launch_transfer_window(self):
        """v2.2.1 B3: TransferWindow 를 별도 프로세스로 띄운다.

        tray 의 _launch_window('transfers') 와 동일 패턴. PyInstaller 번들/dev 모드 자동 분기.
        이미 떠있는 창이 있을 수 있으나 OS 가 보통 동일 process 의 새 인스턴스를 focus.
        """
        import subprocess
        try:
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--window", "transfers"]
            else:
                main_py = os.path.abspath(__file__)
                cmd = [sys.executable, main_py, "--window", "transfers"]
            threading.Thread(
                target=lambda: subprocess.Popen(cmd, start_new_session=True),
                daemon=True,
            ).start()
        except Exception as e:
            logger.debug(f"transfer_window 자동 표시 실패: {e}")

    # v2.2.1 B1: 임계값 — 이 미만은 알림 skip (작은 파일 spam 방지)
    _NOTIFY_SIZE_THRESHOLD = 10 * 1024 * 1024  # 10 MB

    def _notify_transfer(self, action, filename, total_size, direction):
        """v2.2.1 B1: plyer 로 OS 알림 (큰 파일만, 비동기 — UI 블로킹 회피).

        Args:
            action: "start" 또는 "complete"
            filename: 파일/폴더 이름
            total_size: 바이트 수
            direction: "send" 또는 "receive"
        """
        if total_size < self._NOTIFY_SIZE_THRESHOLD:
            return

        from core.file_transfer import format_size
        size_str = format_size(total_size)

        verb = {
            ("send", "start"): "전송 시작",
            ("send", "complete"): "전송 완료",
            ("receive", "start"): "수신 시작",
            ("receive", "complete"): "수신 완료",
        }.get((direction, action))
        if not verb:
            return
        message = f"{verb}: {filename} ({size_str})"

        def _do_notify():
            try:
                from plyer import notification
                notification.notify(
                    title="Infinite Clipboard",
                    message=message,
                    timeout=4,
                    app_name="Infinite Clipboard",
                )
            except Exception as e:
                # plyer 가 환경에 따라 (Linux dbus 미설치 등) 실패 가능 — 무시
                logger.debug(f"알림 실패 (무시): {e}")

        threading.Thread(target=_do_notify, daemon=True).start()

    def _update_transfer_bytes(self, transfer_id, bytes_transferred):
        """전송 진행률 업데이트 (스로틀링 적용)"""
        with self._progress_lock:
            info = self._transfer_progress.get(transfer_id)
            if info:
                info["bytes_transferred"] = bytes_transferred
        self._save_transfer_state()  # 스로틀링: 0.5초 이내이면 무시

    def _finish_transfer(self, transfer_id):
        """전송 완료 처리 → 활성에서 제거, 완료 목록에 추가"""
        finished_info = None
        with self._progress_lock:
            info = self._transfer_progress.pop(transfer_id, None)
            if info:
                self._completed_transfers.append({
                    "transfer_id": transfer_id,
                    "filename": info["filename"],
                    "total_size": info["total_size"],
                    "direction": info["direction"],
                    "completed_at": time.time(),
                })
                if len(self._completed_transfers) > 20:
                    self._completed_transfers = self._completed_transfers[-20:]
                finished_info = info
        self._save_transfer_state(force=True)
        # v2.2.1 B1: 완료 알림
        if finished_info:
            self._notify_transfer(
                "complete", finished_info["filename"],
                finished_info["total_size"], finished_info["direction"],
            )
        # v2.2.1 B3: active transfer 0 → 트레이 green 으로 복귀
        self._notify_state_changed()

    # v2.2.1 B2: UI cancel IPC ──────────────────────────────────────────

    @staticmethod
    def _get_cancel_request_file():
        """TransferWindow 의 cancel 버튼이 transfer_id append 하는 파일."""
        from config import _get_config_dir
        return str(_get_config_dir() / "cancel_requests.json")

    def _watch_cancel_requests(self):
        """별도 프로세스 TransferWindow 의 cancel 버튼 클릭 폴링.

        TransferWindow 가 cancel_requests.json 에 transfer_id 리스트로 append.
        본 thread 가 0.5초마다 폴링해 _user_cancel_transfer 호출 후 파일 비움.
        """
        cancel_file = self._get_cancel_request_file()
        while self.running:
            try:
                if os.path.exists(cancel_file):
                    with open(cancel_file, "r", encoding="utf-8") as f:
                        requests = json.load(f)
                    if isinstance(requests, list) and requests:
                        for tid in list(requests):
                            self._user_cancel_transfer(tid)
                        # 처리 후 파일 비움
                        try:
                            os.unlink(cancel_file)
                        except OSError:
                            with open(cancel_file, "w", encoding="utf-8") as f:
                                json.dump([], f)
            except (json.JSONDecodeError, OSError) as e:
                logger.debug(f"cancel request 폴링: {e}")
            time.sleep(0.5)

    def _user_cancel_transfer(self, transfer_id):
        """v2.2.1 B2: 사용자 UI cancel — peer 에 송출 + 자기 cleanup."""
        from core.protocol import is_valid_transfer_id
        if not is_valid_transfer_id(transfer_id):
            return

        logger.info(f"[UI] 사용자 cancel 요청: {transfer_id}")

        # peer 에 cancel 송출 (검증 실패 시 ValueError 무시)
        try:
            self._send_msg(MSG_FILE_CANCEL, {"transfer_id": transfer_id, "reason": "user"})
        except Exception as e:
            logger.error(f"cancel 송출 실패: {e}")

        # 자기 송수신 양쪽 cleanup — _handle_file_cancel 재사용
        self._handle_file_cancel({"transfer_id": transfer_id, "reason": "user"})

    def _cancel_transfer_tracking(self, transfer_id):
        """전송 에러/취소 시 추적 제거 + 임시 파일 정리"""
        with self._progress_lock:
            self._transfer_progress.pop(transfer_id, None)
        self._save_transfer_state(force=True)
        # v2.2.1 B3: 트레이 갱신
        self._notify_state_changed()
        # 임시 청크 디렉토리 정리
        from core.file_transfer import get_temp_dir
        temp_dir = get_temp_dir(transfer_id)
        if temp_dir.exists():
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"[파일] 임시 디렉토리 삭제: {temp_dir}")

    def _notify_state_changed(self):
        """상태 변경 시 UI 콜백 호출"""
        if self.on_state_changed:
            try:
                self.on_state_changed()
            except Exception as e:
                logger.error(f"상태 변경 콜백 오류: {e}")

    def _is_network_active(self):
        """네트워크가 활성 상태인지 확인 (전송 대상이 있을 때만 True)"""
        if self.config.mode == "server":
            return self.connected and self.connected_clients > 0
        else:
            return self.connected


def _watch_config_for_restart(app, tray=None):
    """설정 파일 변경 감지 → 앱 재시작 트리거 (데몬 스레드)"""
    from config import CONFIG_FILE
    try:
        last_mtime = CONFIG_FILE.stat().st_mtime if CONFIG_FILE.exists() else 0
    except OSError:
        last_mtime = 0

    while app.running:
        time.sleep(2)
        try:
            if CONFIG_FILE.exists():
                current_mtime = CONFIG_FILE.stat().st_mtime
                if current_mtime > last_mtime:
                    logger.info("[설정] 변경 감지, 재시작 예약")
                    app._restart_requested = True
                    if tray:
                        tray.stop()
                    app.stop()
                    break
        except OSError:
            pass


def _run_window_only(window_type: str) -> None:
    """트레이 메뉴가 자기 자신을 `--window <type>` 으로 재호출했을 때의 경로.

    서버/클라이언트 네트워크 로직을 실행하지 않고 UI 창 하나만 띄운다.
    창이 닫히면 프로세스 종료. PyInstaller 번들에서도 동일 바이너리가
    `sys.executable` 이므로 이 방식이 이식성 있다.
    """
    import customtkinter

    customtkinter.set_appearance_mode("System")
    root = customtkinter.CTk()
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

    win = None
    if window_type == "settings":
        from config import load_config
        from ui.settings_window import SettingsWindow
        config = load_config()
        win = SettingsWindow(config)
    elif window_type == "history":
        import json
        from config import _get_config_dir
        from core.clipboard_manager import ClipboardManager
        from ui.history_window import HistoryWindow
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
    elif window_type == "transfers":
        from config import _get_config_dir
        from ui.transfer_window import TransferWindow
        state_file = str(_get_config_dir() / "transfer_state.json")
        win = TransferWindow(state_file)
    elif window_type == "about":
        from ui.about_window import AboutWindow
        win = AboutWindow()

    if win is None:
        sys.exit(1)

    win.after(150, win.focus_force)
    win.protocol("WM_DELETE_WINDOW", lambda: _close_all(win))
    root.mainloop()


def main():
    """메인 함수"""
    from version import __version__, __app_name__
    parser = argparse.ArgumentParser(description=f"{__app_name__} v{__version__}")
    parser.add_argument("--version", action="version",
                        version=f"{__app_name__} {__version__}")
    parser.add_argument("--mode", choices=["server", "client"], help="실행 모드")
    parser.add_argument("--host", help="서버 IP (클라이언트 모드)")
    parser.add_argument("--port", type=int, help="포트 번호")
    # --key는 제거됨 — 프로세스 목록(ps/Task Manager)에서 노출되므로
    # 인증 키는 settings.json에서만 읽는다.
    parser.add_argument("--no-tray", action="store_true", help="트레이 없이 콘솔 모드")
    parser.add_argument("--debug", action="store_true", help="디버그 모드 (상세 로그)")
    # 내부용: 트레이가 자기 자신을 재호출해 UI 창만 띄울 때 사용
    parser.add_argument("--window", choices=["settings", "history", "transfers", "about"],
                        help=argparse.SUPPRESS)
    args = parser.parse_args()

    # 로깅 초기화
    _setup_logging(debug=args.debug)

    # UI 창 전용 서브 모드 — 서버/클라이언트 초기화 없이 조기 반환
    if args.window:
        _run_window_only(args.window)
        return

    # 설정 로드
    config = load_config()

    # CLI 인자로 설정 오버라이드
    if args.mode:
        config.mode = args.mode
    if args.host:
        config.server_host = args.host
    if args.port:
        config.port = args.port

    logger.info(f"{__app_name__} v{__version__} 시작")
    logger.info(f"모드: {config.mode} | 기기: {config.device_name}")
    logger.info(f"OS: {platform.system()} {platform.release()}")
    logger.info(f"로그 파일: {LOG_FILE}")

    # 앱 시작
    app = InfiniteClipboard(config)
    app.start()

    if args.no_tray:
        # 설정 변경 감시 시작
        threading.Thread(
            target=_watch_config_for_restart, args=(app,), daemon=True
        ).start()

        # 콘솔 모드 — Ctrl+C로 종료
        try:
            while app.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("종료 요청 (Ctrl+C)")
        finally:
            app.stop()
    else:
        # 트레이 모드 — 시스템 트레이에서 실행 (블로킹)
        from ui.tray import TrayApp
        tray = TrayApp(app)
        # v2.3 audit P2: 양방향 link — app._cleanup_staging 이 tray.notify 호출.
        app.tray = tray

        # 설정 변경 감시 시작
        threading.Thread(
            target=_watch_config_for_restart, args=(app, tray), daemon=True
        ).start()

        try:
            tray.run()
        except KeyboardInterrupt:
            logger.info("종료 요청 (Ctrl+C)")
        finally:
            app.stop()

    # 재시작 요청 시 새 프로세스 시작 후 현재 프로세스 종료
    if app._restart_requested:
        logger.info("[설정] 앱 재시작 실행")
        import subprocess
        if getattr(sys, "frozen", False):
            # PyInstaller 번들: sys.executable 이 바이너리이고 sys.argv[0] 도
            # 같은 바이너리 경로라, sys.argv 를 그대로 추가하면 바이너리 경로가
            # positional 인자로 한 번 더 들어가 argparse 가 SystemExit(2) 로 즉사한다.
            # argv[1:] (실제 사용자 인자)만 전달해야 한다.
            cmd = [sys.executable] + sys.argv[1:]
        else:
            # 개발 모드: python + main.py + 인자
            cmd = [sys.executable] + sys.argv
        subprocess.Popen(cmd, start_new_session=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
