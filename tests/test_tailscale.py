"""tailscale.py 단위 테스트.

- is_tailscale_ip CGNAT 판별
- _iter_tailscale_candidates 우선순위 + os.path.isfile 필터
- get_tailscale_ip 후보 순회 (앞 후보 실패 → 뒤 후보 성공 시 뒤 결과 반환)
"""

import subprocess
from unittest.mock import MagicMock

import pytest

from core import tailscale


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("100.64.0.1", True),
        ("100.127.255.255", True),
        ("100.100.123.45", True),
        ("192.168.1.1", False),
        ("10.0.0.1", False),
        ("100.63.255.255", False),
        ("100.128.0.1", False),
        ("not-an-ip", False),
        ("", False),
    ],
)
def test_is_tailscale_ip(ip, expected):
    assert tailscale.is_tailscale_ip(ip) is expected


def test_iter_candidates_uses_path_first(monkeypatch):
    monkeypatch.setattr(tailscale.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(tailscale.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tailscale.os.path, "isfile", lambda p: p == "/usr/bin/tailscale")

    candidates = tailscale._iter_tailscale_candidates()

    assert candidates == ["/usr/bin/tailscale"]


def test_iter_candidates_macos_falls_back_to_app_bundle(monkeypatch):
    monkeypatch.setattr(tailscale.shutil, "which", lambda _: None)
    monkeypatch.setattr(tailscale.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        tailscale.os.path,
        "isfile",
        lambda path: path in tailscale._MACOS_TAILSCALE_PATHS,
    )

    candidates = tailscale._iter_tailscale_candidates()

    # App 번들 → wrapper symlink 순서 유지
    assert candidates == list(tailscale._MACOS_TAILSCALE_PATHS)


def test_iter_candidates_macos_dedupes_when_which_hits_wrapper(monkeypatch):
    """`/usr/local/bin` 이 PATH 에 있어 shutil.which 가 wrapper 를 찾는 경우,
    동일 경로가 _MACOS_TAILSCALE_PATHS 와 중복 등록되지 않아야 한다."""
    monkeypatch.setattr(
        tailscale.shutil, "which", lambda _: "/usr/local/bin/tailscale"
    )
    monkeypatch.setattr(tailscale.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(tailscale.os.path, "isfile", lambda _path: True)

    candidates = tailscale._iter_tailscale_candidates()

    assert candidates.count("/usr/local/bin/tailscale") == 1
    # App 번들 경로는 뒤에 한 번 더 들어옴
    assert "/Applications/Tailscale.app/Contents/MacOS/Tailscale" in candidates


def test_get_tailscale_ip_returns_none_when_no_candidates(monkeypatch):
    monkeypatch.setattr(tailscale, "_iter_tailscale_candidates", list)
    assert tailscale.get_tailscale_ip() is None


def _mk_completed(rc, stdout="", stderr=""):
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = rc
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def test_get_tailscale_ip_skips_failing_candidate_and_uses_next(monkeypatch, caplog):
    monkeypatch.setattr(
        tailscale,
        "_iter_tailscale_candidates",
        lambda: ["/path/A", "/path/B"],
    )
    monkeypatch.setattr(tailscale.platform, "system", lambda: "Darwin")

    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd[0])
        if cmd[0] == "/path/A":
            return _mk_completed(1, stderr="not running")
        return _mk_completed(0, stdout="100.100.0.5\n")

    monkeypatch.setattr(tailscale.subprocess, "run", fake_run)

    with caplog.at_level("INFO", logger="core.tailscale"):
        ip = tailscale.get_tailscale_ip()

    assert ip == "100.100.0.5"
    assert calls == ["/path/A", "/path/B"]
    log_text = caplog.text
    assert "/path/A" in log_text  # 실패 path 도 로그에 남아야 함
    assert "/path/B" in log_text


def test_get_tailscale_ip_rejects_non_cgnat(monkeypatch):
    monkeypatch.setattr(
        tailscale, "_iter_tailscale_candidates", lambda: ["/path/X"]
    )
    monkeypatch.setattr(tailscale.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        tailscale.subprocess,
        "run",
        lambda *_a, **_kw: _mk_completed(0, stdout="192.168.1.10\n"),
    )

    assert tailscale.get_tailscale_ip() is None


def test_get_tailscale_ip_handles_subprocess_exception(monkeypatch):
    monkeypatch.setattr(
        tailscale,
        "_iter_tailscale_candidates",
        lambda: ["/path/A", "/path/B"],
    )
    monkeypatch.setattr(tailscale.platform, "system", lambda: "Darwin")

    def fake_run(cmd, **_kwargs):
        if cmd[0] == "/path/A":
            raise subprocess.TimeoutExpired(cmd, timeout=5)
        return _mk_completed(0, stdout="100.64.1.1\n")

    monkeypatch.setattr(tailscale.subprocess, "run", fake_run)

    assert tailscale.get_tailscale_ip() == "100.64.1.1"
