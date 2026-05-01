# -*- mode: python ; coding: utf-8 -*-
"""
Infinite Clipboard — 3 OS 공통 PyInstaller spec

이 파일이 빌드 설정의 단일 진실 원천.
build_{mac,linux,win} 스크립트는 `pyinstaller <이 파일>` 만 호출한다.

주의:
- 버전 변경 시 version.py 만 수정하면 됨 (자동 참조)
- assets/generated, ui/assets 는 런타임 `_MEIPASS` 기반으로 찾음 → 반드시 datas 에 포함
- PIL hidden imports 3종은 CTkImage 가 Tk 와 연결될 때 런타임 검색되므로 명시 필요
"""

from pathlib import Path
import sys

# ── 경로 ──────────────────────────────────────────────────────────────
# SPECPATH 는 PyInstaller 가 자동 주입하는 spec 파일 위치
PROJECT_ROOT = Path(SPECPATH).resolve().parent

# version.py 에서 버전 메타데이터 로드 (단일 소스)
sys.path.insert(0, str(PROJECT_ROOT))
from version import __version__, __app_name__  # noqa: E402
sys.path.pop(0)

# customtkinter 내장 에셋(theme JSON, font 등) 경로
import customtkinter  # noqa: E402
CTK_PATH = Path(customtkinter.__path__[0])

# ── 번들에 포함할 데이터 ──────────────────────────────────────────────
datas = [
    (str(CTK_PATH), "customtkinter"),
    (str(PROJECT_ROOT / "assets" / "generated"), "assets/generated"),
    (str(PROJECT_ROOT / "ui" / "assets"), "ui/assets"),
]

# ── 숨은 import ───────────────────────────────────────────────────────
# CTkImage 가 Tk PhotoImage 로 변환될 때 런타임에 찾음 — 누락 시 창이 조용히 닫힘
hiddenimports = [
    "PIL.ImageTk",
    "PIL._imagingtk",
    "PIL._tkinter_finder",
]

# ── Analysis ──────────────────────────────────────────────────────────
a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=__app_name__,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # --windowed
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(PROJECT_ROOT / "assets" / "generated" / "icon.icns")],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=__app_name__,
)

# ── macOS .app 번들 ───────────────────────────────────────────────────
# LSUIElement=1 → Dock/Cmd+Tab 에서 숨기고 트레이 전용으로 동작
# NSHighResolutionCapable → Retina 렌더링 활성화
# CFBundleName/Version → "정보 보기" 및 About 다이얼로그에서 표시
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{__app_name__}.app",
        icon=str(PROJECT_ROOT / "assets" / "generated" / "icon.icns"),
        bundle_identifier="com.infiniteclipboard.app",
        version=__version__,
        info_plist={
            "CFBundleName": __app_name__,
            "CFBundleDisplayName": __app_name__,
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
        },
    )
