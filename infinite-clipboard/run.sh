#!/bin/bash
cd "$(dirname "$(readlink -f "$0")")"
# KDE Plasma Wayland 등 SNI 기반 DE에서 pystray가 Xorg 백엔드로 떨어지는 것 방지
export PYSTRAY_BACKEND="${PYSTRAY_BACKEND:-appindicator}"
python3 start.py
