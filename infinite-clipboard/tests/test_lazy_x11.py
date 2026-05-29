"""v3.0 S2b: X11 lazy 백엔드 round-trip 테스트.

**실제 클립보드 round-trip** — 이 개발 환경(헤드리스)에선 skip 되고, CI(Xvfb)
또는 실제 X 세션에서 실행된다 (.github/workflows/test.yml 의 Linux Xvfb 잡).

검증: X11LazyProvider 가 CLIPBOARD 를 소유 → 별도 프로세스(xclip)가 paste 할 때
SelectionRequest 로 콜백 발화 → fetch_callback 이 준 바이트를 그 target 으로 서빙.
  - 이미지: image/png 단발
  - 파일: text/uri-list (스테이징 경로 → file:// URI)
  - 대용량: INCR 청크 (>max-request-size)
"""

import shutil
import subprocess
import sys
import time
import uuid

import pytest

# Linux + DISPLAY + xclip + python-xlib 없으면 전체 skip (헤드리스 로컬 보호)
import os

_SKIP_REASON = None
if sys.platform != "linux":
    _SKIP_REASON = "X11 백엔드는 Linux 전용"
elif not os.environ.get("DISPLAY"):
    _SKIP_REASON = "DISPLAY 없음 (헤드리스) — CI Xvfb/실 세션에서만"
elif shutil.which("xclip") is None:
    _SKIP_REASON = "xclip 미설치"
else:
    try:
        import Xlib  # noqa: F401
    except Exception:
        _SKIP_REASON = "python-xlib 미설치"

pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


def _make_offer(kind: str, items, total_size: int) -> dict:
    return {
        "offer_id": str(uuid.uuid4()),
        "source_peer": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "kind": kind,
        "items": items,
        "total_size": total_size,
        "created_at": 1.0,
    }


def _xclip_paste(target: str, timeout: float = 10.0) -> bytes:
    """별도 프로세스(xclip)로 CLIPBOARD 읽기 = paste."""
    out = subprocess.run(
        ["xclip", "-selection", "clipboard", "-o", "-t", target],
        capture_output=True, timeout=timeout,
    )
    return out.stdout


def test_x11_image_roundtrip():
    """이미지 offer → image/png paste 시 fetch 바이트 일치 + lazy (fetch 는 paste 후)."""
    from core.lazy_x11 import X11LazyProvider

    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\x03" * 500  # 임의 바이너리
    fetched_at = {"t": None}

    def fetch(offer_id):
        from core.lazy_clipboard import FetchedContent, KIND_IMAGE
        fetched_at["t"] = time.monotonic()
        return FetchedContent(kind=KIND_IMAGE, data=png)

    prov = X11LazyProvider()
    try:
        offer = _make_offer("image", [{"name": "c.png", "size": len(png), "hash": ""}], len(png))
        t_reg = time.monotonic()
        assert prov.register_offer(offer, fetch) is True, "CLIPBOARD 소유 실패"
        time.sleep(0.05)
        got = _xclip_paste("image/png")
        assert got == png, f"바이트 불일치: got={len(got)} want={len(png)}"
        # laziness: fetch 는 register 이후(=paste 시점)에 일어났어야
        assert fetched_at["t"] is not None and fetched_at["t"] >= t_reg
    finally:
        prov.stop()


def test_x11_file_uri_list_roundtrip(tmp_path):
    """파일 offer → text/uri-list paste 시 스테이징 경로의 file:// URI 서빙."""
    from core.lazy_x11 import X11LazyProvider
    from core.lazy_clipboard import FetchedContent, KIND_FILE

    f1 = tmp_path / "a.txt"; f1.write_text("A")
    f2 = tmp_path / "b.txt"; f2.write_text("B")
    paths = [str(f1), str(f2)]

    def fetch(offer_id):
        return FetchedContent(kind=KIND_FILE, paths=paths)

    prov = X11LazyProvider()
    try:
        offer = _make_offer(
            "file",
            [{"name": "a.txt", "size": 1, "hash": ""}, {"name": "b.txt", "size": 1, "hash": ""}],
            2,
        )
        assert prov.register_offer(offer, fetch) is True
        time.sleep(0.05)
        got = _xclip_paste("text/uri-list").decode("utf-8")
        assert "file://" in got
        assert got.count("file://") == 2
        assert f1.name in got and f2.name in got
    finally:
        prov.stop()


def test_x11_incr_large_payload():
    """대용량(>64KB INCR 청크 + max-request 초과 가능) 이미지도 바이트 일치."""
    from core.lazy_x11 import X11LazyProvider
    from core.lazy_clipboard import FetchedContent, KIND_IMAGE

    big = (b"\x89PNG" + bytes(range(256)) * 4096)  # ~1MB

    def fetch(offer_id):
        return FetchedContent(kind=KIND_IMAGE, data=big)

    prov = X11LazyProvider()
    try:
        offer = _make_offer("image", [{"name": "big.png", "size": len(big), "hash": ""}], len(big))
        assert prov.register_offer(offer, fetch) is True
        time.sleep(0.05)
        got = _xclip_paste("image/png")
        assert got == big, f"대용량 불일치: got={len(got)} want={len(big)}"
    finally:
        prov.stop()


def test_x11_is_supported():
    """import 만으로 is_supported 확인 (소유권 무관 — DISPLAY 있으면 동작)."""
    from core.lazy_x11 import X11LazyProvider
    prov = X11LazyProvider()
    try:
        assert prov.is_supported("image") is True
        assert prov.is_supported("file") is True
        assert prov.is_supported("text") is False
    finally:
        prov.stop()
