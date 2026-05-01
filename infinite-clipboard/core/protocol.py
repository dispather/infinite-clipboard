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

# ── Protocol version (R3, hard break) ──────────────────────────────────
# Major.minor 만 비교 — patch 차이는 호환. v2.2 부터 nonce HMAC handshake.
PROTOCOL_VERSION = "2.2"


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
MSG_FILE_ERROR = "file_error"        # 파일 전송 오류
MSG_TRANSFER_COMPLETE = "transfer_complete"  # 전체 전송 완료

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

    def create_handshake_challenge(self, server_nonce: str) -> bytes:
        if not is_valid_nonce(server_nonce):
            raise ValueError(f"invalid server_nonce: {server_nonce!r}")
        return self.create_message(MSG_HANDSHAKE_CHALLENGE, {
            "server_nonce": server_nonce,
            "server_version": PROTOCOL_VERSION,
        })

    @staticmethod
    def parse_handshake_challenge(data: object) -> Optional[Dict[str, str]]:
        """검증 통과 시 {server_nonce, server_version} 반환, 실패 시 None."""
        if not isinstance(data, dict):
            return None
        sn = data.get("server_nonce")
        sv = data.get("server_version")
        if not is_valid_nonce(sn):
            return None
        if not isinstance(sv, str) or not sv or len(sv) > 32:
            return None
        return {"server_nonce": sn, "server_version": sv}

    def create_handshake_response(
        self, client_nonce: str, name: str, hmac_value: str,
    ) -> bytes:
        if not is_valid_nonce(client_nonce):
            raise ValueError(f"invalid client_nonce: {client_nonce!r}")
        if not isinstance(name, str) or len(name) > 256:
            raise ValueError(f"invalid name: {name!r}")
        if not isinstance(hmac_value, str) or len(hmac_value) != 64:
            raise ValueError(f"invalid hmac: {hmac_value!r}")
        return self.create_message(MSG_HANDSHAKE_RESPONSE, {
            "client_nonce": client_nonce,
            "client_version": PROTOCOL_VERSION,
            "name": name,
            "hmac": hmac_value,
        })

    @staticmethod
    def parse_handshake_response(data: object) -> Optional[Dict[str, str]]:
        if not isinstance(data, dict):
            return None
        cn = data.get("client_nonce")
        cv = data.get("client_version")
        nm = data.get("name")
        hm = data.get("hmac")
        if not is_valid_nonce(cn):
            return None
        if not isinstance(cv, str) or not cv or len(cv) > 32:
            return None
        if not isinstance(nm, str) or not nm or len(nm) > 256:
            return None
        if not isinstance(hm, str) or len(hm) != 64:
            return None
        return {
            "client_nonce": cn,
            "client_version": cv,
            "name": nm,
            "hmac": hm,
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
    ) -> bytes:
        """
        바이너리 청크 메시지를 생성합니다 (base64 인코딩 없이 원시 바이너리 전송).

        형식: [4바이트 길이 헤더] + [0x00] + [4바이트 메타길이] + [메타 JSON] + [원시 바이너리]

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
            # file_path 는 file_transfer 에서 _is_safe_rel_path 로 검증됨 (중복 회피)

        meta_data["binary_data"] = binary_data
        return message

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
