"""감사 Critical #7: 클립보드 쓰기 subprocess returncode 확인 회귀 테스트.

과거엔 pbcopy/wl-copy/xclip/xsel/uri-list 쓰기 경로가 모두 process.communicate()
결과와 무관하게 무조건 True 를 반환해, 문서화된 폴백 체인(wl-copy→xclip/xsel→
Klipper)이 실제로는 절대 타지 않는 죽은 코드였다. 실제 OS 클립보드 도구를
호출하지 않도록 subprocess.Popen 을 모두 mock 한다.
"""

import shutil
import subprocess

from core import clipboard_manager as cm


class _FakeProcess:
    """subprocess.Popen 을 대체하는 더미 — returncode/timeout 시나리오를 제어한다."""

    def __init__(self, returncode=0, raise_timeout=False):
        self.returncode = returncode
        self._raise_timeout = raise_timeout
        self.killed = False

    def communicate(self, payload=None, timeout=None):
        if self._raise_timeout and timeout is not None:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        return (b"", b"")

    def kill(self):
        self.killed = True


# ── _write_via_subprocess 단위 테스트 ──────────────────────────────────────

def test_write_via_subprocess_success(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _FakeProcess(returncode=0))
    assert cm._write_via_subprocess(["fake-tool"], b"payload") is True


def test_write_via_subprocess_nonzero_returncode_reports_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _FakeProcess(returncode=1))
    assert cm._write_via_subprocess(["fake-tool"], b"payload") is False


def test_write_via_subprocess_timeout_reports_failure_and_kills(monkeypatch):
    proc = _FakeProcess(raise_timeout=True)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
    assert cm._write_via_subprocess(["fake-tool"], b"payload", timeout=0.01) is False
    assert proc.killed is True


# ── MacClipboard.set_content (pbcopy) ──────────────────────────────────────

def test_mac_pbcopy_failure_returns_false(monkeypatch):
    """과거엔 pbcopy 가 비정상 종료해도 항상 True 를 반환했다."""
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _FakeProcess(returncode=1))
    handler = cm.MacClipboard()
    assert handler.set_content("text", "hello") is False


def test_mac_pbcopy_success_returns_true(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _FakeProcess(returncode=0))
    handler = cm.MacClipboard()
    assert handler.set_content("text", "hello") is True


# ── LinuxClipboard.set_content (wl-copy → xclip/xsel → Klipper 폴백 체인) ──

def _make_linux_handler(monkeypatch, tool="xclip"):
    """실제 시스템 프로세스/시그널 없이 LinuxClipboard 인스턴스를 만든다."""
    monkeypatch.setattr(cm.LinuxClipboard, "_init_klipper_dbus", lambda self: None)
    monkeypatch.setattr(cm.LinuxClipboard, "_init_cli_tools", lambda self: setattr(self, "tool", tool))
    monkeypatch.setattr(cm.LinuxClipboard, "_start_watch", lambda self: None)
    return cm.LinuxClipboard()


def test_linux_wlcopy_failure_falls_back_to_xclip(monkeypatch):
    """과거엔 wl-copy 가 실패해도 그 자리에서 True 를 반환해 xclip 폴백이 죽은
    코드였다. 이제 실패 시 실제로 xclip 으로 넘어가야 한다."""
    calls = []

    def fake_popen(cmd, **kw):
        calls.append(cmd[0])
        if cmd[0] == "wl-copy":
            return _FakeProcess(returncode=1)  # wl-copy 실패
        return _FakeProcess(returncode=0)      # xclip 성공

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wl-copy" if name == "wl-copy" else None)

    handler = _make_linux_handler(monkeypatch, tool="xclip")
    result = handler.set_content("text", "hello")

    assert result is True
    assert calls == ["wl-copy", "xclip"], f"폴백 체인이 실제로 안 탐: {calls}"


def test_linux_all_backends_fail_returns_false(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _FakeProcess(returncode=1))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wl-copy" if name == "wl-copy" else None)

    handler = _make_linux_handler(monkeypatch, tool="xclip")
    handler._klipper_iface = None  # 최후 폴백도 없음
    assert handler.set_content("text", "hello") is False


def test_linux_uri_list_write_failure_returns_false(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _FakeProcess(returncode=1))
    handler = _make_linux_handler(monkeypatch, tool="xclip")
    assert handler._set_files_to_clipboard(["/tmp/a.txt"]) is False


def test_linux_image_write_failure_returns_false(monkeypatch):
    import base64
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _FakeProcess(returncode=1))
    handler = _make_linux_handler(monkeypatch, tool="xclip")
    payload = base64.b64encode(b"\x89PNG\r\n").decode("ascii")
    assert handler._set_image_to_clipboard(payload) is False
