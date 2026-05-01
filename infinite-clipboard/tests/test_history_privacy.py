"""v2.2 R1: history privacy — 민감 패턴 감지 회귀 테스트."""

import pytest

from core.privacy import detect_sensitive_kind, is_sensitive_text


# ── 명확한 민감 패턴은 감지되어야 ─────────────────────────────────────


@pytest.mark.parametrize("text,expected_kind", [
    # JWT
    ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
     "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0."
     "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c", "jwt"),
    # AWS Access Key (예시 형식)
    ("AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
    # PEM block
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...", "pem_block"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAA...", "pem_block"),
    ("-----BEGIN CERTIFICATE-----\nMIID...", "pem_block"),
    # GitHub PAT
    ("ghp_abcdefghij1234567890abcdefghij1234567890", "github_token"),
    ("ghs_abcdefghij1234567890abcdefghij1234567890", "github_token"),
    # Google API key (정확히 AIza + 35자 = 39자)
    ("AIzaSyAbcdef1234567890abcdef1234567890a", "google_api_key"),
    # Slack token
    ("xoxb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx", "slack_token"),
    # Stripe live secret key
    ("sk_live_abcdefghij1234567890ABCDEFGHIJ", "stripe_live_key"),
])
def test_known_sensitive_patterns_detected(text, expected_kind):
    assert detect_sensitive_kind(text) == expected_kind
    assert is_sensitive_text(text) is True


# ── 일반 텍스트는 통과해야 (false positive 없음) ─────────────────────


@pytest.mark.parametrize("text", [
    "안녕하세요",
    "Hello world",
    "https://example.com/path?query=value",
    "1234567890",
    "abcdefghij" * 5,
    "이것은 한국어 클립보드 내용입니다.",
    "{'key': 'value'}",
    "package.json 의 dependencies 갱신 완료",
    "",
    "abc",
    # short tokens 형식 비슷하지만 명확한 prefix 없음
    "short-not-a-jwt",
    "AKI" + "B" * 16,  # AKIA prefix 가 아니라 안 잡힘
])
def test_normal_text_passes(text):
    assert detect_sensitive_kind(text) == ""
    assert is_sensitive_text(text) is False


# ── 비-string 입력 안전 처리 ─────────────────────────────────────────


@pytest.mark.parametrize("value", [None, 123, [], {}, 1.5])
def test_non_string_safe(value):
    assert detect_sensitive_kind(value) == ""
    assert is_sensitive_text(value) is False


# ── 민감 패턴이 긴 텍스트 안에 묻혀 있어도 감지 ─────────────────────


def test_jwt_inside_paragraph():
    text = (
        "Here is a sample JWT for testing: "
        "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiYWxpY2UifQ.signature_here_xxxx "
        "Please don't paste this anywhere."
    )
    assert detect_sensitive_kind(text) == "jwt"


def test_pem_inside_log_dump():
    text = "Error: failed to read key file\n-----BEGIN PRIVATE KEY-----\nMII..."
    assert detect_sensitive_kind(text) == "pem_block"
