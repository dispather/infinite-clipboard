# -*- mode: python ; coding: utf-8 -*-
"""
Infinite Clipboard — 3 OS 공통 PyInstaller spec

이 파일이 빌드 설정의 단일 진실 원천.
build_{mac,linux,win} 스크립트는 `pyinstaller --clean --noconfirm build/infinite-clipboard.spec` 만 호출.

OS 분기:
- APP_NAME: Linux 만 "InfiniteClipboard" (PKGBUILD 가 dist/InfiniteClipboard/ 가정),
  Win/Mac 은 `__app_name__` ("Infinite Clipboard" — 공백 포함, installer/bundle 호환)
- 아이콘: Win=.ico, Mac=.icns, Linux=icon-512.png
- hiddenimports: pystray._{win32,darwin,appindicator}, plyer.platforms.{win,macosx,linux}.notification 등
- runtime_hooks: Linux 만 build/runtime_hooks/linux_system_packages.py 등록 (gi/AppIndicator brige, 함정 #7)

주의:
- 버전 변경 시 version.py 만 수정 (자동 참조). pyproject.toml 만 수동 동기화.
- assets/generated, ui/assets 는 런타임 `_MEIPASS` 기반으로 찾음 → 반드시 datas 에 포함
- PIL hidden imports 3종은 CTkImage 가 Tk 와 연결될 때 런타임 검색되므로 명시 필요 (함정 #6)
- macOS bundle_identifier 는 절대 변경 금지 (함정 #17 — Launchpad 2개 표시 회귀 위험)
"""

from pathlib import Path
import sys

# SPECPATH 는 PyInstaller 가 자동 주입하는 spec 파일 위치 (= build/)
PROJECT_ROOT = Path(SPECPATH).resolve().parent  # type: ignore[name-defined]  # noqa
ASSETS = PROJECT_ROOT / "assets" / "generated"
MAIN_SCRIPT = str(PROJECT_ROOT / "main.py")

# ── 플랫폼 ─────────────────────────────────────────────────────────────
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# ── version.py 에서 버전 메타데이터 로드 (단일 소스) ──────────────────
sys.path.insert(0, str(PROJECT_ROOT))
from version import __version__, __app_name__  # noqa: E402
sys.path.pop(0)

# ── APP_NAME OS 분기 ──────────────────────────────────────────────────
# Linux: PKGBUILD 가 dist/InfiniteClipboard/ 를 가정 → 공백 없는 이름
# Win/Mac: 기존 installer / .app bundle 호환 위해 공백 포함 "Infinite Clipboard"
APP_NAME = "InfiniteClipboard" if IS_LINUX else __app_name__

# ── 아이콘 OS 분기 ────────────────────────────────────────────────────
if IS_WIN:
    ICON = str(ASSETS / "icon.ico")
elif IS_MAC:
    ICON = str(ASSETS / "icon.icns")
else:
    ICON = str(ASSETS / "icon-512.png")

if not Path(ICON).exists():
    raise SystemExit(
        f"[spec] 아이콘 없음: {ICON}\n"
        f"  먼저 build/generate_icons.sh 실행 또는 assets/generated/ 확인"
    )

# ── customtkinter 데이터 (테마/JSON 포함) ─────────────────────────────
import customtkinter  # noqa: E402
CTK_DATA = Path(customtkinter.__path__[0])

# ── 번들에 포함할 데이터 ──────────────────────────────────────────────
# assets/generated 의 개별 파일 iterate (디렉토리 통째보다 명확 — 신규 파일 자동 감지)
asset_datas = [
    (str(ASSETS / f.name), "assets/generated")
    for f in ASSETS.iterdir()
    if f.is_file()
]

datas = [
    (str(CTK_DATA), "customtkinter"),
    (str(PROJECT_ROOT / "ui" / "assets"), "ui/assets"),
] + asset_datas

# ── 숨은 import (공통 + OS 별) ────────────────────────────────────────
# CTkImage 가 Tk PhotoImage 로 변환될 때 런타임 검색 — 누락 시 창 조용히 닫힘 (함정 #6)
hiddenimports = [
    "PIL.ImageTk",
    "PIL._imagingtk",
    "PIL._tkinter_finder",
    "core.protocol", "core.network", "core.clipboard_manager",
    "core.file_transfer", "core.tailscale", "core.privacy", "core.autostart",
    # v3.0 lazy clipboard 팩토리/ABC (크로스 OS). OS별 백엔드는 아래 분기에서 명시
    "core.lazy_clipboard",
]

if IS_WIN:
    hiddenimports += [
        "pystray._win32",
        "plyer.platforms.win.notification",
        "win32api", "win32con", "win32gui", "win32clipboard",
    ]
elif IS_MAC:
    hiddenimports += [
        "pystray._darwin",
        "plyer.platforms.macosx.notification",
        # PyObjC 프레임워크 심볼
        "AppKit", "Foundation", "Quartz", "objc",
        # v3.0 lazy clipboard 백엔드 (팩토리가 함수 안에서 lazy import → 명시, 함정 #6 류)
        "core.lazy_mac",
    ]
elif IS_LINUX:
    from PyInstaller.utils.hooks import collect_submodules  # noqa: E402
    hiddenimports += [
        # appindicator 우선, gtk 폴백
        "pystray._appindicator",
        "pystray._gtk",
        "plyer.platforms.linux.notification",
        # gi / gi.repository 는 시스템 site-packages 에서 로드 (runtime_hooks 참조)
        # v3.0 lazy clipboard 백엔드 — 팩토리(get_lazy_provider)가 함수 안에서 lazy
        # import 하므로 정적 분석이 놓칠 수 있어 명시 (누락 시 번들에서 lazy 가 조용히
        # 비활성 → fallback. 함정 #6 류 silent 회귀 회피).
        "core.lazy_x11",
        "core.lazy_wayland",
        # vendored pywayland 바인딩(core/_wayland_proto): 각 __init__ 이 하위 모듈을
        # 전부 re-export → 패키지 루트만 명시해도 import graph 가 전부 수집.
        "core._wayland_proto.wayland",
        "core._wayland_proto.ext_data_control_v1",
    ]
    # pywayland 런타임(cffi 확장 포함) 서브모듈 일괄 수집. 빌드 env 에 pywayland 가
    # 설치돼 있어야 함(requirements_linux.txt). _proto 바인딩은 위 import graph 가 담당.
    hiddenimports += collect_submodules("pywayland")

# ── 제외 (바이너리 크기 절감) ──────────────────────────────────────────
excludes = [
    "tkinter.test",
    "unittest",
    "test",
    "pytest",
    "IPython",
    "matplotlib",
    "numpy.f2py",
]
# Linux: gi 는 번들에 포함하지 않고 runtime_hooks 가 sys.path 에 시스템 site-packages 추가.
# excludes 에 gi 를 넣으면 PyInstaller 가 import graph 차단 → 런타임 sys.path 조작도 막힘.
# 그래서 gi 는 excludes 에 안 넣음 (Analysis 가 수집 시도했다가 실패해도 경고만, 번들 미포함).

# ── runtime_hooks (Linux 만) ──────────────────────────────────────────
runtime_hooks = []
if IS_LINUX:
    runtime_hooks = [
        str(Path(SPECPATH) / "runtime_hooks" / "linux_system_packages.py"),  # type: ignore[name-defined]  # noqa
    ]

# ── Analysis ──────────────────────────────────────────────────────────
a = Analysis(
    [MAIN_SCRIPT],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # upx 압축은 일부 백신 오탐 유발
    console=False,       # --windowed (Windows: runw.exe / macOS: GUI / Linux: 콘솔 없음)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

# ── macOS .app 번들 ───────────────────────────────────────────────────
# bundle_identifier 는 절대 변경 금지 (함정 #17 — Launchpad 2개 표시 회귀 위험)
# version 은 version.py 자동 참조 (single source of truth)
if IS_MAC:
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=ICON,
        bundle_identifier="com.infiniteclipboard.app",
        version=__version__,
        info_plist={
            "CFBundleName": __app_name__,
            "CFBundleDisplayName": __app_name__,
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            # 메뉴바 전용 — Dock/Cmd+Tab 에서 숨기고 트레이로만 동작
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            # 네트워크/파일 접근 권한 (macOS 10.15+ Gatekeeper 안내)
            "NSAppTransportSecurity": {
                "NSAllowsLocalNetworking": True,
            },
        },
    )
