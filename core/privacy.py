"""
Privacy / sensitive content 감지 (v2.2 R1).

main.py 의 history 저장 전 호출되어 명확한 토큰 / 키 / PEM 등이 포함된
텍스트 클립보드를 history 에서 제외한다. 텍스트 동기화 자체에는 영향 없음
(클립보드는 정상 전송, history JSON 에만 저장 안 함).

설계 원칙:
- 명확한 패턴만 (false positive 회피). JWT, AWS Access Key, PEM 헤더,
  GitHub/Google/Slack 토큰 등 형식이 고유한 케이스만 감지.
- 광범위한 "password=..." 패턴은 의도적 제외 (false positive 큼).
- 사용자가 history_privacy_mode=False 로 끄면 모든 텍스트 history 저장.
"""

import re
from typing import Pattern, Tuple


# ── 민감 패턴 ───────────────────────────────────────────────────────────
# 각 패턴은 (이름, 정규식). 이름은 로그/디버그용.
_SENSITIVE_PATTERNS: Tuple[Tuple[str, Pattern[str]], ...] = (
    # JSON Web Token (header.payload.signature)
    ("jwt",
     re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*")),

    # AWS Access Key ID
    ("aws_access_key",
     re.compile(r"\bAKIA[0-9A-Z]{16}\b")),

    # PEM-encoded private key / certificate
    ("pem_block",
     re.compile(r"-----BEGIN [A-Z ]+-----")),

    # GitHub personal access token (ghp_, gho_, ghu_, ghs_, ghr_)
    ("github_token",
     re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),

    # Google API key
    ("google_api_key",
     re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),

    # Slack token (xoxa-, xoxb-, xoxp-, xoxr-, xoxs-)
    ("slack_token",
     re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),

    # Stripe live key
    ("stripe_live_key",
     re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b")),

    # Generic SSH/PGP private key marker (위 PEM 으로도 잡히지만 명시적 fallback)
    ("ssh_private",
     re.compile(r"-----BEGIN OPENSSH PRIVATE KEY-----")),
)


def detect_sensitive_kind(text: str) -> str:
    """text 안의 민감 패턴 유형을 반환. 없으면 빈 문자열.

    Returns:
        예: "jwt", "aws_access_key", "" (감지 안 됨)
    """
    if not isinstance(text, str) or not text:
        return ""
    for name, pat in _SENSITIVE_PATTERNS:
        if pat.search(text):
            return name
    return ""


def is_sensitive_text(text: str) -> bool:
    """text 가 민감 패턴 1 개 이상 포함하면 True."""
    return bool(detect_sensitive_kind(text))
