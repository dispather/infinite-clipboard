"""스파이크 테스트 페이로드.

INCR(X11) / HGLOBAL(Windows) 경계를 넘기는 크기를 일부러 포함해, 작은 문자열로는
드러나지 않는 chunked serve 경로까지 검증한다.
"""
import os
import tempfile
import hashlib

# 작은 페이로드 — INCR 불필요. 메커니즘(콜백 발화 + 단발 응답) 증명용. 한글 포함(UTF-8 멀티바이트).
TEXT_PAYLOAD = ("infinite-clipboard lazy spike 한글 텍스트 " * 8).encode("utf-8")

# 큰 페이로드 — 512KB. X11 max-request-size / Windows HGLOBAL 경계를 넘겨 INCR/chunked 경로 강제.
BIG_PAYLOAD = os.urandom(512 * 1024)


def write_temp_file(data: bytes, name: str) -> str:
    """파일 기반 포맷(CF_HDROP / file promise / text/uri-list)용 실제 파일 생성.

    반환 경로는 호출자가 정리(임시 디렉토리). 스파이크는 throwaway 라 적극 cleanup 안 함.
    """
    d = tempfile.mkdtemp(prefix="ic_spike_")
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
