"""v3.0 S2b: macOS lazy 백엔드 round-trip 테스트.

**실제 클립보드 round-trip** — 이 개발 환경(Linux)에선 skip 되고, CI(macos-14)
또는 실제 macOS 에서 실행된다 (.github/workflows/test.yml 의 macos 잡).

검증: MacLazyProvider 가 NSPasteboard 를 지연 소유(setDataProvider:forTypes:) → 별도
프로세스(_mac_paste_helper.py)가 dataForType:/readObjectsForClasses: 로 paste 할 때
provideDataForType 콜백 발화 → fetch_callback 이 준 바이트를 그 UTI 로 제공.
  - 이미지: public.png (raw 바이트, S0 PASS)
  - 파일: public.file-url (스테이징 경로 → file:// URL, data-lazy)
  - 대용량(~1MB): run loop 펌핑 하에 data provide

provideDataForType 콜백은 백그라운드 런루프 스레드에서 펌핑된다 — 이 잡이 그 프로덕션
스레딩 모델(메인 스레드 아닌 곳에서 콜백 발화)을 CI 에서 처음 실증한다.
"""

import os
import subprocess
import sys
import tempfile
import time
import uuid

import pytest

_SKIP_REASON = None
if sys.platform != "darwin":
    _SKIP_REASON = "macOS 백엔드는 macOS 전용"
else:
    try:
        import objc  # noqa: F401
        from AppKit import NSPasteboard  # noqa: F401
    except Exception:
        _SKIP_REASON = "pyobjc 미설치"

pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")

_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_mac_paste_helper.py")


def _make_offer(kind: str, items, total_size: int) -> dict:
    return {
        "offer_id": str(uuid.uuid4()),
        "source_peer": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "kind": kind,
        "items": items,
        "total_size": total_size,
        "created_at": 1.0,
    }


def _paste(kind: str, timeout: float = 15.0) -> bytes:
    """별도 프로세스로 paste 하며 **메인 스레드 run loop 를 펌핑**(콜백 발화 조건).

    provideDataForType 콜백은 메인 run loop 에서 발화하므로, paster 가 읽는 동안 이
    테스트(메인 스레드)가 run loop 를 펌핑해야 한다(실앱은 Tk mainloop 가 대신). paster 는
    raw 바이트를 temp 파일에 써서 stdout 파이프 데드락을 피한다.
    """
    from Foundation import NSRunLoop, NSDate, NSDefaultRunLoopMode

    fd, outpath = tempfile.mkstemp(prefix="ic_mac_test_")
    os.close(fd)
    proc = subprocess.Popen(
        [sys.executable, _HELPER, outpath, kind],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        rl = NSRunLoop.currentRunLoop()
        deadline = time.monotonic() + timeout
        while proc.poll() is None and time.monotonic() < deadline:
            rl.runMode_beforeDate_(
                NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05),
            )
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
            raise AssertionError("paster timeout — provideDataForType 미발화")
        _o, err = proc.communicate()
        if proc.returncode != 0:
            raise AssertionError(
                f"paster rc={proc.returncode}: {(err or b'').decode('utf-8', 'replace')[:200]}"
            )
        with open(outpath, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(outpath)
        except OSError:
            pass


def test_mac_image_roundtrip():
    """이미지 offer → public.png paste 시 fetch 바이트 일치 + lazy (fetch 는 paste 후)."""
    from core.lazy_mac import MacLazyProvider
    from core.lazy_clipboard import FetchedContent, KIND_IMAGE

    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\x03" * 500
    fetched_at = {"t": None}

    def fetch(offer_id):
        fetched_at["t"] = time.monotonic()
        return FetchedContent(kind=KIND_IMAGE, data=png)

    prov = MacLazyProvider()
    try:
        offer = _make_offer("image", [{"name": "c.png", "size": len(png), "hash": ""}], len(png))
        t_reg = time.monotonic()
        assert prov.register_offer(offer, fetch) is True, "NSPasteboard 등록 실패"
        time.sleep(0.05)
        got = _paste("png")
        assert got == png, f"바이트 불일치: got={len(got)} want={len(png)}"
        assert fetched_at["t"] is not None and fetched_at["t"] >= t_reg
    finally:
        prov.stop()


def test_mac_file_url_roundtrip(tmp_path):
    """파일 offer → public.file-url paste 시 스테이징 경로가 NSURL 로 복원."""
    from core.lazy_mac import MacLazyProvider
    from core.lazy_clipboard import FetchedContent, KIND_FILE

    f1 = tmp_path / "a.txt"; f1.write_text("A")
    f2 = tmp_path / "b.txt"; f2.write_text("B")
    paths = [str(f1), str(f2)]

    def fetch(offer_id):
        return FetchedContent(kind=KIND_FILE, paths=paths)

    prov = MacLazyProvider()
    try:
        offer = _make_offer(
            "file",
            [{"name": "a.txt", "size": 1, "hash": ""}, {"name": "b.txt", "size": 1, "hash": ""}],
            2,
        )
        assert prov.register_offer(offer, fetch) is True
        time.sleep(0.05)
        got = _paste("fileurl").decode("utf-8")
        got_names = {os.path.basename(p) for p in got.splitlines() if p.strip()}
        assert got_names == {"a.txt", "b.txt"}, f"경로 불일치: {got!r}"
    finally:
        prov.stop()


def test_mac_large_payload():
    """대용량(~1MB) 이미지 data provide — run loop 펌핑 하 바이트 일치."""
    from core.lazy_mac import MacLazyProvider
    from core.lazy_clipboard import FetchedContent, KIND_IMAGE

    big = (b"\x89PNG" + bytes(range(256)) * 4096)  # ~1MB

    def fetch(offer_id):
        return FetchedContent(kind=KIND_IMAGE, data=big)

    prov = MacLazyProvider()
    try:
        offer = _make_offer("image", [{"name": "big.png", "size": len(big), "hash": ""}], len(big))
        assert prov.register_offer(offer, fetch) is True
        time.sleep(0.05)
        got = _paste("png")
        assert got == big, f"대용량 불일치: got={len(got)} want={len(big)}"
    finally:
        prov.stop()


def test_mac_is_supported():
    """is_supported 정적 확인."""
    from core.lazy_mac import MacLazyProvider
    prov = MacLazyProvider()
    try:
        assert prov.is_supported("image") is True
        assert prov.is_supported("file") is True
        assert prov.is_supported("text") is False
    finally:
        prov.stop()
