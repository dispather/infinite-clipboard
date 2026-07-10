"""v3.0: X11 종단(E2E) — 실 provider + 실 클립보드(xclip) + 실 네트워크.

물리적 2대 PC 없이 가능한 가장 강한 자동 검증. 헤드리스 로컬에선 skip 되고
CI(test.yml Xvfb) 또는 실 X11 세션에서 실행된다. loopback 테스트가 *못 본* 지점 —
**실제 X11LazyProvider 가 orchestration·실 클립보드와 묶여 동작**하는가 — 을 검증한다:

  두 InfiniteClipboard(localhost server+client) →
  server 가 파일 복사(_announce_offer) → MSG_CLIP_OFFER →
  client 가 **실제 X11LazyProvider** 로 CLIPBOARD selection 소유(register_offer) →
  별도 프로세스 `xclip -o -t text/uri-list`(= paste) →
  provider SelectionRequest → _fetch_offer → MSG_CLIP_FETCH → server 전송 →
  client 조립 → provider 가 staged 파일의 file:// URI 제공 → xclip stdout.

localhost 네트워킹은 2-PC 와 동일 코드 경로이고, xclip 의 SelectionRequest 는 파일
매니저 paste 와 동일한 X11 요청이다. 남는 미검증은 (a) 물리적 cross-machine,
(b) macOS Tk run loop — 둘 다 첫 실사용/실기기에서.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import unquote, urlparse

import pytest

_SKIP = None
if sys.platform != "linux":
    _SKIP = "X11 E2E 는 Linux 전용"
elif not os.environ.get("DISPLAY"):
    _SKIP = "DISPLAY 없음 (헤드리스) — CI Xvfb/실 X11 세션에서만"
elif shutil.which("xclip") is None:
    _SKIP = "xclip 미설치"
else:
    try:
        import Xlib  # noqa: F401
    except Exception:
        _SKIP = "python-xlib 미설치"

pytestmark = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")

from config import AppConfig  # noqa: E402
from core.protocol import generate_peer_id  # noqa: E402
from main import InfiniteClipboard  # noqa: E402

_KEY = "e2e-xvfb-shared-secret-key-0123456789ab"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _make_app(mode, port, download_path) -> InfiniteClipboard:
    download_path.mkdir(parents=True, exist_ok=True)
    cfg = AppConfig(
        mode=mode, server_host="127.0.0.1", port=port, auth_key=_KEY,
        peer_id=generate_peer_id(), download_path=str(download_path),
        tailscale_trust=False, bind_address="127.0.0.1",
        # v3.0.5 는 lazy_paste 기본 False(받기 모드) — 이 테스트는 실 X11 lazy
        # provider round-trip 검증이 목적이므로 명시적으로 켠다. grace(기본 2.0s)
        # 는 OS 자동 peek 과 진짜 paste 를 구분하는 가드인데, 이 테스트의 xclip
        # paste 는 등록 직후(<1.3s) 일어나 grace 안에서 매번 거부당하므로 끈다.
        lazy_paste=True, fetch_grace_seconds=0,
    )
    return InfiniteClipboard(cfg)


def _xclip_paste(target: str, timeout: float = 20.0) -> bytes:
    out = subprocess.run(
        ["xclip", "-selection", "clipboard", "-o", "-t", target],
        capture_output=True, timeout=timeout,
    )
    return out.stdout


def test_x11_e2e_file_copy_paste(tmp_path):
    """server 복사 → client 가 실 X11 provider 등록 → xclip paste → 파일 종단 전달."""
    f1 = tmp_path / "e2e.txt"
    f1.write_bytes(b"x11-end-to-end " * 800)  # ~12KB

    port = _free_port()
    server = _make_app("server", port, tmp_path / "s_dl")
    client = _make_app("client", port, tmp_path / "c_dl")
    server._start_server()
    client._start_client()
    try:
        assert _wait_until(lambda: client.client and client.client.connected), "연결 실패"
        assert _wait_until(lambda: len(server.peers) == 1), "peer 학습 실패"

        # server 복사 → offer. client 가 실 X11 provider 로 CLIPBOARD 소유.
        server._announce_offer([str(f1)])
        assert _wait_until(
            lambda: bool(client.received_offers) and client.lazy_provider is not None,
            timeout=5.0,
        ), "client 가 offer 를 실 provider 로 등록하지 못함 (provider None?)"
        time.sleep(0.4)  # selection 소유 안정화

        # paste (별도 프로세스) — 한 번 비면 재시도 (X11 타이밍)
        uris = b""
        for _ in range(3):
            uris = _xclip_paste("text/uri-list")
            if b"file://" in uris:
                break
            time.sleep(0.3)
        text = uris.decode("utf-8", "replace")
        assert "file://" in text, f"paste 결과에 file:// 없음: {text!r}"

        line = next(l for l in text.replace("\r", "").split("\n") if l.startswith("file://"))
        path = unquote(urlparse(line).path)
        assert os.path.exists(path), f"staged 파일 없음: {path}"
        assert open(path, "rb").read() == f1.read_bytes(), "종단 전달 내용 불일치"
    finally:
        client.stop()
        server.stop()
