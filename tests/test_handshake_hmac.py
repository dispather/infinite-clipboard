"""v2.2 R3: HMAC handshake 헬퍼 단위 테스트.

3-step mutual HMAC handshake (challenge/response/ack) 의 wire format,
nonce 검증, HMAC 계산, version 호환성 분기를 검증한다.
"""

import pytest

from core.protocol import (
    Protocol, PROTOCOL_VERSION,
    MSG_HANDSHAKE_CHALLENGE, MSG_HANDSHAKE_RESPONSE, MSG_HANDSHAKE_ACK,
    NONCE_HEX_LEN, PEER_ID_HEX_LEN,
    generate_nonce, is_valid_nonce,
    generate_peer_id, is_valid_peer_id,
    compute_handshake_hmac, verify_handshake_hmac,
)


# ─── nonce 생성/검증 ─────────────────────────────────────────────────


def test_generate_nonce_format():
    n = generate_nonce()
    assert is_valid_nonce(n)
    assert len(n) == NONCE_HEX_LEN  # 64
    assert all(c in "0123456789abcdef" for c in n)


def test_generate_nonce_uniqueness():
    """100 개 생성 — 충돌 없어야 (256-bit entropy)."""
    nonces = {generate_nonce() for _ in range(100)}
    assert len(nonces) == 100


@pytest.mark.parametrize("bad", [
    "",
    "shorter",
    "X" * 64,                         # uppercase
    "g" * 64,                         # non-hex
    "0" * 63,                         # too short
    "0" * 65,                         # too long
    None,
    123,
    bytes(32),
])
def test_invalid_nonce_rejected(bad):
    assert is_valid_nonce(bad) is False


# ─── v3.0: peer_id 생성/검증 ─────────────────────────────────────────


def test_generate_peer_id_format():
    p = generate_peer_id()
    assert is_valid_peer_id(p)
    assert len(p) == PEER_ID_HEX_LEN  # 32
    assert all(c in "0123456789abcdef" for c in p)


def test_generate_peer_id_uniqueness():
    """100 개 생성 — 충돌 없어야 (128-bit entropy)."""
    ids = {generate_peer_id() for _ in range(100)}
    assert len(ids) == 100


@pytest.mark.parametrize("bad", [
    "",
    "shorter",
    "X" * 32,                         # uppercase
    "g" * 32,                         # non-hex
    "0" * 31,                         # too short
    "0" * 33,                         # too long
    "0" * 64,                         # nonce 길이 (peer_id 아님)
    None,
    123,
    bytes(16),
])
def test_invalid_peer_id_rejected(bad):
    assert is_valid_peer_id(bad) is False


# ─── HMAC 계산/검증 ──────────────────────────────────────────────────


def test_hmac_deterministic():
    """같은 입력 → 같은 HMAC."""
    n1 = generate_nonce()
    n2 = generate_nonce()
    h1 = compute_handshake_hmac("shared-key", n1, n2)
    h2 = compute_handshake_hmac("shared-key", n1, n2)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_hmac_directional():
    """nonce 순서 바뀌면 HMAC 도 다름 (mutual auth 의 핵심)."""
    n1 = generate_nonce()
    n2 = generate_nonce()
    h_ab = compute_handshake_hmac("k", n1, n2)
    h_ba = compute_handshake_hmac("k", n2, n1)
    assert h_ab != h_ba


def test_hmac_different_key_different_hmac():
    n1 = generate_nonce()
    n2 = generate_nonce()
    assert compute_handshake_hmac("k1", n1, n2) != compute_handshake_hmac("k2", n1, n2)


def test_hmac_invalid_inputs_raise():
    """잘못된 nonce / 빈 키 → ValueError."""
    n = generate_nonce()
    with pytest.raises(ValueError):
        compute_handshake_hmac("k", "bad", n)
    with pytest.raises(ValueError):
        compute_handshake_hmac("k", n, "bad")
    with pytest.raises(ValueError):
        compute_handshake_hmac("", n, n)


def test_verify_hmac_success():
    n1 = generate_nonce()
    n2 = generate_nonce()
    h = compute_handshake_hmac("k", n1, n2)
    assert verify_handshake_hmac("k", n1, n2, h) is True


def test_verify_hmac_wrong_key():
    n1 = generate_nonce()
    n2 = generate_nonce()
    h = compute_handshake_hmac("k1", n1, n2)
    assert verify_handshake_hmac("k2", n1, n2, h) is False


def test_verify_hmac_wrong_nonce():
    n1 = generate_nonce()
    n2 = generate_nonce()
    other = generate_nonce()
    h = compute_handshake_hmac("k", n1, n2)
    assert verify_handshake_hmac("k", n1, other, h) is False


def test_verify_hmac_garbage_input():
    n1 = generate_nonce()
    n2 = generate_nonce()
    for bad in (None, 123, "", "short", "g" * 64, bytes(32)):
        assert verify_handshake_hmac("k", n1, n2, bad) is False


# ─── Wire format: challenge ──────────────────────────────────────────


def test_challenge_roundtrip():
    p = Protocol("k")
    sn = generate_nonce()
    spid = generate_peer_id()
    wire = p.create_handshake_challenge(sn, spid)
    parsed = p.parse_message(wire[4:])
    assert parsed["type"] == MSG_HANDSHAKE_CHALLENGE
    payload = Protocol.parse_handshake_challenge(parsed["data"])
    assert payload == {
        "server_nonce": sn,
        "server_version": PROTOCOL_VERSION,
        "server_peer_id": spid,
    }


def test_challenge_rejects_invalid_nonce():
    p = Protocol("k")
    with pytest.raises(ValueError):
        p.create_handshake_challenge("not-a-nonce", generate_peer_id())


def test_challenge_rejects_invalid_peer_id():
    """v3.0: 형식 위반 server_peer_id → ValueError."""
    p = Protocol("k")
    with pytest.raises(ValueError):
        p.create_handshake_challenge(generate_nonce(), "not-a-peer-id")


def test_parse_challenge_rejects_garbage():
    assert Protocol.parse_handshake_challenge(None) is None
    assert Protocol.parse_handshake_challenge({}) is None
    assert Protocol.parse_handshake_challenge({"server_nonce": "bad"}) is None
    assert Protocol.parse_handshake_challenge(
        {"server_nonce": generate_nonce(), "server_version": "",
         "server_peer_id": generate_peer_id()}
    ) is None
    # version 너무 김
    assert Protocol.parse_handshake_challenge(
        {"server_nonce": generate_nonce(), "server_version": "x" * 100,
         "server_peer_id": generate_peer_id()}
    ) is None
    # v3.0: server_peer_id 누락 → None (v2.x server hard break)
    assert Protocol.parse_handshake_challenge(
        {"server_nonce": generate_nonce(), "server_version": PROTOCOL_VERSION}
    ) is None
    # v3.0: server_peer_id 형식 위반 → None
    assert Protocol.parse_handshake_challenge(
        {"server_nonce": generate_nonce(), "server_version": PROTOCOL_VERSION,
         "server_peer_id": "bad"}
    ) is None


# ─── Wire format: response ───────────────────────────────────────────


def test_response_roundtrip():
    p = Protocol("k")
    sn = generate_nonce()
    cn = generate_nonce()
    pid = generate_peer_id()
    h = compute_handshake_hmac("k", sn, cn)
    wire = p.create_handshake_response(cn, "MyDevice", h, pid)
    parsed = p.parse_message(wire[4:])
    assert parsed["type"] == MSG_HANDSHAKE_RESPONSE
    payload = Protocol.parse_handshake_response(parsed["data"])
    assert payload == {
        "client_nonce": cn,
        "client_version": PROTOCOL_VERSION,
        "name": "MyDevice",
        "hmac": h,
        "peer_id": pid,
    }


def test_response_rejects_invalid_inputs():
    p = Protocol("k")
    cn = generate_nonce()
    pid = generate_peer_id()
    h = "a" * 64
    with pytest.raises(ValueError):
        p.create_handshake_response("bad-nonce", "name", h, pid)
    with pytest.raises(ValueError):
        p.create_handshake_response(cn, "x" * 300, h, pid)
    with pytest.raises(ValueError):
        p.create_handshake_response(cn, "name", "short", pid)
    # v3.0: 형식 위반 peer_id → ValueError
    with pytest.raises(ValueError):
        p.create_handshake_response(cn, "name", h, "not-a-peer-id")


def test_parse_response_rejects_garbage():
    assert Protocol.parse_handshake_response(None) is None
    assert Protocol.parse_handshake_response({}) is None
    valid = {
        "client_nonce": generate_nonce(),
        "client_version": PROTOCOL_VERSION,
        "name": "dev",
        "hmac": "a" * 64,
        "peer_id": generate_peer_id(),
    }
    assert Protocol.parse_handshake_response(valid) is not None
    # 누락 필드 (v3.0: peer_id 포함)
    for missing in ("client_nonce", "client_version", "name", "hmac", "peer_id"):
        d = dict(valid)
        d.pop(missing)
        assert Protocol.parse_handshake_response(d) is None
    # v3.0: peer_id 형식 위반 → None
    bad_pid = dict(valid)
    bad_pid["peer_id"] = "bad"
    assert Protocol.parse_handshake_response(bad_pid) is None


# ─── Wire format: ack ────────────────────────────────────────────────


def test_ack_roundtrip():
    p = Protocol("k")
    h = "f" * 64
    wire = p.create_handshake_ack(h)
    parsed = p.parse_message(wire[4:])
    assert parsed["type"] == MSG_HANDSHAKE_ACK
    payload = Protocol.parse_handshake_ack(parsed["data"])
    assert payload == {"hmac": h}


def test_ack_rejects_short_hmac():
    p = Protocol("k")
    with pytest.raises(ValueError):
        p.create_handshake_ack("short")


# ─── Version compatibility ────────────────────────────────────────────


@pytest.mark.parametrize("remote,compatible", [
    ("3.0", True),
    ("3.0.0", True),    # patch 차이는 호환 (major.minor 만 비교)
    ("3.0.99", True),
    ("2.2", False),     # v3.0 hard break — v2.x 거부
    ("2.2.0", False),
    ("2.2.99", False),
    ("2.1", False),
    ("2.0.0", False),
    ("3.1", False),     # major.minor 다름
    ("1.9", False),
    ("", False),
    ("garbage", False),
    (None, False),
    (3.0, False),       # not a string
])
def test_version_compatibility(remote, compatible):
    assert Protocol.is_compatible_version(remote) is compatible
