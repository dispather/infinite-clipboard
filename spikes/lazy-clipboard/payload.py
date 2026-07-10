"""스파이크 테스트 페이로드.

INCR(X11) / HGLOBAL(Windows) 경계를 넘기는 크기를 일부러 포함해, 작은 문자열로는
드러나지 않는 chunked serve 경로까지 검증한다.
"""
import os
import struct
import tempfile
import hashlib
import zlib

# 작은 페이로드 — INCR 불필요. 메커니즘(콜백 발화 + 단발 응답) 증명용. 한글 포함(UTF-8 멀티바이트).
TEXT_PAYLOAD = ("infinite-clipboard lazy spike 한글 텍스트 " * 8).encode("utf-8")

# 큰 페이로드 — 512KB. X11 max-request-size / Windows HGLOBAL 경계를 넘겨 INCR/chunked 경로 강제.
BIG_PAYLOAD = os.urandom(512 * 1024)


def make_png_payload(width: int = 600, height: int = 600) -> bytes:
    """stdlib 만으로 유효한 RGB PNG 생성 (Pillow 의존 회피 — 스파이크 CI 최소 deps 유지).

    이미지 lazy 검증(S0)용. 랜덤 픽셀이라 압축이 거의 안 돼 ~1MB → X11 INCR / Windows HGLOBAL
    / Wayland fd write loop 경계를 넘긴다 (작은 이미지로는 안 드러나는 chunked 경로 강제).
    image/png · public.png · 등록 포맷 "PNG" 은 OS 가 opaque 바이트로 다루므로 정확히 round-trip.
    """
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit, color type 2 = RGB
    raw = bytearray()
    for _ in range(height):
        raw += b"\x00" + os.urandom(width * 3)  # 행마다 filter byte 0 + RGB 픽셀
    idat = zlib.compress(bytes(raw), 1)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# 이미지 페이로드 — 실제 유효 PNG (~1MB). 이미지 lazy 메커니즘 검증용.
IMAGE_PAYLOAD = make_png_payload()


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
