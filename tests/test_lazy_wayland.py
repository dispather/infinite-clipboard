"""v3.0 S2b: Wayland lazy 백엔드 round-trip 테스트.

**실제 클립보드 round-trip** — 이 개발 환경(헤드리스)에선 skip 되고, CI(headless
sway, ext-data-control XML 존재 시) 또는 실제 Wayland 세션(kwin 등)에서 실행된다
(.github/workflows/test.yml 의 linux-wayland 잡).

검증: WaylandLazyProvider 가 ext-data-control selection 을 소유 → 별도 프로세스
(wl-paste)가 paste 할 때 data source 의 send(mime, fd) 콜백 발화 → fetch_callback 이
준 바이트를 그 mime 으로 fd 에 서빙.
  - 이미지: image/png
  - 파일: text/uri-list (스테이징 경로 → file:// URI)
  - 대용량: 파이프 fd write (Wayland 는 INCR 불필요)

ext-data-control-v1 미광고 컴포지터(구버전 sway/zwlr 전용)면 register_offer 가 False →
환경 차이로 skip (false FAIL 회피 — 스파이크 NEEDS-MANUAL 철학과 동일).
"""

import os
import shutil
import subprocess
import sys
import time
import uuid

import pytest

# Linux + WAYLAND_DISPLAY + wl-paste + pywayland + _wayland_proto 없으면 전체 skip
_SKIP_REASON = None
if sys.platform != "linux":
    _SKIP_REASON = "Wayland 백엔드는 Linux 전용"
elif not os.environ.get("WAYLAND_DISPLAY"):
    _SKIP_REASON = "WAYLAND_DISPLAY 없음 (헤드리스/X11) — CI sway/실 세션에서만"
elif shutil.which("wl-paste") is None:
    _SKIP_REASON = "wl-clipboard(wl-paste) 미설치"
else:
    try:
        import pywayland  # noqa: F401
        from core._wayland_proto.ext_data_control_v1 import (  # noqa: F401
            ExtDataControlManagerV1,
        )
    except Exception:
        _SKIP_REASON = "pywayland/_wayland_proto 미설치"

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


def _wl_paste(mime: str, timeout: float = 10.0) -> bytes:
    """별도 프로세스(wl-paste)로 selection 읽기 = paste."""
    out = subprocess.run(
        ["wl-paste", "-t", mime],
        capture_output=True, timeout=timeout,
    )
    return out.stdout


def _register_or_skip(prov, offer, fetch) -> None:
    """register_offer 시도 — False 면 ext-data-control 미광고로 보고 skip (환경 차이)."""
    if prov.register_offer(offer, fetch) is not True:
        pytest.skip("ext-data-control-v1 미광고/소유 실패 — 컴포지터 환경 차이 (실 kwin 은 PASS)")


def test_wayland_image_roundtrip():
    """이미지 offer → image/png paste 시 fetch 바이트 일치 + lazy (fetch 는 paste 후)."""
    from core.lazy_wayland import WaylandLazyProvider
    from core.lazy_clipboard import FetchedContent, KIND_IMAGE

    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\x03" * 500  # 임의 바이너리
    fetched_at = {"t": None}

    def fetch(offer_id):
        fetched_at["t"] = time.monotonic()
        return FetchedContent(kind=KIND_IMAGE, data=png)

    prov = WaylandLazyProvider()
    try:
        offer = _make_offer("image", [{"name": "c.png", "size": len(png), "hash": ""}], len(png))
        t_reg = time.monotonic()
        _register_or_skip(prov, offer, fetch)
        time.sleep(0.05)
        got = _wl_paste("image/png")
        assert got == png, f"바이트 불일치: got={len(got)} want={len(png)}"
        # laziness: fetch 는 register 이후(=paste 시점)에 일어났어야
        assert fetched_at["t"] is not None and fetched_at["t"] >= t_reg
    finally:
        prov.stop()


def test_wayland_file_uri_list_roundtrip(tmp_path):
    """파일 offer → text/uri-list paste 시 스테이징 경로의 file:// URI 서빙."""
    from core.lazy_wayland import WaylandLazyProvider
    from core.lazy_clipboard import FetchedContent, KIND_FILE

    f1 = tmp_path / "a.txt"; f1.write_text("A")
    f2 = tmp_path / "b.txt"; f2.write_text("B")
    paths = [str(f1), str(f2)]

    def fetch(offer_id):
        return FetchedContent(kind=KIND_FILE, paths=paths)

    prov = WaylandLazyProvider()
    try:
        offer = _make_offer(
            "file",
            [{"name": "a.txt", "size": 1, "hash": ""}, {"name": "b.txt", "size": 1, "hash": ""}],
            2,
        )
        _register_or_skip(prov, offer, fetch)
        time.sleep(0.05)
        got = _wl_paste("text/uri-list").decode("utf-8")
        assert "file://" in got
        assert got.count("file://") == 2
        assert f1.name in got and f2.name in got
    finally:
        prov.stop()


def test_wayland_large_payload():
    """대용량(~1MB) 이미지도 fd write 파이프로 바이트 일치 (Wayland INCR 불필요)."""
    from core.lazy_wayland import WaylandLazyProvider
    from core.lazy_clipboard import FetchedContent, KIND_IMAGE

    big = (b"\x89PNG" + bytes(range(256)) * 4096)  # ~1MB

    def fetch(offer_id):
        return FetchedContent(kind=KIND_IMAGE, data=big)

    prov = WaylandLazyProvider()
    try:
        offer = _make_offer("image", [{"name": "big.png", "size": len(big), "hash": ""}], len(big))
        _register_or_skip(prov, offer, fetch)
        time.sleep(0.05)
        got = _wl_paste("image/png")
        assert got == big, f"대용량 불일치: got={len(got)} want={len(big)}"
    finally:
        prov.stop()


def test_wayland_kde_hint_suppresses_fetch(tmp_path):
    """KDE privacy 힌트 회귀: x-kde-passwordManagerHint 가 offer 에 포함되고, 그 mime 만
    읽으면 fetch 없이 'secret' 를 서빙한다 (Klipper peek 이 전송을 트리거 안 하게).
    진짜 데이터 mime(text/uri-list)을 읽을 때만 fetch 가 발화한다."""
    from core.lazy_wayland import WaylandLazyProvider
    from core.lazy_clipboard import FetchedContent, KIND_FILE

    f1 = tmp_path / "a.txt"; f1.write_text("A")
    calls = {"n": 0}

    def fetch(offer_id):
        calls["n"] += 1
        return FetchedContent(kind=KIND_FILE, paths=[str(f1)])

    prov = WaylandLazyProvider()
    try:
        offer = _make_offer("file", [{"name": "a.txt", "size": 1, "hash": ""}], 1)
        _register_or_skip(prov, offer, fetch)
        time.sleep(0.05)
        # 힌트 mime 이 offer 목록에 노출되는가
        listed = subprocess.run(
            ["wl-paste", "-l"], capture_output=True, timeout=10,
        ).stdout.decode("utf-8", "replace")
        assert "x-kde-passwordManagerHint" in listed, f"힌트 mime 미노출: {listed!r}"
        # 힌트만 읽으면 'secret' + fetch 호출 0 (= Klipper peek 이 전송 안 함)
        assert _wl_paste("x-kde-passwordManagerHint") == b"secret"
        assert calls["n"] == 0, "힌트 read 가 fetch 를 트리거하면 안 됨"
        # 진짜 데이터 mime 을 읽으면 그제서야 fetch (정상 paste 는 동작)
        uri = _wl_paste("text/uri-list").decode("utf-8")
        assert "file://" in uri
        assert calls["n"] == 1, "데이터 mime read 는 fetch 1회를 발화해야"
    finally:
        prov.stop()


def test_wayland_is_supported():
    """is_supported 정적 확인 (selection 소유 무관 — WAYLAND_DISPLAY 연결만 필요)."""
    from core.lazy_wayland import WaylandLazyProvider
    prov = WaylandLazyProvider()
    try:
        assert prov.is_supported("image") is True
        assert prov.is_supported("file") is True
        assert prov.is_supported("text") is False
    finally:
        prov.stop()
