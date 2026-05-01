"""
Tailscale 네트워크 감지 유틸리티

- Tailscale IP 자동 감지
- IP가 Tailscale CGNAT 대역(100.64.0.0/10)인지 판별
"""

import ipaddress
import platform
import shutil
import subprocess
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Tailscale CGNAT 대역: 100.64.0.0/10 (100.64.0.0 ~ 100.127.255.255)
_TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# macOS App Store 버전의 CLI 경로
_MACOS_TAILSCALE_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"

# Windows 표준 설치 경로 (PATH 에 등록 안 된 경우 fallback).
# PyInstaller frozen .exe 가 explorer 환경에서 launch 됐을 때 사용자 PATH 의
# Tailscale 항목이 갱신 안 된 채로 들어오는 케이스 대응.
_WINDOWS_TAILSCALE_PATHS = (
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
)


def _find_tailscale_cli() -> Optional[str]:
    """tailscale CLI 실행 경로를 반환한다. 없으면 None."""
    # PATH에서 먼저 탐색 (Linux, Windows, Homebrew macOS)
    path = shutil.which("tailscale")
    if path:
        return path

    # macOS App Store 버전
    if platform.system() == "Darwin":
        import os
        if os.path.isfile(_MACOS_TAILSCALE_CLI):
            return _MACOS_TAILSCALE_CLI

    # Windows 표준 설치 경로 (PATH 에 없는 경우)
    if platform.system() == "Windows":
        import os
        for p in _WINDOWS_TAILSCALE_PATHS:
            if os.path.isfile(p):
                return p

    return None


def get_tailscale_ip() -> Optional[str]:
    """
    이 기기의 Tailscale IPv4 주소를 반환한다.
    tailscale CLI가 없거나 연결되지 않았으면 None.

    Windows 주의 (v2.1.1): PyInstaller `runw.exe` bootloader 는 console 없이
    실행되므로, console 자식 (tailscale.exe) subprocess 호출 시
    `creationflags=CREATE_NO_WINDOW` 와 `stdin=DEVNULL` 을 명시하지 않으면
    pipe 처리에서 hang 하여 timeout 으로 실패한다 (5 초 → "미연결" 표시).
    """
    cli = _find_tailscale_cli()
    if not cli:
        logger.debug("tailscale CLI를 찾을 수 없음")
        return None

    # Windows GUI → console 자식 hang 회피 (subprocess.CREATE_NO_WINDOW = 0x08000000)
    creationflags = 0
    if platform.system() == "Windows":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    try:
        result = subprocess.run(
            [cli, "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        if result.returncode == 0 and result.stdout.strip():
            ip = result.stdout.strip().splitlines()[0]
            if is_tailscale_ip(ip):
                return ip
    except Exception as e:
        logger.debug(f"Tailscale IP 감지 실패: {e}")

    return None


def is_tailscale_ip(ip_str: str) -> bool:
    """IP 주소가 Tailscale CGNAT 대역(100.64.0.0/10)에 속하는지 확인한다."""
    try:
        return ipaddress.ip_address(ip_str) in _TAILSCALE_NETWORK
    except ValueError:
        return False
