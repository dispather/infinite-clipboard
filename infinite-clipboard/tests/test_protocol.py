"""프로토콜 직렬화/파싱 + 인증 핸드셰이크 회귀 테스트."""

import struct

import pytest

from core.protocol import (
    Protocol, MSG_CLIPBOARD, MSG_FILE_CHUNK, MSG_HANDSHAKE, MSG_FILE_CANCEL,
    MSG_FILE_CANCEL_ACK,
    MSG_CLIP_OFFER, MSG_CLIP_FETCH, MSG_CLIP_FETCH_FAIL,
    CLIP_OFFER_KIND_FILE, CLIP_OFFER_KIND_IMAGE,
    FETCH_FAIL_EXPIRED, FETCH_FAIL_OFFLINE,
    CANCEL_REASON_SUPERSEDED, CANCEL_REASON_USER, CANCEL_REASON_ERROR,
    CANCEL_ACK_ROLE_SENDER, CANCEL_ACK_ROLE_RECEIVER, CANCEL_ACK_ROLE_NONE,
    CANCEL_ACK_STATUS_OK, CANCEL_ACK_STATUS_UNKNOWN,
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


# ── v2.3.1: cancel ack 회귀 ─────────────────────────────────────────────


def test_file_cancel_ack_roundtrip():
    """ack 송수신 — 모든 role/status 조합 wire 통과."""
    p = Protocol()
    for role in (CANCEL_ACK_ROLE_SENDER, CANCEL_ACK_ROLE_RECEIVER, CANCEL_ACK_ROLE_NONE):
        for status in (CANCEL_ACK_STATUS_OK, CANCEL_ACK_STATUS_UNKNOWN):
            wire = p.create_file_cancel_ack(_VALID_TID, role, status)
            parsed = p.parse_message(wire[4:])
            assert parsed["type"] == MSG_FILE_CANCEL_ACK
            payload = Protocol.parse_file_cancel_ack(parsed["data"])
            assert payload == {
                "transfer_id": _VALID_TID, "role": role, "status": status,
            }


def test_file_cancel_ack_default_status_ok():
    p = Protocol()
    wire = p.create_file_cancel_ack(_VALID_TID, CANCEL_ACK_ROLE_SENDER)
    parsed = p.parse_message(wire[4:])
    payload = Protocol.parse_file_cancel_ack(parsed["data"])
    assert payload["status"] == CANCEL_ACK_STATUS_OK


def test_file_cancel_ack_rejects_invalid_transfer_id():
    p = Protocol()
    for bad in ("../escape", "not-a-uuid", "", "12345678-1234-1234-1234-123456789012"):
        with pytest.raises(ValueError):
            p.create_file_cancel_ack(bad, CANCEL_ACK_ROLE_SENDER)


def test_file_cancel_ack_rejects_invalid_role():
    p = Protocol()
    with pytest.raises(ValueError):
        p.create_file_cancel_ack(_VALID_TID, "malicious")
    with pytest.raises(ValueError):
        p.create_file_cancel_ack(_VALID_TID, "")


def test_file_cancel_ack_rejects_invalid_status():
    p = Protocol()
    with pytest.raises(ValueError):
        p.create_file_cancel_ack(
            _VALID_TID, CANCEL_ACK_ROLE_SENDER, status="garbage",
        )


def test_parse_file_cancel_ack_rejects_garbage():
    """비-dict, 누락 필드, 화이트리스트 위반 모두 silent ignore (None)."""
    assert Protocol.parse_file_cancel_ack(None) is None
    assert Protocol.parse_file_cancel_ack("not a dict") is None
    assert Protocol.parse_file_cancel_ack([1, 2, 3]) is None
    assert Protocol.parse_file_cancel_ack({}) is None
    # 누락 필드
    assert Protocol.parse_file_cancel_ack(
        {"transfer_id": _VALID_TID, "role": CANCEL_ACK_ROLE_SENDER}
    ) is None  # status 누락
    assert Protocol.parse_file_cancel_ack(
        {"transfer_id": _VALID_TID, "status": CANCEL_ACK_STATUS_OK}
    ) is None  # role 누락
    # 형식/화이트리스트 위반
    assert Protocol.parse_file_cancel_ack(
        {"transfer_id": "../bad", "role": CANCEL_ACK_ROLE_SENDER, "status": "ok"}
    ) is None
    assert Protocol.parse_file_cancel_ack(
        {"transfer_id": _VALID_TID, "role": "X", "status": "ok"}
    ) is None
    assert Protocol.parse_file_cancel_ack(
        {"transfer_id": _VALID_TID, "role": CANCEL_ACK_ROLE_SENDER, "status": "Y"}
    ) is None


# ─── v3.0: clip offer/fetch/fetch_fail ───────────────────────────────

_PEER_A = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
_PEER_B = "0011223344556677889900aabbccddee"


def test_clip_offer_roundtrip():
    p = Protocol()
    items = [
        {"name": "report.pdf", "size": 1234, "hash": "ab12"},
        {"name": "photo.png", "size": 5678, "hash": ""},  # lazy: hash 빈 문자열 허용
    ]
    wire = p.create_clip_offer(
        offer_id=_VALID_TID, source_peer=_PEER_A, kind=CLIP_OFFER_KIND_FILE,
        items=items, total_size=6912, created_at=1717000000.0,
    )
    parsed = p.parse_message(wire[4:])
    assert parsed["type"] == MSG_CLIP_OFFER
    payload = Protocol.parse_clip_offer(parsed["data"])
    assert payload == {
        "offer_id": _VALID_TID,
        "source_peer": _PEER_A,
        "kind": CLIP_OFFER_KIND_FILE,
        "items": items,
        "total_size": 6912,
        "created_at": 1717000000.0,
    }


def test_clip_offer_image_kind():
    p = Protocol()
    wire = p.create_clip_offer(
        offer_id=_VALID_TID, source_peer=_PEER_A, kind=CLIP_OFFER_KIND_IMAGE,
        items=[{"name": "clip.png", "size": 100, "hash": ""}],
        total_size=100, created_at=1,
    )
    payload = Protocol.parse_clip_offer(p.parse_message(wire[4:])["data"])
    assert payload["kind"] == CLIP_OFFER_KIND_IMAGE


def test_clip_offer_create_rejects_invalid():
    p = Protocol()
    base = dict(offer_id=_VALID_TID, source_peer=_PEER_A,
                kind=CLIP_OFFER_KIND_FILE,
                items=[{"name": "a", "size": 1, "hash": ""}],
                total_size=1, created_at=1)
    # offer_id 형식
    with pytest.raises(ValueError):
        p.create_clip_offer(**{**base, "offer_id": "not-a-uuid"})
    # source_peer 형식
    with pytest.raises(ValueError):
        p.create_clip_offer(**{**base, "source_peer": "short"})
    # kind 화이트리스트 (text 는 lazy 대상 아님)
    with pytest.raises(ValueError):
        p.create_clip_offer(**{**base, "kind": "text"})
    # 빈 items
    with pytest.raises(ValueError):
        p.create_clip_offer(**{**base, "items": []})
    # item 형식 위반 (size 음수)
    with pytest.raises(ValueError):
        p.create_clip_offer(**{**base, "items": [{"name": "a", "size": -1, "hash": ""}]})
    # total_size 음수
    with pytest.raises(ValueError):
        p.create_clip_offer(**{**base, "total_size": -5})


def test_clip_offer_parse_rejects_garbage():
    assert Protocol.parse_clip_offer(None) is None
    assert Protocol.parse_clip_offer({}) is None
    valid = {
        "offer_id": _VALID_TID, "source_peer": _PEER_A,
        "kind": CLIP_OFFER_KIND_FILE,
        "items": [{"name": "a", "size": 1, "hash": ""}],
        "total_size": 1, "created_at": 1,
    }
    assert Protocol.parse_clip_offer(valid) is not None
    for missing in ("offer_id", "source_peer", "kind", "items", "total_size", "created_at"):
        d = dict(valid)
        d.pop(missing)
        assert Protocol.parse_clip_offer(d) is None
    # item 에 name 누락
    bad_item = dict(valid)
    bad_item["items"] = [{"size": 1, "hash": ""}]
    assert Protocol.parse_clip_offer(bad_item) is None


def test_clip_fetch_roundtrip():
    p = Protocol()
    wire = p.create_clip_fetch(_VALID_TID, requester_peer=_PEER_B, receiver_peer=_PEER_A)
    parsed = p.parse_message(wire[4:])
    assert parsed["type"] == MSG_CLIP_FETCH
    payload = Protocol.parse_clip_fetch(parsed["data"])
    assert payload == {
        "offer_id": _VALID_TID,
        "requester_peer": _PEER_B,
        "receiver_peer": _PEER_A,
    }


def test_clip_fetch_empty_receiver_is_broadcast():
    p = Protocol()
    wire = p.create_clip_fetch(_VALID_TID, requester_peer=_PEER_B)
    payload = Protocol.parse_clip_fetch(p.parse_message(wire[4:])["data"])
    assert payload["receiver_peer"] == ""


def test_clip_fetch_rejects_invalid():
    p = Protocol()
    with pytest.raises(ValueError):
        p.create_clip_fetch("bad", requester_peer=_PEER_B)
    with pytest.raises(ValueError):
        p.create_clip_fetch(_VALID_TID, requester_peer="short")
    with pytest.raises(ValueError):
        p.create_clip_fetch(_VALID_TID, requester_peer=_PEER_B, receiver_peer="bad")
    # parse: requester 형식 위반
    assert Protocol.parse_clip_fetch(
        {"offer_id": _VALID_TID, "requester_peer": "short"}
    ) is None
    assert Protocol.parse_clip_fetch(None) is None


def test_clip_fetch_fail_roundtrip():
    p = Protocol()
    wire = p.create_clip_fetch_fail(_VALID_TID, FETCH_FAIL_EXPIRED, receiver_peer=_PEER_B)
    parsed = p.parse_message(wire[4:])
    assert parsed["type"] == MSG_CLIP_FETCH_FAIL
    payload = Protocol.parse_clip_fetch_fail(parsed["data"])
    assert payload == {
        "offer_id": _VALID_TID,
        "reason": FETCH_FAIL_EXPIRED,
        "receiver_peer": _PEER_B,
    }


def test_clip_fetch_fail_rejects_invalid_reason():
    p = Protocol()
    with pytest.raises(ValueError):
        p.create_clip_fetch_fail(_VALID_TID, "not-a-reason")
    # 화이트리스트 외 reason → parse None
    assert Protocol.parse_clip_fetch_fail(
        {"offer_id": _VALID_TID, "reason": "bogus", "receiver_peer": _PEER_B}
    ) is None
    # 유효 reason 은 통과
    assert Protocol.parse_clip_fetch_fail(
        {"offer_id": _VALID_TID, "reason": FETCH_FAIL_OFFLINE}
    ) is not None
