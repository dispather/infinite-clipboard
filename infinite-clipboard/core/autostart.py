# pyright: reportAttributeAccessIssue=false
# (winreg는 Windows 전용이라 Linux/macOS Pyright에서 경고 — 런타임에는 _windows_* 분기만 타므로 안전)
"""
OS별 자동 시작(Login Item) 등록/해제 모듈.

Windows: HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run 레지스트리
macOS:   ~/Library/LaunchAgents/com.dispather.infinite-clipboard.plist
Linux:   ~/.config/autostart/infinite-clipboard.desktop

앱 내 설정창의 "Windows/macOS 시작 시 자동 실행" 체크박스가 이 모듈을 호출한다.
설치판(Inno Setup / PKGBUILD)은 설치 단계에서 별도로 처리하며, 앱 내 토글이
상시 우선순위.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


APP_ID = "InfiniteClipboard"
APP_NAME = "Infinite Clipboard"
MACOS_BUNDLE_ID = "com.dispather.infinite-clipboard"


def _executable_path() -> str:
    """현재 실행 파일 경로.

    - PyInstaller 번들 실행 중: sys.executable (예: /opt/infinite-clipboard/InfiniteClipboard)
    - Python 스크립트로 실행 중: python3 /path/to/main.py
    """
    if getattr(sys, "frozen", False):
        return sys.executable
    # 개발 모드: python3 main.py
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{sys.executable}" "{main_py}"'


# ─── Windows ────────────────────────────────────────────────────────────

def _windows_enable(exe_path: str) -> bool:
    try:
        import winreg  # type: ignore
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        # exe_path에 공백이 있을 수 있으므로 큰따옴표로 감쌈
        value = exe_path if exe_path.startswith('"') else f'"{exe_path}"'
        winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, value)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def _windows_disable() -> bool:
    try:
        import winreg  # type: ignore
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, APP_ID)
        except FileNotFoundError:
            pass  # 이미 없음
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def _windows_is_enabled() -> bool:
    try:
        import winreg  # type: ignore
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ,
        )
        try:
            winreg.QueryValueEx(key, APP_ID)
            return True
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False


# ─── macOS ──────────────────────────────────────────────────────────────

def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MACOS_BUNDLE_ID}.plist"


def _macos_launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


def _macos_enable(exe_path: str) -> bool:
    # .app 번들이면 `open -a <앱>` 형태로, 개발 모드면 python+스크립트 형태로
    if exe_path.startswith('"') and "python" in exe_path:
        # 개발 모드: python3 main.py 형태
        program_args = exe_path.replace('"', "").split(" ", 1)
    elif ".app/Contents/MacOS/" in exe_path:
        # .app 번들: MacOS/Infinite Clipboard 직접 실행
        program_args = [exe_path]
    else:
        program_args = [exe_path]

    args_xml = "\n".join(
        f"        <string>{p}</string>" for p in program_args
    )
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{MACOS_BUNDLE_ID}</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""
    # 설계: plist 파일만 저장하고 `launchctl bootstrap` 은 호출하지 않는다.
    # 이유: bootstrap 은 RunAtLoad=true plist 를 즉시 실행하므로 현재 실행 중인
    # 앱과 중복 기동되어 트레이 아이콘이 2개로 보임. LaunchAgent 의 본질은
    # "다음 로그인 시 자동 시작" 이므로 파일 저장만으로 충분 (macOS 가 로그인 시
    # ~/Library/LaunchAgents/ 를 자동 로드).
    try:
        path = _macos_plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plist, encoding="utf-8")
        return True
    except Exception:
        return False


def _macos_disable() -> bool:
    # bootout 역시 호출 안 함 — 파일 삭제만으로 다음 로그인부터 자동 시작 안 됨.
    # 현재 세션에 이미 로드된 서비스(예: 과거 토글 ON 후 재로그인한 사용자)가
    # 있을 수 있으니 존재 시 한 번 bootout 시도 (실패해도 무시).
    try:
        path = _macos_plist_path()
        if path.exists():
            subprocess.run(
                ["launchctl", "bootout", _macos_launchctl_domain(), str(path)],
                capture_output=True,
            )
            path.unlink()
        return True
    except Exception:
        return False


def _macos_is_enabled() -> bool:
    # plist 파일이 존재하고 RunAtLoad=true 를 포함하면 활성화된 것으로 간주.
    # launchctl 등록 여부는 체크 안 함 — bootstrap 을 쓰지 않는 설계이므로
    # "다음 로그인부터 자동 시작" 이 기대 동작이고 현재 세션의 등록은 무관.
    path = _macos_plist_path()
    if not path.exists():
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return False
    return "<key>RunAtLoad</key>" in content and "<true/>" in content


# ─── Linux (FreeDesktop XDG autostart) ──────────────────────────────────

def _linux_desktop_path() -> Path:
    xdg_config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg_config) / "autostart" / "infinite-clipboard.desktop"


def _linux_enable(exe_path: str) -> bool:
    # 시스템 설치된 경우 /usr/share/applications/infinite-clipboard.desktop 심볼릭 링크 우선
    system_desktop = Path("/usr/share/applications/infinite-clipboard.desktop")
    target = _linux_desktop_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if system_desktop.exists():
            # 심볼릭 링크로 간결히
            if target.exists() or target.is_symlink():
                target.unlink()
            target.symlink_to(system_desktop)
        else:
            # 개발 모드 / AppImage 등 시스템 설치 아닌 경우 — 동적 .desktop 생성
            content = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=Tailscale LAN 클립보드/파일 공유
Exec={exe_path}
Icon=infinite-clipboard
Terminal=false
Categories=Utility;Network;
X-GNOME-Autostart-enabled=true
StartupNotify=false
"""
            target.write_text(content, encoding="utf-8")
            target.chmod(0o755)
        return True
    except Exception:
        return False


def _linux_disable() -> bool:
    try:
        target = _linux_desktop_path()
        if target.exists() or target.is_symlink():
            target.unlink()
        return True
    except Exception:
        return False


def _linux_is_enabled() -> bool:
    target = _linux_desktop_path()
    return target.exists() or target.is_symlink()


# ─── 공개 API ───────────────────────────────────────────────────────────

def is_supported() -> bool:
    """현재 OS에서 자동 시작 토글을 지원하는지."""
    return platform.system() in ("Windows", "Darwin", "Linux")


def is_enabled() -> bool:
    """자동 시작이 현재 활성화되어 있는지."""
    system = platform.system()
    if system == "Windows":
        return _windows_is_enabled()
    if system == "Darwin":
        return _macos_is_enabled()
    if system == "Linux":
        return _linux_is_enabled()
    return False


def set_enabled(enabled: bool) -> bool:
    """자동 시작 활성/비활성 토글. 성공 시 True."""
    system = platform.system()
    exe = _executable_path()
    if system == "Windows":
        return _windows_enable(exe) if enabled else _windows_disable()
    if system == "Darwin":
        return _macos_enable(exe) if enabled else _macos_disable()
    if system == "Linux":
        return _linux_enable(exe) if enabled else _linux_disable()
    return False
