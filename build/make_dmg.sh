#!/bin/bash
# Infinite Clipboard — macOS DMG 패키징
#
# dist/Infinite Clipboard.app 을 드래그 설치형 DMG 로 감싼다.
# hdiutil 만 사용 (추가 툴 불필요). .app 은 build_mac.sh 로 먼저 빌드되어 있어야 함.

set -euo pipefail

cd "$(dirname "$0")/.." || exit 1

# IC_BUILD_DIR — build_mac.sh 와 동일 default (sync 폴더 밖)
IC_BUILD_DIR="${IC_BUILD_DIR:-$HOME/Library/Caches/InfiniteClipboard-Build}"

# version.py 에서 버전 읽기 (spec 과 동일 소스)
VERSION=$(python3 -c "import sys; sys.path.insert(0, '.'); from version import __version__; print(__version__)")
APP_NAME="Infinite Clipboard"
APP_PATH="$IC_BUILD_DIR/dist/${APP_NAME}.app"

# Apple Silicon(arm64)과 Intel(x86_64) 빌드가 동일 버전 문자열을 쓰면
# DMG 파일명이 겹쳐 CI release 단계에서 한쪽이 다른 쪽을 덮어쓴다
# (macos-15 + macos-15-intel 매트릭스, 2026-07-11). arch 를 파일명에 포함하되
# **공백/괄호는 파일명에 넣지 않는다** — GitHub Release 는 업로드 시 asset
# 파일명의 공백/괄호를 전부 `.` 로 치환한다(하이픈은 안전). "(Apple Silicon)"
# 처럼 괄호+공백을 섞으면 "Infinite.Clipboard.3.0.8..Apple.Silicon..dmg" 식으로
# 마침표가 중복 찍혀 보기 흉해진다 — 실측: 릴리스 asset 다운로드해 확인.
# 파일명(DMG_NAME)은 하이픈 suffix 로, Finder 마운트 시 보이는 볼륨 이름
# (VOLNAME, URL 에 노출 안 됨)만 사람이 읽기 좋은 괄호 형태를 유지한다.
case "$(uname -m)" in
    arm64)  ARCH_SUFFIX="apple-silicon"; ARCH_LABEL="Apple Silicon" ;;
    x86_64) ARCH_SUFFIX="intel";         ARCH_LABEL="Intel" ;;
    *)      ARCH_SUFFIX="$(uname -m)";   ARCH_LABEL="$(uname -m)" ;;
esac
DMG_NAME="${APP_NAME} ${VERSION}-${ARCH_SUFFIX}"
VOLNAME="${APP_NAME} ${VERSION} (${ARCH_LABEL})"
DMG_PATH="$IC_BUILD_DIR/dist/${DMG_NAME}.dmg"
STAGING="$IC_BUILD_DIR/dist/.dmg-staging"

echo "========================================"
echo "  DMG 패키징 — ${DMG_NAME}"
echo "========================================"
echo "출력 위치: $IC_BUILD_DIR/dist/"

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
# -volname: Finder 에서 마운트될 때 보일 이름 (파일명과 다름 — 위 주석 참조).
hdiutil create \
    -volname "$VOLNAME" \
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
