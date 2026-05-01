#!/bin/bash
# Infinite Clipboard — macOS DMG 패키징
#
# dist/Infinite Clipboard.app 을 드래그 설치형 DMG 로 감싼다.
# hdiutil 만 사용 (추가 툴 불필요). .app 은 build_mac.sh 로 먼저 빌드되어 있어야 함.

set -euo pipefail

cd "$(dirname "$0")/.." || exit 1

# version.py 에서 버전 읽기 (spec 과 동일 소스)
VERSION=$(python3 -c "import sys; sys.path.insert(0, '.'); from version import __version__; print(__version__)")
APP_NAME="Infinite Clipboard"
APP_PATH="dist/${APP_NAME}.app"
DMG_NAME="${APP_NAME} ${VERSION}"
DMG_PATH="dist/${DMG_NAME}.dmg"
STAGING="dist/.dmg-staging"

echo "========================================"
echo "  DMG 패키징 — ${DMG_NAME}"
echo "========================================"

# ── 사전 조건 확인 ────────────────────────────────────────────────────
if [ ! -d "$APP_PATH" ]; then
    echo "ERROR: $APP_PATH 없음. build_mac.sh 로 먼저 빌드하세요." >&2
    exit 1
fi

# ── staging 디렉토리 구성 ─────────────────────────────────────────────
# .app + /Applications 심링크 만 담은 깔끔한 뷰를 만든다.
rm -rf "$STAGING"
mkdir -p "$STAGING"
cp -R "$APP_PATH" "$STAGING/"
ln -s /Applications "$STAGING/Applications"

# ── 기존 DMG 제거 후 새로 생성 ────────────────────────────────────────
rm -f "$DMG_PATH"

# UDZO = zlib 압축. .app 크기의 ~40% 수준으로 축소.
# -volname: Finder 에서 마운트될 때 보일 이름.
hdiutil create \
    -volname "$DMG_NAME" \
    -srcfolder "$STAGING" \
    -ov \
    -format UDZO \
    -fs HFS+ \
    "$DMG_PATH" >/dev/null

# ── staging 정리 ──────────────────────────────────────────────────────
rm -rf "$STAGING"

SIZE=$(du -sh "$DMG_PATH" | cut -f1)
echo ""
echo "DMG 생성 완료: $DMG_PATH ($SIZE)"
echo "확인: open '$DMG_PATH'"
