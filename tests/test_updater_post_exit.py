"""core/updater.py 종료 후 설치 — helper 스크립트 생성·실행 (2026-09-29 자동 업데이트 Task 3).

텍스트 단언만으로는 «대상 셸이 그 텍스트를 어떻게 실행하나»를 못 본다 — Linux/mac helper 는
가짜 pkexec/pacman/open/xattr 를 PATH 앞에 두고 **실제 /bin/sh 로 한 번 실행**한다.
Windows helper(.ps1)는 Windows CI 잡에서만 실제 powershell.exe 로 실행한다.
⚠️ 이 테스트는 진짜 polkit 창·macOS open/hdiutil·Inno 설치기 의미는 못 본다(실기 항목).
"""

import os
import platform
import shlex
import stat
import subprocess
import time
from pathlib import Path

import pytest

from core import updater
from core.updater import UpdateError

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX sh helper")
windows_only = pytest.mark.skipif(platform.system() != "Windows", reason="powershell helper")


def _write_exec(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _wait_for(path: Path, timeout=10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def weird_dir(tmp_path):
    # 공백 + 작은따옴표 + 한글 — 인용 실수가 있으면 여기서 깨진다
    d = tmp_path / "a b'c 한글"
    d.mkdir()
    return d


# ── 명령 형태 ────────────────────────────────────────────────────


@posix_only
def test_post_exit_command_posix_is_sh_helper(weird_dir):
    cmd = updater.post_exit_command(
        "Linux", "silent", 12345, weird_dir / "p.pkg.tar.zst",
        executable="/opt/infinite-clipboard/InfiniteClipboard",
        log_path=weird_dir / "update-helper.log", workdir=weird_dir)
    assert cmd[0] == "/bin/sh" and len(cmd) == 2
    helper = Path(cmd[1])
    assert helper.exists()
    r = subprocess.run(["sh", "-n", str(helper)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_post_exit_command_windows_is_powershell(weird_dir):
    cmd = updater.post_exit_command(
        "Windows", "silent", 42, weird_dir / "infinite-clipboard-setup-3.0.99.exe",
        executable=r"C:\Users\k\AppData\Local\Programs\Infinite Clipboard\Infinite Clipboard.exe",
        log_path=weird_dir / "update-helper.log", workdir=weird_dir)
    assert cmd[0] == "powershell.exe"
    assert "-File" in cmd and cmd[-1].endswith(".ps1")
    raw = Path(cmd[-1]).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")   # UTF-8 BOM — Windows PowerShell 5.1 가 한글 경로를 ANSI 로 오독하지 않게
    text = raw.decode("utf-8-sig")
    assert "/SILENT" in text and "Wait-Process -Id 42" in text
    assert "a b''c 한글" in text   # PS 단일 인용 안의 ' 는 '' 로


def test_post_exit_command_windows_interactive_has_no_silent(weird_dir):
    cmd = updater.post_exit_command(
        "Windows", "interactive", 42, weird_dir / "setup.exe",
        executable=r"C:\Program Files\Infinite Clipboard\Infinite Clipboard.exe",
        log_path=weird_dir / "l.log", workdir=weird_dir)
    text = Path(cmd[-1]).read_bytes().decode("utf-8-sig")
    assert "/SILENT" not in text and "-ArgumentList" not in text


def test_post_exit_command_mac_requires_bundle(weird_dir):
    with pytest.raises(UpdateError):
        updater.post_exit_command("Darwin", "silent", 1, weird_dir / "x.dmg",
                                  executable="/usr/local/bin/ic",
                                  log_path=weird_dir / "l.log", workdir=weird_dir)


def test_post_exit_command_page_mode_rejected(weird_dir):
    with pytest.raises(ValueError):
        updater.post_exit_command("Linux", "page", 1, weird_dir / "x",
                                  executable="/x", log_path=weird_dir / "l", workdir=weird_dir)


# ── Linux helper 실제 실행 ───────────────────────────────────────


def _linux_fixture(weird_dir, pacman_rc: int):
    fake_bin = weird_dir / "bin"
    fake_bin.mkdir()
    record = weird_dir / "pacman-args.txt"
    marker = weird_dir / "relaunched.txt"
    _write_exec(fake_bin / "pkexec", 'exec "$@"\n')
    _write_exec(fake_bin / "pacman",
                f'if kill -0 "$WATCH_PID" 2>/dev/null; then s=alive; else s=dead; fi\n'
                f'printf "%s|%s\\n" "$s" "$*" >> {shlex.quote(str(record))}\n'
                f'exit {pacman_rc}\n')
    app = _write_exec(weird_dir / "Infinite App", f'echo ran >> {shlex.quote(str(marker))}\n')
    pkg = weird_dir / "infinite-clipboard-3.0.99-1-x86_64.pkg.tar.zst"
    pkg.write_bytes(b"pkg")
    return fake_bin, record, marker, app, pkg


@posix_only
@pytest.mark.parametrize("pacman_rc", [0, 1])
def test_linux_helper_runs_after_pid_exit_and_always_relaunches(weird_dir, pacman_rc):
    fake_bin, record, marker, app, pkg = _linux_fixture(weird_dir, pacman_rc)
    sleeper = subprocess.Popen(["sleep", "1"])
    # 종료된 자식을 바로 회수 — 안 하면 좀비로 남아 kill -0 이 계속 성공한다(실제 앱은 그 부모가 회수)
    import threading
    threading.Thread(target=sleeper.wait, daemon=True).start()
    log = weird_dir / "update-helper.log"
    cmd = updater.post_exit_command("Linux", "silent", sleeper.pid, pkg,
                                    executable=str(app), log_path=log, workdir=weird_dir)
    env = dict(os.environ, PATH=f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
               WATCH_PID=str(sleeper.pid))
    t0 = time.time()
    subprocess.run(cmd, env=env, timeout=30, check=True)
    sleeper.wait()
    assert time.time() - t0 >= 0.8   # PID 가 살아있는 동안 기다렸다

    line = record.read_text(encoding="utf-8").strip()
    status, args = line.split("|", 1)
    assert status == "dead"                                    # 앱 종료 «후»에 설치
    assert args == f"-U --noconfirm {pkg}"                     # 공백·따옴표 경로가 한 인자로
    assert _wait_for(marker), "설치 성공/실패와 무관하게 앱 재실행"
    assert not pkg.exists()                                    # 받은 패키지 정리
    text = log.read_text(encoding="utf-8")
    assert ("installed" in text) if pacman_rc == 0 else ("install failed" in text)


# ── mac helper 실제 실행 (Linux 에서 가짜 open/xattr) ────────────


def _mac_fixture(weird_dir):
    fake_bin = weird_dir / "bin"
    fake_bin.mkdir()
    opened = weird_dir / "opened.txt"
    _write_exec(fake_bin / "open", f'printf "%s\\n" "$1" >> {shlex.quote(str(opened))}\n')
    _write_exec(fake_bin / "xattr", "exit 0\n")
    apps = weird_dir / "Applications"
    apps.mkdir()
    target = apps / "Infinite Clipboard.app"
    (target / "Contents").mkdir(parents=True)
    (target / "Contents" / "v").write_text("old", encoding="utf-8")
    new = apps / "Infinite Clipboard.app.new"
    (new / "Contents").mkdir(parents=True)
    (new / "Contents" / "v").write_text("new", encoding="utf-8")
    exe = target / "Contents" / "MacOS" / "Infinite Clipboard"
    return fake_bin, opened, apps, target, new, exe


@posix_only
def test_mac_helper_swaps_bundle_and_opens(weird_dir):
    fake_bin, opened, apps, target, new, exe = _mac_fixture(weird_dir)
    cmd = updater.post_exit_command("Darwin", "silent", 999999, weird_dir / "x.dmg",
                                    executable=str(exe), log_path=weird_dir / "h.log",
                                    workdir=weird_dir)
    env = dict(os.environ, PATH=f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    subprocess.run(cmd, env=env, timeout=30, check=True)
    assert (target / "Contents" / "v").read_text(encoding="utf-8") == "new"
    assert sorted(p.name for p in apps.iterdir()) == ["Infinite Clipboard.app"]
    assert _wait_for(opened) and opened.read_text(encoding="utf-8").strip() == str(target)


@posix_only
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root 는 권한 무시")
def test_mac_helper_failure_keeps_old_bundle_and_opens_it(weird_dir):
    fake_bin, opened, apps, target, new, exe = _mac_fixture(weird_dir)
    cmd = updater.post_exit_command("Darwin", "silent", 999999, weird_dir / "x.dmg",
                                    executable=str(exe), log_path=weird_dir / "h.log",
                                    workdir=weird_dir)
    env = dict(os.environ, PATH=f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    apps.chmod(0o555)   # 부모 폴더 쓰기 금지 → mv 실패
    try:
        subprocess.run(cmd, env=env, timeout=30, check=True)
    finally:
        apps.chmod(0o755)
    assert (target / "Contents" / "v").read_text(encoding="utf-8") == "old"
    assert _wait_for(opened) and opened.read_text(encoding="utf-8").strip() == str(target)
    assert "install failed" in (weird_dir / "h.log").read_text(encoding="utf-8")


# ── prepare_mac_bundle (hdiutil/ditto 는 가짜 실행기로) ──────────


def test_prepare_mac_bundle_attach_copy_detach(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    target = tmp_path / "Infinite Clipboard.app"
    out = updater.prepare_mac_bundle(tmp_path / "x.dmg", target, run=run)
    assert out == tmp_path / "Infinite Clipboard.app.new"
    assert [c[0] for c in calls] == ["hdiutil", "ditto", "hdiutil"]
    assert calls[0][1] == "attach" and "-nobrowse" in calls[0]
    assert calls[1][1].endswith("/Infinite Clipboard.app") and calls[1][2] == str(out)
    assert calls[2][1] == "detach"


def test_prepare_mac_bundle_detaches_even_if_copy_fails(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        rc = 1 if cmd[0] == "ditto" else 0
        return subprocess.CompletedProcess(cmd, rc, "", "boom")

    with pytest.raises(UpdateError) as e:
        updater.prepare_mac_bundle(tmp_path / "x.dmg", tmp_path / "A.app", run=run)
    assert e.value.reason == "prepare"
    assert calls[-1][:2] == ["hdiutil", "detach"]


# ── spawn_post_exit ──────────────────────────────────────────────


@posix_only
def test_spawn_post_exit_detached_with_clean_env(tmp_path, monkeypatch):
    out = tmp_path / "env.txt"
    script = _write_exec(tmp_path / "s.sh",
                         f'printf "%s|%s\\n" "${{LD_LIBRARY_PATH-unset}}" "$PYINSTALLER_RESET_ENVIRONMENT" > {shlex.quote(str(out))}\n')
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/infinite-clipboard/_internal")
    updater.spawn_post_exit(["/bin/sh", str(script)], "Linux")
    assert _wait_for(out)
    assert out.read_text(encoding="utf-8").strip() == "unset|1"


# ── Windows helper 실제 실행 (Windows CI 전용) ───────────────────


@windows_only
@pytest.mark.parametrize("setup_rc", [0, 1])
def test_windows_helper_relaunches_regardless_of_setup_exit(weird_dir, tmp_path, setup_rc):
    # helper·setup·app «위치»는 weird_dir(공백·'·한글 — PS 인용 검증). 마커는 ASCII 경로 —
    # cmd.exe 는 .cmd «내용»을 콘솔 OEM 코드페이지(영문 러너 cp437)로 읽어 한글이 깨진다.
    marker = tmp_path / "relaunched.txt"
    setup = weird_dir / "infinite-clipboard-setup-3.0.99.cmd"
    setup.write_text(f"@echo off\r\nexit /b {setup_rc}\r\n", encoding="ascii")
    app = weird_dir / "app.cmd"
    app.write_text(f'@echo off\r\necho ran> "{marker}"\r\n', encoding="ascii")
    log = weird_dir / "update-helper.log"
    cmd = updater.post_exit_command("Windows", "silent", 999999, setup,
                                    executable=str(app), log_path=log, workdir=weird_dir)
    subprocess.run(cmd, timeout=60, check=False)
    assert _wait_for(marker, timeout=20)
    assert f"setup exit={setup_rc}" in log.read_text(encoding="utf-8", errors="replace")
    assert not setup.exists()
