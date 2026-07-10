#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# Infinite Clipboard v2 — Linux 빌드 (CachyOS/Arch 기본)
#
# 산출물 (모두 sync 폴더 밖 — IC_BUILD_DIR 기본 ~/.cache/InfiniteClipboard-Build):
#   $IC_BUILD_DIR/dist/InfiniteClipboard/InfiniteClipboard (실행 파일)
#   $IC_BUILD_DIR/infinite-clipboard-<version>-1-x86_64.pkg.tar.zst (pacman 패키지)
#
# 변경: IC_BUILD_DIR 환경변수로 빌드 출력 위치 override 가능 (3 OS 공통).
#       sync 폴더 안에는 산출물/임시폴더 일체 생성 안 함 (CLAUDE.md 함정 #20 정책).
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

# ─── IC_BUILD_DIR — sync 폴더 밖 빌드 출력 위치 (3 OS 공통 인터페이스) ──
IC_BUILD_DIR="${IC_BUILD_DIR:-$HOME/.cache/InfiniteClipboard-Build}"
mkdir -p "$IC_BUILD_DIR"

echo "━━━ Infinite Clipboard v2 — Linux 빌드 ━━━"
echo "출력 위치: $IC_BUILD_DIR"
echo "프로젝트:   $ROOT"
echo

# ─── 1. 아이콘 자산 확인 및 필요 시 생성 ───────────────────────────────
if [[ ! -f "assets/generated/icon-512.png" ]]; then
    echo "▶ 아이콘 자산 생성 중..."
    "$SCRIPT_DIR/generate_icons.sh"
fi

# ─── 2. venv 활성화 ────────────────────────────────────────────────────
if [[ -d ".venv" ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
else
    echo "▶ venv 생성 (--system-site-packages: gi/AppIndicator 접근)"
    python3 -m venv --system-site-packages .venv
    # shellcheck disable=SC1091
    source .venv/bin/activate
    pip install -q -r requirements_linux.txt
fi

# ─── 3. PyInstaller 설치 ───────────────────────────────────────────────
pip install -q pyinstaller

# ─── 4. 버전 확인 및 PKGBUILD 동기화 ───────────────────────────────────
VERSION="$(python -c 'from version import __version__; print(__version__)')"
PKGBUILD_SRC="$SCRIPT_DIR/PKGBUILD"
PKG_FILE="$IC_BUILD_DIR/infinite-clipboard-${VERSION}-1-x86_64.pkg.tar.zst"

if [[ ! -f "$PKGBUILD_SRC" ]]; then
    echo "❌ PKGBUILD 없음: $PKGBUILD_SRC"
    exit 1
fi

echo "▶ 패키지 버전: $VERSION"
python - "$PKGBUILD_SRC" "$VERSION" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
version = sys.argv[2]
text = path.read_text(encoding="utf-8")
text = re.sub(r"^pkgver=.*$", f"pkgver={version}", text, flags=re.MULTILINE)
text = re.sub(
    r"infinite-clipboard-[0-9][^-]*-1-x86_64\.pkg\.tar\.zst",
    f"infinite-clipboard-{version}-1-x86_64.pkg.tar.zst",
    text,
)
path.write_text(text, encoding="utf-8")
PY

# ─── 5. PyInstaller 빌드 (출력은 IC_BUILD_DIR 안) ─────────────────────
echo "▶ PyInstaller 실행 → $IC_BUILD_DIR/dist/"
pyinstaller build/infinite-clipboard.spec --noconfirm --clean \
    --distpath "$IC_BUILD_DIR/dist" \
    --workpath "$IC_BUILD_DIR/build"

DIST_DIR="$IC_BUILD_DIR/dist/InfiniteClipboard"
if [[ ! -d "$DIST_DIR" ]]; then
    echo "❌ PyInstaller dist 없음: $DIST_DIR"
    exit 1
fi

# ─── 6. Arch/CachyOS 패키지 생성 (makepkg 도 IC_BUILD_DIR 에서 실행) ──
if ! command -v makepkg >/dev/null 2>&1; then
    echo "❌ makepkg 없음. CachyOS/Arch의 pacman 패키지를 만들려면 pacman 패키지 도구가 필요합니다."
    exit 1
fi

echo "▶ pacman 패키지 생성 → $IC_BUILD_DIR/"
# PKGBUILD 를 IC_BUILD_DIR 로 복사 후 그곳에서 makepkg 실행.
# pkg/, src/, *.pkg.tar.zst 가 모두 IC_BUILD_DIR 에 생성됨 (sync 폴더 밖).
# PKGBUILD 의 package() 는 IC_PROJECT_ROOT/IC_DIST_DIR env 로 실제 dist 위치 인지.
# (저장소 평탄화 이후 프로젝트 루트 == 저장소 루트라 IC_REPO_ROOT 구분 불필요)
cp "$PKGBUILD_SRC" "$IC_BUILD_DIR/PKGBUILD"
(
    cd "$IC_BUILD_DIR"
    export IC_PROJECT_ROOT="$ROOT"
    export IC_DIST_DIR="$DIST_DIR"
    makepkg -f
)

if [[ ! -f "$PKG_FILE" ]]; then
    echo "❌ 패키지 생성 실패: $PKG_FILE"
    exit 1
fi

echo
echo "✅ 빌드 완료 (산출물 모두 sync 폴더 밖)"
echo "   실행:        $DIST_DIR/InfiniteClipboard"
echo "   설치 패키지: $PKG_FILE"
echo "   설치:        sudo pacman -U \"$PKG_FILE\""
