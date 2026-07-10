"""
Tailscale 네트워크 감지 유틸리티

- Tailscale IP 자동 감지
- IP가 Tailscale CGNAT 대역(100.64.0.0/10)인지 판별
"""

import ipaddress
import logging
import os
import platform
import shutil
import subprocess
from typing import List, Optional

logger = logging.getLogger(__name__)

# Tailscale CGNAT 대역: 100.64.0.0/10 (100.64.0.0 ~ 100.127.255.255)
_TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# macOS CLI 후보 (우선순위 순).
# `.app` 번들이 LaunchServices 로 launch 되면 PATH 에 `/usr/local/bin` 이
# 포함되지 않아 `shutil.which("tailscale")` 가 실패하므로 명시 fallback.
#   1) App Store 번들 직접 호출 — 셸 환경에서 정상 동작 확인됨.
#   2) 메뉴바 "Install CLI" 가 만든 wrapper symlink. (1) 이 Sequoia
#      TCC/sandbox 사유로 silent fail 할 때 우회 경로.
_MACOS_TAILSCALE_PATHS = (
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/local/bin/tailscale",
)

# Windows 표준 설치 경로 (PATH 에 등록 안 된 경우 fallback).
# PyInstaller frozen .exe 가 explorer 환경에서 launch 됐을 때 사용자 PATH 의
# Tailscale 항목이 갱신 안 된 채로 들어오는 케이스 대응.
_WINDOWS_TAILSCALE_PATHS = (
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
)


def _iter_tailscale_candidates() -> List[str]:
    """탐색해 볼 tailscale CLI 경로를 우선순위 순으로 반환한다.
    PATH lookup → OS별 표준 경로 순. 중복/부재 경로는 제외."""
    candidates: List[str] = []
    seen = set()

    def add(path: Optional[str]) -> None:
        if not path or path in seen:
            return
        if not os.path.isfile(path):
            return
        seen.add(path)
        candidates.append(path)

    add(shutil.which("tailscale"))

    system = platform.system()
    if system == "Darwin":
        for p in _MACOS_TAILSCALE_PATHS:
            add(p)
    elif system == "Windows":
        for p in _WINDOWS_TAILSCALE_PATHS:
            add(p)

    return candidates


def get_tailscale_ip() -> Optional[str]:
    """
    이 기기의 Tailscale IPv4 주소를 반환한다.
    후보 CLI 경로를 우선순위 순으로 시도하고, 첫 성공 결과를 사용한다.
    어느 후보도 성공하지 못하면 None.

    Windows 주의 (v2.1.1): PyInstaller `runw.exe` bootloader 는 console 없이
    실행되므로, console 자식 (tailscale.exe) subprocess 호출 시
    `creationflags=CREATE_NO_WINDOW` 와 `stdin=DEVNULL` 을 명시하지 않으면
    pipe 처리에서 hang 하여 timeout 으로 실패한다 (5 초 → "미연결" 표시).

    macOS 주의 (v2.4.0): `.app` LaunchServices 환경엔 `/usr/local/bin` 이
    PATH 에 없어 `shutil.which("tailscale")` 가 실패한다. App 번들 직접
    호출 (`/Applications/Tailscale.app/.../Tailscale`) 은 보통 동작하지만
    Sequoia 일부 환경에서 silent fail 보고가 있어, "Install CLI" wrapper
    symlink (`/usr/local/bin/tailscale`) 를 두 번째 후보로 시도한다.
    """
    candidates = _iter_tailscale_candidates()
    if not candidates:
        logger.info("tailscale CLI candidate not found on this system")
        return None

    creationflags = 0
    if platform.system() == "Windows":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    for cli in candidates:
        try:
            result = subprocess.run(
                [cli, "ip", "-4"],
                capture_output=True,
                text=True,
                timeout=5,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception as e:
            logger.info("tailscale CLI %s failed: %s", cli, e)
            continue

        stdout = (result.stdout or "").strip()
        if result.returncode != 0 or not stdout:
            stderr = (result.stderr or "").strip()
            logger.info(
                "tailscale CLI %s returned rc=%d stdout=%r stderr=%r",
                cli,
                result.returncode,
                stdout,
                stderr,
            )
            continue

        ip = stdout.splitlines()[0].strip()
        if is_tailscale_ip(ip):
            logger.info("tailscale IP %s detected via %s", ip, cli)
            return ip
        logger.info("tailscale CLI %s returned non-CGNAT IP %s", cli, ip)

    logger.info("tailscale IP unresolved (tried %d candidates)", len(candidates))
    return None


def is_tailscale_ip(ip_str: str) -> bool:
    """IP 주소가 Tailscale CGNAT 대역(100.64.0.0/10)에 속하는지 확인한다."""
    try:
        return ipaddress.ip_address(ip_str) in _TAILSCALE_NETWORK
    except ValueError:
        return False
