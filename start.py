#!/usr/bin/env python3
"""
Infinite Clipboard 런처 — 더블클릭으로 실행

플랫폼을 자동 감지하고, 저장된 설정이 있으면 바로 실행,
없으면 간단한 모드 선택 다이얼로그를 표시한다.

의존성: 표준 라이브러리만 사용 (tkinter)
실행: uv가 없어도 python start.py로 실행 가능
"""

import os
import sys
import json
import subprocess
import platform
import shutil
from pathlib import Path


def get_config_path():
    """OS별 설정 파일 경로"""
    system = platform.system()
    if system == "Windows":
        app_data = os.environ.get("APPDATA", os.path.expanduser("~"))
        return Path(app_data) / "InfiniteClipboard" / "settings.json"
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "InfiniteClipboard" / "settings.json"
    else:
        return Path.home() / ".config" / "InfiniteClipboard" / "settings.json"


def load_config():
    """저장된 설정 로드. 없으면 None"""
    config_path = get_config_path()
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _find_tailscale_cli():
    """tailscale CLI 경로 탐색 (macOS App Store 버전 포함)"""
    path = shutil.which("tailscale")
    if path:
        return path
    # macOS App Store 버전
    if platform.system() == "Darwin":
        mac_cli = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
        if os.path.isfile(mac_cli):
            return mac_cli
    return None


def _detect_tailscale():
    """Tailscale IP 감지 (경량, 외부 의존 없음)"""
    cli = _find_tailscale_cli()
    if not cli:
        return ""
    try:
        result = subprocess.run(
            [cli, "ip", "-4"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return ""


def save_initial_config(mode, host="100.64.0.1", port=9999, key=""):
    """초기 설정 저장"""
    import secrets, string
    if not key:
        key = "".join(secrets.choice(string.digits) for _ in range(6))
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "mode": mode,
        "server_host": host,
        "port": port,
        "auth_key": key,
        "device_name": platform.node(),
    }
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    return config


def find_uv():
    """uv 실행 경로 찾기"""
    uv_path = shutil.which("uv")
    if uv_path:
        return uv_path

    # 기본 설치 경로 확인
    candidates = [
        Path.home() / ".local" / "bin" / "uv",
        Path.home() / ".cargo" / "bin" / "uv",
    ]
    if platform.system() == "Windows":
        candidates = [p.with_suffix(".exe") for p in candidates]

    for p in candidates:
        if p.exists():
            return str(p)

    return None


def _install_uv():
    """uv 자동 설치. 성공하면 uv 경로 반환, 실패하면 None."""
    system = platform.system()
    print("[설정] uv 패키지 매니저를 설치합니다...")

    try:
        if system == "Windows":
            subprocess.run(
                ["powershell", "-ExecutionPolicy", "ByPass", "-c",
                 "irm https://astral.sh/uv/install.ps1 | iex"],
                check=True, timeout=120,
            )
        else:
            subprocess.run(
                ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
                check=True, timeout=120,
            )

        # 설치 후 경로 재탐색
        uv = find_uv()
        if uv:
            print(f"[설정] uv 설치 완료: {uv}")
        return uv

    except Exception as e:
        print(f"[경고] uv 자동 설치 실패: {e}")
        return None


def _pip_fallback(script_dir, config):
    """uv 없이 pip으로 의존성 설치 후 직접 실행"""
    system = platform.system()
    python = sys.executable

    # 플랫폼별 requirements 파일
    if system == "Windows":
        req_file = script_dir / "requirements_win.txt"
    elif system == "Darwin":
        req_file = script_dir / "requirements_mac.txt"
    else:
        req_file = script_dir / "requirements_linux.txt"

    if not req_file.exists():
        req_file = script_dir / "requirements.txt"

    print(f"[설정] pip으로 의존성 설치 중... ({req_file.name})")
    try:
        subprocess.run(
            [python, "-m", "pip", "install", "-q", "-r", str(req_file)],
            check=True, timeout=300, cwd=str(script_dir),
        )
    except Exception as e:
        print(f"[오류] pip 의존성 설치 실패: {e}")
        print(f"  수동 설치: {python} -m pip install -r {req_file}")
        return

    # main.py 직접 실행
    main_py = script_dir / "main.py"
    cmd = [python, str(main_py), "--mode", config["mode"]]

    if config["mode"] == "client":
        cmd.extend(["--host", config.get("server_host", config.get("host", "100.64.0.1"))])
    if config.get("port"):
        cmd.extend(["--port", str(config["port"])])
    # auth_key는 argv로 전달하지 않음 — ps/Task Manager 노출 방지.
    # main.py가 settings.json에서 직접 로드한다.

    if system == "Windows":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        subprocess.run(cmd, cwd=str(script_dir), startupinfo=startupinfo)
    else:
        subprocess.run(cmd, cwd=str(script_dir))


def _detect_distro_pkg_manager():
    """Linux 배포판의 패키지 매니저 감지. (명령어, 설치 서브커맨드) 튜플 반환."""
    for cmd, install in [
        ("pacman", "-S"),
        ("apt", "install"),
        ("dnf", "install"),
        ("zypper", "install"),
    ]:
        if shutil.which(cmd):
            return cmd, install
    return None, None


def _check_linux_deps():
    """Linux 시스템 의존성 검사. 누락 항목이 있으면 안내 메시지를 반환, 없으면 None."""
    if platform.system() != "Linux":
        return None

    missing = []

    # 1) AppIndicator (트레이 아이콘 필수)
    try:
        import gi
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3  # noqa: F401
        except (ValueError, ImportError):
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3  # noqa: F401
    except Exception:
        missing.append("appindicator")

    # 2) 클립보드 도구 (세션 타입에 따라)
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session_type == "wayland":
        if not shutil.which("wl-copy") or not shutil.which("wl-paste"):
            missing.append("wl-clipboard")
    else:
        if not shutil.which("xclip") and not shutil.which("xsel"):
            missing.append("xclip")

    if not missing:
        return None

    # 배포판별 설치 명령어 생성
    pkg_cmd, install_sub = _detect_distro_pkg_manager()
    pkg_map = {
        "pacman": {"appindicator": "libayatana-appindicator", "wl-clipboard": "wl-clipboard", "xclip": "xclip"},
        "apt":    {"appindicator": "libayatana-appindicator3-1 gir1.2-ayatanaappindicator3-0.1", "wl-clipboard": "wl-clipboard", "xclip": "xclip"},
        "dnf":    {"appindicator": "libayatana-appindicator-gtk3", "wl-clipboard": "wl-clipboard", "xclip": "xclip"},
        "zypper": {"appindicator": "libayatana-appindicator3-1", "wl-clipboard": "wl-clipboard", "xclip": "xclip"},
    }

    if pkg_cmd and pkg_cmd in pkg_map:
        pkgs = " ".join(pkg_map[pkg_cmd][m] for m in missing)
        install_cmd = f"sudo {pkg_cmd} {install_sub} {pkgs}"
    else:
        install_cmd = "패키지 매니저를 찾을 수 없습니다. 수동으로 설치해 주세요."

    names = {"appindicator": "트레이 아이콘 (AppIndicator)", "wl-clipboard": "Wayland 클립보드 (wl-clipboard)", "xclip": "X11 클립보드 (xclip)"}
    desc = "\n".join(f"  • {names.get(m, m)}" for m in missing)

    return (
        f"다음 시스템 패키지가 필요합니다:\n{desc}\n\n"
        f"설치 명령어:\n  {install_cmd}\n\n"
        f"설치 후 다시 실행해 주세요."
    )


def _show_missing_deps_dialog(message):
    """누락된 의존성 안내를 tkinter 다이얼로그로 표시."""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Infinite Clipboard — 의존성 필요")
    root.resizable(False, False)
    root.eval("tk::PlaceWindow . center")

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="시스템 패키지 누락", font=("", 13, "bold")).pack(pady=(0, 10))

    text = tk.Text(frame, wrap="word", width=55, height=12, font=("monospace", 10))
    text.insert("1.0", message)
    text.config(state="disabled")
    text.pack(pady=(0, 10))

    # 설치 명령어 복사 버튼
    def copy_cmd():
        # "설치 명령어:\n  ..." 부분에서 명령어만 추출
        for line in message.splitlines():
            stripped = line.strip()
            if stripped.startswith("sudo "):
                root.clipboard_clear()
                root.clipboard_append(stripped)
                copy_btn.config(text="복사됨!")
                root.after(1500, lambda: copy_btn.config(text="명령어 복사"))
                return

    copy_btn = ttk.Button(frame, text="명령어 복사", command=copy_cmd)
    copy_btn.pack(side="left", padx=(0, 5))
    ttk.Button(frame, text="닫기", command=root.destroy).pack(side="right")

    root.mainloop()


def show_setup_dialog():
    """첫 실행 시 모드 선택 다이얼로그 (tkinter — 표준 라이브러리)"""
    import tkinter as tk
    from tkinter import ttk

    result = {}
    ts_ip = _detect_tailscale()

    def on_start():
        mode = "server" if mode_var.get() == "서버" else "client"
        result["mode"] = mode
        result["host"] = host_entry.get().strip() or ""
        result["port"] = int(port_entry.get().strip() or "9999")
        root.destroy()

    def on_mode_change(*_):
        is_server = mode_var.get() == "서버"
        if is_server:
            host_frame.pack_forget()
        else:
            host_frame.pack(fill="x", pady=5, after=mode_frame)
            # 클라이언트 모드 전환 시 자기 IP가 남아있으면 지우기
            current = host_entry.get().strip()
            if current == ts_ip or not current:
                host_entry.delete(0, tk.END)
        # 컨텐츠에 맞게 창 크기 자동 조절
        root.update_idletasks()
        root.geometry("")

    root = tk.Tk()
    root.title("Infinite Clipboard 설정")
    root.resizable(False, False)

    # 가운데 정렬
    root.eval("tk::PlaceWindow . center")

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Infinite Clipboard", font=("", 14, "bold")).pack(pady=(0, 15))

    # 모드 선택
    mode_frame = ttk.Frame(frame)
    mode_frame.pack(fill="x", pady=5)
    ttk.Label(mode_frame, text="모드:").pack(side="left")
    mode_var = tk.StringVar(value="서버")
    mode_combo = ttk.Combobox(mode_frame, textvariable=mode_var, values=["서버", "클라이언트"], state="readonly", width=15)
    mode_combo.pack(side="right")
    mode_var.trace_add("write", on_mode_change)

    # 서버 IP (클라이언트 모드에서만 표시)
    host_frame = ttk.Frame(frame)
    ttk.Label(host_frame, text="서버 IP:").pack(side="left")
    host_entry = ttk.Entry(host_frame, width=18)
    host_entry.pack(side="right")
    # 서버 모드 기본이므로 처음엔 숨김
    host_frame.pack_forget()

    # 포트
    port_frame = ttk.Frame(frame)
    port_frame.pack(fill="x", pady=5)
    ttk.Label(port_frame, text="포트:").pack(side="left")
    port_entry = ttk.Entry(port_frame, width=18)
    port_entry.insert(0, "9999")
    port_entry.pack(side="right")

    # 시작 버튼
    ttk.Button(frame, text="시작", command=on_start).pack(pady=(15, 0), fill="x")

    root.mainloop()
    return result if result else None


def _get_local_venv_path(script_dir):
    """싱크 폴더 밖의 OS별 로컬 venv 경로 반환.

    Windows: %LOCALAPPDATA%/InfiniteClipboard/.venv
    macOS:   ~/Library/Caches/InfiniteClipboard/.venv
    Linux:   ~/.cache/InfiniteClipboard/.venv
    """
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

    venv_dir = base / "InfiniteClipboard" / ".venv"
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    return venv_dir


def _ensure_venv(script_dir, uv):
    """로컬 venv가 올바르게 존재하는지 확인. 없거나 문제가 있으면 생성."""
    venv_dir = _get_local_venv_path(script_dir)
    cfg_path = venv_dir / "pyvenv.cfg"

    needs_create = False

    if not cfg_path.exists():
        needs_create = True
    elif platform.system() == "Linux":
        # Linux에서는 시스템 사이트 패키지 접근이 필수 (gi/AppIndicator)
        try:
            cfg_text = cfg_path.read_text()
            if "include-system-site-packages = true" not in cfg_text:
                print("[설정] venv에 시스템 패키지 접근이 필요합니다. 재생성합니다...")
                shutil.rmtree(venv_dir)
                needs_create = True
        except Exception:
            needs_create = True

    if needs_create and uv:
        cmd = [uv, "venv", str(venv_dir)]
        if platform.system() == "Linux":
            cmd.insert(2, "--system-site-packages")
        subprocess.run(cmd, cwd=str(script_dir))
        print(f"[설정] venv 생성 완료: {venv_dir}")

    return venv_dir


def launch(config):
    """
    앱 실행. 우선순위:
    1. uv가 있으면 uv run main.py (의존성 자동 관리)
    2. uv가 없으면 자동 설치 시도
    3. uv 설치도 실패하면 pip install + python main.py 폴백
    """
    script_dir = Path(__file__).parent.resolve()

    uv = find_uv()

    # uv가 없으면 자동 설치 시도
    if not uv:
        uv = _install_uv()

    # uv가 여전히 없으면 pip 폴백
    if not uv:
        print("[설정] uv를 사용할 수 없어 pip으로 실행합니다.")
        _pip_fallback(script_dir, config)
        return

    # 싱크 폴더 밖의 로컬 venv 경로 확보
    venv_dir = _ensure_venv(script_dir, uv)

    # UV_PROJECT_ENVIRONMENT로 venv 위치를 프로젝트 밖으로 지정
    env = os.environ.copy()
    env["UV_PROJECT_ENVIRONMENT"] = str(venv_dir)

    # uv run으로 실행 (의존성 자동 해결)
    main_py = script_dir / "main.py"
    cmd = [uv, "run", str(main_py), "--mode", config["mode"]]

    if config["mode"] == "client":
        cmd.extend(["--host", config.get("server_host", config.get("host", "100.64.0.1"))])
    if config.get("port"):
        cmd.extend(["--port", str(config["port"])])
    # auth_key는 argv로 전달하지 않음 — ps/Task Manager 노출 방지.
    # main.py가 settings.json에서 직접 로드한다.

    if platform.system() == "Windows":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        subprocess.run(cmd, cwd=str(script_dir), env=env, startupinfo=startupinfo)
    else:
        subprocess.run(cmd, cwd=str(script_dir), env=env)


def main():
    # Linux 시스템 의존성 사전 검사
    dep_msg = _check_linux_deps()
    if dep_msg:
        print(f"[오류] {dep_msg}")
        try:
            _show_missing_deps_dialog(dep_msg)
        except Exception:
            pass  # tkinter도 없으면 콘솔 출력만
        return

    # 설정이 이미 있으면 바로 실행
    config = load_config()
    if config and config.get("mode"):
        launch(config)
        return

    # 첫 실행: 설정 다이얼로그
    result = show_setup_dialog()
    if not result:
        return  # 취소

    # 설정 저장
    save_initial_config(**result)
    launch(result)


if __name__ == "__main__":
    main()
