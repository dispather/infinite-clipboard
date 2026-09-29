"""core/updater.py 순수 로직 — 버전 비교·릴리스 선택·자산 매칭·설치 가능 판정·상태 파일·자식 env.

2026-09-29 자동 업데이트(docs/plans/2026-09-29-auto-update.md Task 1). 네트워크 없이
릴리스 목록 dict 픽스처만으로 검증한다.
"""

import json
import sys
from pathlib import Path

import pytest

from core import updater
from core.updater import UpdateInfo


def _asset(name, digest="sha256:" + "ab" * 32, size=100):
    return {
        "name": name,
        "size": size,
        "digest": digest,
        "browser_download_url": f"https://example.invalid/dl/{name}",
    }


def _release(tag, *, draft=False, prerelease=False, assets=None):
    ver = tag.lstrip("v")
    if assets is None:
        assets = [
            _asset(f"infinite-clipboard-{ver}-1-x86_64.pkg.tar.zst"),
            _asset(f"infinite-clipboard-setup-{ver}.exe"),
            _asset(f"Infinite.Clipboard.{ver}-apple-silicon.dmg"),
            _asset(f"Infinite.Clipboard.{ver}-intel.dmg"),
        ]
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "html_url": f"https://github.com/dispather/infinite-clipboard/releases/tag/{tag}",
        "assets": assets,
    }


# ── parse_version ────────────────────────────────────────────────


@pytest.mark.parametrize("tag,expected", [
    ("v3.0.14", (3, 0, 14)),
    ("3.0.14", (3, 0, 14)),
    ("v10.2.0", (10, 2, 0)),
    ("nightly", None),
    ("v3.0", None),
    ("v3.0.14-rc1", None),
    ("", None),
])
def test_parse_version(tag, expected):
    assert updater.parse_version(tag) == expected


# ── pick_update ──────────────────────────────────────────────────


RELEASES = [
    _release("v9.9.9", draft=True),
    _release("v4.0.0", prerelease=True),
    _release("nightly"),
    _release("v3.0.12"),
    _release("v3.0.14"),
]


def test_pick_update_highest_published_semver():
    info = updater.pick_update(RELEASES, "3.0.13", "Linux", "x86_64")
    assert info is not None
    assert info.version == "3.0.14"
    assert info.asset_name == "infinite-clipboard-3.0.14-1-x86_64.pkg.tar.zst"
    assert info.sha256 == "ab" * 32
    assert info.html_url.endswith("/v3.0.14")


def test_pick_update_same_version_is_none():
    assert updater.pick_update(RELEASES, "3.0.14", "Linux", "x86_64") is None


def test_pick_update_never_downgrades():
    assert updater.pick_update(RELEASES, "3.1.0", "Linux", "x86_64") is None


def test_pick_update_unparseable_current_is_none():
    # 소스 체크아웃의 이상한 버전 문자열로 엉뚱한 업데이트를 제안하지 않는다
    assert updater.pick_update(RELEASES, "dev", "Linux", "x86_64") is None


@pytest.mark.parametrize("system,machine,expected", [
    # v3.0.13 실물 자산 이름 [실측 gh api 2026-09-29]
    ("Windows", "AMD64", "infinite-clipboard-setup-3.0.13.exe"),
    ("Linux", "x86_64", "infinite-clipboard-3.0.13-1-x86_64.pkg.tar.zst"),
    ("Darwin", "arm64", "Infinite.Clipboard.3.0.13-apple-silicon.dmg"),
    ("Darwin", "x86_64", "Infinite.Clipboard.3.0.13-intel.dmg"),
])
def test_pick_update_asset_per_os(system, machine, expected):
    info = updater.pick_update([_release("v3.0.13")], "3.0.12", system, machine)
    assert info is not None and info.asset_name == expected


def test_pick_update_no_asset_for_this_os_is_none():
    rel = _release("v3.0.14", assets=[_asset("infinite-clipboard-setup-3.0.14.exe")])
    assert updater.pick_update([rel], "3.0.13", "Linux", "x86_64") is None


def test_pick_update_unsupported_linux_arch_is_none():
    assert updater.pick_update(RELEASES, "3.0.13", "Linux", "aarch64") is None


def test_pick_update_missing_digest_gives_none_sha():
    rel = _release("v3.0.14", assets=[
        {"name": "infinite-clipboard-3.0.14-1-x86_64.pkg.tar.zst", "size": 5,
         "browser_download_url": "https://example.invalid/x"},
    ])
    info = updater.pick_update([rel], "3.0.13", "Linux", "x86_64")
    assert info is not None and info.sha256 is None


def test_pick_update_ignores_malformed_entries():
    junk = [None, "x", {"tag_name": None}, {"tag_name": "v3.0.20", "assets": "nope"}]
    info = updater.pick_update(junk + [_release("v3.0.14")], "3.0.13", "Linux", "x86_64")
    assert info is not None and info.version == "3.0.14"


# ── install_mode ─────────────────────────────────────────────────


def _which_all(name):
    return f"/usr/bin/{name}"


def _which_none(name):
    return None


def test_install_mode_not_frozen_is_page():
    assert updater.install_mode("Linux", "/usr/bin/python3", frozen=False,
                                env={}, which=_which_all) == "page"


def test_install_mode_windows_per_user_silent():
    env = {"LOCALAPPDATA": r"C:\Users\kim\AppData\Local"}
    exe = r"C:\Users\kim\AppData\Local\Programs\Infinite Clipboard\Infinite Clipboard.exe"
    assert updater.install_mode("Windows", exe, frozen=True, env=env,
                                which=_which_none) == "silent"


def test_install_mode_windows_case_insensitive():
    env = {"LOCALAPPDATA": r"C:\Users\Kim\AppData\Local"}
    exe = r"c:\users\kim\appdata\local\programs\Infinite Clipboard\Infinite Clipboard.exe"
    assert updater.install_mode("Windows", exe, frozen=True, env=env,
                                which=_which_none) == "silent"


def test_install_mode_windows_all_users_interactive():
    env = {"LOCALAPPDATA": r"C:\Users\kim\AppData\Local"}
    exe = r"C:\Program Files\Infinite Clipboard\Infinite Clipboard.exe"
    assert updater.install_mode("Windows", exe, frozen=True, env=env,
                                which=_which_none) == "interactive"


def test_install_mode_linux_opt_with_tools_silent():
    assert updater.install_mode("Linux", "/opt/infinite-clipboard/InfiniteClipboard",
                                frozen=True, env={}, which=_which_all) == "silent"


def test_install_mode_linux_without_pkexec_page():
    def which(name):
        return "/usr/bin/pacman" if name == "pacman" else None
    assert updater.install_mode("Linux", "/opt/infinite-clipboard/InfiniteClipboard",
                                frozen=True, env={}, which=which) == "page"


def test_install_mode_linux_other_location_page():
    assert updater.install_mode("Linux", "/home/u/InfiniteClipboard/InfiniteClipboard",
                                frozen=True, env={}, which=_which_all) == "page"


def test_install_mode_mac_writable_silent():
    exe = "/Applications/Infinite Clipboard.app/Contents/MacOS/Infinite Clipboard"
    assert updater.install_mode("Darwin", exe, frozen=True, env={}, which=_which_none,
                                access=lambda p, m: True) == "silent"


def test_install_mode_mac_not_writable_page():
    exe = "/Applications/Infinite Clipboard.app/Contents/MacOS/Infinite Clipboard"
    assert updater.install_mode("Darwin", exe, frozen=True, env={}, which=_which_none,
                                access=lambda p, m: False) == "page"


def test_install_mode_mac_not_in_bundle_page():
    assert updater.install_mode("Darwin", "/usr/local/bin/ic", frozen=True, env={},
                                which=_which_none, access=lambda p, m: True) == "page"


def test_app_bundle_path():
    exe = "/Applications/Infinite Clipboard.app/Contents/MacOS/Infinite Clipboard"
    assert updater.app_bundle_path(exe) == Path("/Applications/Infinite Clipboard.app")
    assert updater.app_bundle_path("/usr/bin/x") is None


# ── 상태 파일 ────────────────────────────────────────────────────


def test_state_roundtrip_uses_isolated_config_dir(tmp_path):
    # conftest 의 autouse 픽스처가 config._get_config_dir 를 tmp_path 로 돌린다
    assert updater.load_state() == {}
    updater.save_state({"last_check_at": 123.0, "notified_version": "3.0.14"})
    path = tmp_path / "update_state.json"
    assert json.loads(path.read_text(encoding="utf-8"))["notified_version"] == "3.0.14"
    assert updater.load_state()["last_check_at"] == 123.0


def test_state_corrupt_file_is_empty(tmp_path):
    (tmp_path / "update_state.json").write_text("{not json", encoding="utf-8")
    assert updater.load_state() == {}


def test_state_non_dict_is_empty(tmp_path):
    (tmp_path / "update_state.json").write_text("[1,2]", encoding="utf-8")
    assert updater.load_state() == {}


def test_state_file_is_not_settings_json(tmp_path):
    # settings.json mtime 변화 = 앱 재시작(main._watch_config_for_restart) — 절대 건드리면 안 됨
    updater.save_state({"x": 1})
    assert not (tmp_path / "settings.json").exists()


def test_update_state_merges(tmp_path):
    updater.save_state({"a": 1})
    updater.update_state(b=2)
    updater.update_state(a=None)   # None = 키 삭제
    assert updater.load_state() == {"b": 2}


# ── clean_child_env ──────────────────────────────────────────────


def test_clean_child_env_strips_bundle_paths():
    env = {
        "LD_LIBRARY_PATH": "/opt/infinite-clipboard/_internal",
        "_PYI_ARCHIVE_FILE": "/opt/infinite-clipboard/InfiniteClipboard",
        "_PYI_PARENT_PROCESS_LEVEL": "-1",
        "_MEIPASS2": "/tmp/_MEI123",
        "PATH": "/usr/bin",
    }
    out = updater.clean_child_env(env)
    assert "LD_LIBRARY_PATH" not in out
    assert not any(k.startswith("_PYI_") for k in out)
    assert "_MEIPASS2" not in out
    assert out["PATH"] == "/usr/bin"
    assert out["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert out["LANG"] == "en_US.UTF-8"
    assert env["LD_LIBRARY_PATH"] == "/opt/infinite-clipboard/_internal"   # 원본 불변


def test_clean_child_env_restores_orig():
    env = {"LD_LIBRARY_PATH": "/bundle", "LD_LIBRARY_PATH_ORIG": "/usr/local/lib",
           "DYLD_LIBRARY_PATH": "/b2", "DYLD_LIBRARY_PATH_ORIG": "/x", "LANG": "ko_KR.UTF-8"}
    out = updater.clean_child_env(env)
    assert out["LD_LIBRARY_PATH"] == "/usr/local/lib"
    assert out["DYLD_LIBRARY_PATH"] == "/x"
    assert "LD_LIBRARY_PATH_ORIG" not in out
    assert out["LANG"] == "ko_KR.UTF-8"


# ── 규칙 #5: core 는 UI 무관 ──────────────────────────────────────


def test_updater_import_pulls_no_ui_modules():
    import subprocess
    code = (
        "import sys; import core.updater; "
        "bad=[m for m in ('tkinter','customtkinter','pystray') if m in sys.modules]; "
        "print(','.join(bad))"
    )
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True,
                         text=True, check=True).stdout.strip()
    assert out == ""
