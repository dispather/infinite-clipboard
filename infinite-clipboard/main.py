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
import json  # v3.0 S4: 모듈 레벨 (watch 폴링 스레드들이 사용 — 함수별 로컬 import 통일)
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
    # v3.0 lazy clipboard 메시지 + fetch 실패 사유
    MSG_CLIP_OFFER, MSG_CLIP_FETCH, MSG_CLIP_FETCH_FAIL,
    CLIP_OFFER_KIND_FILE,
    FETCH_FAIL_SUPERSEDED, FETCH_FAIL_EXPIRED, FETCH_FAIL_MISSING,
    FETCH_FAIL_OFFLINE, FETCH_FAIL_ERROR,
)
from core.network import NetworkServer, NetworkClient
from core.protocol import Protocol
from core.clipboard_manager import ClipboardManager
from core.file_transfer import FileTransferManager, FileMetadata, CheckpointManager, Checkpoint, format_size
from core.privacy import detect_sensitive_kind
# v3.0 lazy provider (OS 별 백엔드 팩토리 — 헤드리스/미지원 시 None graceful)
from core.lazy_clipboard import get_lazy_provider, FetchedContent, KIND_FILE

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
        # v3.0: peer 레지스트리 (targeted relay 라우팅의 기반, Task 3 가 사용).
        # 서버 모드: {peer_id: name} 연결된 클라이언트들. 클라이언트 모드: 서버
        # peer_id 는 self.client.server_peer_id 에 보관되므로 여기선 비워 둔다.
        self.peers = {}

        # ── v3.0 S2c lazy 오케스트레이션 ────────────────────────────────
        # [source 측] 현재 내가 제공 중인 offer (copy 시점 경로/메타만 보관, 데이터 X).
        #   {offer_id, kind, metadata, file_paths, created_at}. 새 복사가 supersede.
        self.current_offer = None
        self._offer_lock = threading.Lock()
        # [source 측] 동시 outgoing fetch 응답 직렬화 — invariant (A) 글로벌 큐.
        # 함정 #1(paste-order race) 회귀 위험 0: 한 시점 1개 전송만.
        self._outgoing_fetch_lock = threading.Lock()
        # [receiver 측] 등록한 offer 들 {offer_id: offer dict}. paste 시 fetch 에 사용.
        self.received_offers = {}
        # [receiver 측] S4 받기 fallback — lazy 미지원/등록 실패 offer {offer_id: info}.
        # 사용자가 전송창 "받기" 버튼으로 download_path 에 수동 수신. _offer_lock 으로 보호.
        self.receivable_offers = {}
        # [receiver 측] OS lazy provider (첫 offer 수신 시 lazy-init). 미지원/헤드리스 None.
        self.lazy_provider = None
        self._lazy_provider_inited = False
        # [receiver 측] 진행 중 fetch 1개 (invariant A — 직렬화). _fetch_lock 으로 보호.
        #   {offer_id, transfer_id, event: Event, paths: [..]|None, fail: reason|None}
        self._active_fetch = None
        self._fetch_lock = threading.Lock()  # fetch 동작 직렬화 (한 번에 1개)
        # _active_fetch 필드 접근 가드 — 네트워크 스레드(시그널)와 fetch 스레드(대기)가
        # 공유하므로 _fetch_lock(=fetch 가 점유 중) 과 별개 짧은 락으로 보호 (deadlock 회피).
        self._active_fetch_lock = threading.Lock()

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

        # v3.0 S4: TransferWindow "받기" 버튼 IPC 폴링 (lazy fallback 수동 수신)
        receive_thread = threading.Thread(target=self._watch_receive_requests, daemon=True)
        receive_thread.start()

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
        # v3.0: lazy provider 백엔드 스레드 정리 (규칙 #9 silent)
        if self.lazy_provider is not None:
            try:
                self.lazy_provider.stop()
            except Exception:
                pass
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
            peer_id=self.config.peer_id,
        )

        self.server.on_client_connected = self._on_server_client_connected
        self.server.on_client_disconnected = self._on_server_client_disconnected
        self.server.on_message_received = self._on_server_message

        self.server.start()
        self.connected = True
        # 실제 bind IP 는 NetworkServer.start() 가 로깅 (Tailscale 자동 / 0.0.0.0)

    def _on_server_client_connected(self, sock, address, name, peer_id):
        """클라이언트 연결 이벤트 (v3.0: peer_id 레지스트리 기록)"""
        with self.server.clients_lock:
            self.connected_clients = len(self.server.clients)
        self.peers[peer_id] = name
        logger.info(
            f"[서버] 클라이언트 연결: {name} peer={peer_id[:8]}… "
            f"({self.connected_clients}대)"
        )
        self._notify_state_changed()

    def _on_server_client_disconnected(self, sock, address, name, peer_id):
        """클라이언트 연결 해제 이벤트 (v3.0: 레지스트리에서 제거)"""
        with self.server.clients_lock:
            self.connected_clients = len(self.server.clients)
        self.peers.pop(peer_id, None)
        logger.info(
            f"[서버] 클라이언트 해제: {name} peer={peer_id[:8]}… "
            f"({self.connected_clients}대)"
        )
        self._notify_state_changed()

    def _server_route(self, msg_type, data, sock, local_handler):
        """v3.0 JSON 메시지 라우팅: receiver_peer 있으면 그 peer 1 소켓에만 중계.

        - receiver_peer == 내 peer_id → 내가 최종 대상, 로컬 처리만 (중계 안 함)
        - receiver_peer == 다른 peer → relay only (로컬 처리 안 함)
        - receiver_peer 없음("") → 로컬 처리 + broadcast (eager 호환)
        폴더 규칙 #3: 바이너리 chunk 는 별도(_raw) 경로, 이건 JSON 메시지용.
        """
        receiver = data.get("receiver_peer") if isinstance(data, dict) else None
        if receiver:
            if receiver == self.config.peer_id:
                if local_handler:
                    local_handler(data)
            else:
                self.server.send_to_peer(receiver, msg_type, data)
        else:
            if local_handler:
                local_handler(data)
            self.server.broadcast(msg_type, data, exclude_sock=sock)

    def _on_server_message(self, sock, message):
        """서버: 클라이언트로부터 메시지 수신"""
        msg_type = message.get("type")
        data = message.get("data")

        if msg_type == MSG_CLIPBOARD:
            self._handle_clipboard_received(data)
            # 다른 클라이언트에 브로드캐스트
            self.server.broadcast(MSG_CLIPBOARD, data, exclude_sock=sock)

        # ── v3.0 lazy clipboard ──
        elif msg_type == MSG_CLIP_OFFER:
            # copy 알림 — 로컬 처리(서버도 receiver 일 수 있음) + broadcast
            self._handle_clip_offer(data)
            self.server.broadcast(MSG_CLIP_OFFER, data, exclude_sock=sock)

        elif msg_type == MSG_CLIP_FETCH:
            # paste 요청 — receiver_peer(=source) 로 routed
            self._server_route(MSG_CLIP_FETCH, data, sock, self._handle_clip_fetch)

        elif msg_type == MSG_CLIP_FETCH_FAIL:
            # fetch 실패 — receiver_peer(=requester) 로 routed
            self._server_route(MSG_CLIP_FETCH_FAIL, data, sock, self._handle_clip_fetch_fail)

        elif msg_type == MSG_FILE_READY:
            # v3.0: fetch 응답의 일부로 requester 에게만 targeted (receiver_peer 라우팅)
            self._server_route(MSG_FILE_READY, data, sock, self._handle_file_ready)

        elif msg_type == MSG_FILE_REQUEST:
            self._handle_file_request(data)
            self.server.broadcast(MSG_FILE_REQUEST, data, exclude_sock=sock)

        elif msg_type == MSG_FILE_CHUNK:
            # v3.0 targeted relay: receiver_peer 가 있으면 그 peer 1 소켓에만 중계.
            #   - receiver_peer == 내 peer_id  → 내가 최종 수신자, 로컬 처리 (중계 안 함)
            #   - receiver_peer == 다른 peer   → relay only (로컬 처리 안 함)
            #   - receiver_peer 없음("")       → eager 호환: 로컬 처리 + broadcast
            raw = message.get("_raw")
            receiver_peer = data.get("receiver_peer") if isinstance(data, dict) else None
            if receiver_peer:
                if receiver_peer == self.config.peer_id:
                    self._handle_file_chunk(data)
                elif raw:
                    self.server.send_raw_to_peer(receiver_peer, raw)
                else:
                    # raw 부재(드문 경로) — 재직렬화해서 targeted 전송
                    self.server.send_to_peer(receiver_peer, msg_type, data)
            else:
                self._handle_file_chunk(data)
                # 원본 와이어 바이트로 중계 (재직렬화 방지, 바이너리 프레임 유지)
                if raw:
                    self.server.broadcast_raw(raw, exclude_sock=sock)
                else:
                    self.server.broadcast(msg_type, data, exclude_sock=sock)

        elif msg_type == MSG_FILE_END:
            self._server_route(msg_type, data, sock, self._handle_file_end)

        elif msg_type == MSG_TRANSFER_COMPLETE:
            self._server_route(msg_type, data, sock, self._handle_transfer_complete)

        elif msg_type == MSG_FILE_ERROR:
            self._server_route(msg_type, data, sock, self._handle_file_error_received)

        elif msg_type == MSG_FILE_CANCEL:
            # v2.1: 자체 처리 + broadcast (서버도 송수신 당사자일 수 있음)
            self._handle_file_cancel(data)
            self.server.broadcast(msg_type, data, exclude_sock=sock)

        elif msg_type == MSG_FILE_CANCEL_ACK:
            # v2.3.1: cancel ack — 자체 처리 (originator 가 서버일 수 있음) + broadcast
            self._handle_file_cancel_ack(data)
            self.server.broadcast(msg_type, data, exclude_sock=sock)

        elif msg_type == MSG_FILE_START:
            # v3.0: receiver_peer 있으면 targeted (수신 측은 START 핸들러 없음 → local=None)
            self._server_route(msg_type, data, sock, None)

        elif msg_type in (MSG_FILE_ACK, MSG_FILE_RESUME):
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
            peer_id=self.config.peer_id,
        )

        self.client.on_connected = self._on_client_connected
        self.client.on_disconnected = self._on_client_disconnected
        self.client.on_message_received = self._on_client_message

        self.client.start()

    def _on_client_connected(self):
        """서버 연결 성공 (v3.0: 학습한 서버 peer_id 로깅)"""
        self.connected = True
        server_peer = getattr(self.client, "server_peer_id", "")
        logger.info(
            f"[클라이언트] 서버 연결 성공 "
            f"(server_peer={server_peer[:8]}… my_peer={self.config.peer_id[:8]}…)"
        )
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

        # ── v3.0 lazy clipboard (서버가 나에게 routed/broadcast) ──
        elif msg_type == MSG_CLIP_OFFER:
            self._handle_clip_offer(data)

        elif msg_type == MSG_CLIP_FETCH:
            # 내가 source — 서버가 fetch 를 나에게 중계
            self._handle_clip_fetch(data)

        elif msg_type == MSG_CLIP_FETCH_FAIL:
            self._handle_clip_fetch_fail(data)

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
                            # v3.0 S2c: eager 전송 대신 offer broadcast (paste 시점 fetch).
                            self._announce_offer(content)
                        else:
                            # 텍스트는 그대로 inline 전송. 이미지 lazy 는 Task 7(S3)에서 이관.
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

    # ── v3.0 S2c: targeted 전송 헬퍼 (한 peer 에게만) ──────────────────
    # 서버 모드: peer 소켓에 직접. 클라이언트 모드: 서버로 보내면 서버가 data/meta 의
    # receiver_peer 로 라우팅 (Task 3 의 _server_route). 폴더 규칙 #3 데이터 흐름.

    def _send_msg_to(self, msg_type, data, receiver_peer):
        """JSON 메시지를 receiver_peer 에게만 (data 에 receiver_peer 동봉돼 있어야 함)."""
        if self.config.mode == "server" and self.server:
            self.server.send_to_peer(receiver_peer, msg_type, data)
        elif self.config.mode == "client" and self.client:
            self.client.send(msg_type, data)

    def _send_raw_to(self, raw_bytes, receiver_peer):
        """직렬화된(raw) 메시지를 receiver_peer 에게만 (meta 에 receiver_peer 동봉)."""
        if self.config.mode == "server" and self.server:
            self.server.send_raw_to_peer(receiver_peer, raw_bytes)
        elif self.config.mode == "client" and self.client:
            self.client.send_raw(raw_bytes)

    # ── v3.0 S2c: lazy offer/fetch 오케스트레이션 ──────────────────────

    def _announce_offer(self, file_paths):
        """[source] 파일 복사 감지 → 경로/메타만 보관 + MSG_CLIP_OFFER broadcast.

        eager 전송 안 함. 데이터는 다른 PC 가 paste(fetch) 하는 시점에만 흐른다.
        새 복사는 이전 offer 를 supersede (offer_id 불일치 → 옛 fetch 는 superseded 거부).
        """
        try:
            metadata = self.file_manager.collect_metadata(file_paths)
        except Exception as e:
            logger.error(f"offer 메타데이터 수집 오류: {e}")
            return
        # offer_id == metadata.transfer_id (둘 다 UUID v4) → offer↔transfer 자연 연결
        offer_id = metadata.transfer_id
        items = [
            {"name": os.path.basename(f["path"]) or f["path"],
             "size": int(f.get("size", 0)), "hash": ""}
            for f in metadata.files
        ]
        created_at = time.time()
        with self._offer_lock:
            self.current_offer = {
                "offer_id": offer_id, "kind": CLIP_OFFER_KIND_FILE,
                "metadata": metadata, "file_paths": file_paths,
                "created_at": created_at,
            }
        try:
            raw = self.protocol.create_clip_offer(
                offer_id, self.config.peer_id, CLIP_OFFER_KIND_FILE,
                items, int(metadata.total_size), created_at,
            )
        except Exception as e:
            logger.error(f"offer 생성 오류: {e}")
            return
        self._send_raw_msg(raw)  # broadcast (receiver_peer 없음)
        logger.info(
            f"[offer] 알림: {metadata.file_count}개 "
            f"({format_size(metadata.total_size)}) offer={offer_id[:8]}…"
        )

    def _ensure_lazy_provider(self):
        """[receiver] 첫 offer 수신 시 OS lazy provider 1회 생성 (없으면 None=fallback)."""
        if not self._lazy_provider_inited:
            self._lazy_provider_inited = True
            try:
                self.lazy_provider = get_lazy_provider()
            except Exception as e:
                logger.warning(f"lazy provider 생성 실패 — fallback: {e}")
                self.lazy_provider = None
            if self.lazy_provider is None:
                logger.info("[offer] lazy provider 없음 (헤드리스/미지원) — 받기 fallback 대상")
        return self.lazy_provider

    def _handle_clip_offer(self, data):
        """[receiver] MSG_CLIP_OFFER 수신 → lazy provider 에 등록 (paste 시 fetch)."""
        offer = self.protocol.parse_clip_offer(data)
        if offer is None:
            return
        # 자기 자신의 offer 는 무시 (broadcast 가 되돌아온 경우)
        if offer["source_peer"] == self.config.peer_id:
            return
        offer_id = offer["offer_id"]
        with self._offer_lock:
            # 새 offer 가 이전 것을 supersede — 최신만 유지
            self.received_offers = {offer_id: offer}
            # 이전 받기 fallback 잔여 정리 (supersede)
            old_receivable = bool(self.receivable_offers)
            self.receivable_offers = {}
        provider = self._ensure_lazy_provider()
        ok = False
        if provider is not None and provider.is_supported(offer["kind"]):
            try:
                provider.clear()  # 이전 등록 해제 (supersede)
                ok = provider.register_offer(offer, self._provider_fetch)
            except Exception as e:
                logger.warning(f"offer 등록 실패 — 받기 fallback 으로: {e}")
                ok = False
        if ok:
            # happy path — paste 로 바로 가져올 수 있음. 알림 무음 (Gate S4).
            logger.info(f"[offer] 수신·등록(OK, happy-path): offer={offer_id[:8]}…")
            if old_receivable:
                self._save_transfer_state(force=True)  # 옛 받기 목록 비움 반영
        else:
            # lazy 미지원/헤드리스/등록 실패 → S4 받기 fallback (알림 + 전송창 받기 행)
            self._add_receivable(offer)
            logger.info(f"[offer] 수신(받기 fallback): offer={offer_id[:8]}…")

    def _fetch_timeout(self, total_size: int) -> float:
        """fetch 하드 타임아웃 (Rec 2). 크기 비례 + 하한/상한."""
        return max(30.0, min(600.0, total_size / (256 * 1024)))

    def _fetch_offer(self, offer_id):
        """[receiver] lazy provider 콜백 — paste 시점 **동기** fetch.

        FetchedContent(kind=file, paths=[스테이징 경로]) 반환, 실패 시 예외(→provider
        가 빈 결과 처리 → 받기 fallback). invariant (A): 한 시점 1개 fetch (_fetch_lock).
        """
        with self._fetch_lock:
            with self._offer_lock:
                offer = self.received_offers.get(offer_id)
            if offer is None:
                raise RuntimeError(f"알 수 없는 offer: {offer_id}")
            source_peer = offer["source_peer"]
            total = int(offer.get("total_size", 0))
            # Rec 3: requester 가 fetch 전에 저장 공간 검사 (실제 저장 위치 근사)
            if not self.file_manager.check_disk_space(total):
                raise RuntimeError("저장 공간 부족")
            event = threading.Event()
            with self._active_fetch_lock:
                self._active_fetch = {
                    "offer_id": offer_id, "transfer_id": offer_id,
                    "event": event, "paths": None, "fail": None,
                }
            try:
                raw = self.protocol.create_clip_fetch(
                    offer_id, self.config.peer_id, receiver_peer=source_peer,
                )
                self._send_raw_to(raw, source_peer)
                timeout = self._fetch_timeout(total)
                if not event.wait(timeout=timeout):
                    raise TimeoutError(f"fetch 타임아웃 ({timeout:.0f}s)")
                with self._active_fetch_lock:
                    af = self._active_fetch or {}
                    fail = af.get("fail")
                    paths = af.get("paths")
                if fail:
                    raise RuntimeError(f"fetch 실패: {fail}")
                if not paths:
                    raise RuntimeError("fetch 결과 없음")
                return FetchedContent(kind=KIND_FILE, paths=list(paths))
            finally:
                with self._active_fetch_lock:
                    self._active_fetch = None

    def _signal_fetch(self, transfer_id, paths=None, fail=None) -> bool:
        """[receiver] 진행 중 fetch 에 결과/실패 전달 + 대기 해제. 매칭 시 True."""
        with self._active_fetch_lock:
            af = self._active_fetch
            if af is None or af.get("transfer_id") != transfer_id:
                return False
            if fail is not None:
                af["fail"] = fail
            else:
                af["paths"] = paths
            af["event"].set()
            return True

    def _handle_clip_fetch(self, data):
        """[source] MSG_CLIP_FETCH 수신 → offer 검증 후 requester 에게만 전송 시작."""
        parsed = self.protocol.parse_clip_fetch(data)
        if parsed is None:
            return
        offer_id = parsed["offer_id"]
        requester = parsed["requester_peer"]
        with self._offer_lock:
            offer = self.current_offer
        if offer is None or offer.get("offer_id") != offer_id:
            self._send_fetch_fail(offer_id, FETCH_FAIL_SUPERSEDED, requester)
            return
        if time.time() - offer["created_at"] > self.config.offer_ttl_hours * 3600:
            self._send_fetch_fail(offer_id, FETCH_FAIL_EXPIRED, requester)
            return
        if not all(os.path.exists(p) for p in offer["file_paths"]):
            self._send_fetch_fail(offer_id, FETCH_FAIL_MISSING, requester)
            return
        threading.Thread(
            target=self._serve_fetch, args=(offer, requester), daemon=True,
        ).start()

    def _serve_fetch(self, offer, requester):
        """[source] fetch 응답 — requester 에게만 FILE_READY + 파일 전송. (A) 직렬화."""
        with self._outgoing_fetch_lock:  # invariant (A): 한 시점 1개 outgoing
            offer_id = offer["offer_id"]
            try:
                metadata = offer["metadata"]
                file_paths = offer["file_paths"]
                # 수신측 pending 등록용 FILE_READY (targeted, receiver_peer 동봉)
                ready = metadata.to_dict()
                ready["receiver_peer"] = requester
                self._send_msg_to(MSG_FILE_READY, ready, requester)
                # active outgoing state (직렬화돼 supersede 발생 안 함)
                self.file_manager.begin_outgoing(metadata, file_paths)
                with self._transfers_lock:
                    self.outgoing_files[offer_id] = (metadata, file_paths)
                self._send_files(offer_id, metadata, file_paths, receiver_peer=requester)
            except Exception as e:
                logger.error(f"[fetch] 응답 오류: {e}")
                self._send_fetch_fail(offer_id, FETCH_FAIL_ERROR, requester)

    def _send_fetch_fail(self, offer_id, reason, requester):
        """[source] requester 에게만 MSG_CLIP_FETCH_FAIL."""
        try:
            raw = self.protocol.create_clip_fetch_fail(
                offer_id, reason, receiver_peer=requester,
            )
            self._send_raw_to(raw, requester)
            logger.info(f"[fetch] 거부({reason}) → {requester[:8]}… offer={offer_id[:8]}…")
        except Exception as e:
            logger.warning(f"FETCH_FAIL 송출 실패: {e}")

    def _handle_clip_fetch_fail(self, data):
        """[receiver] MSG_CLIP_FETCH_FAIL 수신 → 진행 중 fetch 실패 처리."""
        parsed = self.protocol.parse_clip_fetch_fail(data)
        if parsed is None:
            return
        self._signal_fetch(parsed["offer_id"], fail=parsed["reason"])

    # ── v3.0 S4: 받기 fallback (lazy 미지원/실패 시 수동 수신) ──────────

    def _provider_fetch(self, offer_id):
        """provider 콜백용 래퍼 — paste 실패 시 알림 후 re-raise(provider 가 빈 결과).

        클립보드는 보존된다(provider 가 아무것도 안 내주면 OS 는 이전 내용 유지). Gate S4.
        """
        try:
            return self._fetch_offer(offer_id)
        except Exception:
            with self._offer_lock:
                offer = self.received_offers.get(offer_id)
            name = self._offer_display_name(offer) if offer else "파일"
            self._notify("받기 실패", f"{name} — 원본에서 받을 수 없음 (클립보드 유지)")
            raise

    @staticmethod
    def _offer_display_name(offer) -> str:
        """offer items → 표시 이름 ('a.txt' 또는 'a.txt 외 2개')."""
        if not offer:
            return "파일"
        items = offer.get("items") or []
        if not items:
            return "파일"
        first = items[0].get("name") or "파일"
        return first if len(items) == 1 else f"{first} 외 {len(items) - 1}개"

    def _add_receivable(self, offer) -> None:
        """받기 fallback 대상 등록 + 알림 + 전송창 갱신용 상태 저장."""
        name = self._offer_display_name(offer)
        with self._offer_lock:
            self.receivable_offers[offer["offer_id"]] = {
                "offer_id": offer["offer_id"],
                "source_peer": offer["source_peer"],
                "name": name,
                "kind": offer["kind"],
                "total_size": int(offer.get("total_size", 0)),
                "created_at": offer.get("created_at", time.time()),
            }
        self._save_transfer_state(force=True)
        self._notify("파일 받기", f"{name} — 전송 창에서 [받기]")

    def _clear_receivable(self, offer_id) -> None:
        with self._offer_lock:
            existed = self.receivable_offers.pop(offer_id, None)
        if existed:
            self._save_transfer_state(force=True)

    def _notify(self, title, message) -> None:
        """일반 OS 알림 (크기 무관). tray 우선, 없으면 plyer (비동기)."""
        tray = self.tray
        if tray is not None:
            try:
                tray.notify(title, message)
                return
            except Exception:
                pass

        def _do():
            try:
                from plyer import notification
                notification.notify(
                    title=title, message=message, timeout=5,
                    app_name="Infinite Clipboard",
                )
            except Exception as e:
                logger.debug(f"알림 실패 (무시): {e}")

        threading.Thread(target=_do, daemon=True).start()

    def _receive_offer(self, offer_id) -> None:
        """[받기 버튼] offer 를 fetch 해 download_path 에 저장 (provider 무관 수동 수신)."""
        with self._offer_lock:
            info = self.receivable_offers.get(offer_id)
        name = info.get("name", "파일") if info else "파일"
        try:
            fetched = self._fetch_offer(offer_id)  # staging 경로 (메커니즘 재사용)
        except Exception as e:
            logger.warning(f"[받기] fetch 실패: {e}")
            self._notify("받기 실패", f"{name} — 원본에서 받을 수 없음")
            return

        import shutil
        dest_dir = self.config.download_path
        saved = 0
        try:
            os.makedirs(dest_dir, exist_ok=True)
            for p in fetched.paths:
                base = os.path.basename(p.rstrip("/\\")) or "received"
                target = os.path.join(dest_dir, base)
                if os.path.isdir(p):
                    shutil.copytree(p, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(p, target)
                saved += 1
        except Exception as e:
            logger.error(f"[받기] 저장 실패: {e}")
            self._notify("받기 실패", f"{name} — 저장 오류")
            return

        self._clear_receivable(offer_id)
        self._notify("받기 완료", f"{name} — {saved}개 → {dest_dir}")
        logger.info(f"[받기] 완료: {saved}개 → {dest_dir}")

    @staticmethod
    def _get_receive_request_file():
        """TransferWindow 의 받기 버튼이 offer_id append 하는 파일."""
        from config import _get_config_dir
        return str(_get_config_dir() / "receive_requests.json")

    def _watch_receive_requests(self):
        """별도 프로세스 TransferWindow 받기 버튼 폴링 → _receive_offer (per-request 스레드)."""
        req_file = self._get_receive_request_file()
        while self.running:
            try:
                if os.path.exists(req_file):
                    with open(req_file, "r", encoding="utf-8") as f:
                        requests = json.load(f)
                    if isinstance(requests, list) and requests:
                        for oid in list(requests):
                            # fetch 는 블로킹 → per-request 스레드
                            threading.Thread(
                                target=self._receive_offer, args=(oid,), daemon=True,
                            ).start()
                        try:
                            os.unlink(req_file)
                        except OSError:
                            with open(req_file, "w", encoding="utf-8") as f:
                                json.dump([], f)
            except (json.JSONDecodeError, OSError) as e:
                logger.debug(f"receive request 폴링: {e}")
            time.sleep(0.5)

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
        """FILE_READY 수신 → 수신 pending 등록 (v3.0: 자동 전송 요청 없음).

        v3.0 S2c: FILE_READY 는 source 의 fetch 응답(_serve_fetch) 일부로 requester
        에게만 온다. 여기선 pending_transfers 등록 + 디스크 검사만 하고, 전송은 곧
        이어질 FILE_START/CHUNK 가 진행한다. eager 자동 REQUEST 는 제거됨.
        v2.1: 단일 active incoming invariant 강제 (기존 incoming supersede + cleanup).
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
                # v3.0: 진행 중 fetch 가 있으면 실패 신호 → 받기 fallback (Task 8)
                self._signal_fetch(metadata.transfer_id, fail=FETCH_FAIL_ERROR)
                return

            # 전송 상태 추적 시작 (수신)
            display_name = metadata.root_name or f"{metadata.file_count}개 파일"
            self._track_transfer(metadata.transfer_id, display_name, metadata.total_size, "receive")

            # v3.0 S2c: **eager 자동 MSG_FILE_REQUEST 송출 제거**. 전송은 source 의
            # fetch 응답(_serve_fetch)이 구동한다. 여기선 수신 pending 등록만 (chunk 수신
            # 준비). FILE_READY 는 이제 fetch 응답의 일부로 requester 에게만 targeted 온다.
            logger.info(
                f"[파일] 수신 준비(lazy): {metadata.file_count}개, "
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

    def _send_files(self, transfer_id, metadata, file_paths, completed_files=None,
                    receiver_peer=""):
        """파일 전송 실행 (별도 스레드에서 호출)

        v3.0: receiver_peer 가 있으면 chunk meta 에 동봉 → 서버가 그 peer 에게만
        중계 (lazy fetch 응답). 빈 문자열이면 eager broadcast (현행 호환).
        Task 6 의 fetch 핸들러가 requester peer_id 를 주입한다.
        """
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

                # FILE_START (v3.0: receiver_peer 있으면 targeted)
                start_data = {
                    "transfer_id": transfer_id,
                    "file_path": rel_path,
                    "file_index": file_idx,
                    "file_size": file_entry["size"],
                }
                if receiver_peer:
                    start_data["receiver_peer"] = receiver_peer
                    self._send_msg_to(MSG_FILE_START, start_data, receiver_peer)
                else:
                    self._send_msg(MSG_FILE_START, start_data)

                # FILE_CHUNK × N (바이너리 프레임 + 진행률 추적)
                def chunk_callback(chunk_index, chunk_data, chunk_hash,
                                   _tid=transfer_id, _rel=rel_path,
                                   _rcv=receiver_peer):
                    nonlocal bytes_sent
                    if _tid in self._cancelled_transfers:
                        raise InterruptedError("전송 중단 요청")
                    raw = self.protocol.create_binary_chunk(
                        _tid, _rel, chunk_index, chunk_data, chunk_hash,
                        receiver_peer=_rcv,
                    )
                    if _rcv:
                        self._send_raw_to(raw, _rcv)
                    else:
                        self._send_raw_msg(raw)
                    bytes_sent += len(chunk_data)
                    self._update_transfer_bytes(_tid, bytes_sent)

                sha256 = self.file_manager.send_file(abs_path, chunk_callback)

                # FILE_END (v3.0: receiver_peer 있으면 targeted)
                end_data = {
                    "transfer_id": transfer_id,
                    "file_path": rel_path,
                    "sha256": sha256,
                }
                if receiver_peer:
                    end_data["receiver_peer"] = receiver_peer
                    self._send_msg_to(MSG_FILE_END, end_data, receiver_peer)
                else:
                    self._send_msg(MSG_FILE_END, end_data)
            else:
                # for 루프가 break 없이 완료된 경우만 TRANSFER_COMPLETE
                complete_data = {"transfer_id": transfer_id}
                if receiver_peer:
                    complete_data["receiver_peer"] = receiver_peer
                    self._send_msg_to(MSG_TRANSFER_COMPLETE, complete_data, receiver_peer)
                else:
                    self._send_msg(MSG_TRANSFER_COMPLETE, complete_data)
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
                # v3.0 S2c: lazy fetch 가 진행 중이면 그 fetch 에 경로를 넘긴다 — OS
                # 클립보드 set 은 provider 가 paste 시점에 수행하므로 여기서 직접 set 안 함.
                # 매칭되는 fetch 가 없으면(방어적) 기존처럼 직접 set.
                if not self._signal_fetch(transfer_id, paths=clipboard_paths):
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
            # v3.0 S4: 받기 fallback 대상 (전송창이 "받기" 행으로 표시)
            with self._offer_lock:
                state["receivable"] = list(self.receivable_offers.values())

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
