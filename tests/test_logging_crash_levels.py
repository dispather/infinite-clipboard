"""에러 로그 (a)(b) 회귀 — 크래시·미처리 예외 기록 + 정상 끊김을 ERROR 에서 내리기.

(a) faulthandler·sys.excepthook·threading.excepthook 이 없어 네이티브 크래시와 스레드
    예외가 로그에 안 남았다(창 모드 번들은 sys.stderr=None). 이제 crash.log + CRITICAL.
(b) 피어 정상 끊김(ECONNRESET·EPIPE·EOF)이 ERROR 의 3/4 을 차지해 진짜 결함을 묻었다.
"""

import errno
import faulthandler
import logging
import os
import subprocess
import sys
import threading

import pytest

from core import network as net
from core.file_transfer import FileTransferManager, TransferSizeExceeded

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def restore_hooks():
    """테스트가 바꾼 전역 훅과 faulthandler 대상을 원래대로 돌린다."""
    import main
    saved = (sys.excepthook, threading.excepthook, faulthandler.is_enabled())
    yield main
    sys.excepthook, threading.excepthook = saved[0], saved[1]
    if main._crash_log_handle is not None:
        faulthandler.disable()
        main._crash_log_handle.close()
        main._crash_log_handle = None
    if saved[2] and sys.__stderr__ is not None:
        faulthandler.enable(file=sys.__stderr__)


def _raise_and_capture():
    try:
        raise RuntimeError("boom-for-test")
    except RuntimeError:
        return sys.exc_info()


def test_excepthook_logs_critical_with_traceback(restore_hooks, tmp_path, caplog):
    main = restore_hooks
    main._install_crash_capture(tmp_path / "crash.log")
    caplog.set_level(logging.DEBUG)

    sys.excepthook(*_raise_and_capture())

    crit = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(crit) == 1
    assert crit[0].exc_info is not None
    assert "boom-for-test" in caplog.text
    assert "Traceback" in caplog.text


def test_thread_exception_logs_critical_with_traceback(restore_hooks, tmp_path, caplog):
    main = restore_hooks
    main._install_crash_capture(tmp_path / "crash.log")
    caplog.set_level(logging.DEBUG)

    def _worker():
        raise ValueError("thread-boom-for-test")

    t = threading.Thread(target=_worker, name="test-worker")
    t.start()
    t.join()

    crit = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(crit) == 1
    assert "test-worker" in crit[0].getMessage()
    assert "thread-boom-for-test" in caplog.text
    assert "Traceback" in caplog.text


def test_faulthandler_targets_crash_log(restore_hooks, tmp_path):
    main = restore_hooks
    crash = tmp_path / "crash.log"
    main._install_crash_capture(crash)
    assert faulthandler.is_enabled()
    assert main._crash_log_handle.name == str(crash)
    assert not main._crash_log_handle.closed


@pytest.mark.skipif(sys.platform == "win32", reason="SIGSEGV 자식 프로세스 판정은 POSIX 기준")
def test_native_crash_lands_in_crash_log(tmp_path):
    """실제 네이티브 크래시를 일으켜 crash.log 에 스택이 남는지 — stderr 없는 창 모드와 같은 조건."""
    crash = tmp_path / "crash.log"
    code = (
        "import sys; sys.stderr = None\n"
        "import main\n"
        f"main._install_crash_capture({str(crash)!r})\n"
        "import faulthandler; faulthandler._sigsegv()\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT,
        capture_output=True, timeout=60,
    )
    assert proc.returncode != 0
    text = crash.read_text(encoding="utf-8", errors="replace")
    assert "Fatal Python error" in text


# ── (b) 정상 끊김 → ERROR 아님 ──────────────────────────────────────────

@pytest.mark.parametrize("exc", [
    ConnectionResetError(errno.ECONNRESET, "reset"),
    BrokenPipeError(errno.EPIPE, "pipe"),
    ConnectionAbortedError(errno.ECONNABORTED, "aborted"),
    OSError(errno.ECONNRESET, "reset as plain OSError"),
    net.PeerClosed("연결 끊김"),
])
def test_peer_disconnect_classified(exc):
    assert net.is_peer_disconnect(exc)


def test_windows_winerror_disconnect_classified():
    e = OSError(0, "WinError 10054")
    e.winerror = 10054
    assert net.is_peer_disconnect(e)


@pytest.mark.parametrize("exc", [
    ValueError("메시지 크기 초과"),
    OSError(errno.EACCES, "permission"),
    ConnectionError("server HMAC verification failed"),
])
def test_real_errors_not_classified_as_disconnect(exc):
    assert not net.is_peer_disconnect(exc)


class _ResetSocket:
    def recv(self, n):
        raise ConnectionResetError(errno.ECONNRESET, "Connection reset by peer")


class _EofSocket:
    def recv(self, n):
        return b""


@pytest.mark.parametrize("sock", [_ResetSocket(), _EofSocket()])
def test_client_receive_disconnect_is_not_error(sock, caplog):
    caplog.set_level(logging.DEBUG, logger="core.network")
    client = net.NetworkClient("127.0.0.1", 1, "k", "test-device")
    client.running = True
    client.socket = sock
    client._receive()
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == []
    assert any(r.levelno == logging.INFO and "연결 끊김" in r.getMessage()
               for r in caplog.records)


def test_size_exceeded_is_typed_and_value_error(tmp_path):
    """크기 초과 거절은 전용 타입 — main 이 WARNING 으로 분기할 수 있고 ValueError 호환 유지."""
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 2048)
    mgr = FileTransferManager(download_path=str(tmp_path / "dl"))
    mgr.max_file_size = 1024
    with pytest.raises(TransferSizeExceeded):
        mgr.collect_metadata([str(f)])
    assert issubclass(TransferSizeExceeded, ValueError)
