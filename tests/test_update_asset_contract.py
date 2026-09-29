"""자동 업데이트 «파일명 계약» — 빌드 스크립트 산출명 ↔ core.updater 자산 패턴 (2026-09-29).

업데이터가 한 번 배포된 뒤 빌드 스크립트의 산출 파일명을 바꾸면, 이미 설치된 모든 사본이
새 릴리스에서 자기 자산을 못 찾아 «업데이트 없음»으로 조용히 멈춘다(함정 #37 같은 변경).
이 테스트는 빌드 스크립트에서 이름 규칙을 «추출»해 패턴과 대조한다 — 추출이 0건이면
실패한다(무결과를 통과로 읽지 않는다).
"""

import re
from pathlib import Path

import pytest

from core import updater

ROOT = Path(__file__).resolve().parent.parent
VERSION = "3.0.99"


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _one(pattern, text, what):
    found = re.findall(pattern, text, re.MULTILINE)
    assert len(found) == 1, f"{what}: 추출 {len(found)}건 — 빌드 스크립트 형식이 바뀌었으면 이 테스트와 updater 패턴을 함께 고칠 것"
    return found[0]


def _github_asset_name(local_name: str) -> str:
    """GitHub Release 업로드 시 공백·괄호를 '.' 로 바꾸는 규칙(함정 #37 실측)."""
    return re.sub(r"[ ()]", ".", local_name)


def test_windows_installer_name_matches_pattern():
    base = _one(r"^OutputBaseFilename=(\S+)$", _read("build/installer.iss"), "OutputBaseFilename")
    name = base.replace("{#MyAppVersion}", VERSION) + ".exe"
    assert name == f"infinite-clipboard-setup-{VERSION}.exe"
    assert re.search(updater.asset_pattern("Windows", "AMD64"), _github_asset_name(name))


def test_linux_package_name_matches_pattern():
    pkgbuild = _read("build/PKGBUILD")
    pkgname = _one(r"^pkgname=(\S+)$", pkgbuild, "pkgname")
    pkgrel = _one(r"^pkgrel=(\S+)$", pkgbuild, "pkgrel")
    arch = _one(r"^arch=\('(\w+)'\)$", pkgbuild, "arch")
    name = f"{pkgname}-{VERSION}-{pkgrel}-{arch}.pkg.tar.zst"   # makepkg 기본 PKGEXT
    assert re.search(updater.asset_pattern("Linux", "x86_64"), _github_asset_name(name))


def test_linux_install_path_matches_updater():
    pkgbuild = _read("build/PKGBUILD")
    assert _one(r"^Exec=(\S+)$", pkgbuild, "desktop Exec") == updater.LINUX_APP_PATH
    assert updater.LINUX_APP_PATH.startswith(updater.LINUX_INSTALL_DIR)


@pytest.mark.parametrize("machine", ["arm64", "x86_64"])
def test_mac_dmg_name_matches_pattern(machine):
    script = _read("build/make_dmg.sh")
    app_name = _one(r'^APP_NAME="([^"]+)"$', script, "APP_NAME")
    suffix = _one(rf'^\s*{machine}\)\s+ARCH_SUFFIX="([^"]+)"', script, f"ARCH_SUFFIX({machine})")
    template = _one(r'^DMG_NAME="([^"]+)"$', script, "DMG_NAME")
    _one(r'^DMG_PATH="[^"]*\$\{DMG_NAME\}\.dmg"$', script, "DMG_PATH .dmg")
    local = (template.replace("${APP_NAME}", app_name).replace("${VERSION}", VERSION)
             .replace("${ARCH_SUFFIX}", suffix)) + ".dmg"
    pattern = updater.asset_pattern("Darwin", machine)
    assert re.search(pattern, _github_asset_name(local)), (local, pattern)
    # 다른 아키텍처 패턴에는 걸리지 않아야 한다 (Intel 에 arm 번들을 깔지 않게)
    other = updater.asset_pattern("Darwin", "x86_64" if machine == "arm64" else "arm64")
    assert not re.search(other, _github_asset_name(local))


def test_mac_bundle_name_in_dmg_matches_updater():
    app_name = _one(r'^APP_NAME="([^"]+)"$', _read("build/make_dmg.sh"), "APP_NAME")
    assert updater.MAC_APP_NAME == f"{app_name}.app"


def test_real_v3_0_13_asset_names_match():
    """실물 대조 — v3.0.13 릴리스 자산 이름 [실측 gh api 2026-09-29]."""
    real = {
        ("Windows", "AMD64"): "infinite-clipboard-setup-3.0.13.exe",
        ("Linux", "x86_64"): "infinite-clipboard-3.0.13-1-x86_64.pkg.tar.zst",
        ("Darwin", "arm64"): "Infinite.Clipboard.3.0.13-apple-silicon.dmg",
        ("Darwin", "x86_64"): "Infinite.Clipboard.3.0.13-intel.dmg",
    }
    for (system, machine), name in real.items():
        assert re.search(updater.asset_pattern(system, machine), name), name
