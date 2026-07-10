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

from config import AppConfig, load_config, save_config, get_last_config_warning
from core.protocol import (
    MSG_CLIPBOARD, MSG_PING, MSG_PONG,
    MSG_FILE_READY, MSG_FILE_START,
    MSG_FILE_CHUNK, MSG_FILE_END, MSG_FILE_ACK,
    MSG_FILE_CANCEL, MSG_FILE_CANCEL_ACK, MSG_FILE_ERROR,
    CANCEL_ACK_ROLE_SENDER, CANCEL_ACK_ROLE_RECEIVER, CANCEL_ACK_ROLE_NONE,
    CANCEL_ACK_STATUS_OK, CANCEL_ACK_STATUS_UNKNOWN,
    MSG_TRANSFER_COMPLETE,
    # v3.0 lazy clipboard 메시지 + fetch 실패 사유
    MSG_CLIP_OFFER, MSG_CLIP_FETCH, MSG_CLIP_FETCH_FAIL,
    CLIP_OFFER_KIND_FILE, CLIP_OFFER_KIND_IMAGE,
    FETCH_FAIL_SUPERSEDED, FETCH_FAIL_EXPIRED, FETCH_FAIL_MISSING,
    FETCH_FAIL_OFFLINE, FETCH_FAIL_ERROR,
)
from core.network import NetworkServer, NetworkClient
from core.protocol import Protocol
from core.clipboard_manager import ClipboardManager
from core.file_transfer import (
    FileTransferManager, FileMetadata, CheckpointManager, Checkpoint,
    format_size, CHUNK_SIZE,
)
from core.privacy import detect_sensitive_kind
# v3.0 lazy provider (OS 별 백엔드 팩토리 — 헤드리스/미지원 시 None graceful)
from core.lazy_clipboard import get_lazy_provider, FetchedContent, KIND_FILE, KIND_IMAGE
from ui.i18n import get_language, t

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


class _GracePeek(Exception):
    """v3.0.3 타이밍 가드 — 등록 직후 grace 안의 read(OS 셸/DE 의 즉시 peek)를 나타낸다.

    _provider_fetch 가 던지면 provider 는 빈 결과를 내주고(전송 안 함), 진짜 paste(grace
    후)는 다시 콜백을 호출해 정상 전송된다. 일반 fetch 실패와 구분해 알림을 띄우지 않는다.
    """


class FetchFailure(Exception):
    """_fetch_offer 실패 시 원인 코드(reason)를 담아 던진다 (이어받기 재시도 UI 용).

    reason 은 FAIL_REASON_INFO 의 키와 일치 — 네트워크 FETCH_FAIL 5종(core.protocol)
    이거나, 로컬 전용 사유(unknown_offer/no_space/timeout/fetch_empty/save_error) 중 하나.
    """

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        super().__init__(detail or reason)


# 받기(_receive_offer) 실패 원인 → (사용자 메시지, 재시도 가능 여부).
# terminal(False): 같은 offer_id 로 재시도해도 동일하게 실패할 게 확실 — receivable
#   목록에서 제거(기존 M6 동작 그대로). retryable(True): 일시적 원인으로 간주 —
#   receivable 엔트리를 살려두고 전송창에 "재시도" 버튼을 남긴다.
FAIL_REASON_INFO = {
    FETCH_FAIL_SUPERSEDED: ("원본에서 새로 복사됨 — 이전 파일은 받을 수 없음", False),
    FETCH_FAIL_EXPIRED: ("전송 유효시간 만료 — 원본에서 다시 복사해주세요", False),
    FETCH_FAIL_MISSING: ("원본 파일이 삭제/이동됨", False),
    "unknown_offer": ("전송 정보가 만료됨 — 원본에서 다시 복사해주세요", False),
    FETCH_FAIL_OFFLINE: ("원본 PC 연결 끊김", True),
    FETCH_FAIL_ERROR: ("전송 중 오류 발생", True),
    "cancelled": ("전송이 취소됨", True),
    "chunk_hash_mismatch": ("데이터 손상 감지", True),
    "assemble_failed": ("파일 조립 실패", True),
    "source_error": ("원본 측 오류", True),
    "timeout": ("응답 시간 초과", True),
    "no_space": ("저장 공간 부족", True),
    "save_error": ("저장 중 오류(권한/디스크)", True),
    "fetch_empty": ("알 수 없는 오류", True),
}


class InfiniteClipboard:
    """앱 핵심 로직 — 네트워크 + 클립보드 + 파일전송 조립"""

    def __init__(self, config: AppConfig):
        self.config = config
        # 트레이는 장수 프로세스 — 언어를 앱 시작 시점 1회만 계산해 보관(재시작 전제).
        self._lang = get_language(self.config)
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

        # 클립보드 이력 (최근 N개). H2: 로컬 클립보드 모니터 스레드와 네트워크
        # 수신 스레드가 동시에 mutate 할 수 있어 락으로 보호.
        self.clipboard_history = []
        self._history_lock = threading.Lock()

        # 네트워크 (모드에 따라 서버 또는 클라이언트)
        self.server = None
        self.client = None

        # 상태
        self.connected = False
        self.connected_clients = 0
        # C3/C4: 트레이가 아직 없는 시점(app.start() 는 TrayApp 생성 전 호출됨)에
        # 발생한 시작 실패를 main() 이 tray 준비 후 한 번에 notify 할 수 있게 보관.
        self._startup_error = None
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
        # [receiver 측] 리뷰 발견: H7 의 _source_peer_for_transfer 가 received_offers
        # (offer_id 1개만 유지하는 supersede 캐시) 를 조회했는데, 무관한 다른 peer 의
        # 새 offer 가 broadcast 되기만 해도 그 캐시가 통째로 교체돼 진행 중이던 전송의
        # source_peer 조회가 실패했다(→ MSG_FILE_ERROR 가 broadcast 로 새서 H7 이 막던
        # 정보 노출이 재현). offer 수명과 무관하게 transfer_id 별로 source_peer 를
        # 별도 보관 — _transfers_lock 으로 보호(다른 전송 상태 dict 와 동일 락 재사용).
        self._transfer_source_peers = {}

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
        # v3.0 S3: 이미지 offer 스냅샷 temp 정리
        self._cleanup_offer_image()
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

        # C3: bind 실패(포트 충돌 등)를 감싸지 않으면 tray 가 생기기도 전에
        # 무음으로 전체 크래시(windowed 빌드는 콘솔이 없어 로그도 안 보임).
        try:
            self.server.start()
            self.connected = True
        except OSError as e:
            logger.error(f"서버 시작 실패 (포트 {self.config.port}): {e}")
            self.server = None
            self.connected = False
            self._startup_error = (
                f"서버 시작 실패: 포트 {self.config.port}을(를) 사용할 수 없습니다 "
                f"({e}). 다른 프로그램이 포트를 사용 중이거나 권한이 없을 수 있습니다. "
                f"설정에서 포트를 변경해보세요."
            )
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

    def _verify_sender_identity(self, sock, data, field_name) -> bool:
        """서버: JSON 메시지의 자기신고 identity 필드가 handshake 로 확인된 소켓
        peer_id 와 일치하는지 검증한다 (C1 — 위조된 requester_peer/source_peer 로
        다른 peer 를 사칭하는 것 차단). 불일치/누락 시 False (호출자가 drop)."""
        claimed = data.get(field_name) if isinstance(data, dict) else None
        with self.server.clients_lock:
            actual = self.server.clients.get(sock, {}).get("peer_id")
        return bool(claimed) and claimed == actual

    def _server_route(self, msg_type, data, sock, local_handler):
        """v3.0 JSON 메시지 라우팅: receiver_peer 있으면 그 peer 1 소켓에만 중계.

        - receiver_peer == 내 peer_id → 내가 최종 대상, 로컬 처리만 (중계 안 함)
        - receiver_peer == 다른 peer → relay only (로컬 처리 안 함)
        - receiver_peer 없음("") → 로컬 처리 + broadcast (eager 호환)
        폴더 규칙 #3: 바이너리 chunk 는 별도(_raw) 경로, 이건 JSON 메시지용.

        Returns:
            Optional[bool]: relay-only 경로에서 send_to_peer 의 성공 여부
                (M7 — 호출자가 대상 부재를 감지해 반응할 수 있게). 로컬 처리/
                broadcast 경로는 판단할 대상이 없어 None.
        """
        receiver = data.get("receiver_peer") if isinstance(data, dict) else None
        if receiver:
            if receiver == self.config.peer_id:
                if local_handler:
                    local_handler(data)
                return None
            else:
                return self.server.send_to_peer(receiver, msg_type, data)
        else:
            if local_handler:
                local_handler(data)
            self.server.broadcast(msg_type, data, exclude_sock=sock)
            return None

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
            # C1: source_peer 자기신고가 실제 소켓 identity 와 일치하는지 검증
            if not self._verify_sender_identity(sock, data, "source_peer"):
                logger.warning(f"[보안] MSG_CLIP_OFFER source_peer 위조 의심 — drop ({sock})")
            else:
                # copy 알림 — 로컬 처리(서버도 receiver 일 수 있음) + broadcast
                self._handle_clip_offer(data)
                self.server.broadcast(MSG_CLIP_OFFER, data, exclude_sock=sock)

        elif msg_type == MSG_CLIP_FETCH:
            # C1: requester_peer 자기신고가 실제 소켓 identity 와 일치하는지 검증
            # (위조 시 다른 peer 를 사칭해 그 peer 에게 원치 않는 파일/클립보드
            # 강제 전송을 유발할 수 있었음 — 감사 Critical #1)
            if not self._verify_sender_identity(sock, data, "requester_peer"):
                logger.warning(f"[보안] MSG_CLIP_FETCH requester_peer 위조 의심 — drop ({sock})")
            else:
                # paste 요청 — receiver_peer(=source) 로 routed
                relayed = self._server_route(MSG_CLIP_FETCH, data, sock, self._handle_clip_fetch)
                if relayed is False:
                    # M7: send_to_peer 반환값을 버리면 source 가 오프라인일 때
                    # requester 는 응답을 영원히 못 받고 최대 600초(하드 타임아웃)
                    # 대기한다. FETCH_FAIL_OFFLINE 으로 즉시 알린다.
                    parsed = self.protocol.parse_clip_fetch(data)
                    if parsed:
                        self._send_fetch_fail(
                            parsed["offer_id"], FETCH_FAIL_OFFLINE, parsed["requester_peer"],
                        )

        elif msg_type == MSG_CLIP_FETCH_FAIL:
            # fetch 실패 — receiver_peer(=requester) 로 routed
            self._server_route(MSG_CLIP_FETCH_FAIL, data, sock, self._handle_clip_fetch_fail)

        elif msg_type == MSG_FILE_READY:
            # v3.0: fetch 응답의 일부로 requester 에게만 targeted (receiver_peer 라우팅)
            self._server_route(MSG_FILE_READY, data, sock, self._handle_file_ready)

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

        elif msg_type == MSG_FILE_ACK:
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

    def _on_client_disconnected(self, reason: str = ""):
        """서버 연결 끊김

        M9: handshake 단계에서 프로토콜 버전 불일치(hard break)로 끊긴 거라면
        사용자에게 구체적으로 알린다 — 과거엔 이 상세 사유가 로그에만 남고
        UI/알림은 "연결 끊김"이라는 일반 문구뿐이라, 다른 PC 가 구버전이라
        영영 동기화가 안 되는 상황을 사용자가 원인조차 알 수 없었다.
        """
        self.connected = False
        logger.info(f"[클라이언트] 서버 연결 끊김{f' ({reason})' if reason else ''}")
        if "version mismatch" in reason or "hard break" in reason:
            self._notify(
                t("버전 불일치로 연결 실패", self._lang),
                t(
                    "상대 PC 의 Infinite Clipboard 버전이 다릅니다 — 양쪽 모두 "
                    "최신 버전으로 업그레이드하세요",
                    self._lang,
                ),
            )
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

    def _lazy_owns_clipboard(self) -> bool:
        """받는 PC 에서 lazy provider 가 현재 클립보드를 원격 offer placeholder 로 소유
        중이면 True → 모니터는 이번 폴링에서 클립보드 읽기를 건너뛴다.

        이유: 우리가 등록한 lazy placeholder 를 "로컬 복사" 로 오인하면 ① has_changed 의
        클립보드 읽기가 우리 자신의 paste-render(WM_RENDERFORMAT / SelectionRequest /
        send / provideDataForType)를 유발해 paste 도 안 했는데 네트워크 fetch 하고,
        ② 받은 staging 경로로 offer 를 재broadcast 하는 self-loop 가 생긴다. 사용자가
        로컬에서 새로 복사하면 OS 가 소유권을 회수 → provider 가 False 반환 → 정상 재개.
        """
        prov = self.lazy_provider
        if prov is None:
            return False
        try:
            return bool(prov.owns_clipboard())
        except Exception:
            return False

    def _monitor_clipboard(self):
        """클립보드 변경 폴링 루프"""
        while self.running:
            try:
                # lazy provider 가 클립보드를 소유 중이면(원격 offer placeholder) self-loop
                # 방지를 위해 읽기 skip — 사용자 로컬 복사 시 소유권 회수되어 자동 재개.
                if self._is_network_active() and not self._lazy_owns_clipboard():
                    changed, content_type, content = self.clipboard.has_changed()

                    if changed and content is not None:
                        logger.info(f"[클립보드] 변경 감지: {content_type}")
                        if content_type == "files":
                            # v3.0 S2c: eager 전송 대신 offer broadcast (paste 시점 fetch).
                            self._announce_offer(content)
                        elif content_type == "image":
                            # v3.0 S3: 이미지도 lazy — base64 디코드 → 임시 스냅샷 → offer
                            self._announce_image_offer(content)
                        else:
                            # 텍스트만 inline 전송 (작고 즉시성 중요)
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
        # H2: 로컬 모니터 스레드(_monitor_clipboard)와 네트워크 수신 스레드가
        # 동시에 호출할 수 있어 mutate+trim+저장을 한 크리티컬 섹션으로 묶는다.
        # 안 그러면 두 insert 사이에 length 체크가 끼어들어 trim 이 틀어지거나,
        # 파일 쓰기 도중 리스트가 바뀌어 json.dump 가 일관되지 않은 스냅샷을 볼 수 있다.
        with self._history_lock:
            self.clipboard_history.insert(0, entry)
            if len(self.clipboard_history) > self.config.clipboard_history_size:
                self.clipboard_history.pop()
            self._save_history_file()

    def _save_history_file(self):
        """이력을 JSON 파일에 저장 (별도 프로세스 History 창 공유용, 원자적 쓰기).

        v2.2 R1: POSIX 0o600 권한 적용. history 에는 텍스트 클립보드 내용이
        그대로 들어갈 수 있어 (multi-user 시스템에서) 다른 사용자 읽기 차단.
        Windows 는 user 폴더 ACL 기본이라 별도 처리 불필요.

        H2: 호출자(_add_to_history)가 이미 self._history_lock 을 보유한 상태에서
        호출해야 한다 — self.clipboard_history 를 락 없이 읽지 않기 위함.
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
            # C2: 이 전송을 기다리는 _active_fetch 가 있으면 즉시 깨움 (최대 600초
            # hang 방지 — 진행 도중 실패는 이 레거시 FILE_ERROR 경로로만 보고됨)
            self._signal_fetch(transfer_id, fail=data.get("error", "source_error"))

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
            # C2: 진행 중이던 fetch 를 취소로 즉시 깨움 (hang 방지)
            self._signal_fetch(transfer_id, fail="cancelled")

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
        self._cleanup_offer_image()  # 이전 offer 가 이미지였으면 스냅샷 temp 정리
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

    def _offer_image_dir(self):
        """source 측 이미지 offer 스냅샷 임시 디렉토리 (paste 전까지 바이트 보관)."""
        d = Path(tempfile.gettempdir()) / "ic_offer_images"
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        return d

    def _cleanup_offer_image(self):
        """현재 offer 가 이미지면 그 스냅샷 temp 파일 삭제 (supersede/종료 시).

        M5: 삭제를 _outgoing_fetch_lock 없이 하면, 마침 다른 peer 가 이 offer 를
        paste 해 _serve_fetch(같은 락을 잡고 파일을 스트리밍)가 진행 중일 때
        동시에 os.remove 가 실행될 수 있다. Windows 는 열려 있는 파일을 삭제
        못해(PermissionError, OSError 로 잡혀 조용히 무시됨) temp 파일이 계속
        쌓인다. 같은 락을 잡아 _serve_fetch 가 끝난 뒤에만 삭제되도록 직렬화.
        """
        with self._offer_lock:
            prev = self.current_offer
        if prev and prev.get("_image_temp"):
            with self._outgoing_fetch_lock:
                try:
                    os.remove(prev["_image_temp"])
                except OSError as e:
                    logger.debug(f"이미지 offer 스냅샷 삭제 실패 (무시): {e}")

    def _announce_image_offer(self, b64_data):
        """[source] 이미지 복사 → 바이트를 임시 파일로 스냅샷 + MSG_CLIP_OFFER(kind=image).

        파일과 달리 클립보드 이미지는 디스크 경로가 없으므로, copy 시점에 바이트를
        임시 파일로 떠 두고(다른 PC 가 paste 할 때 그 파일을 단일-파일 전송으로 보냄).
        네트워크 전송은 paste 시점에만 발생(0바이트 copy 유지). base64 decode 는 로컬 CPU
        만 사용 — 향후 capture-only 최적화 여지(현재는 단순/저위험 우선).
        """
        self._cleanup_offer_image()  # 이전 이미지 offer 스냅샷 정리
        try:
            png_bytes = base64.b64decode(b64_data)
        except Exception as e:
            logger.error(f"이미지 offer 디코드 오류: {e}")
            return
        if not png_bytes:
            return
        try:
            tmp_dir = self._offer_image_dir()
            fd, tmp_path = tempfile.mkstemp(prefix="ic_offer_img_", suffix=".png", dir=tmp_dir)
            with os.fdopen(fd, "wb") as f:
                f.write(png_bytes)
        except Exception as e:
            logger.error(f"이미지 스냅샷 오류: {e}")
            return
        try:
            metadata = self.file_manager.collect_metadata([tmp_path])
        except Exception as e:
            logger.error(f"이미지 offer 메타데이터 오류: {e}")
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return
        offer_id = metadata.transfer_id
        items = [{"name": "clipboard.png", "size": len(png_bytes), "hash": ""}]
        created_at = time.time()
        with self._offer_lock:
            self.current_offer = {
                "offer_id": offer_id, "kind": CLIP_OFFER_KIND_IMAGE,
                "metadata": metadata, "file_paths": [tmp_path],
                "created_at": created_at, "_image_temp": tmp_path,
            }
        try:
            raw = self.protocol.create_clip_offer(
                offer_id, self.config.peer_id, CLIP_OFFER_KIND_IMAGE,
                items, len(png_bytes), created_at,
            )
        except Exception as e:
            logger.error(f"이미지 offer 생성 오류: {e}")
            return
        self._send_raw_msg(raw)  # broadcast
        logger.info(
            f"[offer] 이미지 알림: {format_size(len(png_bytes))} offer={offer_id[:8]}…"
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
            # 새 offer 가 이전 것을 supersede — 최신만 유지.
            # _registered_at: fetch grace 기준 시각. 등록(클립보드 placeholder) 직전에
            # 박아두면 그 직후 OS 셸의 즉시 peek 까지 grace 안에 들어와 차단된다(race 없음).
            offer["_registered_at"] = time.time()
            self.received_offers = {offer_id: offer}
            # 이전 받기 fallback 잔여 정리 (supersede)
            old_receivable = bool(self.receivable_offers)
            self.receivable_offers = {}
        # v3.0.5: 기본은 명시적 '받기' 모드 — 받는 PC 가 클립보드를 소유하지 않으므로
        # 클립보드 매니저/파일인식 앱의 자동 read 가 paste 없이 전송을 트리거하지 못한다
        # (함정 #28: Wayland 는 peek vs paste 구분 신호가 없어 paste-트리거 lazy 가 샘).
        # config.lazy_paste=True 면 자동 peek 없는 환경 한정 기존 paste-트리거 lazy 등록.
        ok = False
        if getattr(self.config, "lazy_paste", False):
            provider = self._ensure_lazy_provider()
            if provider is not None and provider.is_supported(offer["kind"]):
                try:
                    provider.clear()  # 이전 등록 해제 (supersede)
                    ok = provider.register_offer(offer, self._provider_fetch)
                except Exception as e:
                    logger.warning(f"offer 등록 실패 — 받기 모드로: {e}")
                    ok = False
        if ok:
            # opt-in lazy paste — Ctrl+V 로 바로 가져옴. 알림 무음 (Gate S4).
            logger.info(f"[offer] 수신·등록(OK, lazy-paste): offer={offer_id[:8]}…")
            if old_receivable:
                self._save_transfer_state(force=True)  # 옛 받기 목록 비움 반영
        else:
            # 기본 경로 — 명시적 '받기' (알림 + 전송창 받기 행). 자동 전송 없음.
            self._add_receivable(offer)
            logger.info(f"[offer] 수신(받기 모드): offer={offer_id[:8]}…")

    def _fetch_timeout(self, total_size: int) -> float:
        """fetch 하드 타임아웃 (Rec 2). 크기 비례 + 하한/상한."""
        return max(30.0, min(600.0, total_size / (256 * 1024)))

    def _load_resume_for_offer(self, offer_id, offer):
        """[receiver] 이 offer 에 대한 이전 체크포인트가 있으면 이어받기 힌트로 변환.

        transfer_id == offer_id 라 같은 offer 를 재fetch 하면 이전 시도의
        체크포인트(core.file_transfer.CheckpointManager)를 그대로 조회할 수
        있다. 체크포인트가 가리키는 파일이 지금 offer 의 파일 목록과 어긋나면
        (오퍼가 그 사이 바뀌었거나 stale) 안전하게 무시하고 처음부터 fetch한다
        — 부분 적용은 하지 않는다.
        """
        try:
            checkpoint = self.checkpoint_manager.load(offer_id)
        except Exception:
            return None
        if checkpoint is None:
            return None
        # offer["items"]["name"] 은 표시용 basename 뿐(_announce_offer 참조,
        # 폴더 전송은 하위 경로를 포함하는 rel path 라 basename 비교로 대조).
        offer_basenames = {item.get("name") for item in (offer.get("items") or [])}
        candidates = list(checkpoint.completed_files)
        if checkpoint.current_file:
            candidates.append(checkpoint.current_file)
        if not candidates or not all(
            os.path.basename(c) in offer_basenames for c in candidates
        ):
            return None
        return {
            "completed_files": checkpoint.completed_files,
            "current_file": checkpoint.current_file,
            "last_chunk_index": checkpoint.last_chunk_index,
        }

    def _fetch_offer(self, offer_id):
        """[receiver] lazy provider 콜백 — paste 시점 **동기** fetch.

        FetchedContent(kind=file, paths=[스테이징 경로]) 반환, 실패 시 예외(→provider
        가 빈 결과 처리 → 받기 fallback). invariant (A): 한 시점 1개 fetch (_fetch_lock).
        """
        with self._fetch_lock:
            with self._offer_lock:
                offer = self.received_offers.get(offer_id)
            if offer is None:
                raise FetchFailure("unknown_offer", f"알 수 없는 offer: {offer_id}")
            source_peer = offer["source_peer"]
            # 리뷰 발견: received_offers 는 최신 offer 1개만 유지하는 supersede
            # 캐시라, 이 fetch 가 끝나기 전에 다른 offer(다른 peer 포함)가 도착
            # 하면 evict 된다. transfer_id(==offer_id) 별로 source_peer 를 별도
            # 보관해 이 fetch 진행 중엔 H7 타겟 라우팅이 항상 조회 가능하게 함.
            with self._transfers_lock:
                self._transfer_source_peers[offer_id] = source_peer
            total = int(offer.get("total_size", 0))
            # Rec 3: requester 가 fetch 전에 저장 공간 검사 (실제 저장 위치 근사)
            if not self.file_manager.check_disk_space(total):
                raise FetchFailure("no_space", "저장 공간 부족")
            event = threading.Event()
            with self._active_fetch_lock:
                self._active_fetch = {
                    "offer_id": offer_id, "transfer_id": offer_id,
                    "event": event, "paths": None, "fail": None,
                }
            try:
                resume = self._load_resume_for_offer(offer_id, offer)
                raw = self.protocol.create_clip_fetch(
                    offer_id, self.config.peer_id, receiver_peer=source_peer,
                    resume=resume,
                )
                self._send_raw_to(raw, source_peer)
                timeout = self._fetch_timeout(total)
                if not event.wait(timeout=timeout):
                    raise FetchFailure("timeout", f"fetch 타임아웃 ({timeout:.0f}s)")
                with self._active_fetch_lock:
                    af = self._active_fetch or {}
                    fail = af.get("fail")
                    paths = af.get("paths")
                if fail:
                    raise FetchFailure(fail, f"fetch 실패: {fail}")
                if not paths:
                    raise FetchFailure("fetch_empty", "fetch 결과 없음")
                # v3.0 S3: 이미지는 조립된 단일 파일을 바이트로 읽어 data 로 반환
                # (provider 가 image/png 으로 OS 클립보드에 제공). 파일은 paths 그대로.
                if offer.get("kind") == CLIP_OFFER_KIND_IMAGE:
                    with open(paths[0], "rb") as f:
                        return FetchedContent(kind=KIND_IMAGE, data=f.read())
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

    def _source_peer_for_transfer(self, transfer_id) -> str:
        """[receiver] H7: transfer_id(==offer_id)로 이 전송의 source peer 조회.

        MSG_FILE_ERROR 를 broadcast 하면 무관한 피어에게 전송 실패 상세(파일 경로 등)
        가 노출된다 — 그 peer 에게만 targeted 전송하기 위해 조회한다.

        리뷰 발견: 원래 received_offers(offer_id 1개만 유지하는 supersede 캐시)
        를 봤는데, 무관한 다른 peer 의 새 offer 가 broadcast 되기만 해도 그
        캐시가 통째로 교체돼 이 전송의 source_peer 조회가 실패했다(→ 실패 시
        broadcast 로 새서 H7 이 막던 정보 노출이 재현). _fetch_offer 가 채우는
        transfer_id 전용 `_transfer_source_peers` 를 우선 조회하고, 없으면
        received_offers 로 폴백한다(예: "받기" 흐름 등 다른 경로 대비).
        둘 다 없으면 빈 문자열(호출부가 broadcast 로 graceful degrade)."""
        with self._transfers_lock:
            source_peer = self._transfer_source_peers.get(transfer_id, "")
        if source_peer:
            return source_peer
        with self._offer_lock:
            offer = self.received_offers.get(transfer_id)
        return offer.get("source_peer", "") if offer else ""

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
        resume = parsed.get("resume") or {}
        threading.Thread(
            target=self._serve_fetch, args=(offer, requester, resume), daemon=True,
        ).start()

    def _serve_fetch(self, offer, requester, resume=None):
        """[source] fetch 응답 — requester 에게만 FILE_READY + 파일 전송. (A) 직렬화.

        resume: receiver 가 보낸 이어받기 힌트(completed_files/current_file/
        last_chunk_index) — 없으면 처음부터(기존과 동일).
        """
        resume = resume or {}
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
                self._send_files(
                    offer_id, metadata, file_paths, receiver_peer=requester,
                    completed_files=set(resume.get("completed_files") or []),
                    resume_file=resume.get("current_file", ""),
                    resume_chunk_index=max(0, resume.get("last_chunk_index", -1) + 1),
                )
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

        **v3.0.3 타이밍 가드**: 등록 직후 `fetch_grace_seconds` 안에 들어온 read 는 OS 셸
        (Windows explorer.exe)/DE 의 즉시 peek 으로 간주해 fetch 하지 않는다(GracePeek 으로
        조용히 빈 결과 → 전송 안 함). 진짜 paste 는 항상 grace 보다 한참 뒤라 정상 전송된다.
        provider 의 1회 캐시는 GracePeek 시 채워지지 않으므로(예외=미캐시) 이후 진짜 paste 가
        다시 콜백을 호출한다. grace=0 이면 가드 끔(eager). (받기 버튼은 _fetch_offer 직접
        호출이라 grace 무관 — 명시적 수신은 즉시.)
        """
        grace = float(getattr(self.config, "fetch_grace_seconds", 0) or 0)
        if grace > 0:
            with self._offer_lock:
                offer = self.received_offers.get(offer_id)
                reg_at = offer.get("_registered_at", 0) if offer else 0
            if reg_at and (time.time() - reg_at) < grace:
                logger.info(
                    f"[offer] grace peek 무시 ({time.time() - reg_at:.2f}s < {grace:.1f}s) "
                    f"— 전송 안 함 offer={offer_id[:8]}…"
                )
                raise _GracePeek(offer_id)
        try:
            return self._fetch_offer(offer_id)
        except _GracePeek:
            raise  # 가드 — 알림 없이 provider 가 빈 결과 처리
        except Exception:
            with self._offer_lock:
                offer = self.received_offers.get(offer_id)
            name = self._offer_display_name(offer) if offer else "파일"
            self._notify(
                t("받기 실패", self._lang),
                t("{name} — 원본에서 받을 수 없음 (클립보드 유지)", self._lang).format(name=name),
            )
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
        self._notify(
            t("파일 받기", self._lang),
            t("{name} — 전송 창에서 [받기]", self._lang).format(name=name),
        )

    def _clear_receivable(self, offer_id) -> None:
        with self._offer_lock:
            existed = self.receivable_offers.pop(offer_id, None)
        if existed:
            self._save_transfer_state(force=True)

    def _mark_receivable_failed(self, offer_id, reason) -> None:
        """받기 실패(재시도 가능) — receivable 목록에서 지우지 않고 사유만 기록.

        M6 은 실패 시 무조건 clear 해 위젯의 requested 플래그가 리셋 안 되는
        문제를 고쳤다(_handle_receive_failure 의 terminal 분기는 그 동작 그대로
        유지). retryable 원인은 대신 entry 를 살려두고 last_failure 타임스탬프를
        남긴다 — 전송창이 이 타임스탬프 변화를 보고 requested 를 명시적으로
        리셋해 "재시도" 버튼을 다시 활성화한다(M6 재발 없이 재시도 허용).
        """
        message, _retryable = FAIL_REASON_INFO.get(reason, FAIL_REASON_INFO["error"])
        with self._offer_lock:
            entry = self.receivable_offers.get(offer_id)
            if entry is None:
                return
            entry["last_failure"] = {
                "reason": reason, "message": message, "failed_at": time.time(),
            }
        self._save_transfer_state(force=True)

    def _handle_receive_failure(self, offer_id, name, reason) -> None:
        """[받기] 실패 분기 — terminal 은 목록 제거, retryable 은 재시도 가능하게 보존."""
        message, retryable = FAIL_REASON_INFO.get(reason, FAIL_REASON_INFO["error"])
        logger.warning(f"[받기] 실패: offer={offer_id[:8]}… reason={reason} retryable={retryable}")
        if retryable:
            self._mark_receivable_failed(offer_id, reason)
            self._notify(
                t("받기 실패", self._lang),
                t("{name} — {message} (재시도 가능)", self._lang).format(name=name, message=message),
            )
        else:
            self._clear_receivable(offer_id)
            self._notify(
                t("받기 실패", self._lang),
                t("{name} — {message}", self._lang).format(name=name, message=message),
            )

    def _annotate_completed_transfer(self, transfer_id, **fields) -> None:
        """완료 목록의 기존 엔트리에 필드 추가 (예: 받기 버튼 저장 경로).

        _finish_transfer 가 만드는 엔트리는 fetch 완료 시점 기준이라 아직
        로컬 저장 위치를 모른다 — _receive_offer 가 download_path 복사에
        성공한 뒤 사후 보강한다. entry 가 20건 cap 으로 이미 evict 됐으면
        조용히 무시(폴더 열기 버튼만 안 뜨는 낮은 영향).
        """
        updated = False
        with self._progress_lock:
            for entry in self._completed_transfers:
                if entry.get("transfer_id") == transfer_id:
                    entry.update(fields)
                    updated = True
                    break
        if updated:
            self._save_transfer_state(force=True)

    def _notify(self, title, message) -> None:
        """일반 OS 알림 (크기 무관). tray 우선, 없으면 plyer (비동기).

        M12: 두 경로 모두 실패하면 사용자는 "받기 실패"/"덮어쓰기 실패"/
        "버전 불일치" 같은 중요한 알림을 영영 못 본다. tray 실패는 완전
        무로그, plyer 최종 실패도 DEBUG(기본 콘솔엔 안 보임)에 묻혀 이
        상황 자체를 아무도 눈치챌 수 없었다. tray 실패는 최소 DEBUG 로,
        양쪽 다 실패하면 WARNING(기본 콘솔에 보임)으로 올린다.
        """
        tray = self.tray
        if tray is not None:
            try:
                tray.notify(title, message)
                return
            except Exception as e:
                logger.debug(f"tray 알림 실패, plyer 로 폴백: {e}")

        def _do():
            try:
                from plyer import notification
                notification.notify(
                    title=title, message=message, timeout=5,
                    app_name="Infinite Clipboard",
                )
            except Exception as e:
                logger.warning(
                    f"알림 전송 완전 실패(tray+plyer 둘 다) — 사용자가 '{title}' "
                    f"알림을 못 봤을 수 있음: {e}"
                )

        threading.Thread(target=_do, daemon=True).start()

    def _receive_offer(self, offer_id) -> None:
        """[받기 버튼] offer 를 fetch 해 download_path 에 저장 (provider 무관 수동 수신)."""
        with self._offer_lock:
            info = self.receivable_offers.get(offer_id)
        name = info.get("name", "파일") if info else "파일"
        try:
            fetched = self._fetch_offer(offer_id)  # staging 경로 (메커니즘 재사용)
        except FetchFailure as e:
            self._handle_receive_failure(offer_id, name, e.reason)
            return
        except Exception as e:
            # _fetch_offer 가 FetchFailure 로 감싸지 않은 미분류 예외(네트워크 전송 등) —
            # 일시적일 가능성이 높다고 보고 retryable 로 취급.
            logger.warning(f"[받기] fetch 실패(미분류): {e}")
            self._handle_receive_failure(offer_id, name, "error")
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
            # fetch 자체는 성공했으니 offer_id 는 여전히 유효 — save_error 는
            # retryable 로 분류되어 receivable 목록에 남는다(재시도는 재fetch
            # 부터 처음부터 다시, 스테이징 바이트 재사용 안 함 — 단순성 우선).
            self._handle_receive_failure(offer_id, name, "save_error")
            return

        self._clear_receivable(offer_id)
        self._annotate_completed_transfer(offer_id, path=dest_dir, via_receive_button=True)
        self._notify(
            t("받기 완료", self._lang),
            t("{name} — {saved}개 → {dest_dir}", self._lang).format(
                name=name, saved=saved, dest_dir=dest_dir
            ),
        )
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
                # H5: 여기서 pending_transfers/active incoming 슬롯을 정리하지
                # 않으면, source 는 이 실패를 모른 채 chunk 를 계속 보내고 그
                # "방치된" 전송이 나중에 조립 완료되어 사용자가 이미 실패 알림을
                # 본 뒤에도 클립보드를 무단으로 덮어쓴다 (_handle_file_cancel 의
                # incoming cleanup 과 동일 패턴 + source 에게 CANCEL 통지).
                self.file_manager.cancel_incoming(metadata.transfer_id, reason="disk_full")
                self.file_manager.cleanup_incoming_artifacts(metadata.transfer_id)
                self.file_manager.end_incoming(metadata.transfer_id)
                with self._transfers_lock:
                    self.pending_transfers.pop(metadata.transfer_id, None)
                # source_peer 조회는 _cancel_transfer_tracking(아래에서
                # _transfer_source_peers 를 정리함) 보다 반드시 먼저 — 순서가
                # 바뀌면 조회할 때 이미 지워진 뒤라 매번 broadcast 로 샌다.
                cancel_data = {"transfer_id": metadata.transfer_id, "reason": "error"}
                source_peer = self._source_peer_for_transfer(metadata.transfer_id)
                self._cancel_transfer_tracking(metadata.transfer_id)
                if source_peer:
                    cancel_data["receiver_peer"] = source_peer
                    self._send_msg_to(MSG_FILE_CANCEL, cancel_data, source_peer)
                else:
                    self._send_msg(MSG_FILE_CANCEL, cancel_data)
                # v3.0: 진행 중 fetch 가 있으면 실패 신호 → 받기 fallback (Task 8)
                self._signal_fetch(metadata.transfer_id, fail=FETCH_FAIL_ERROR)
                return

            # 전송 상태 추적 시작 (수신)
            display_name = metadata.root_name or f"{metadata.file_count}개 파일"
            self._track_transfer(metadata.transfer_id, display_name, metadata.total_size, "receive")

            # v3.0 S2c: 전송은 source 의 fetch 응답(_serve_fetch)이 구동한다.
            # 여기선 수신 pending 등록만 (chunk 수신 준비). FILE_READY 는 이제
            # fetch 응답의 일부로 requester 에게만 targeted 온다. 이어받기는
            # MSG_CLIP_FETCH 의 resume 필드로 처리(_load_resume_for_offer 참조).
            logger.info(
                f"[파일] 수신 준비(lazy): {metadata.file_count}개, "
                f"{format_size(metadata.total_size)}"
            )
        except Exception as e:
            logger.error(f"FILE_READY 처리 오류: {e}")

    def _send_files(self, transfer_id, metadata, file_paths, completed_files=None,
                    receiver_peer="", resume_file="", resume_chunk_index=0):
        """파일 전송 실행 (별도 스레드에서 호출)

        v3.0: receiver_peer 가 있으면 chunk meta 에 동봉 → 서버가 그 peer 에게만
        중계 (lazy fetch 응답). 빈 문자열이면 eager broadcast (현행 호환).
        Task 6 의 fetch 핸들러가 requester peer_id 를 주입한다.

        resume_file/resume_chunk_index: 이어받기 — resume_file 과 일치하는
        파일은 resume_chunk_index 부터만 청크를 보낸다(그 앞은 receiver 가
        이전 시도에서 이미 받아 체크포인트로 확인된 상태).
        """
        completed_files = completed_files or set()
        bytes_sent = 0  # 전체 전송 바이트 누적

        # 이미 완료된 파일의 바이트를 초기값에 반영
        for f in metadata.files:
            if f["path"] in completed_files:
                bytes_sent += f["size"]
            elif f["path"] == resume_file:
                bytes_sent += min(resume_chunk_index * CHUNK_SIZE, f["size"])

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

                start_chunk_index = resume_chunk_index if rel_path == resume_file else 0
                sha256 = self.file_manager.send_file(
                    abs_path, chunk_callback, start_chunk_index=start_chunk_index,
                )

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
            # H7: MSG_FILE_ERROR 는 이 전송의 상대(receiver_peer)에게만 targeted —
            # broadcast 하면 무관 피어에게 전송 실패 상세(파일 경로 등)가 노출된다.
            error_data = {"transfer_id": transfer_id, "error": str(e)}
            if receiver_peer:
                error_data["receiver_peer"] = receiver_peer
                self._send_msg_to(MSG_FILE_ERROR, error_data, receiver_peer)
            else:
                self._send_msg(MSG_FILE_ERROR, error_data)
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
            # H7: source peer 에게만 targeted (broadcast 시 무관 피어에 상세 노출).
            # 조회는 _cancel_transfer_tracking(_transfer_source_peers 를 정리함)
            # 보다 반드시 먼저 — 순서가 바뀌면 매번 broadcast 로 샌다.
            error_data = {
                "transfer_id": transfer_id,
                "error": f"청크 해시 불일치: {data.get('file_path')}#{data.get('chunk_index')}",
            }
            source_peer = self._source_peer_for_transfer(transfer_id)
            self._cancel_transfer_tracking(transfer_id)
            if source_peer:
                error_data["receiver_peer"] = source_peer
                self._send_msg_to(MSG_FILE_ERROR, error_data, source_peer)
            else:
                self._send_msg(MSG_FILE_ERROR, error_data)
            with self._transfers_lock:
                self.pending_transfers.pop(transfer_id, None)
            # C2: 이 전송을 기다리는 _active_fetch 가 있으면 즉시 깨움 (hang 방지)
            self._signal_fetch(transfer_id, fail="chunk_hash_mismatch")

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
            # H7: source peer 에게만 targeted (broadcast 시 무관 피어에 상세 노출).
            # 조회는 _cancel_transfer_tracking(_transfer_source_peers 를 정리함)
            # 보다 반드시 먼저 — 순서가 바뀌면 매번 broadcast 로 샌다.
            error_data = {
                "transfer_id": transfer_id,
                "error": f"파일 조립 실패: {file_path}",
            }
            source_peer = self._source_peer_for_transfer(transfer_id)
            self._cancel_transfer_tracking(transfer_id)
            if source_peer:
                error_data["receiver_peer"] = source_peer
                self._send_msg_to(MSG_FILE_ERROR, error_data, source_peer)
            else:
                self._send_msg(MSG_FILE_ERROR, error_data)
            # C2: 이 전송을 기다리는 _active_fetch 가 있으면 즉시 깨움 (hang 방지)
            self._signal_fetch(transfer_id, fail="assemble_failed")
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
            restored_paths, failed_overwrites = self.file_manager.restore_folder(
                transfer_id, metadata, dest_dir=staging_dir,
                conflict_policy=self.config.file_conflict_policy,
            )
            self._finish_transfer(transfer_id)
            logger.info(f"[파일] 전체 완료, 임시 저장: {len(restored_paths)}개 파일")

            # H8: overwrite 정책을 선택했는데 기존 파일 unlink 실패로 skip 강등된
            # 경우, 사용자는 자신의 파일이 교체됐다고 오인할 수 있어 명시적으로 알린다.
            if failed_overwrites:
                names = ", ".join(failed_overwrites[:3])
                if len(failed_overwrites) > 3:
                    names += f" 외 {len(failed_overwrites) - 3}개"
                self._notify(
                    t("덮어쓰기 실패", self._lang),
                    t(
                        "{names} — 기존 파일 유지됨 (교체 안 됨, 사용 중이거나 권한 문제)",
                        self._lang,
                    ).format(names=names),
                )

            if restored_paths:
                # 폴더 전송이면 폴더 경로를, 파일이면 개별 경로를 클립보드에 설정
                if metadata.transfer_type == "folder" and metadata.root_name:
                    folder_path = str(staging_dir / metadata.root_name)
                    clipboard_paths = [folder_path]
                else:
                    clipboard_paths = restored_paths
                # v3.0 S2c: lazy fetch 가 진행 중이면 그 fetch 에 경로를 넘긴다 — OS
                # 클립보드 set 은 provider 가 paste 시점에 수행하므로 여기서 직접 set 안 함.
                # C1 방어심층화: 매칭되는 fetch 가 없으면 이 전송을 요청한 적이 없다는
                # 뜻이므로(정상 흐름은 "받기"/lazy-paste 모두 _fetch_offer 를 거쳐 미리
                # _active_fetch 를 채워둔다) 더 이상 클립보드에 직접 set 하지 않는다 —
                # 과거엔 여기가 위조된 requester_peer 로 동의 없는 clipboard 강제 주입을
                # 허용하는 지점이었다.
                if not self._signal_fetch(transfer_id, paths=clipboard_paths):
                    logger.warning(
                        f"[보안] 대응하는 fetch 없이 TRANSFER_COMPLETE 수신 — "
                        f"clipboard 미설정 (transfer={transfer_id})"
                    )
        finally:
            # 예외 발생 시에도 반드시 정리
            self.checkpoint_manager.delete(transfer_id)
            # v2.1: manager active state 정리 (transfer_id 매칭 시에만)
            self.file_manager.end_incoming(transfer_id)
            with self._transfers_lock:
                self.pending_transfers.pop(transfer_id, None)
                self._transfer_source_peers.pop(transfer_id, None)

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
        with self._transfers_lock:
            self._transfer_source_peers.pop(transfer_id, None)
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


def _load_clipboard_history_file(history_file) -> tuple:
    """clipboard_history.json 을 읽어 (history: list, corrupted: bool) 반환.

    M13: 과거엔 `except Exception: pass` 로 완전히 무음이라, 사용자는 이력이
    손상돼서 비어있는 건지 원래 없는 건지 구분할 수 없었다. config.py C4 와
    동일 패턴(백업 + 로그) 으로 흔적을 남기고, corrupted 플래그로 UI 가 구분되는
    메시지를 보여줄 수 있게 한다.
    """
    import json
    import shutil

    if not history_file.exists():
        return [], False

    try:
        with open(history_file, "r", encoding="utf-8") as hf:
            loaded = json.load(hf)
        if not isinstance(loaded, list):
            raise ValueError(f"unexpected history format: {type(loaded).__name__}")
        return loaded, False
    except (json.JSONDecodeError, OSError, ValueError) as e:
        logger.warning(f"[history] clipboard_history.json 손상 — 초기화: {e}")
        try:
            backup_path = history_file.with_name(
                f"{history_file.name}.corrupt-{int(time.time())}"
            )
            shutil.copy2(history_file, backup_path)
            logger.info(f"[history] 손상된 이력 파일 백업: {backup_path}")
        except OSError as backup_err:
            logger.error(f"[history] 손상 파일 백업 실패: {backup_err}")
        return [], True


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
        # 리뷰 발견: 이 짧게 사는 창 프로세스가 메인 프로세스와 거의 동시에
        # load_config() 를 호출하면(둘 다 교정이 필요한 상태일 때) 각자 다른
        # 랜덤 auth_key/peer_id 로 저장을 시도해 레이스가 난다. 자동교정 저장은
        # 오래 사는 메인 프로세스에게만 맡긴다.
        config = load_config(persist_corrections=False)
        win = SettingsWindow(config)
    elif window_type == "history":
        from config import _get_config_dir, load_config
        from core.clipboard_manager import ClipboardManager
        from ui.history_window import HistoryWindow
        history_file = _get_config_dir() / "clipboard_history.json"
        history, corrupted = _load_clipboard_history_file(history_file)
        cm = ClipboardManager()
        # 설정된 언어를 창에 전달 (자동교정 저장은 메인 프로세스 몫 — persist_corrections=False)
        config = load_config(persist_corrections=False)
        win = HistoryWindow(history, cm, corrupted=corrupted, config=config)
    elif window_type == "transfers":
        from config import _get_config_dir, load_config
        from ui.transfer_window import TransferWindow
        state_file = str(_get_config_dir() / "transfer_state.json")
        config = load_config(persist_corrections=False)
        win = TransferWindow(state_file, config=config)
    elif window_type == "about":
        from config import load_config
        from ui.about_window import AboutWindow
        config = load_config(persist_corrections=False)
        win = AboutWindow(config=config)

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
    # C3/C4: tray 가 생기기 전에 발생한 시작 경고(설정 손상/서버 바인드 실패)를
    # 모아뒀다가 tray 준비 후 한 번에 notify.
    startup_warnings = []
    if get_last_config_warning():
        startup_warnings.append(get_last_config_warning())

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
    if app._startup_error:
        startup_warnings.append(app._startup_error)

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

        # C3/C4: tray 준비 완료 — 시작 단계에서 쌓인 경고를 이제 notify
        for warning in startup_warnings:
            tray.notify("Infinite Clipboard", warning)

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
