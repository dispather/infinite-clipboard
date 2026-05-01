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

echo "========================================"
echo "  Infinite Clipboard v2 — macOS 빌드"
echo "========================================"

cd "$(dirname "$0")/.." || exit 1

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

# ── 의존성 동기화 + pyinstaller ───────────────────────────────────────
# uv 는 기본적으로 .venv 를 참조 → --active 로 VIRTUAL_ENV 지정 사용 강제
export VIRTUAL_ENV="$PWD/$BUILD_VENV"
uv sync --active
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

# ── 빌드 ──────────────────────────────────────────────────────────────
# uv run 대신 venv 내 pyinstaller 를 직접 호출 — 프로젝트 기본 venv(.venv)
# 로 전환되는 것을 방지
"$BUILD_VENV/bin/pyinstaller" --clean --noconfirm build/infinite-clipboard.spec

echo ""
echo "빌드 완료: dist/Infinite Clipboard.app"
echo "실행: open 'dist/Infinite Clipboard.app'"
