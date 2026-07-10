"""
서버/클라이언트 네트워크 계층

TCP 소켓 기반 서버·클라이언트 구현.
Protocol 모듈을 사용하여 메시지 생성/파싱, 3-step mutual HMAC 핸드셰이크 인증
수행(v2.2 R3 hard break — 폐기된 단일 SHA-256 핸드셰이크를 대체).
콜백 패턴으로 상위 계층에 이벤트 전달.
"""

import socket
import threading
import time
import logging
from typing import Dict, Any, Optional, Callable

from core.protocol import (
    Protocol, MSG_PING, MSG_PONG,
    MSG_HANDSHAKE_CHALLENGE, MSG_HANDSHAKE_RESPONSE, MSG_HANDSHAKE_ACK,
    PROTOCOL_VERSION,
    generate_nonce, compute_handshake_hmac, verify_handshake_hmac,
    generate_peer_id, is_valid_peer_id,
)

logger = logging.getLogger(__name__)


# ── 모듈 레벨 상수/헬퍼 ──────────────────────────────────────────────────

# 단일 메시지 최대 크기 (32MB) — v3.0 부터 파일 청크는 base64 없이 원시 바이너리
# 프레임(marker+meta_len+meta+raw bytes)으로 전송돼(Protocol.create_binary_chunk),
# 1MB 청크의 정상 최대는 메타 JSON 몇백 바이트를 더한 ~1MB 수준. 32MB 상한은
# 비정상 패킷(버그/악의적)으로 인한 메모리 폭발 방어용 상한.
MAX_MESSAGE_SIZE = 32 * 1024 * 1024

# 동시 연결(accept~_handle_client 스레드 종료 전) 상한 — LAN 환경에서 실사용
# 기기 수(수 대~십수 대)보다 넉넉하되, handshake 를 완료하지 않고 연결만 열어
# 스레드/fd 를 고갈시키는 slow-loris 류 공격을 차단 (H6).
MAX_PENDING_CONNECTIONS = 32


def _recv_all(sock: socket.socket, length: int) -> bytes:
    """
    지정된 길이만큼 데이터를 완전히 수신합니다.

    65536바이트 청크 단위로 루프하며, 연결이 끊기면 빈 바이트를 반환합니다.
    MAX_MESSAGE_SIZE를 초과하면 ValueError를 발생시킵니다.

    Args:
        sock: 수신할 소켓
        length: 수신할 총 바이트 수

    Returns:
        bytes: 수신된 데이터 (length 미만이면 연결 끊김)

    Raises:
        ValueError: length가 MAX_MESSAGE_SIZE를 초과할 때
    """
    if length > MAX_MESSAGE_SIZE:
        raise ValueError(
            f"메시지 크기({length:,} bytes)가 상한({MAX_MESSAGE_SIZE:,} bytes)을 초과"
        )

    buf = bytearray(length)
    pos = 0
    while pos < length:
        chunk = sock.recv(min(length - pos, 65536))
        if not chunk:
            break
        buf[pos:pos + len(chunk)] = chunk
        pos += len(chunk)
    return bytes(buf[:pos])


# ── 서버 ────────────────────────────────────────────────────────────────

class NetworkServer:
    """
    TCP 네트워크 서버

    클라이언트 연결을 수락하고, 핸드셰이크 인증 후 메시지를 수신/전송합니다.
    Tailscale 네트워크(100.64.0.0/10)에서 접속하면 인증을 건너뛸 수 있습니다.
    콜백을 통해 상위 계층에 이벤트를 전달합니다.
    """

    def __init__(
        self,
        port: int,
        auth_key: str,
        tailscale_trust: bool = True,
        bind_address: str = "",
        peer_id: str = "",
    ):
        """
        서버 인스턴스를 초기화합니다.

        Args:
            port: 바인드할 포트 번호
            auth_key: 클라이언트 인증에 사용할 공유 키
            tailscale_trust: M4 — 인증을 우회/완화하지 않는다(HMAC 은 항상
                필수). True 면 접속 peer 가 Tailscale IP 대역일 때 진단용
                로그 한 줄만 남긴다.
            bind_address: bind 할 IP 주소. 빈 문자열이면 v2.2 R2 자동 모드
                (Tailscale IP 우선, 미감지 시 0.0.0.0 fallback). 명시적
                "0.0.0.0" 은 모든 인터페이스 노출 (opt-in).
            peer_id: v3.0 이 서버의 안정적 식별자 (challenge 로 클라이언트에게 알림).
                보통 config.peer_id 가 전달된다. 비거나 형식 위반이면 자동 생성
                (standalone/테스트 안전망 — 핸드셰이크 invariant 보장).
        """
        self.port = port
        self.auth_key = auth_key
        self.tailscale_trust = tailscale_trust
        self.bind_address = bind_address
        self.peer_id = peer_id if is_valid_peer_id(peer_id) else generate_peer_id()
        self.protocol = Protocol(auth_key)

        # 클라이언트 관리: {socket: {"name": str, "peer_id": str}}
        # v3.0: 값을 dict 로 확장 (peer_id 라우팅용). broadcast 는 key(socket)만
        # 쓰므로 값 변경 영향 없음. peer_id→socket 역조회는 Task 3 에서 추가.
        self.clients: Dict[socket.socket, Dict[str, str]] = {}
        self.clients_lock = threading.Lock()
        self._write_locks: Dict[socket.socket, threading.Lock] = {}  # 소켓별 write 직렬화

        # H6: accept 됐으나 아직 _handle_client 스레드가 안 끝난 연결 수.
        # clients_lock 과 별개 — pending 은 handshake 미완료 연결도 포함해야 함
        # (clients dict 는 handshake 성공 후에만 채워짐).
        self._pending_lock = threading.Lock()
        self._pending_count = 0

        self.server_socket: Optional[socket.socket] = None
        self.running = False

        # 콜백 속성 (상위 계층에서 설정)
        self.on_client_connected: Optional[Callable] = None       # (sock, address, name, peer_id)
        self.on_client_disconnected: Optional[Callable] = None    # (sock, address, name, peer_id)
        self.on_message_received: Optional[Callable] = None       # (sock, message)

    def start(self):
        """
        서버를 시작합니다.

        v2.2 R2: bind_address 정책
          - "" (빈 문자열, 기본): Tailscale IP 자동 감지. 못 찾으면 0.0.0.0 fallback + warning
          - "0.0.0.0": 모든 인터페이스 (opt-in, 물리 LAN 노출)
          - 명시적 IP: 그 인터페이스만
        """
        self.running = True

        # bind 주소 결정
        bind_ip = self._resolve_bind_address()

        # 서버 소켓 생성 및 바인드
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((bind_ip, self.port))
        self.server_socket.listen(5)

        logger.info(f"서버 시작: {bind_ip}:{self.port}")

        # 클라이언트 수락 스레드
        accept_thread = threading.Thread(target=self._accept_clients, daemon=True)
        accept_thread.start()

    def _resolve_bind_address(self) -> str:
        """v2.2 R2: bind_address 설정에 따라 실제 bind IP 반환."""
        # 명시적 값 (validation 통과한 IP)
        if self.bind_address:
            if self.bind_address == "0.0.0.0":
                # M1: 프로토콜은 HMAC 인증만 하고 페이로드 자체는 암호화하지 않는
                # 평문 JSON — 0.0.0.0 은 물리 LAN(공유 Wi-Fi 등)의 다른 기기도
                # 접속을 "시도"할 수 있게 하고, auth_key 를 모르는 제3자도 패킷을
                # 스니핑하면 클립보드 내용을 그대로 읽을 수 있다.
                logger.warning(
                    "서버가 0.0.0.0 으로 bind — 모든 인터페이스 노출 (opt-in). "
                    "프로토콜은 평문(암호화 없음)이라 같은 물리 LAN 의 제3자가 "
                    "클립보드 내용을 스니핑할 수 있음. Tailscale 외 노출이 "
                    "의도되지 않았다면 설정창에서 'Tailscale 자동' 으로 변경"
                )
            return self.bind_address

        # 자동 모드: Tailscale IP 감지
        from core.tailscale import get_tailscale_ip
        ts_ip = get_tailscale_ip()
        if ts_ip:
            logger.info(f"서버 bind 자동: Tailscale IP {ts_ip}")
            return ts_ip

        # Tailscale 미감지 → 0.0.0.0 fallback
        logger.warning(
            "Tailscale IP 감지 실패 — 0.0.0.0 으로 fallback. "
            "Tailscale 미설치 / 미실행 / 권한 문제 가능"
        )
        return "0.0.0.0"

    def stop(self):
        """
        서버를 중지합니다.

        v2.1: shutdown(SHUT_RDWR) 후 close 로 race 축소.
        running=False 를 먼저 set 하면 _handle_client / _accept_clients 의
        except 블록 가드가 의식적 종료를 silent 로 처리한다 (WinError 10038 /
        OSError EBADF 1줄 출력 방지).
        """
        self.running = False

        # 모든 클라이언트 연결 종료 — shutdown 먼저 호출로 recv 가 0byte EOF 반환
        with self.clients_lock:
            for client_socket in list(self.clients.keys()):
                try:
                    client_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    # 이미 닫힌 소켓 — 무시
                    pass
                try:
                    client_socket.close()
                except OSError:
                    pass
            self.clients.clear()
            self._write_locks.clear()

        with self._pending_lock:
            self._pending_count = 0

        # 서버 소켓 종료
        if self.server_socket:
            try:
                self.server_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.server_socket.close()
            except OSError:
                pass
            self.server_socket = None

        logger.info("서버 종료됨")

    def _accept_clients(self):
        """
        클라이언트 연결 수락 루프.

        새 연결마다 _handle_client 스레드를 생성합니다.
        """
        while self.running:
            try:
                client_socket, address = self.server_socket.accept()
                client_socket.settimeout(30)

                # H6: handshake 미완료 연결이 스레드/fd 를 무한히 점유하는
                # slow-loris 방어 — 상한 초과 시 스레드를 만들지 않고 즉시 거부.
                with self._pending_lock:
                    if self._pending_count >= MAX_PENDING_CONNECTIONS:
                        logger.warning(
                            f"동시 연결 상한({MAX_PENDING_CONNECTIONS}) 초과 — "
                            f"연결 거부(slow-loris 방어): {address}"
                        )
                        try:
                            client_socket.close()
                        except OSError:
                            pass
                        continue
                    self._pending_count += 1

                logger.info(f"새 연결 시도: {address}")

                # 클라이언트 처리 스레드 시작
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_socket, address),
                    daemon=True,
                )
                client_thread.start()

            except Exception as e:
                if self.running:
                    logger.error(f"클라이언트 수락 오류: {e}")

    def _handle_client(self, sock: socket.socket, address):
        """
        개별 클라이언트 연결을 처리합니다.

        핸드셰이크 인증 → 메시지 수신 루프 → 연결 종료 정리.

        Args:
            sock: 클라이언트 소켓
            address: 클라이언트 주소 (host, port)
        """
        client_name = f"{address[0]}:{address[1]}"
        client_peer_id = ""  # v3.0: response 파싱 후 채워짐 (disconnect 콜백까지 유지)

        try:
            # ── v2.2 R3: 3-step mutual HMAC handshake (v3.0: + peer_id) ──
            # 1. Server → Client: CHALLENGE (server_nonce, server_version, server_peer_id)
            server_nonce = generate_nonce()
            challenge = self.protocol.create_handshake_challenge(server_nonce, self.peer_id)
            sock.sendall(challenge)

            # 2. Client → Server: RESPONSE (client_nonce, version, name, hmac)
            header = _recv_all(sock, Protocol.HEADER_SIZE)
            if len(header) < Protocol.HEADER_SIZE:
                raise ConnectionError("handshake response 헤더 수신 실패")
            msg_len = Protocol.read_header(header)
            data = _recv_all(sock, msg_len)
            if len(data) < msg_len:
                raise ConnectionError("handshake response 데이터 수신 실패")
            message = self.protocol.parse_message(data)
            if not message or message.get("type") != MSG_HANDSHAKE_RESPONSE:
                got = message.get("type") if message else "parse-fail"
                raise ConnectionError(
                    f"v2.2 expects MSG_HANDSHAKE_RESPONSE, got {got!r}. "
                    f"Peer is likely v2.1 or older - hard break, upgrade peer to v2.2"
                )

            payload = Protocol.parse_handshake_response(message.get("data"))
            if not payload:
                raise ConnectionError("invalid handshake response payload")

            # version 호환 (major.minor 같아야)
            if not Protocol.is_compatible_version(payload["client_version"]):
                raise ConnectionError(
                    f"protocol version mismatch: client={payload['client_version']!r}, "
                    f"server={PROTOCOL_VERSION!r} - hard break, upgrade required"
                )

            # HMAC 검증 (replay 방어 + key 보유 증명)
            client_ip = address[0]
            if not verify_handshake_hmac(
                self.auth_key, server_nonce, payload["client_nonce"], payload["hmac"]
            ):
                logger.warning(f"인증 실패 (HMAC mismatch): {address}")
                sock.close()
                return

            # M4: 여기 도달했다는 건 위 HMAC 검증을 이미 통과했다는 뜻 — 즉 이
            # 블록은 인증 여부에 영향을 주지 않는 진단용 로그일 뿐이다(과거엔
            # "Tailscale 이면 인증 완화" 로 오인되기 쉬운 이름/문서였음).
            if self.tailscale_trust:
                from core.tailscale import is_tailscale_ip
                if is_tailscale_ip(client_ip):
                    logger.info(f"Tailscale peer 인증 성공: {client_ip}")

            # 3. Server → Client: ACK (server HMAC for mutual auth)
            server_hmac = compute_handshake_hmac(
                self.auth_key, payload["client_nonce"], server_nonce
            )
            ack = self.protocol.create_handshake_ack(server_hmac)
            sock.sendall(ack)

            client_name = payload["name"]
            claimed_peer_id = payload["peer_id"]  # v3.0: 상대가 자기신고한 peer_id

            # H1: peer_id 유일성 검증. HMAC 은 auth_key 보유만 증명할 뿐 peer_id
            # 가 유일함을 보장하지 않는다 — 이미 다른 소켓이 같은 peer_id 로
            # 등록돼 있으면 identity squatting(다른 peer 사칭) 또는 targeted
            # relay(_socket_for_peer) 하이재킹 가능성이 있어 거부한다.
            # client_peer_id(finally 의 on_client_disconnected 에 쓰임) 는
            # 등록 성공 이후에만 채운다 — 거부된 시도의 peer_id 로 finally 가
            # main.py 의 self.peers.pop(peer_id) 를 잘못 호출해 진짜 피해자의
            # 등록을 지우는 것을 방지한다 (HMAC 실패 시의 기존 처리와 동일 패턴).
            with self.clients_lock:
                conflict = any(
                    s is not sock and info.get("peer_id") == claimed_peer_id
                    for s, info in self.clients.items()
                )
                if not conflict:
                    self.clients[sock] = {"name": client_name, "peer_id": claimed_peer_id}
                    self._write_locks[sock] = threading.Lock()

            if conflict:
                logger.warning(
                    f"핸드셰이크 거부 — peer_id {claimed_peer_id[:8]}… 이미 다른 "
                    f"연결에서 사용 중 (identity squatting 의심): {address}"
                )
                return

            client_peer_id = claimed_peer_id

            logger.info(
                f"클라이언트 연결됨 (HMAC v{PROTOCOL_VERSION}): "
                f"{client_name} peer={client_peer_id[:8]}… ({address})"
            )

            # on_client_connected 콜백 호출
            if self.on_client_connected:
                try:
                    self.on_client_connected(sock, address, client_name, client_peer_id)
                except Exception as e:
                    logger.error(f"on_client_connected 콜백 오류: {e}")

            # ── 메시지 수신 루프 ──
            while self.running:
                try:
                    header = _recv_all(sock, Protocol.HEADER_SIZE)
                    if len(header) < Protocol.HEADER_SIZE:
                        break

                    msg_len = Protocol.read_header(header)
                    data = _recv_all(sock, msg_len)
                    if len(data) < msg_len:
                        break

                    message = self.protocol.parse_message(data)
                    if message:
                        # 원본 와이어 바이트 첨부 (중계 시 재직렬화 방지)
                        message["_raw"] = header + data
                        # on_message_received 콜백 호출
                        if self.on_message_received:
                            try:
                                self.on_message_received(sock, message)
                            except Exception as e:
                                logger.error(f"on_message_received 콜백 오류: {e}")

                except socket.timeout:
                    # 타임아웃(30초) 시 PING 전송하여 연결 유지 확인
                    try:
                        wl = self._get_write_lock(sock)
                        ping = self.protocol.create_message(MSG_PING)
                        if wl:
                            with wl:
                                sock.sendall(ping)
                        else:
                            sock.sendall(ping)
                    except Exception:
                        break

        except Exception as e:
            # v2.1: 의식적 종료 (stop) 중에는 socket close → recv OSError 가 자연스러움
            if self.running:
                logger.error(f"클라이언트 처리 오류 ({client_name}): {e}")
            else:
                logger.debug(f"종료 중 client recv 정리: {client_name}")

        finally:
            # H6: 이 연결이 점유하던 pending 슬롯 반환 (성공/실패 무관 1회)
            with self._pending_lock:
                self._pending_count = max(0, self._pending_count - 1)

            # 클라이언트 제거 및 정리
            with self.clients_lock:
                if sock in self.clients:
                    del self.clients[sock]
                self._write_locks.pop(sock, None)

            try:
                sock.close()
            except OSError:
                pass

            logger.info(f"클라이언트 연결 종료: {client_name}")

            # on_client_disconnected 콜백 호출
            if self.on_client_disconnected:
                try:
                    self.on_client_disconnected(sock, address, client_name, client_peer_id)
                except Exception as e:
                    logger.error(f"on_client_disconnected 콜백 오류: {e}")

    def _get_write_lock(self, sock: socket.socket) -> Optional[threading.Lock]:
        """소켓별 write lock 을 clients_lock 보호 하에 조회 (M3 — dict 자체 조회를
        lock 밖에서 하면 등록/제거와 레이스). 조회만 락 안에서 하고, 반환된 Lock
        으로 실제 sendall 을 감싸는 건 호출부가 락 밖에서 수행 — 느린 클라이언트가
        clients_lock 을 붙잡는 것은 방지."""
        with self.clients_lock:
            return self._write_locks.get(sock)

    def broadcast(self, msg_type: str, data: Any = None, exclude_sock: socket.socket = None):
        """
        모든 연결된 클라이언트에게 메시지를 전송합니다.

        lock 안에서는 클라이언트 목록만 복사하고, lock 밖에서 전송합니다.
        느린 클라이언트의 sendall 블로킹이 다른 클라이언트를 막지 않습니다.

        Args:
            msg_type: 메시지 타입 (MSG_* 상수)
            data: 전송할 데이터
            exclude_sock: 전송에서 제외할 소켓 (발신자 제외용)
        """
        message_bytes = self.protocol.create_message(msg_type, data)

        # lock 안에서 목록만 복사
        with self.clients_lock:
            targets = [
                s for s in self.clients.keys()
                if s != exclude_sock
            ]

        # lock 밖에서 전송 — 느린 클라이언트가 다른 전송을 블로킹하지 않음
        failed = []
        for client_socket in targets:
            try:
                wl = self._get_write_lock(client_socket)
                if wl:
                    with wl:
                        client_socket.sendall(message_bytes)
                else:
                    client_socket.sendall(message_bytes)
            except Exception as e:
                # L3: stop() 이 소켓을 닫는 중이면 이 실패는 정상 종료의
                # 자연스러운 결과 — self.running 이 False 인 그 경우만 debug 로.
                if self.running:
                    logger.error(f"브로드캐스트 전송 오류: {e}")
                else:
                    logger.debug(f"종료 중 브로드캐스트 정리: {e}")
                failed.append(client_socket)

        # 전송 실패한 소켓 정리
        if failed:
            with self.clients_lock:
                for s in failed:
                    self.clients.pop(s, None)
                    try:
                        s.close()
                    except Exception:
                        pass

    def broadcast_raw(self, raw_message: bytes, exclude_sock: socket.socket = None):
        """이미 직렬화된 메시지 바이트를 모든 클라이언트에게 전송합니다."""
        with self.clients_lock:
            targets = [
                s for s in self.clients.keys()
                if s != exclude_sock
            ]

        failed = []
        for client_socket in targets:
            try:
                wl = self._get_write_lock(client_socket)
                if wl:
                    with wl:
                        client_socket.sendall(raw_message)
                else:
                    client_socket.sendall(raw_message)
            except Exception as e:
                if self.running:
                    logger.error(f"브로드캐스트(raw) 전송 오류: {e}")
                else:
                    logger.debug(f"종료 중 브로드캐스트(raw) 정리: {e}")
                failed.append(client_socket)

        if failed:
            with self.clients_lock:
                for s in failed:
                    self.clients.pop(s, None)
                    try:
                        s.close()
                    except Exception:
                        pass

    def send_to(self, sock: socket.socket, msg_type: str, data: Any = None):
        """
        특정 클라이언트에게 메시지를 전송합니다.

        Args:
            sock: 대상 클라이언트 소켓
            msg_type: 메시지 타입 (MSG_* 상수)
            data: 전송할 데이터
        """
        try:
            message_bytes = self.protocol.create_message(msg_type, data)
            wl = self._get_write_lock(sock)
            if wl:
                with wl:
                    sock.sendall(message_bytes)
            else:
                sock.sendall(message_bytes)
        except Exception as e:
            if self.running:
                logger.error(f"메시지 전송 오류: {e}")
            else:
                logger.debug(f"종료 중 메시지 전송 정리: {e}")

    # ── v3.0: targeted relay (peer_id → 특정 소켓) ─────────────────────
    # 별-토폴로지에서 서버가 receiver_peer 가진 메시지를 broadcast 대신 해당
    # peer 에게만 중계. lazy fetch 의 핵심 — "B 가 paste 하면 B 에게만 chunk".

    def _socket_for_peer(self, peer_id: str) -> Optional[socket.socket]:
        """peer_id 로 등록된 소켓을 역조회. lock 보호. 없으면 None."""
        if not peer_id:
            return None
        with self.clients_lock:
            for sock, info in self.clients.items():
                if info.get("peer_id") == peer_id:
                    return sock
        return None

    def _drop_socket(self, sock: socket.socket) -> None:
        """전송 실패한 소켓을 정리 (broadcast 의 failed 처리와 동일)."""
        with self.clients_lock:
            self.clients.pop(sock, None)
            self._write_locks.pop(sock, None)
        try:
            sock.close()
        except Exception:
            pass

    def send_to_peer(self, peer_id: str, msg_type: str, data: Any = None) -> bool:
        """특정 peer 에게 JSON 메시지를 전송. 대상 없으면 warning + drop (graceful).

        Returns:
            bool: 전송 시도 성공 여부 (대상 없음 / 전송 실패 시 False)
        """
        sock = self._socket_for_peer(peer_id)
        if sock is None:
            logger.warning(f"send_to_peer: peer {peer_id[:8]}… 없음 — drop")
            return False
        try:
            message_bytes = self.protocol.create_message(msg_type, data)
            wl = self._get_write_lock(sock)
            if wl:
                with wl:
                    sock.sendall(message_bytes)
            else:
                sock.sendall(message_bytes)
            return True
        except Exception as e:
            if self.running:
                logger.error(f"send_to_peer 전송 오류 ({peer_id[:8]}…): {e}")
            else:
                logger.debug(f"종료 중 send_to_peer 정리 ({peer_id[:8]}…): {e}")
            self._drop_socket(sock)
            return False

    def send_raw_to_peer(self, peer_id: str, raw_message: bytes) -> bool:
        """특정 peer 에게 이미 직렬화된 바이트(바이너리 chunk 등)를 전송.

        대상 없으면 warning + drop. 바이너리 chunk 의 targeted relay 경로.

        Returns:
            bool: 전송 시도 성공 여부.
        """
        sock = self._socket_for_peer(peer_id)
        if sock is None:
            logger.warning(f"send_raw_to_peer: peer {peer_id[:8]}… 없음 — drop")
            return False
        try:
            wl = self._get_write_lock(sock)
            if wl:
                with wl:
                    sock.sendall(raw_message)
            else:
                sock.sendall(raw_message)
            return True
        except Exception as e:
            if self.running:
                logger.error(f"send_raw_to_peer 전송 오류 ({peer_id[:8]}…): {e}")
            else:
                logger.debug(f"종료 중 send_raw_to_peer 정리 ({peer_id[:8]}…): {e}")
            self._drop_socket(sock)
            return False


# ── 클라이언트 ──────────────────────────────────────────────────────────

class NetworkClient:
    """
    TCP 네트워크 클라이언트

    서버에 연결하고, 핸드셰이크 인증 후 메시지를 수신/전송합니다.
    연결이 끊어지면 자동으로 재연결을 시도합니다.
    """

    def __init__(self, host: str, port: int, auth_key: str, device_name: str,
                 peer_id: str = ""):
        """
        클라이언트 인스턴스를 초기화합니다.

        Args:
            host: 서버 호스트 주소
            port: 서버 포트 번호
            auth_key: 서버 인증에 사용할 공유 키
            device_name: 이 디바이스의 식별 이름
            peer_id: v3.0 이 클라이언트의 안정적 식별자 (response 로 서버에 알림).
                보통 config.peer_id 가 전달된다. 비거나 형식 위반이면 자동 생성.
        """
        self.host = host
        self.port = port
        self.auth_key = auth_key
        self.device_name = device_name
        self.peer_id = peer_id if is_valid_peer_id(peer_id) else generate_peer_id()
        # v3.0: 핸드셰이크 challenge 에서 학습한 서버 peer_id (Task 3 라우팅이 사용).
        self.server_peer_id = ""
        self.protocol = Protocol(auth_key)

        self.socket: Optional[socket.socket] = None
        self.connected = False
        self.running = False
        self.reconnect_interval = 5  # 재연결 대기 시간 (초)
        self._conn_lock = threading.Lock()  # connected/socket 접근 보호
        self._write_lock = threading.Lock()  # sendall 직렬화
        self._ever_connected = False  # 한 번이라도 연결 성공한 적 있는지
        # 리뷰 발견: on_disconnected 콜백은 원래 was_connected(=_ever_connected)
        # 가 True 일 때만 불렸는데, 이러면 "한 번도 연결된 적 없는" 첫 페어링
        # 시도에서 버전 불일치(hard break)가 나도 M9 의 reason 통지가 영영 안
        # 불린다 — 신규 페어링/한쪽만 업그레이드가 가장 흔한 실사용 트리거인데
        # 그 경우를 놓침. hard break 사유는 재시도해도 스스로 해소 안 되는
        # 정적인 문제라 was_connected 게이트를 우회하되, 5초마다 재시도하며
        # 매번 알리면 스팸이 되므로 동일 사유는 1회만 통지한다.
        self._last_notified_disconnect_reason: Optional[str] = None

        # 콜백 속성 (상위 계층에서 설정)
        self.on_connected: Optional[Callable] = None           # ()
        # M9: reason(str, 기본 "") — handshake 실패(버전 불일치 등)처럼 원인이
        # 있으면 채워서 전달. main.py 가 하드브레이크/버전 불일치를 사용자에게
        # 구분해 알릴 수 있게 함 (과거엔 상세 사유가 로그에만 남고 UI 는
        # "연결 끊김"이라는 일반 문구뿐이었음).
        self.on_disconnected: Optional[Callable] = None        # (reason: str = "")
        self.on_message_received: Optional[Callable] = None    # (message)

    def start(self):
        """
        클라이언트를 시작합니다.

        연결 루프 스레드를 시작합니다.
        """
        self.running = True

        # 연결 루프 스레드
        connection_thread = threading.Thread(target=self._connection_loop, daemon=True)
        connection_thread.start()

        logger.info(f"클라이언트 시작: {self.host}:{self.port} (디바이스: {self.device_name})")

    def stop(self):
        """
        클라이언트를 중지합니다.

        v2.1: shutdown(SHUT_RDWR) 후 close 로 _receive 의 recv 가
        OSError 대신 0byte EOF 로 반환되도록 한다.
        """
        self.running = False
        with self._conn_lock:
            self.connected = False
            if self.socket:
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    self.socket.close()
                except OSError:
                    pass
                self.socket = None

        logger.info("클라이언트 종료됨")

    def _connection_loop(self):
        """
        연결 유지 루프.

        연결되지 않은 상태면 _connect()를 시도하고,
        연결된 상태면 _receive()로 메시지를 수신합니다.
        실패 시 reconnect_interval만큼 대기 후 재시도합니다.
        """
        while self.running:
            if not self.connected:
                self._connect()
            else:
                self._receive()

            # 연결이 끊어진 상태에서 재연결 대기
            if not self.connected and self.running:
                time.sleep(self.reconnect_interval)

    def _connect(self):
        """
        서버에 연결하고 v2.2 R3 mutual HMAC handshake 를 수행합니다.

        흐름:
          1. CHALLENGE 수신 (server-first)
          2. version 호환 확인
          3. RESPONSE 송신 (client_nonce + HMAC)
          4. ACK 수신 + server HMAC 검증

        성공 시 connected=True, 실패 시 소켓 정리.
        """
        try:
            logger.info(f"서버 연결 시도: {self.host}:{self.port}")

            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(10)
            self.socket.connect((self.host, self.port))

            # 1. CHALLENGE 수신 (server-first)
            header = _recv_all(self.socket, Protocol.HEADER_SIZE)
            if len(header) < Protocol.HEADER_SIZE:
                raise ConnectionError("challenge 헤더 수신 실패")
            msg_len = Protocol.read_header(header)
            data = _recv_all(self.socket, msg_len)
            if len(data) < msg_len:
                raise ConnectionError("challenge 데이터 수신 실패")
            message = self.protocol.parse_message(data)
            if not message or message.get("type") != MSG_HANDSHAKE_CHALLENGE:
                got = message.get("type") if message else "parse-fail"
                raise ConnectionError(
                    f"v2.2 expects MSG_HANDSHAKE_CHALLENGE first, got {got!r}. "
                    f"Server is likely v2.1 or older - hard break, upgrade server to v2.2"
                )

            challenge = Protocol.parse_handshake_challenge(message.get("data"))
            if not challenge:
                raise ConnectionError("invalid challenge payload")

            # 2. version 호환 확인
            if not Protocol.is_compatible_version(challenge["server_version"]):
                raise ConnectionError(
                    f"server version {challenge['server_version']!r} "
                    f"!= client {PROTOCOL_VERSION!r} - hard break, upgrade required"
                )

            # v3.0: 서버 peer_id 학습 (Task 3 라우팅에서 서버를 타깃할 때 사용)
            self.server_peer_id = challenge["server_peer_id"]

            # 3. RESPONSE 송신 (client_nonce + HMAC + peer_id)
            client_nonce = generate_nonce()
            client_hmac = compute_handshake_hmac(
                self.auth_key, challenge["server_nonce"], client_nonce
            )
            response = self.protocol.create_handshake_response(
                client_nonce, self.device_name, client_hmac, self.peer_id,
            )
            self.socket.sendall(response)

            # 4. ACK 수신 + server HMAC 검증
            header = _recv_all(self.socket, Protocol.HEADER_SIZE)
            if len(header) < Protocol.HEADER_SIZE:
                raise ConnectionError("ACK 헤더 수신 실패")
            msg_len = Protocol.read_header(header)
            data = _recv_all(self.socket, msg_len)
            if len(data) < msg_len:
                raise ConnectionError("ACK 데이터 수신 실패")
            message = self.protocol.parse_message(data)
            if not message or message.get("type") != MSG_HANDSHAKE_ACK:
                raise ConnectionError("invalid handshake ACK")
            ack = Protocol.parse_handshake_ack(message.get("data"))
            if not ack:
                raise ConnectionError("invalid ACK payload")

            # server HMAC = HMAC(key, client_nonce || server_nonce). 양방향 키 보유 증명
            if not verify_handshake_hmac(
                self.auth_key, client_nonce, challenge["server_nonce"], ack["hmac"],
            ):
                raise ConnectionError("server HMAC verification failed - wrong key on server")

            # 연결 성공
            with self._conn_lock:
                self.connected = True
                self._ever_connected = True
                self.socket.settimeout(30)  # 수신 타임아웃 설정
            # 성공했으니 이전에 억눌렀던 disconnect 사유를 초기화 — 나중에
            # 다시 같은(또는 다른) hard break 가 나면 다시 1회 통지해야 하므로.
            self._last_notified_disconnect_reason = None

            logger.info(
                f"서버 연결 성공 (HMAC v{PROTOCOL_VERSION}): {self.host}:{self.port} "
                f"server_peer={self.server_peer_id[:8]}… my_peer={self.peer_id[:8]}…"
            )

            # on_connected 콜백 호출
            if self.on_connected:
                try:
                    self.on_connected()
                except Exception as e:
                    logger.error(f"on_connected 콜백 오류: {e}")

        except Exception as e:
            # v2.1: 의식적 종료 중 connect 시도가 close 로 끊긴 경우 silent
            if self.running:
                logger.error(f"서버 연결 실패: {e}")
            else:
                logger.debug(f"종료 중 connect 정리: {e}")
            with self._conn_lock:
                was_connected = self._ever_connected
                self.connected = False
                if self.socket:
                    try:
                        self.socket.close()
                    except OSError:
                        pass
                    self.socket = None

            # 한 번이라도 연결된 적 있을 때 + 의식적 종료가 아닐 때만 콜백 —
            # 단, 리뷰 발견: hard break(버전 불일치)는 재시도해도 스스로 안
            # 풀리는 정적인 문제라 was_connected 게이트를 우회한다(가장 흔한
            # 실사용 트리거인 "한 번도 연결된 적 없는 첫 페어링"을 놓치지
            # 않기 위함). 대신 5초 재시도 루프가 매번 알리는 스팸을 막기 위해
            # 동일 사유는 1회만 통지.
            reason = str(e)
            is_hard_break = "hard break" in reason or "version mismatch" in reason
            should_notify = was_connected or (
                is_hard_break and reason != self._last_notified_disconnect_reason
            )
            if should_notify and self.running and self.on_disconnected:
                if is_hard_break:
                    self._last_notified_disconnect_reason = reason
                try:
                    self.on_disconnected(reason=reason)
                except Exception as cb_err:
                    logger.error(f"on_disconnected 콜백 오류: {cb_err}")

    def _receive(self):
        """
        서버로부터 메시지를 수신합니다.

        헤더 수신 → _recv_all로 페이로드 수신 → parse_message → 콜백 호출.
        타임아웃 시 PING 전송, 연결 끊김 시 상태 초기화.
        """
        try:
            header = _recv_all(self.socket, Protocol.HEADER_SIZE)
            if len(header) < Protocol.HEADER_SIZE:
                raise ConnectionError("연결 끊김")

            msg_len = Protocol.read_header(header)
            data = _recv_all(self.socket, msg_len)
            if len(data) < msg_len:
                raise ConnectionError("데이터 수신 실패")

            message = self.protocol.parse_message(data)
            if message:
                # on_message_received 콜백 호출
                if self.on_message_received:
                    try:
                        self.on_message_received(message)
                    except Exception as e:
                        logger.error(f"on_message_received 콜백 오류: {e}")

        except socket.timeout:
            # 타임아웃(30초) 시 PING 전송하여 연결 유지 확인
            try:
                with self._conn_lock:
                    if self.socket:
                        ping = self.protocol.create_message(MSG_PING)
                        with self._write_lock:
                            self.socket.sendall(ping)
            except Exception:
                with self._conn_lock:
                    self.connected = False

        except Exception as e:
            # v2.1: 의식적 종료 중에는 socket close → recv OSError 가 정상
            if self.running:
                logger.error(f"수신 오류: {e}")
            else:
                logger.debug(f"종료 중 수신 정리: {e}")
            with self._conn_lock:
                self.connected = False

            # on_disconnected 콜백은 의식적 종료가 아닐 때만 호출 (UI 깜빡임 방지)
            if self.running and self.on_disconnected:
                try:
                    self.on_disconnected()
                except Exception as cb_err:
                    logger.error(f"on_disconnected 콜백 오류: {cb_err}")

    def send(self, msg_type: str, data: Any = None):
        """
        서버에 메시지를 전송합니다.

        Args:
            msg_type: 메시지 타입 (MSG_* 상수)
            data: 전송할 데이터
        """
        with self._conn_lock:
            if not self.connected or not self.socket:
                logger.warning("서버에 연결되지 않은 상태에서 전송 시도")
                return
            sock = self.socket

        try:
            message_bytes = self.protocol.create_message(msg_type, data)
            with self._write_lock:
                sock.sendall(message_bytes)
        except Exception as e:
            if self.running:
                logger.error(f"메시지 전송 오류: {e}")
            else:
                logger.debug(f"종료 중 메시지 전송 정리: {e}")

    def send_raw(self, raw_message: bytes):
        """이미 직렬화된 메시지 바이트를 서버에 전송합니다."""
        with self._conn_lock:
            if not self.connected or not self.socket:
                return
            sock = self.socket

        try:
            with self._write_lock:
                sock.sendall(raw_message)
        except Exception as e:
            if self.running:
                logger.error(f"메시지(raw) 전송 오류: {e}")
            else:
                logger.debug(f"종료 중 메시지(raw) 전송 정리: {e}")
            with self._conn_lock:
                self.connected = False
