#!/bin/bash
# Infinite Clipboard - Linux build (spec-based)
#
# 함정 #16 회피: 직접 PyInstaller 인자 대신 build/infinite-clipboard.spec
# 호출. assets/generated, ui/assets, customtkinter datas + PIL hidden imports
# 3종이 자동 포함됨.
#
# venv 는 --system-site-packages 필수: pystray 가 KDE Plasma Wayland 등 SNI
# 기반 DE 에서 트레이 메뉴 띄우려면 시스템 PyGObject + libayatana-appindicator
# 참조 필요.

set -e

echo "========================================"
echo "  Infinite Clipboard - Linux Build"
echo "========================================"

cd "$(dirname "$0")/.." || exit 1

# venv 활성화 (없으면 생성)
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "[1/3] Creating venv with --system-site-packages ..."
    python3 -m venv --system-site-packages .venv
    source .venv/bin/activate
    pip install -r requirements_linux.txt
fi

# 의존성 + PyInstaller 설치 (이미 있으면 빠르게 skip)
echo "[1/3] Installing build deps ..."
pip install -r requirements_linux.txt -q
echo "[2/3] Installing PyInstaller ..."
pip install pyinstaller -q

# spec 기반 빌드 — build_win.bat / build_mac.sh 와 동일 패턴 (함정 #16)
echo "[3/3] Building (spec-based) ..."
pyinstaller --clean --noconfirm build/infinite-clipboard.spec

echo ""
echo "========================================"
echo "  Build complete."
echo "========================================"
echo "Output: dist/Infinite Clipboard/"
echo "Run:    './dist/Infinite Clipboard/Infinite Clipboard'"
