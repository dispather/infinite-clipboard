#!/bin/bash
# Infinite Clipboard — macOS PyInstaller 빌드
#
# 빌드 설정은 build/infinite-clipboard.spec 이 단일 진실 원천.
# 이 스크립트는 "빌드 전용" Python 3.12 (Tk 8.6 링크) + .venv-build 를 준비하고
# pyinstaller 를 spec 으로 호출한다.
#
# 왜 빌드 전용 Python 인가:
# - Homebrew/uv-managed Python 은 모두 Tcl/Tk 9.0 으로 링크됨 → macOS 26 에서
#   창 닫기 시 TkWmProtocolEventProc 에서 세그폴트 발생 (상위 Python 코드로
#   우회 불가). Tk 8.6 링크 Python 을 쓰면 해소.
# - dev 용 .venv (Linux 에서 PyGObject 참조 필요) 를 건드리지 않기 위해
#   빌드 전용 .venv-build 를 별도 유지.

set -euo pipefail

cd "$(dirname "$0")/.." || exit 1

# ── IC_BUILD_DIR — sync 폴더 밖 빌드 출력 (3 OS 공통 인터페이스) ──────
# default: ~/Library/Caches/InfiniteClipboard-Build (macOS 표준 캐시 위치)
IC_BUILD_DIR="${IC_BUILD_DIR:-$HOME/Library/Caches/InfiniteClipboard-Build}"
mkdir -p "$IC_BUILD_DIR"

echo "========================================"
echo "  Infinite Clipboard v2 — macOS 빌드"
echo "========================================"
echo "출력 위치: $IC_BUILD_DIR"
echo "프로젝트:   $PWD"
echo

# ── 도구 확인 ─────────────────────────────────────────────────────────
for tool in uv pyenv brew; do
    if ! command -v $tool >/dev/null 2>&1; then
        echo "ERROR: $tool 가 필요합니다." >&2
        exit 1
    fi
done

# ── tcl-tk@8 확인 ─────────────────────────────────────────────────────
TCLTK_PREFIX="/opt/homebrew/opt/tcl-tk@8"
if [ ! -f "$TCLTK_PREFIX/lib/libtk8.6.dylib" ]; then
    echo "tcl-tk@8 설치 중..."
    brew install tcl-tk@8
fi

# ── 빌드용 Python 3.12 (Tk 8.6 링크) 확인 ─────────────────────────────
BUILD_PY_VERSION="3.12.13"
BUILD_PY="$HOME/.pyenv/versions/${BUILD_PY_VERSION}/bin/python3.12"

needs_rebuild=false
if [ ! -x "$BUILD_PY" ]; then
    needs_rebuild=true
else
    # Tk 링크 검증 — 9.x 로 링크되어 있으면 재빌드
    tk_linkage=$(otool -L "$(find "$HOME/.pyenv/versions/${BUILD_PY_VERSION}" -name '_tkinter*.so' 2>/dev/null | head -1)" 2>/dev/null | grep -oE "libtk[0-9]+\.[0-9]+" | head -1 || true)
    if [ "$tk_linkage" != "libtk8.6" ]; then
        echo "기존 Python ${BUILD_PY_VERSION} 가 Tk 8.6 링크가 아님 (${tk_linkage:-없음}) — 재빌드 필요"
        needs_rebuild=true
    fi
fi

if [ "$needs_rebuild" = true ]; then
    echo "Python ${BUILD_PY_VERSION} 을 tcl-tk@8 링크로 빌드 중... (수 분 소요)"
    export PYTHON_CONFIGURE_OPTS="--with-tcltk-includes='-I${TCLTK_PREFIX}/include' --with-tcltk-libs='-L${TCLTK_PREFIX}/lib -ltcl8.6 -ltk8.6'"
    export LDFLAGS="-L${TCLTK_PREFIX}/lib"
    export CPPFLAGS="-I${TCLTK_PREFIX}/include"
    export PKG_CONFIG_PATH="${TCLTK_PREFIX}/lib/pkgconfig"
    pyenv install -s -f "${BUILD_PY_VERSION}"
fi

# Tk 8.6 링크 재확인
tk_linkage=$("$BUILD_PY" -c "import tkinter; print(tkinter.TkVersion)")
if [ "$tk_linkage" != "8.6" ]; then
    echo "ERROR: 빌드 Python 이 Tk 8.6 이 아닙니다 (현재: $tk_linkage)" >&2
    exit 1
fi
echo "빌드 Python: $BUILD_PY (Tk $tk_linkage)"

# ── 빌드 전용 venv ────────────────────────────────────────────────────
BUILD_VENV=".venv-build"
if [ ! -d "$BUILD_VENV" ] || [ ! -x "$BUILD_VENV/bin/python" ]; then
    echo "빌드 venv 생성: $BUILD_VENV"
    uv venv --python "$BUILD_PY" "$BUILD_VENV"
fi

# venv 의 Python 이 Tk 8.6 을 여전히 참조하는지 확인 (symlink 교체 감지)
venv_tk=$("$BUILD_VENV/bin/python" -c "import tkinter; print(tkinter.TkVersion)" 2>/dev/null || echo "error")
if [ "$venv_tk" != "8.6" ]; then
    echo "빌드 venv 의 Tk 가 8.6 이 아님 (${venv_tk}) — 재생성"
    rm -rf "$BUILD_VENV"
    uv venv --python "$BUILD_PY" "$BUILD_VENV"
fi

# ── 의존성 설치 + pyinstaller ─────────────────────────────────────────
# uv 는 기본적으로 .venv 를 참조 → --active 로 VIRTUAL_ENV 지정 사용 강제.
#
# `uv sync` 대신 `uv pip install -r requirements_mac.txt`:
#   uv.lock 은 [tool.uv].environments = ["win32","darwin","linux"] universal
#   resolution 으로 lock 됨 → [project.optional-dependencies].win 의
#   마커 없는 pywin32 가 lock 에 포함되어 macOS sync 시 강제 만족 시도해
#   "no macOS-compatible wheels" 로 실패. --no-extra win 도 lockfile 의
#   이미 lock 된 항목은 못 빼므로 lockfile 자체를 우회.
#   requirements_mac.txt 는 pip 호환 형식이라 lockfile 무관 + 마커 매끄러움.
export VIRTUAL_ENV="$PWD/$BUILD_VENV"
uv pip install -r requirements_mac.txt
uv pip install --quiet pyinstaller

# ── 필수 자원 존재 검증 ───────────────────────────────────────────────
if [ ! -f "assets/generated/icon.icns" ]; then
    echo "ERROR: assets/generated/icon.icns 없음. build/generate_icons.sh 로 먼저 생성하세요." >&2
    exit 1
fi
if [ ! -d "assets/generated" ] || [ ! -d "ui/assets/icons/png" ]; then
    echo "ERROR: assets/generated 또는 ui/assets/icons/png 없음." >&2
    exit 1
fi

# ── 빌드 (출력은 IC_BUILD_DIR 안 — sync 폴더 밖) ──────────────────────
# uv run 대신 venv 내 pyinstaller 를 직접 호출 — 프로젝트 기본 venv(.venv)
# 로 전환되는 것을 방지
"$BUILD_VENV/bin/pyinstaller" --clean --noconfirm build/infinite-clipboard.spec \
    --distpath "$IC_BUILD_DIR/dist" \
    --workpath "$IC_BUILD_DIR/build"

# ── 애드혹 코드 서명 (2026-07-10, 알림 "받기" 액션 버튼용) ─────────────
# UNUserNotificationCenter(core/notify_mac.py)는 코드 서명된 앱 번들에게만
# 알림 권한을 준다 — 무서명이면 요청 자체가 거부돼 이 백엔드가 항상
# is_supported()=False 로 저하한다(크래시는 안 남, 기존 plyer 알림으로 폴백).
# 애드혹 서명(`--sign -`)은 유료 Apple Developer 계정 없이도 이 요구사항을
# 만족시킨다 — 배포용 서명(Gatekeeper 통과)과는 별개 문제이며, 기존
# `xattr -dr com.apple.quarantine` 수동 우회 안내는 그대로 유효하다.
codesign --force --deep --sign - "$IC_BUILD_DIR/dist/Infinite Clipboard.app"

echo ""
echo "빌드 완료 (산출물 sync 폴더 밖):"
echo "  .app:  $IC_BUILD_DIR/dist/Infinite Clipboard.app"
echo "  실행:  open '$IC_BUILD_DIR/dist/Infinite Clipboard.app'"
echo "  DMG:   build/make_dmg.sh (.app 빌드 후 별도 호출)"
