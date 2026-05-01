"""프로토콜 직렬화/파싱 + 인증 핸드셰이크 회귀 테스트."""

import struct

import pytest

from core.protocol import (
    Protocol, MSG_CLIPBOARD, MSG_FILE_CHUNK, MSG_HANDSHAKE, MSG_FILE_CANCEL,
    CANCEL_REASON_SUPERSEDED, CANCEL_REASON_USER, CANCEL_REASON_ERROR,
    is_valid_transfer_id,
)


# UUID v4 fixtures — 실제 uuid.uuid4() 출력과 동일 형식
_VALID_TID = "12345678-1234-4abc-8def-1234567890ab"
_VALID_TID_2 = "abcdef01-2345-4678-9abc-def012345678"


def test_message_roundtrip_text():
    p = Protocol("key-xyz")
    wire = p.create_message(MSG_CLIPBOARD, {"content_type": "text", "content": "안녕"})
    # 헤더 4바이트 + 페이로드
    payload_len = struct.unpack(">I", wire[:4])[0]
    assert payload_len == len(wire) - 4
    parsed = p.parse_message(wire[4:])
    assert parsed["type"] == MSG_CLIPBOARD
    assert parsed["data"]["content"] == "안녕"


def test_message_roundtrip_korean_large():
    p = Protocol()
    big = "가" * 10_000
    wire = p.create_message(MSG_CLIPBOARD, {"content_type": "text", "content": big})
    parsed = p.parse_message(wire[4:])
    assert parsed["data"]["content"] == big


def test_binary_frame_roundtrip():
    p = Protocol()
    chunk = b"\x00\x01\x02" * 100_000  # 바이트 중 0x00 포함 — 바이너리 프레임 헤더와 충돌 가능성 검증
    wire = p.create_binary_chunk(
        transfer_id=_VALID_TID, file_path="a.bin", chunk_index=0,
        chunk_data=chunk, chunk_hash="ff",
    )
    payload = wire[4:]
    parsed = p.parse_message(payload)
    assert parsed["type"] == MSG_FILE_CHUNK
    assert parsed["data"]["binary_data"] == chunk
    assert parsed["data"]["chunk_index"] == 0


def test_binary_frame_meta_len_bound():
    """조작된 meta_len(거대값)이 silent truncation 대신 명시 에러 반환해야."""
    p = Protocol()
    fake = b"\x00" + struct.pack(">I", 0xFFFFFFFF) + b"{}"
    assert p.parse_message(fake) is None


def test_auth_hash_matches():
    h = Protocol.create_auth_hash("shared-secret-xyz")
    assert len(h) == 64  # SHA-256 hex
    assert Protocol.verify_auth(h, "shared-secret-xyz") is True
    assert Protocol.verify_auth(h, "wrong-key") is False


def test_auth_hash_timing_safe():
    """compare_digest 사용 여부 확인 — 같은 길이 다른 값은 False, 다른 길이도 False."""
    ok = Protocol.create_auth_hash("k")
    assert Protocol.verify_auth("a" * 64, "k") is False
    assert Protocol.verify_auth("", "k") is False
    assert Protocol.verify_auth(ok, "k") is True


def test_handshake_wire_format():
    p = Protocol("auth-test")
    msg = p.create_message(MSG_HANDSHAKE, {
        "name": "MacBook",
        "auth_hash": Protocol.create_auth_hash("auth-test"),
    })
    parsed = p.parse_message(msg[4:])
    assert parsed["type"] == MSG_HANDSHAKE
    assert parsed["data"]["name"] == "MacBook"
    assert Protocol.verify_auth(parsed["data"]["auth_hash"], "auth-test")


def test_parse_garbage_returns_none():
    p = Protocol()
    assert p.parse_message(b"\xff\xfe\xfd not json") is None
    assert p.parse_message(b"") is None


# ── transfer_id 형식 검증 ───────────────────────────────────────────────


def test_is_valid_transfer_id_accepts_uuid4():
    import uuid
    for _ in range(20):
        assert is_valid_transfer_id(str(uuid.uuid4())) is True
    # 명시적 fixture 도 검증
    assert is_valid_transfer_id(_VALID_TID) is True
    assert is_valid_transfer_id(_VALID_TID_2) is True


def test_is_valid_transfer_id_rejects_traversal_and_garbage():
    bad_values = [
        "../escape",
        "C:\\evil",
        "/etc/passwd",
        "",
        "not-a-uuid",
        "12345678-1234-1234-1234-123456789012",  # version != 4
        "12345678-1234-4abc-cdef-1234567890ab",  # 4번째 그룹 first char != [89ab]
        "ABCDEF01-2345-4678-9ABC-DEF012345678",  # uppercase
        "12345678-1234-4abc-8def-1234567890ab\x00",  # null byte
        "a" * 1000,
        None,
        12345,
        {"id": _VALID_TID},
    ]
    for v in bad_values:
        assert is_valid_transfer_id(v) is False, f"should reject: {v!r}"


# ── MSG_FILE_CANCEL 헬퍼 ────────────────────────────────────────────────


def test_file_cancel_roundtrip():
    p = Protocol()
    wire = p.create_file_cancel(_VALID_TID, reason=CANCEL_REASON_USER)
    parsed = p.parse_message(wire[4:])
    assert parsed["type"] == MSG_FILE_CANCEL
    payload = Protocol.parse_file_cancel(parsed["data"])
    assert payload == {"transfer_id": _VALID_TID, "reason": "user"}


def test_file_cancel_default_reason_is_superseded():
    p = Protocol()
    wire = p.create_file_cancel(_VALID_TID_2)
    parsed = p.parse_message(wire[4:])
    payload = Protocol.parse_file_cancel(parsed["data"])
    assert payload["reason"] == CANCEL_REASON_SUPERSEDED


def test_file_cancel_all_reasons_supported():
    p = Protocol()
    for reason in (CANCEL_REASON_SUPERSEDED, CANCEL_REASON_USER, CANCEL_REASON_ERROR):
        wire = p.create_file_cancel(_VALID_TID, reason=reason)
        parsed = p.parse_message(wire[4:])
        payload = Protocol.parse_file_cancel(parsed["data"])
        assert payload["reason"] == reason


def test_file_cancel_rejects_invalid_transfer_id():
    p = Protocol()
    for bad in ("../escape", "not-a-uuid", "", "12345678-1234-1234-1234-123456789012"):
        with pytest.raises(ValueError):
            p.create_file_cancel(bad, reason=CANCEL_REASON_USER)


def test_file_cancel_rejects_invalid_reason():
    p = Protocol()
    with pytest.raises(ValueError):
        p.create_file_cancel(_VALID_TID, reason="malicious")
    with pytest.raises(ValueError):
        p.create_file_cancel(_VALID_TID, reason="")


def test_parse_file_cancel_rejects_garbage():
    # 비-dict
    assert Protocol.parse_file_cancel(None) is None
    assert Protocol.parse_file_cancel("not a dict") is None
    assert Protocol.parse_file_cancel([1, 2, 3]) is None
    # 형식 위반 transfer_id
    assert Protocol.parse_file_cancel(
        {"transfer_id": "../bad", "reason": "user"}
    ) is None
    # 화이트리스트 외 reason
    assert Protocol.parse_file_cancel(
        {"transfer_id": _VALID_TID, "reason": "evil"}
    ) is None
    # 누락 필드
    assert Protocol.parse_file_cancel({"transfer_id": _VALID_TID}) is None
    assert Protocol.parse_file_cancel({"reason": "user"}) is None
    # 빈 dict
    assert Protocol.parse_file_cancel({}) is None
