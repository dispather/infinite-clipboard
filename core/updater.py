"""
자동 업데이트 — GitHub 릴리스 확인·자산 선택·검증 다운로드·종료 후 설치 명령

2026-09-29 (docs/plans/2026-09-29-auto-update-design.md). 트레이 메뉴의
«업데이트 설치 (vX)» 한 번으로 다운로드 → sha256 대조 → 앱 종료 → 설치 → 재실행.

- 규칙 #5: UI 무관 — tkinter/pystray/customtkinter import 금지. 호출·알림·메뉴는 main.py/ui 가 한다.
- 순수 함수 위주: 플랫폼·실행 파일 경로·환경변수·which 를 인자로 받아 헤드리스 테스트한다
  (함정 #41 — platform.system() 에 암묵 의존하지 않는다).
- 상태(update_state.json)는 settings.json 과 분리한다: 설정 파일 mtime 변화는 앱 재시작
  트리거(main._watch_config_for_restart)라, 여기 쓰면 24시간마다 앱이 재시작된다.
"""

import hashlib
import http.client
import json
import logging
import ntpath
import os
import posixpath
import re
import shlex
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

REPO = "dispather/infinite-clipboard"
# /releases/latest 를 쓰지 않는다 — 수동 publish(draft=false PATCH) 후에도 'Latest' 가
# 이전 버전에 남은 적이 있다(v3.0.8, 2026-07-10). 목록에서 가장 높은 정식 버전을 직접 고른다.
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
CHECK_INTERVAL_SECONDS = 24 * 3600
FIRST_CHECK_DELAY_SECONDS = 30

# 자산 파일명 «계약» — 접미 패턴으로 맞춘다. GitHub 은 업로드 시 공백·괄호를 '.' 로 바꾸므로
# (함정 #37) 접두에 의존하지 않는다. 빌드 스크립트 산출명과의 대조는
# tests/test_update_asset_contract.py 가 한다 — 한 번 배포된 뒤 파일명을 바꾸면
# 모든 설치본의 업데이트가 조용히 끊긴다.
_WINDOWS_ASSET = r"^infinite-clipboard-setup-.+\.exe$"
_LINUX_X86_64_ASSET = r"^infinite-clipboard-.+-x86_64\.pkg\.tar\.zst$"
_MAC_ARM64_ASSET = r"-apple-silicon\.dmg$"
_MAC_INTEL_ASSET = r"-intel\.dmg$"

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

# Linux 패키지 설치 위치 (build/PKGBUILD package())
LINUX_INSTALL_DIR = "/opt/infinite-clipboard/"
LINUX_APP_PATH = "/opt/infinite-clipboard/InfiniteClipboard"


@dataclass(frozen=True)
class UpdateInfo:
    """설치 후보 릴리스 1개 + 이 OS 용 자산."""
    version: str            # "3.0.14"
    asset_name: str
    url: str                # browser_download_url
    sha256: Optional[str]   # GitHub digest "sha256:..." 의 hex, 없으면 None(검증 불가)
    size: int
    html_url: str           # 릴리스 페이지 (자가 설치 불가 환경의 폴백)


class UpdateError(Exception):
    """업데이트 확인/다운로드/준비 실패. reason 은 알림 문구 분기용 코드."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# ── 버전·릴리스 선택 ─────────────────────────────────────────────


def parse_version(tag) -> Optional[tuple]:
    """'v3.0.14' / '3.0.14' → (3, 0, 14). 정식 x.y.z 가 아니면 None."""
    if not isinstance(tag, str):
        return None
    m = _VERSION_RE.match(tag.strip())
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def asset_pattern(system: str, machine: str) -> Optional[str]:
    """이 OS·CPU 에 맞는 자산 파일명 정규식. 배포하지 않는 조합이면 None."""
    machine = (machine or "").lower()
    if system == "Windows":
        return _WINDOWS_ASSET
    if system == "Linux":
        return _LINUX_X86_64_ASSET if machine in ("x86_64", "amd64") else None
    if system == "Darwin":
        return _MAC_ARM64_ASSET if machine == "arm64" else _MAC_INTEL_ASSET
    return None


def pick_update(releases: Iterable, current: str, system: str,
                machine: str) -> Optional[UpdateInfo]:
    """릴리스 목록(GitHub API JSON) → 현재보다 높은 최신 정식 버전의 이 OS 자산.

    draft·prerelease·비semver 태그는 건너뛴다. 가장 높은 정식 버전 «1개»만 본다 —
    그 릴리스에 이 OS 자산이 없으면 더 낮은 버전으로 내려가지 않고 None(업데이트 없음).
    """
    cur = parse_version(current)
    pattern = asset_pattern(system, machine)
    if cur is None or pattern is None:
        return None

    best = None
    best_ver = None
    for rel in releases or []:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        ver = parse_version(rel.get("tag_name"))
        if ver is None or not isinstance(rel.get("assets"), list):
            continue
        if best_ver is None or ver > best_ver:
            best, best_ver = rel, ver
    if best is None or best_ver is None or best_ver <= cur:
        return None
    version = ".".join(str(n) for n in best_ver)

    regex = re.compile(pattern)
    for asset in best["assets"]:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str) and regex.search(name):
            digest = asset.get("digest")
            sha = None
            if isinstance(digest, str) and digest.startswith("sha256:"):
                sha = digest[len("sha256:"):].lower()
            size = asset.get("size")
            return UpdateInfo(
                version=version,
                asset_name=name,
                url=url,
                sha256=sha,
                size=size if isinstance(size, int) else 0,
                html_url=str(best.get("html_url") or f"https://github.com/{REPO}/releases"),
            )
    logger.info(f"[업데이트] v{version} 에 이 OS({system}/{machine}) 자산 없음")
    return None


# ── 자가 설치 가능 판정 ──────────────────────────────────────────


def app_bundle_path(executable: str) -> Optional[Path]:
    """macOS 실행 파일 경로에서 '<X>.app' 번들 경로. 번들 밖이면 None."""
    for parent in PurePosixPath(executable).parents:
        if parent.name.endswith(".app"):
            return Path(str(parent))
    return None


def install_mode(system: str, executable: str, *, frozen: bool, env: dict,
                 which: Callable[[str], Optional[str]],
                 access: Callable[[str, int], bool] = os.access) -> str:
    """이 설치본을 앱이 스스로 업데이트할 수 있는가.

    Returns:
        "silent"      — 무음 설치 + 재실행
        "interactive" — Windows 전체 사용자 설치본: 설치기를 대화형으로(무음이면 per-user
                        사본이 하나 더 생김 — installer.iss PrivilegesRequiredOverridesAllowed)
        "page"        — 자가 설치 불가: 릴리스 페이지를 연다(소스 실행·다른 설치 위치·권한 없음)
    """
    if not frozen:
        return "page"
    if system == "Windows":
        local = env.get("LOCALAPPDATA", "")
        if local:
            programs = ntpath.normcase(ntpath.join(local, "Programs")) + "\\"
            if ntpath.normcase(executable).startswith(programs):
                return "silent"
        return "interactive"
    if system == "Linux":
        if (executable.startswith(LINUX_INSTALL_DIR)
                and which("pacman") and which("pkexec")):
            return "silent"
        return "page"
    if system == "Darwin":
        bundle = app_bundle_path(executable)
        if bundle is not None and access(str(bundle.parent), os.W_OK):
            return "silent"
        return "page"
    return "page"


# ── 상태 파일 (settings.json 과 분리) ───────────────────────────


def state_file() -> Path:
    """update_state.json 경로 — 호출 시점에 config 디렉토리를 해석(테스트 격리 conftest 호환)."""
    import config
    return config._get_config_dir() / "update_state.json"


def load_state() -> dict:
    """없거나 손상됐으면 {} — 업데이트 상태 손상이 앱 시작을 막지 않게."""
    path = state_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(data: dict) -> None:
    """원자적 저장(임시 파일 → os.replace)."""
    path = state_file()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".update_state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_state(**changes) -> dict:
    """키 단위 병합 저장. 값이 None 이면 그 키를 지운다. 저장 실패는 로그만."""
    data = load_state()
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    try:
        save_state(data)
    except OSError as e:
        logger.warning(f"[업데이트] 상태 파일 저장 실패: {e}")
    return data


# ── 자식 프로세스 env ────────────────────────────────────────────


def clean_child_env(environ: dict) -> dict:
    """PyInstaller 번들이 자기 env 에 넣은 값을 걷어낸 자식용 env.

    frozen 앱은 LD_LIBRARY_PATH=<bundle>/_internal 과 _PYI_* 를 자기 env 에 둔다
    [실측 2026-09-29 /proc/<pid>/environ]. 그대로 pacman/pkexec/설치기/새 버전 앱에
    넘기면 번들 라이브러리를 물거나 새 번들이 옛 번들의 상태를 이어받을 수 있다.
    """
    env = dict(environ)
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        orig = env.pop(var + "_ORIG", None)
        if orig is not None:
            env[var] = orig
        else:
            env.pop(var, None)
    for key in list(env):
        if key.startswith("_PYI_") or key == "_MEIPASS2":
            env.pop(key)
    # 새로 뜨는 번들이 부모 번들의 런타임 상태를 이어받지 않게(PyInstaller 6.9+)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    # macOS .app 은 locale 이 비어 뜬다(함정 #14 — clipboard_manager._clean_env 와 같은 값)
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_CTYPE", "UTF-8")
    return env


# ── 네트워크: 릴리스 조회 · 검증 다운로드 ───────────────────────

_ca_source_logged = False
_DOWNLOAD_CHUNK = 64 * 1024
_MAX_API_BODY = 2 * 1024 * 1024


def _ssl_context() -> ssl.SSLContext:
    """HTTPS 검증 컨텍스트. certifi 가 있으면 그 CA 묶음을 쓴다.

    frozen macOS 앱의 Python 은 pyenv + Homebrew OpenSSL 로 빌드돼 기본 CA 경로가
    /opt/homebrew/… 를 가리킬 수 있다[추정] — Homebrew 없는 Mac 에선 검증 실패.
    그래서 mac 빌드는 certifi 를 번들한다(requirements_mac.txt). 어떤 CA 를 썼는지
    1회 로그로 남긴다 — mac-studio(Homebrew 있음)의 성공은 일반 Mac 의 증거가 아니다.
    """
    global _ca_source_logged
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        source = f"certifi {certifi.where()}"
    except (ImportError, OSError):
        ctx = ssl.create_default_context()
        if os.name == "nt":
            # Windows 는 create_default_context 가 시스템 인증서 저장소를 읽는다 — 아래 OpenSSL
            # 기본 경로는 이 PC 에 없는 값이라 «CA 없음»으로 오해를 부른다(a5000 실기 2026-10-01)
            source = "system Windows 인증서 저장소"
        else:
            source = f"system {ssl.get_default_verify_paths()}"
    if not _ca_source_logged:
        _ca_source_logged = True
        logger.info(f"[업데이트] HTTPS CA: {source}")
    return ctx


def _api_request(url: str, accept: str = "application/vnd.github+json") -> urllib.request.Request:
    from version import __version__
    # GitHub API 는 User-Agent 가 없으면 403
    return urllib.request.Request(url, headers={
        "User-Agent": f"InfiniteClipboard/{__version__}",
        "Accept": accept,
    })


def fetch_releases(url: str = RELEASES_API, timeout: float = 10) -> list:
    """GitHub 릴리스 목록 JSON. 실패는 UpdateError(network|rate_limited|bad_response)."""
    try:
        with urllib.request.urlopen(_api_request(url), timeout=timeout,
                                    context=_ssl_context()) as resp:
            body = resp.read(_MAX_API_BODY)
    except urllib.error.HTTPError as e:   # URLError 의 하위 — 먼저 잡는다
        if e.code in (403, 429):
            raise UpdateError("rate_limited", f"HTTP {e.code}") from e
        raise UpdateError("bad_response", f"HTTP {e.code}") from e
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        raise UpdateError("network", str(e)) from e
    try:
        data = json.loads(body)
    except ValueError as e:
        raise UpdateError("bad_response", "JSON 아님") from e
    if not isinstance(data, list):
        raise UpdateError("bad_response", "목록 아님")
    return data


def download_verified(info: UpdateInfo, dest_dir, progress: Optional[Callable[[int, int], None]] = None,
                      timeout: float = 30) -> Path:
    """자산을 dest_dir 에 받아 크기·sha256 을 대조한다. 통과한 경로만 반환.

    실패 시 받던 파일(.part)을 지우고 UpdateError:
      unverifiable — GitHub digest 없음(받기 전에 거부 — 검증 못 하는 파일은 설치 안 함)
      network      — 연결/HTTP 실패
      corrupt      — 중간 끊김·크기 불일치·해시 불일치
    한계: 전송 손상만 잡는다. GitHub 계정·릴리스 자체가 탈취되면 digest 도 같이 바뀐다.
    """
    if not info.sha256:
        raise UpdateError("unverifiable", info.asset_name)
    name = info.asset_name
    if not name or name in (".", "..") or os.path.basename(name) != name or "\\" in name:
        raise UpdateError("bad_response", f"자산 이름 이상: {name!r}")

    dest_dir = Path(dest_dir)
    final = dest_dir / name
    part = dest_dir / (name + ".part")
    digest = hashlib.sha256()
    done = 0
    ok = False
    try:
        try:
            with urllib.request.urlopen(_api_request(info.url, "application/octet-stream"),
                                        timeout=timeout, context=_ssl_context()) as resp, \
                    open(part, "wb") as f:
                total = info.size or int(resp.headers.get("Content-Length") or 0)
                while True:
                    chunk = resp.read(_DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
        except http.client.HTTPException as e:   # IncompleteRead 등 — 본문이 중간에 끊김
            raise UpdateError("corrupt", f"{type(e).__name__} ({done} bytes)") from e
        except (urllib.error.URLError, OSError) as e:
            raise UpdateError("network", str(e)) from e

        if info.size and done != info.size:
            raise UpdateError("corrupt", f"크기 {done} != {info.size}")
        if digest.hexdigest() != info.sha256.lower():
            raise UpdateError("corrupt", "sha256 불일치")
        os.replace(part, final)
        ok = True
        logger.info(f"[업데이트] 다운로드·검증 완료: {name} ({done} bytes)")
        return final
    finally:
        if not ok:
            try:
                part.unlink()
            except OSError:
                pass


# ── 종료 후 설치: helper 스크립트 ────────────────────────────────
#
# 설치(파일 교체)는 앱이 «종료된 뒤»에만 한다. 실행 중에 /opt/infinite-clipboard/* 나
# .app 을 갈면 나중에 지연 import 되는 모듈이 새 파일과 섞여 죽을 수 있다[추정].
# helper 는 3 OS 모두 «설치 결과와 무관하게 마지막에 앱을 다시 띄운다» — 설치가 실패해도
# 옛 버전이 다시 뜨고, 재시작 후 검증(main.py)이 update_state.installing_version 으로
# 실패를 알린다. (Windows 도 Inno [Run] 대신 helper 를 쓰는 이유: [Run] 은 설치 «성공 후»에만
# 실행돼, 실패·취소 시 앱이 꺼진 채 남는다 — 2026-09-29 spec review.)

_PID_WAIT_TICKS = 200          # 0.3s × 200 = 최대 60초 대기 (PID 재사용 대비 상한)
MAC_APP_NAME = "Infinite Clipboard.app"   # DMG 안의 번들 이름 (build/make_dmg.sh APP_NAME)


def _ps_quote(value) -> str:
    """PowerShell 단일 인용 리터럴 — 내부 ' 는 '' 로."""
    return "'" + str(value).replace("'", "''") + "'"


def _linux_helper(pid: int, pkg: Path, app: str, log: Path) -> str:
    q = shlex.quote
    return f"""#!/bin/sh
# Infinite Clipboard 업데이트 helper (Linux) — core/updater.py 가 생성
exec >>{q(str(log))} 2>&1
echo "[update-helper] $(date '+%F %T') start pid={pid}"
i=0
while kill -0 {pid} 2>/dev/null && [ "$i" -lt {_PID_WAIT_TICKS} ]; do sleep 0.3; i=$((i+1)); done
if pkexec pacman -U --noconfirm {q(str(pkg))}; then
  echo "[update-helper] installed"
else
  echo "[update-helper] install failed rc=$?"
fi
rm -f {q(str(pkg))}
nohup {q(app)} >/dev/null 2>&1 &
echo "[update-helper] relaunched"
"""


def _mac_helper(pid: int, target: Path, log: Path) -> str:
    q = shlex.quote
    t = str(target)
    return f"""#!/bin/sh
# Infinite Clipboard 업데이트 helper (macOS) — core/updater.py 가 생성
exec >>{q(str(log))} 2>&1
T={q(t)}
N={q(t + ".new")}
O={q(t + ".old")}
echo "[update-helper] $(date '+%F %T') start pid={pid}"
i=0
while kill -0 {pid} 2>/dev/null && [ "$i" -lt {_PID_WAIT_TICKS} ]; do sleep 0.3; i=$((i+1)); done
rm -rf "$O"
if mv "$T" "$O" && mv "$N" "$T"; then
  rm -rf "$O"
  echo "[update-helper] installed"
else
  echo "[update-helper] install failed"
  [ -d "$T" ] || mv "$O" "$T"
  rm -rf "$N"
fi
xattr -dr com.apple.quarantine "$T" 2>/dev/null
open "$T"
echo "[update-helper] relaunched"
"""


def _windows_helper(pid: int, mode: str, setup: Path, app: str, log: Path) -> str:
    args = "" if mode == "interactive" else " -ArgumentList '/SILENT','/SUPPRESSMSGBOXES','/NORESTART'"
    return f"""# Infinite Clipboard 업데이트 helper (Windows) — core/updater.py 가 생성
$ErrorActionPreference = 'Continue'
$log = {_ps_quote(log)}
function Log($m) {{ Add-Content -LiteralPath $log -Value ("[update-helper] " + $m) -Encoding UTF8 }}
Log ("start pid={pid} " + (Get-Date -Format s))
Wait-Process -Id {pid} -Timeout 60 -ErrorAction SilentlyContinue
try {{
  $p = Start-Process -FilePath {_ps_quote(setup)}{args} -Wait -PassThru
  Log ("setup exit=" + $p.ExitCode)
}} catch {{
  Log ("setup failed: " + $_)
}}
Remove-Item -LiteralPath {_ps_quote(setup)} -ErrorAction SilentlyContinue
Start-Process -FilePath {_ps_quote(app)}
Log "relaunched"
"""


def post_exit_command(system: str, mode: str, pid: int, asset_path, *, executable: str,
                      log_path, workdir=None) -> list:
    """앱 종료 «후» 실행할 명령. helper 스크립트를 workdir(기본: 새 임시 폴더)에 쓴다.

    mode 는 install_mode() 의 "silent" | "interactive". "page" 는 설치하지 않으므로 ValueError.
    mac 은 asset_path(DMG)가 아니라 prepare_mac_bundle() 이 미리 만든 '<번들>.new' 를 쓴다.
    """
    if mode not in ("silent", "interactive"):
        raise ValueError(f"설치 명령을 만들 수 없는 모드: {mode}")
    workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="ic_update_"))
    log_path = Path(log_path)
    asset_path = Path(asset_path)

    if system == "Windows":
        helper = workdir / "ic-update-helper.ps1"
        # UTF-8 BOM: Windows PowerShell 5.1 은 BOM 없는 .ps1 을 ANSI 로 읽어 한글 경로가 깨진다
        helper.write_text(_windows_helper(pid, mode, asset_path, executable, log_path),
                          encoding="utf-8-sig")
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-File", str(helper)]

    if system == "Linux":
        body = _linux_helper(pid, asset_path, executable, log_path)
    elif system == "Darwin":
        bundle = app_bundle_path(executable)
        if bundle is None:
            raise UpdateError("prepare", f".app 번들 밖에서 실행 중: {executable}")
        body = _mac_helper(pid, bundle, log_path)
    else:
        raise UpdateError("prepare", f"지원하지 않는 OS: {system}")
    helper = workdir / "ic-update-helper.sh"
    helper.write_text(body, encoding="utf-8")
    return ["/bin/sh", str(helper)]


def prepare_mac_bundle(dmg, target_app, run=subprocess.run) -> Path:
    """앱 «실행 중»에 DMG 를 마운트해 새 번들을 '<target>.new' 로 복사한다.

    종료 후 helper 는 mv 두 번만 하면 되게 — 종료 뒤 실패 창을 최소로. 실패는
    UpdateError("prepare"), 마운트는 항상 분리한다.
    """
    target_app = Path(target_app)
    new_app = target_app.with_name(target_app.name + ".new")
    mount = tempfile.mkdtemp(prefix="ic_update_mnt_")
    env = clean_child_env(os.environ)

    def _run(cmd):
        return run(cmd, capture_output=True, text=True, timeout=300, env=env,
                   stdin=subprocess.DEVNULL)

    r = _run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen",
              "-mountpoint", mount, str(dmg)])
    if r.returncode != 0:
        raise UpdateError("prepare", f"hdiutil attach 실패: {r.stderr.strip()}")
    try:
        if new_app.exists():
            import shutil
            shutil.rmtree(new_app, ignore_errors=True)
        # macOS 전용 경로 — os.path.join 은 테스트가 도는 Windows CI 에서 역슬래시를 붙인다
        r = _run(["ditto", posixpath.join(mount, MAC_APP_NAME), str(new_app)])
        if r.returncode != 0:
            raise UpdateError("prepare", f"ditto 실패: {r.stderr.strip()}")
    finally:
        _run(["hdiutil", "detach", mount, "-force"])
    return new_app


def spawn_post_exit(cmd: list, system: str) -> None:
    """helper 를 앱과 분리된 프로세스로 띄운다 — 앱이 곧 종료돼도 살아남게."""
    kwargs = dict(env=clean_child_env(os.environ), stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if system == "Windows":
        kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                   | subprocess.CREATE_NO_WINDOW)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)
