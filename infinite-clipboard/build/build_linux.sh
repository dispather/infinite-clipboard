#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# Infinite Clipboard v2 — Linux 빌드 (CachyOS/Arch 기본)
#
# 산출물: dist/InfiniteClipboard/InfiniteClipboard (실행 파일)
#         build/infinite-clipboard-<version>-1-x86_64.pkg.tar.zst (pacman 설치 패키지)
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

echo "━━━ Infinite Clipboard v2 — Linux 빌드 ━━━"
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
PKGBUILD="$SCRIPT_DIR/PKGBUILD"
PKG_FILE="$SCRIPT_DIR/infinite-clipboard-${VERSION}-1-x86_64.pkg.tar.zst"

if [[ ! -f "$PKGBUILD" ]]; then
    echo "❌ PKGBUILD 없음: $PKGBUILD"
    exit 1
fi

echo "▶ 패키지 버전: $VERSION"
python - "$PKGBUILD" "$VERSION" <<'PY'
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

# ─── 5. PyInstaller 빌드 ───────────────────────────────────────────────
echo "▶ PyInstaller 실행"
pyinstaller build/infinite-clipboard.spec --noconfirm --clean

# ─── 6. Arch/CachyOS 패키지 생성 ──────────────────────────────────────
if ! command -v makepkg >/dev/null 2>&1; then
    echo "❌ makepkg 없음. CachyOS/Arch의 pacman 패키지를 만들려면 pacman 패키지 도구가 필요합니다."
    exit 1
fi

echo "▶ pacman 패키지 생성"
(cd "$SCRIPT_DIR" && makepkg -f)

if [[ ! -f "$PKG_FILE" ]]; then
    echo "❌ 패키지 생성 실패: $PKG_FILE"
    exit 1
fi

echo
echo "✅ 빌드 완료"
echo "   실행: ./dist/InfiniteClipboard/InfiniteClipboard"
echo "   설치 패키지: $PKG_FILE"
echo "   설치: sudo pacman -U \"$PKG_FILE\""
