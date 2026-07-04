"""
네트워크 프로토콜 정의

4바이트 빅엔디안 길이 헤더 + JSON 평문 페이로드 구조.
XOR 암호화 제거, SHA-256 키 해시 기반 핸드셰이크 인증 사용.
"""

import hashlib
import hmac
import json
import re
import secrets
import struct
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# ── Protocol version (hard break) ──────────────────────────────────────
# Major.minor 만 비교 — patch 차이는 호환.
# v2.2: nonce HMAC handshake. v3.0: handshake 에 stable peer_id 추가
# (라우팅 전제) — wire 비호환이라 v2.x peer 는 version mismatch 로 거부.
PROTOCOL_VERSION = "3.0"


# ── 메시지 타입 상수 ────────────────────────────────────────────────────

MSG_HANDSHAKE = "handshake"          # v2.0/v2.1 deprecated — v2.2 server 가 거부
MSG_ACK = "ack"                      # 수신 확인 / 핸드셰이크 승인

# v2.2 R3 — 3-step mutual HMAC handshake
MSG_HANDSHAKE_CHALLENGE = "handshake_challenge"   # server → client
MSG_HANDSHAKE_RESPONSE = "handshake_response"     # client → server
MSG_HANDSHAKE_ACK = "handshake_ack"               # server → client (mutual final)
MSG_PING = "ping"                    # 연결 유지 확인 요청
MSG_PONG = "pong"                    # 연결 유지 확인 응답

MSG_CLIPBOARD = "clipboard"          # 클립보드 데이터 전송

MSG_FILE_READY = "file_ready"        # 파일 준비됨 (메타데이터만, 전송 대기)
MSG_FILE_REQUEST = "file_request"    # 파일 전송 요청 (붙여넣기 시)
MSG_FILE_START = "file_start"        # 파일 전송 시작 (메타데이터)
MSG_FILE_CHUNK = "file_chunk"        # 파일 청크 데이터
MSG_FILE_PROGRESS = "file_progress"  # 파일 전송 진행률
MSG_FILE_END = "file_end"            # 파일 전송 완료
MSG_FILE_ACK = "file_ack"            # 파일/청크 수신 확인
MSG_FILE_RESUME = "file_resume"      # 이어받기 요청
MSG_FILE_CANCEL = "file_cancel"      # 파일 전송 취소
MSG_FILE_CANCEL_ACK = "file_cancel_ack"  # v2.3.1: 취소 ack (peer cleanup 확인)
MSG_FILE_ERROR = "file_error"        # 파일 전송 오류
MSG_TRANSFER_COMPLETE = "transfer_complete"  # 전체 전송 완료

# v3.0 — lazy clipboard offer/fetch (targeted relay 사용)
MSG_CLIP_OFFER = "clip_offer"            # copy 시점 경로 알림 (broadcast)
MSG_CLIP_FETCH = "clip_fetch"            # paste 시점 fetch 요청 (source 로 routed)
MSG_CLIP_FETCH_FAIL = "clip_fetch_fail"  # fetch 실패 응답 (requester 로 routed)

# ── 파일 전송 설정 ───────────────────────────────────────────────────────

FILE_CHUNK_SIZE = 1024 * 1024                # 1MB 청크
FILE_MAX_SIZE = 10 * 1024 * 1024 * 1024      # 최대 10GB

# ── Cancel reason 상수 ──────────────────────────────────────────────────
# file_transfer manager 와 동기화. 새 reason 추가 시 _CANCEL_REASONS_VALID 도 갱신.

CANCEL_REASON_SUPERSEDED = "superseded"   # 새 transfer 시작으로 자동 취소
CANCEL_REASON_USER = "user"               # UI 취소 버튼
CANCEL_REASON_ERROR = "error"             # 전송 실패

_CANCEL_REASONS_VALID = frozenset({
    CANCEL_REASON_SUPERSEDED,
    CANCEL_REASON_USER,
    CANCEL_REASON_ERROR,
})

# ── v2.3.1: Cancel ACK 상수 ─────────────────────────────────────────────
# peer 가 MSG_FILE_CANCEL 을 받고 cleanup 한 뒤 ack 송출. originator UI 가
# 실제 처리 여부를 알 수 있어 "취소 중..." 상태 즉시 정리 가능.

CANCEL_ACK_ROLE_SENDER = "sender"      # ack 보낸 peer 가 송신 측이었음
CANCEL_ACK_ROLE_RECEIVER = "receiver"  # ack 보낸 peer 가 수신 측이었음
CANCEL_ACK_ROLE_NONE = "none"          # 매칭되는 active transfer 없음 (idempotent)

_CANCEL_ACK_ROLES_VALID = frozenset({
    CANCEL_ACK_ROLE_SENDER,
    CANCEL_ACK_ROLE_RECEIVER,
    CANCEL_ACK_ROLE_NONE,
})

CANCEL_ACK_STATUS_OK = "ok"
CANCEL_ACK_STATUS_UNKNOWN = "unknown"  # transfer_id 미매칭 (이미 정리됨 등)

_CANCEL_ACK_STATUS_VALID = frozenset({
    CANCEL_ACK_STATUS_OK,
    CANCEL_ACK_STATUS_UNKNOWN,
})

# ── v3.0: lazy clipboard offer/fetch 상수 ───────────────────────────────
# offer 종류 — 텍스트는 즉시 전송이라 offer 대상 아님 (file/image 만 lazy).
CLIP_OFFER_KIND_FILE = "file"
CLIP_OFFER_KIND_IMAGE = "image"
_CLIP_OFFER_KINDS = frozenset({CLIP_OFFER_KIND_FILE, CLIP_OFFER_KIND_IMAGE})

# fetch 실패 사유 화이트리스트. 새 사유 추가 시 함께 갱신.
FETCH_FAIL_SUPERSEDED = "superseded"   # 새 복사로 offer 폐기됨
FETCH_FAIL_EXPIRED = "expired"         # TTL 초과
FETCH_FAIL_MISSING = "missing"         # 원본 파일/경로 사라짐
FETCH_FAIL_OFFLINE = "offline"         # source peer 연결 끊김
FETCH_FAIL_ERROR = "error"             # 기타 오류
_FETCH_FAIL_REASONS = frozenset({
    FETCH_FAIL_SUPERSEDED, FETCH_FAIL_EXPIRED, FETCH_FAIL_MISSING,
    FETCH_FAIL_OFFLINE, FETCH_FAIL_ERROR,
})

# ── transfer_id 형식 (UUID v4) 검증 ─────────────────────────────────────
# 수신측이 path 컴포넌트로 직접 사용하므로 (`get_temp_dir`, `CheckpointManager`)
# 형식 위반 시 path traversal 위험이 있어 wire 단계에서 조기 거부한다.
# Task 2.5 에서 file_transfer.py 에도 동일 패턴이 추가되며, 두 쪽 모두 같은 정규식을 유지.
_TRANSFER_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def is_valid_transfer_id(value: object) -> bool:
    """transfer_id 가 UUID v4 형식의 소문자 16진수 문자열인지 검증."""
    return isinstance(value, str) and bool(_TRANSFER_ID_RE.match(value))


# ── v2.2 R3: nonce + HMAC handshake 헬퍼 ────────────────────────────────
# Replay 방어용 32-byte (256-bit) random nonce. hex 인코딩 64 자.

NONCE_BYTES = 32
NONCE_HEX_LEN = NONCE_BYTES * 2

_NONCE_RE = re.compile(rf"^[0-9a-f]{{{NONCE_HEX_LEN}}}$")


def generate_nonce() -> str:
    """Cryptographically random 32-byte nonce in lowercase hex (64 chars)."""
    return secrets.token_hex(NONCE_BYTES)


def is_valid_nonce(value: object) -> bool:
    """Nonce 가 64-char lowercase hex 인지 검증."""
    return isinstance(value, str) and bool(_NONCE_RE.match(value))


# ── v3.0: stable peer_id ────────────────────────────────────────────────
# 라우팅(targeted relay)의 전제. config 가 영속 생성(settings.json), 핸드셰이크로
# 양쪽이 서로의 id 학습. nonce 와 달리 connection 마다 새로 만들지 않는다 —
# 같은 PC 는 재연결해도 같은 peer_id. 16-byte random → 32-char lowercase hex.

PEER_ID_BYTES = 16
PEER_ID_HEX_LEN = PEER_ID_BYTES * 2  # 32

_PEER_ID_RE = re.compile(rf"^[0-9a-f]{{{PEER_ID_HEX_LEN}}}$")


def generate_peer_id() -> str:
    """안정적 peer 식별자 — 16-byte random in lowercase hex (32 chars).

    path-safe (hex 만 사용) 하므로 임시 디렉토리/파일명에 그대로 써도 안전.
    """
    return secrets.token_hex(PEER_ID_BYTES)


def is_valid_peer_id(value: object) -> bool:
    """peer_id 가 32-char lowercase hex 인지 검증."""
    return isinstance(value, str) and bool(_PEER_ID_RE.match(value))


def compute_handshake_hmac(key: str, nonce_a: str, nonce_b: str) -> str:
    """HMAC-SHA256(key, nonce_a || nonce_b) 16진수 문자열.

    nonce 순서가 의미를 가짐 — server→client 방향과 client→server 방향이
    다른 hmac 을 만들어내야 mutual auth 가 성립.

    Raises:
        ValueError: nonce 형식 위반
    """
    if not is_valid_nonce(nonce_a) or not is_valid_nonce(nonce_b):
        raise ValueError("compute_handshake_hmac: invalid nonce format")
    if not isinstance(key, str) or not key:
        raise ValueError("compute_handshake_hmac: empty key")
    msg = (nonce_a + nonce_b).encode("utf-8")
    return hmac.new(key.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_handshake_hmac(
    key: str, nonce_a: str, nonce_b: str, received: object,
) -> bool:
    """timing-safe HMAC 비교."""
    if not isinstance(received, str) or len(received) != 64:
        return False
    try:
        expected = compute_handshake_hmac(key, nonce_a, nonce_b)
    except ValueError:
        return False
    return hmac.compare_digest(expected, received)


class Protocol:
    """
    네트워크 통신 프로토콜

    메시지 형식:
      - JSON: [4바이트 길이 헤더] + [UTF-8 JSON 페이로드]  (첫 바이트가 0x7B '{')
      - 바이너리: [4바이트 길이 헤더] + [0x00] + [4바이트 메타길이] + [메타 JSON] + [원시 바이너리]
    인증 방식: 핸드셰이크 시 SHA-256(키) 해시 교환으로 키 매칭 검증
    """

    HEADER_SIZE = 4  # 메시지 길이 헤더 크기 (바이트)
    BINARY_MARKER = b'\x00'  # 바이너리 프레임 구분자 (JSON은 항상 '{' = 0x7B로 시작)

    def __init__(self, encryption_key: str = ""):
        """
        프로토콜 인스턴스를 초기화합니다.

        Args:
            encryption_key: 인증에 사용할 공유 키 (SHA-256 해시로 변환하여 비교)
        """
        self.key = encryption_key

    def create_message(self, msg_type: str, data: Any = None) -> bytes:
        """
        전송할 메시지를 생성합니다.

        4바이트 빅엔디안 길이 헤더 + UTF-8 인코딩된 JSON 페이로드.

        Args:
            msg_type: 메시지 타입 (MSG_* 상수)
            data: 전송할 데이터 (JSON 직렬화 가능한 객체)

        Returns:
            bytes: [4바이트 헤더] + [JSON 페이로드]
        """
        message = {"type": msg_type, "data": data}

        # JSON 직렬화 (유니코드 유지)
        json_data = json.dumps(message, ensure_ascii=False)
        encoded = json_data.encode("utf-8")

        # 4바이트 빅엔디안 길이 헤더 추가
        header = struct.pack(">I", len(encoded))

        return header + encoded

    def create_file_cancel(
        self,
        transfer_id: str,
        reason: str = CANCEL_REASON_SUPERSEDED,
    ) -> bytes:
        """
        파일 전송 취소 메시지를 생성한다.

        v2.1 active transfer queue 가 새 transfer 를 받으면 기존 진행 중인
        transfer 를 즉시 종료하기 위해 송출. UI 취소 버튼·에러 정리에도 사용.

        Args:
            transfer_id: 취소할 전송의 ID (UUID v4 형식 — 형식 검증함)
            reason: CANCEL_REASON_* 중 하나 (화이트리스트 외 거부)

        Returns:
            bytes: 헤더 포함 완성된 와이어 메시지

        Raises:
            ValueError: transfer_id 형식 또는 reason 화이트리스트 위반 시
        """
        if not is_valid_transfer_id(transfer_id):
            raise ValueError(f"Invalid transfer_id for cancel: {transfer_id!r}")
        if reason not in _CANCEL_REASONS_VALID:
            raise ValueError(f"Invalid cancel reason: {reason!r}")
        return self.create_message(
            MSG_FILE_CANCEL,
            {"transfer_id": transfer_id, "reason": reason},
        )

    @staticmethod
    def parse_file_cancel(data: object) -> Optional[Dict[str, str]]:
        """
        수신된 cancel 페이로드의 transfer_id/reason 형식을 검증한다.

        Args:
            data: parse_message 결과의 ["data"] 부분 (또는 임의 값)

        Returns:
            검증 통과 시 {"transfer_id": str, "reason": str}, 실패 시 None.
            None 반환은 호출자가 silent ignore 하도록 의도된 신호.
        """
        if not isinstance(data, dict):
            return None
        transfer_id = data.get("transfer_id")
        reason = data.get("reason")
        if not is_valid_transfer_id(transfer_id):
            return None
        if reason not in _CANCEL_REASONS_VALID:
            return None
        return {"transfer_id": transfer_id, "reason": reason}

    def create_file_cancel_ack(
        self,
        transfer_id: str,
        role: str,
        status: str = CANCEL_ACK_STATUS_OK,
    ) -> bytes:
        """v2.3.1: cancel 메시지를 받고 cleanup 한 peer 가 originator 에게 ack.

        UI 가 "취소 중..." 상태를 정확한 시점에 정리할 수 있게 한다. v2.3 이하 peer
        는 이 메시지 핸들러가 없어 silent ignore — soft compat (호환성 영향 없음).

        Args:
            transfer_id: 취소된 transfer ID (UUID v4 형식 검증).
            role: ack 보낸 peer 의 active transfer 역할
                (CANCEL_ACK_ROLE_SENDER / RECEIVER / NONE).
            status: cleanup 결과 — ok 또는 unknown (matching transfer 없음).

        Raises:
            ValueError: 형식/화이트리스트 위반.
        """
        if not is_valid_transfer_id(transfer_id):
            raise ValueError(f"Invalid transfer_id for cancel_ack: {transfer_id!r}")
        if role not in _CANCEL_ACK_ROLES_VALID:
            raise ValueError(f"Invalid cancel_ack role: {role!r}")
        if status not in _CANCEL_ACK_STATUS_VALID:
            raise ValueError(f"Invalid cancel_ack status: {status!r}")
        return self.create_message(
            MSG_FILE_CANCEL_ACK,
            {"transfer_id": transfer_id, "role": role, "status": status},
        )

    @staticmethod
    def parse_file_cancel_ack(data: object) -> Optional[Dict[str, str]]:
        """수신된 cancel_ack 페이로드 검증 — silent ignore 시 None 반환."""
        if not isinstance(data, dict):
            return None
        transfer_id = data.get("transfer_id")
        role = data.get("role")
        status = data.get("status")
        if not is_valid_transfer_id(transfer_id):
            return None
        if role not in _CANCEL_ACK_ROLES_VALID:
            return None
        if status not in _CANCEL_ACK_STATUS_VALID:
            return None
        return {"transfer_id": transfer_id, "role": role, "status": status}

    # ── v2.2 R3: 3-step mutual HMAC handshake ──────────────────────────
    #
    # Wire 흐름:
    #   1. Server → Client:  MSG_HANDSHAKE_CHALLENGE  {server_nonce, server_version}
    #   2. Client → Server:  MSG_HANDSHAKE_RESPONSE   {client_nonce, client_version,
    #                                                  name, hmac=H(key, sn||cn)}
    #   3. Server → Client:  MSG_HANDSHAKE_ACK        {hmac=H(key, cn||sn)}
    #
    # Replay 방어: 매 connection 마다 새 nonce. 캡처된 hmac 재사용 불가.
    # Mutual auth: 양쪽 모두 같은 key 보유 증명.
    # Timing-safe: hmac.compare_digest 로 비교.

    def create_handshake_challenge(self, server_nonce: str, server_peer_id: str) -> bytes:
        if not is_valid_nonce(server_nonce):
            raise ValueError(f"invalid server_nonce: {server_nonce!r}")
        if not is_valid_peer_id(server_peer_id):
            raise ValueError(f"invalid server_peer_id: {server_peer_id!r}")
        return self.create_message(MSG_HANDSHAKE_CHALLENGE, {
            "server_nonce": server_nonce,
            "server_version": PROTOCOL_VERSION,
            "server_peer_id": server_peer_id,
        })

    @staticmethod
    def parse_handshake_challenge(data: object) -> Optional[Dict[str, str]]:
        """검증 통과 시 {server_nonce, server_version, server_peer_id} 반환, 실패 시 None.

        v3.0: server_peer_id 누락/형식 위반 시 None (v2.x server hard break 거부).
        """
        if not isinstance(data, dict):
            return None
        sn = data.get("server_nonce")
        sv = data.get("server_version")
        spid = data.get("server_peer_id")
        if not is_valid_nonce(sn):
            return None
        if not isinstance(sv, str) or not sv or len(sv) > 32:
            return None
        if not is_valid_peer_id(spid):
            return None
        return {"server_nonce": sn, "server_version": sv, "server_peer_id": spid}

    def create_handshake_response(
        self, client_nonce: str, name: str, hmac_value: str, peer_id: str,
    ) -> bytes:
        if not is_valid_nonce(client_nonce):
            raise ValueError(f"invalid client_nonce: {client_nonce!r}")
        if not isinstance(name, str) or len(name) > 256:
            raise ValueError(f"invalid name: {name!r}")
        if not isinstance(hmac_value, str) or len(hmac_value) != 64:
            raise ValueError(f"invalid hmac: {hmac_value!r}")
        if not is_valid_peer_id(peer_id):
            raise ValueError(f"invalid peer_id: {peer_id!r}")
        return self.create_message(MSG_HANDSHAKE_RESPONSE, {
            "client_nonce": client_nonce,
            "client_version": PROTOCOL_VERSION,
            "name": name,
            "hmac": hmac_value,
            "peer_id": peer_id,
        })

    @staticmethod
    def parse_handshake_response(data: object) -> Optional[Dict[str, str]]:
        """v3.0: peer_id 누락/형식 위반 시 None (v2.x client hard break 거부)."""
        if not isinstance(data, dict):
            return None
        cn = data.get("client_nonce")
        cv = data.get("client_version")
        nm = data.get("name")
        hm = data.get("hmac")
        pid = data.get("peer_id")
        if not is_valid_nonce(cn):
            return None
        if not isinstance(cv, str) or not cv or len(cv) > 32:
            return None
        if not isinstance(nm, str) or not nm or len(nm) > 256:
            return None
        if not isinstance(hm, str) or len(hm) != 64:
            return None
        if not is_valid_peer_id(pid):
            return None
        return {
            "client_nonce": cn,
            "client_version": cv,
            "name": nm,
            "hmac": hm,
            "peer_id": pid,
        }

    def create_handshake_ack(self, hmac_value: str) -> bytes:
        if not isinstance(hmac_value, str) or len(hmac_value) != 64:
            raise ValueError(f"invalid hmac: {hmac_value!r}")
        return self.create_message(MSG_HANDSHAKE_ACK, {"hmac": hmac_value})

    @staticmethod
    def parse_handshake_ack(data: object) -> Optional[Dict[str, str]]:
        if not isinstance(data, dict):
            return None
        hm = data.get("hmac")
        if not isinstance(hm, str) or len(hm) != 64:
            return None
        return {"hmac": hm}

    @staticmethod
    def is_compatible_version(remote_version: str) -> bool:
        """Major.minor 비교. v2.2 끼리만 호환 (v2.1 등 거부)."""
        if not isinstance(remote_version, str):
            return False
        try:
            local_parts = PROTOCOL_VERSION.split(".")
            remote_parts = remote_version.split(".")
            return local_parts[0] == remote_parts[0] and local_parts[1] == remote_parts[1]
        except (IndexError, AttributeError):
            return False

    def create_binary_chunk(
        self,
        transfer_id: str,
        file_path: str,
        chunk_index: int,
        chunk_data: bytes,
        chunk_hash: str,
        receiver_peer: str = "",
    ) -> bytes:
        """
        바이너리 청크 메시지를 생성합니다 (base64 인코딩 없이 원시 바이너리 전송).

        형식: [4바이트 길이 헤더] + [0x00] + [4바이트 메타길이] + [메타 JSON] + [원시 바이너리]

        Args:
            receiver_peer: v3.0 targeted relay 대상 peer_id. 빈 문자열이면 서버가
                broadcast (eager 호환). 값이 있으면 서버가 그 peer 에게만 중계.

        Returns:
            bytes: 헤더 포함 완성된 와이어 메시지
        """
        meta = json.dumps({
            "type": MSG_FILE_CHUNK,
            "data": {
                "transfer_id": transfer_id,
                "file_path": file_path,
                "chunk_index": chunk_index,
                "hash": chunk_hash,
                "receiver_peer": receiver_peer,
            }
        }, ensure_ascii=False).encode("utf-8")

        # 페이로드: marker(1) + meta_len(4) + meta + binary
        payload = self.BINARY_MARKER + struct.pack(">I", len(meta)) + meta + chunk_data
        header = struct.pack(">I", len(payload))
        return header + payload

    def parse_message(self, data: bytes) -> Optional[Dict[str, Any]]:
        """
        수신된 메시지를 파싱합니다.

        페이로드 첫 바이트가 0x00이면 바이너리 프레임, 아니면 JSON으로 파싱.

        Args:
            data: 수신된 바이트 데이터 (헤더 제외, 페이로드만)

        Returns:
            Dict: {"type": str, "data": Any} 형태의 파싱된 메시지, 실패 시 None
        """
        try:
            if data and data[0:1] == self.BINARY_MARKER:
                return self._parse_binary_frame(data)
            json_data = data.decode("utf-8")
            message = json.loads(json_data)
            # M8: JSON 은 dict 가 아닌 값(문자열/숫자/리스트/null 등)도 유효하게
            # 파싱된다. 호출부(core/network.py)는 message["_raw"]=... 로 바로
            # mutate 하므로, dict 가 아니면 TypeError 가 나 그 연결의 수신 루프
            # 전체가 죽는다 — 다른 실패와 동일하게 조용히 None 반환해야 한다.
            if not isinstance(message, dict):
                logger.error(f"메시지 파싱 오류: 최상위가 dict 아님 ({type(message).__name__})")
                return None
            return message
        except Exception as e:
            logger.error(f"메시지 파싱 오류: {e}")
            return None

    def _parse_binary_frame(self, data: bytes) -> Optional[Dict[str, Any]]:
        """바이너리 프레임 파싱: [0x00][4바이트 메타길이][메타 JSON][원시 바이너리].

        Task 2.5: chunk meta 의 transfer_id / file_path / chunk_index 형식을
        조기 검증하여 라우터/file_manager 부담을 줄인다. 검증 실패 시 None 반환.
        """
        if len(data) < 5:
            logger.error("바이너리 프레임 너무 짧음")
            return None
        meta_len = struct.unpack(">I", data[1:5])[0]
        # 메타 JSON 상한: 64KB (실제 메타는 수백 바이트). 버그/악의 프레임 방어.
        if meta_len > 64 * 1024 or meta_len > len(data) - 5:
            logger.error(
                f"바이너리 프레임 메타 길이 이상: meta_len={meta_len}, "
                f"payload_len={len(data)}"
            )
            return None
        meta_json = data[5:5 + meta_len]
        binary_data = data[5 + meta_len:]
        try:
            message = json.loads(meta_json.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"바이너리 프레임 메타 JSON 파싱 실패: {e}")
            return None

        # Task 2.5: chunk meta schema 검증 — transfer_id 형식 + chunk_index 음수 거부
        meta_data = message.get("data") if isinstance(message, dict) else None
        if not isinstance(meta_data, dict):
            logger.error("바이너리 프레임 meta.data 가 dict 아님")
            return None
        if message.get("type") == MSG_FILE_CHUNK:
            tid = meta_data.get("transfer_id")
            if not is_valid_transfer_id(tid):
                logger.error(
                    f"바이너리 프레임 거부 (invalid transfer_id): {tid!r}"
                )
                return None
            ci = meta_data.get("chunk_index")
            if not isinstance(ci, int) or ci < 0:
                logger.error(
                    f"바이너리 프레임 거부 (invalid chunk_index): {ci!r}"
                )
                return None
            # v3.0: receiver_peer 형식 검증 — "" (broadcast) 또는 valid peer_id.
            # 위반 시 라우터가 잘못된 소켓으로 보내지 않도록 frame 자체 거부.
            rp = meta_data.get("receiver_peer", "")
            if rp != "" and not is_valid_peer_id(rp):
                logger.error(
                    f"바이너리 프레임 거부 (invalid receiver_peer): {rp!r}"
                )
                return None
            # file_path 는 file_transfer 에서 _is_safe_rel_path 로 검증됨 (중복 회피)

        meta_data["binary_data"] = binary_data
        return message

    # ── v3.0: lazy clipboard offer/fetch 메시지 ────────────────────────
    # offer: copy 시점 source 가 broadcast (경로 알림, 데이터 전송 X).
    # fetch: paste 시점 requester 가 source 로 routed (receiver_peer=source).
    # fetch_fail: source 가 requester 로 routed (offer 만료/없음 등).
    # 실제 데이터는 fetch 응답으로 file_start/chunk/end (receiver_peer=requester).

    @staticmethod
    def _is_valid_offer_item(item: object) -> bool:
        """offer item = {"name": str(비어있지 않음), "size": int>=0, "hash": str}.

        lazy 모드라 copy 시점엔 hash="" 가능 (fetch 시 source 가 채움).
        """
        if not isinstance(item, dict):
            return False
        name = item.get("name")
        size = item.get("size")
        h = item.get("hash", "")
        if not isinstance(name, str) or not name or len(name) > 1024:
            return False
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            return False
        if not isinstance(h, str) or len(h) > 128:
            return False
        return True

    def create_clip_offer(
        self, offer_id: str, source_peer: str, kind: str,
        items: list, total_size: int, created_at: float,
    ) -> bytes:
        """copy 시점 lazy offer 생성 (broadcast 용).

        Args:
            offer_id: UUID v4 형식 (is_valid_transfer_id 재사용)
            source_peer: 콘텐츠 보유 peer_id
            kind: "file" 또는 "image"
            items: [{"name", "size", "hash"}] preview 디스크립터
            total_size: 전체 바이트 합
            created_at: 생성 시각 (epoch 초) — TTL 판정용

        Raises:
            ValueError: 형식 위반 시
        """
        if not is_valid_transfer_id(offer_id):
            raise ValueError(f"invalid offer_id: {offer_id!r}")
        if not is_valid_peer_id(source_peer):
            raise ValueError(f"invalid source_peer: {source_peer!r}")
        if kind not in _CLIP_OFFER_KINDS:
            raise ValueError(f"invalid offer kind: {kind!r}")
        if not isinstance(items, list) or not items:
            raise ValueError("offer items must be a non-empty list")
        if not all(self._is_valid_offer_item(it) for it in items):
            raise ValueError("offer items 형식 위반")
        if not isinstance(total_size, int) or isinstance(total_size, bool) or total_size < 0:
            raise ValueError(f"invalid total_size: {total_size!r}")
        if not isinstance(created_at, (int, float)) or isinstance(created_at, bool) \
                or created_at < 0:
            raise ValueError(f"invalid created_at: {created_at!r}")
        return self.create_message(MSG_CLIP_OFFER, {
            "offer_id": offer_id,
            "source_peer": source_peer,
            "kind": kind,
            "items": items,
            "total_size": total_size,
            "created_at": created_at,
        })

    @staticmethod
    def parse_clip_offer(data: object) -> Optional[Dict[str, Any]]:
        """검증 통과 시 offer dict, 실패 시 None (silent ignore 신호)."""
        if not isinstance(data, dict):
            return None
        offer_id = data.get("offer_id")
        source_peer = data.get("source_peer")
        kind = data.get("kind")
        items = data.get("items")
        total_size = data.get("total_size")
        created_at = data.get("created_at")
        if not is_valid_transfer_id(offer_id):
            return None
        if not is_valid_peer_id(source_peer):
            return None
        if kind not in _CLIP_OFFER_KINDS:
            return None
        if not isinstance(items, list) or not items:
            return None
        if not all(Protocol._is_valid_offer_item(it) for it in items):
            return None
        if not isinstance(total_size, int) or isinstance(total_size, bool) or total_size < 0:
            return None
        if not isinstance(created_at, (int, float)) or isinstance(created_at, bool) \
                or created_at < 0:
            return None
        return {
            "offer_id": offer_id,
            "source_peer": source_peer,
            "kind": kind,
            "items": items,
            "total_size": total_size,
            "created_at": created_at,
        }

    def create_clip_fetch(
        self, offer_id: str, requester_peer: str, receiver_peer: str = "",
    ) -> bytes:
        """paste 시점 fetch 요청.

        receiver_peer 는 라우팅 대상(=offer 의 source_peer)으로, main 이 offer 에서
        조회해 채운다. 빈 문자열이면 broadcast (fallback).
        """
        if not is_valid_transfer_id(offer_id):
            raise ValueError(f"invalid offer_id: {offer_id!r}")
        if not is_valid_peer_id(requester_peer):
            raise ValueError(f"invalid requester_peer: {requester_peer!r}")
        if receiver_peer and not is_valid_peer_id(receiver_peer):
            raise ValueError(f"invalid receiver_peer: {receiver_peer!r}")
        return self.create_message(MSG_CLIP_FETCH, {
            "offer_id": offer_id,
            "requester_peer": requester_peer,
            "receiver_peer": receiver_peer,
        })

    @staticmethod
    def parse_clip_fetch(data: object) -> Optional[Dict[str, str]]:
        if not isinstance(data, dict):
            return None
        offer_id = data.get("offer_id")
        requester_peer = data.get("requester_peer")
        receiver_peer = data.get("receiver_peer", "")
        if not is_valid_transfer_id(offer_id):
            return None
        if not is_valid_peer_id(requester_peer):
            return None
        if receiver_peer != "" and not is_valid_peer_id(receiver_peer):
            return None
        return {
            "offer_id": offer_id,
            "requester_peer": requester_peer,
            "receiver_peer": receiver_peer,
        }

    def create_clip_fetch_fail(
        self, offer_id: str, reason: str, receiver_peer: str = "",
    ) -> bytes:
        """fetch 실패 응답 (source → requester 로 routed).

        receiver_peer 는 라우팅 대상(=fetch 의 requester_peer).
        """
        if not is_valid_transfer_id(offer_id):
            raise ValueError(f"invalid offer_id: {offer_id!r}")
        if reason not in _FETCH_FAIL_REASONS:
            raise ValueError(f"invalid fetch fail reason: {reason!r}")
        if receiver_peer and not is_valid_peer_id(receiver_peer):
            raise ValueError(f"invalid receiver_peer: {receiver_peer!r}")
        return self.create_message(MSG_CLIP_FETCH_FAIL, {
            "offer_id": offer_id,
            "reason": reason,
            "receiver_peer": receiver_peer,
        })

    @staticmethod
    def parse_clip_fetch_fail(data: object) -> Optional[Dict[str, str]]:
        if not isinstance(data, dict):
            return None
        offer_id = data.get("offer_id")
        reason = data.get("reason")
        receiver_peer = data.get("receiver_peer", "")
        if not is_valid_transfer_id(offer_id):
            return None
        if reason not in _FETCH_FAIL_REASONS:
            return None
        if receiver_peer != "" and not is_valid_peer_id(receiver_peer):
            return None
        return {
            "offer_id": offer_id,
            "reason": reason,
            "receiver_peer": receiver_peer,
        }

    @staticmethod
    def read_header(header_data: bytes) -> int:
        """
        4바이트 빅엔디안 헤더에서 메시지 길이를 읽습니다.

        Args:
            header_data: 4바이트 헤더 데이터

        Returns:
            int: 페이로드 길이 (바이트)
        """
        return struct.unpack(">I", header_data)[0]

    @staticmethod
    def create_auth_hash(key: str) -> str:
        """
        인증용 SHA-256 해시를 생성합니다.

        Args:
            key: 공유 키 문자열

        Returns:
            str: SHA-256 해시 (16진수 문자열)
        """
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @staticmethod
    def verify_auth(received_hash: str, key: str) -> bool:
        """
        수신된 인증 해시와 로컬 키를 비교하여 인증을 검증합니다.

        핸드셰이크 시 상대방이 보낸 SHA-256 해시가
        자신의 키로 생성한 해시와 일치하는지 확인합니다.

        Args:
            received_hash: 상대방으로부터 수신한 SHA-256 해시
            key: 로컬에 저장된 공유 키

        Returns:
            bool: 인증 성공 여부
        """
        import hmac
        local_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return hmac.compare_digest(received_hash, local_hash)
