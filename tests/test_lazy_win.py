"""v3.0 S2b: Windows lazy 백엔드 round-trip 테스트.

**실제 클립보드 round-trip** — 이 개발 환경(Linux)에선 skip 되고, CI(windows-latest)
또는 실제 Windows 에서 실행된다 (.github/workflows/test.yml 의 windows 잡).

검증: WindowsLazyProvider 가 클립보드를 지연 소유(SetClipboardData(fmt, NULL)) → 별도
프로세스(_win_paste_helper.py)가 GetClipboardData 로 paste 할 때 WM_RENDERFORMAT 콜백
발화 → fetch_callback 이 준 바이트를 그 포맷으로 제공.
  - 이미지: 등록 "PNG" 포맷 (raw PNG 바이트)
  - 파일: CF_HDROP (DROPFILES → 경로, pywin32 DragQueryFile 가 파싱)
  - **대용량(~1MB)**: S0 미결(thin 스파이크 PumpWaitingMessages 폴링에서 NULL)이
    프로덕션 블로킹 PumpMessages 루프에서 해소되는지 검증하는 핵심 테스트.
"""

import base64
import os
import subprocess
import sys
import time
import uuid

import pytest

_SKIP_REASON = None
if sys.platform != "win32":
    _SKIP_REASON = "Windows 백엔드는 Windows 전용"
else:
    try:
        import win32clipboard  # noqa: F401
        import win32con  # noqa: F401
        import win32gui  # noqa: F401
    except Exception:
        _SKIP_REASON = "pywin32 미설치"

pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")

_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_win_paste_helper.py")


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
    """별도 프로세스로 paste — WM_RENDERFORMAT cross-process 유발."""
    out = subprocess.run(
        [sys.executable, _HELPER, kind],
        capture_output=True, timeout=timeout, text=True,
    )
    if out.returncode != 0:
        raise AssertionError(f"paster rc={out.returncode}: {out.stderr.strip()[:200]}")
    return (out.stdout or "").strip().encode("ascii")


def test_win_image_roundtrip():
    """이미지 offer → 등록 PNG paste 시 fetch 바이트 일치 + lazy (fetch 는 paste 후)."""
    from core.lazy_win import WindowsLazyProvider
    from core.lazy_clipboard import FetchedContent, KIND_IMAGE

    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\x03" * 500
    fetched_at = {"t": None}

    def fetch(offer_id):
        fetched_at["t"] = time.monotonic()
        return FetchedContent(kind=KIND_IMAGE, data=png)

    prov = WindowsLazyProvider()
    try:
        offer = _make_offer("image", [{"name": "c.png", "size": len(png), "hash": ""}], len(png))
        t_reg = time.monotonic()
        assert prov.register_offer(offer, fetch) is True, "클립보드 지연 등록 실패"
        time.sleep(0.05)
        got = base64.b64decode(_paste("png"))
        # GlobalSize 라운딩으로 trailing 패딩 가능 → 앞부분 정확 일치 검사
        assert got[:len(png)] == png, f"바이트 불일치: got={len(got)} want={len(png)}"
        assert fetched_at["t"] is not None and fetched_at["t"] >= t_reg
    finally:
        prov.stop()


def test_win_file_hdrop_roundtrip(tmp_path):
    """파일 offer → CF_HDROP paste 시 DROPFILES 가 경로로 파싱(shell DragQueryFile)."""
    from core.lazy_win import WindowsLazyProvider
    from core.lazy_clipboard import FetchedContent, KIND_FILE

    f1 = tmp_path / "a.txt"; f1.write_text("A")
    f2 = tmp_path / "b.txt"; f2.write_text("B")
    paths = [str(f1), str(f2)]

    def fetch(offer_id):
        return FetchedContent(kind=KIND_FILE, paths=paths)

    prov = WindowsLazyProvider()
    try:
        offer = _make_offer(
            "file",
            [{"name": "a.txt", "size": 1, "hash": ""}, {"name": "b.txt", "size": 1, "hash": ""}],
            2,
        )
        assert prov.register_offer(offer, fetch) is True
        time.sleep(0.05)
        got = _paste("hdrop").decode("ascii")
        got_files = [line.strip() for line in got.splitlines() if line.strip()]
        assert len(got_files) == 2, f"경로 수 불일치: {got_files}"
        # 대소문자/구분자 정규화 후 basename 비교 (shell 이 정규화할 수 있음)
        got_names = {os.path.basename(p).lower() for p in got_files}
        assert got_names == {"a.txt", "b.txt"}, f"경로 불일치: {got_files}"
    finally:
        prov.stop()


def test_win_large_payload():
    """대용량(~1MB) 이미지 delayed render — S0 미결(NULL) 가설 검증 (블로킹 루프)."""
    from core.lazy_win import WindowsLazyProvider
    from core.lazy_clipboard import FetchedContent, KIND_IMAGE

    big = (b"\x89PNG" + bytes(range(256)) * 4096)  # ~1MB

    def fetch(offer_id):
        return FetchedContent(kind=KIND_IMAGE, data=big)

    prov = WindowsLazyProvider()
    try:
        offer = _make_offer("image", [{"name": "big.png", "size": len(big), "hash": ""}], len(big))
        assert prov.register_offer(offer, fetch) is True
        time.sleep(0.05)
        got = base64.b64decode(_paste("png"))
        assert got[:len(big)] == big, f"대용량 불일치: got={len(got)} want={len(big)}"
    finally:
        prov.stop()


def test_win_is_supported():
    """is_supported 정적 확인 (창/소유 무관)."""
    from core.lazy_win import WindowsLazyProvider
    prov = WindowsLazyProvider()
    try:
        assert prov.is_supported("image") is True
        assert prov.is_supported("file") is True
        assert prov.is_supported("text") is False
    finally:
        prov.stop()


def test_win_open_clipboard_retry(monkeypatch):
    """OpenClipboard 가 일시적으로 ACCESS_DENIED(5) 여도 재시도로 등록 성공.

    2026-05-31 실사용 로그의 핵심 버그 회귀 — 단 한 번의 일시 경합으로 offer 가
    받기-fallback 으로 새던 문제. OpenClipboard 를 처음 2회 거부시키고, 재시도로
    결국 등록(ok=True)되는지 + owns_clipboard 가 등록/clear 를 반영하는지 검증.
    """
    import core.lazy_win as lw
    from core.lazy_win import WindowsLazyProvider
    from core.lazy_clipboard import FetchedContent, KIND_IMAGE

    real_open = lw.win32clipboard.OpenClipboard
    calls = {"n": 0}

    def flaky_open(hwnd=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            # pywintypes.error 와 동형 — (code, fn, msg). _open_clipboard_retry 가 잡고 재시도.
            raise Exception((5, "OpenClipboard", "액세스가 거부되었습니다."))
        return real_open(hwnd) if hwnd is not None else real_open()

    monkeypatch.setattr(lw.win32clipboard, "OpenClipboard", flaky_open)

    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x01" * 200
    prov = WindowsLazyProvider()
    try:
        offer = _make_offer("image", [{"name": "c.png", "size": len(png), "hash": ""}], len(png))
        ok = prov.register_offer(offer, lambda oid: FetchedContent(kind=KIND_IMAGE, data=png))
        assert ok is True, "재시도로 지연 등록이 성공해야 함 (fallback 회피)"
        assert calls["n"] >= 3, f"OpenClipboard 재시도가 실제로 일어나야 함 (n={calls['n']})"
        assert prov.owns_clipboard() is True, "등록 후 클립보드 소유 중이어야 함"
        prov.clear()
        assert prov.owns_clipboard() is False, "clear 후 소유 아님"
    finally:
        prov.stop()
