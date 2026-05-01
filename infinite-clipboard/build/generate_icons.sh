#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# Infinite Clipboard — 아이콘 자산 자동 생성
#
# 입력:  assets/icon.svg (단일 소스)
# 출력:  assets/generated/
#   tray-green.png / tray-amber.png / tray-red.png / tray-gray.png  (64px, 트레이 상태별)
#   tray-template.png  (512px, macOS 메뉴바 다크/라이트 템플릿 — 흑백 alpha)
#   icon-512.png       (리눅스 앱 아이콘 & 빌드용 마스터)
#   icon.ico           (Windows 멀티사이즈: 16/32/48/64/128/256)
#   icon.icns          (macOS: 16~1024 표준 slot)
#
# 의존: rsvg-convert (librsvg), ImageMagick (magick/convert)
#        macOS에서는 선택적으로 iconutil 사용
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
SRC="$ROOT/assets/icon.svg"
OUT="$ROOT/assets/generated"

if [[ ! -f "$SRC" ]]; then
    echo "❌ 소스 SVG 없음: $SRC" >&2
    exit 1
fi

mkdir -p "$OUT"

# 의존성 체크 + 자동 안내
need() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "❌ '$1' 명령을 찾을 수 없습니다." >&2
        echo "   Arch/CachyOS:  sudo pacman -S $2" >&2
        echo "   Debian/Ubuntu: sudo apt install $2" >&2
        echo "   macOS:         brew install $2" >&2
        exit 1
    fi
}
need rsvg-convert librsvg
# ImageMagick 7+ 는 magick, 6 는 convert
if command -v magick >/dev/null 2>&1; then
    IM="magick"
elif command -v convert >/dev/null 2>&1; then
    IM="convert"
else
    echo "❌ ImageMagick 없음. 설치 필요." >&2
    exit 1
fi

echo "▶ 소스: $SRC"
echo "▶ 출력: $OUT"
echo

# ─── 상태 색상 매핑 ────────────────────────────────────────────────────
# tray.py와 동기화 필수
declare -A STATE_COLORS=(
    [green]="#10b981"   # 연결
    [amber]="#f59e0b"   # 대기
    [red]="#ef4444"     # 끊김
    [gray]="#9ca3af"    # 초기
)

# ─── 1. 상태별 트레이 아이콘 (512px 마스터 → 런타임 리샘플 용이) ──────
echo "━━━ 트레이 상태별 PNG ━━━"
for state in "${!STATE_COLORS[@]}"; do
    color="${STATE_COLORS[$state]}"
    # SVG의 --accent CSS 변수를 sed로 주입 (간단: 기본값 #10b981을 치환)
    temp_svg="$(mktemp --suffix=.svg)"
    sed "s|#10b981|$color|g" "$SRC" > "$temp_svg"
    rsvg-convert -w 512 -h 512 "$temp_svg" -o "$OUT/tray-$state.png"
    rm -f "$temp_svg"
    echo "  ✓ tray-$state.png  ($color)"
done

# ─── 2. 앱 아이콘 마스터 (green = 연결 상태가 기본) ────────────────────
cp "$OUT/tray-green.png" "$OUT/icon-512.png"
echo "  ✓ icon-512.png (앱 아이콘 마스터, green 기반)"

# ─── 3. macOS 메뉴바 템플릿 (단색 + alpha) ─────────────────────────────
echo
echo "━━━ macOS 템플릿 ━━━"
# squircle을 검정 1색으로, ∞ 흰색 유지 → alpha를 검정 실루엣으로 변환
# 간단 접근: squircle 채우기를 black으로 바꾼 SVG 생성 후 렌더
temp_svg="$(mktemp --suffix=.svg)"
sed 's|#10b981|#000000|g' "$SRC" > "$temp_svg"
rsvg-convert -w 512 -h 512 "$temp_svg" -o "$OUT/tray-template.png"
rm -f "$temp_svg"
echo "  ✓ tray-template.png (다크/라이트 자동 반전)"

# ─── 4. Windows .ico ──────────────────────────────────────────────────
echo
echo "━━━ Windows .ico (멀티사이즈) ━━━"
tmp_dir="$(mktemp -d)"
for size in 16 24 32 48 64 128 256; do
    rsvg-convert -w "$size" -h "$size" "$SRC" -o "$tmp_dir/icon-$size.png"
done
"$IM" "$tmp_dir"/icon-{16,24,32,48,64,128,256}.png "$OUT/icon.ico"
rm -rf "$tmp_dir"
echo "  ✓ icon.ico (16/24/32/48/64/128/256)"

# ─── 5. macOS .icns ───────────────────────────────────────────────────
echo
echo "━━━ macOS .icns ━━━"
iconset="$(mktemp -d)"
# Apple 표준 slot 이름 — iconutil과 호환되게 구성
declare -a ICNS_SLOTS=(
    "16x16:16" "16x16@2x:32"
    "32x32:32" "32x32@2x:64"
    "128x128:128" "128x128@2x:256"
    "256x256:256" "256x256@2x:512"
    "512x512:512" "512x512@2x:1024"
)
for slot in "${ICNS_SLOTS[@]}"; do
    name="${slot%%:*}"
    size="${slot##*:}"
    rsvg-convert -w "$size" -h "$size" "$SRC" -o "$iconset/icon_$name.png"
done

if command -v iconutil >/dev/null 2>&1; then
    # macOS에서 실행하면 iconutil이 네이티브 .icns 생성
    mv "$iconset" "$iconset.iconset"
    iconutil -c icns "$iconset.iconset" -o "$OUT/icon.icns"
    rm -rf "$iconset.iconset"
    echo "  ✓ icon.icns (iconutil 사용, 네이티브)"
else
    # Linux에서는 ImageMagick의 icns 인코더 사용
    "$IM" \
        "$iconset/icon_16x16.png" "$iconset/icon_32x32.png" \
        "$iconset/icon_128x128.png" "$iconset/icon_256x256.png" \
        "$iconset/icon_512x512.png" "$iconset/icon_512x512@2x.png" \
        "$OUT/icon.icns"
    rm -rf "$iconset"
    echo "  ✓ icon.icns (ImageMagick 사용, iconutil 없음)"
fi

echo
echo "✅ 트레이/앱 아이콘 자산 생성 완료"

# ─── 6. UI 아이콘 세트 (Lucide) ──────────────────────────────────────
#  입력:  ui/assets/icons/src/*.svg  (21개)
#  출력:  ui/assets/icons/png/<size>/<color>/<name>.png
#          크기:  16 / 20 / 24 / 32 px  (HiDPI 대비로 32 포함)
#          색상:  text (#e4e4e7) / dim (#71717a) / accent (#10b981)
# Lucide SVG 는 stroke="currentColor" 라 sed 로 원하는 색 주입.
echo
echo "━━━ UI 아이콘 세트 (Lucide → PNG) ━━━"

UI_SRC="$ROOT/ui/assets/icons/src"
UI_OUT="$ROOT/ui/assets/icons/png"

if [[ ! -d "$UI_SRC" ]]; then
    echo "  ⚠ $UI_SRC 없음, UI 아이콘 렌더 건너뜀"
else
    # 색상 맵: 이름 → hex (theme.py 와 동기화 필수)
    declare -A UI_COLORS=(
        [text]="#e4e4e7"     # semantic.text (primary)
        [dim]="#71717a"      # semantic.text_dim (secondary)
        [accent]="#10b981"   # semantic.signal_ok (mint, 강조)
    )
    declare -a UI_SIZES=(16 20 24 32)

    icon_count=0
    for svg in "$UI_SRC"/*.svg; do
        [[ -f "$svg" ]] || continue
        name="$(basename "$svg" .svg)"
        for color_name in "${!UI_COLORS[@]}"; do
            hex="${UI_COLORS[$color_name]}"
            # currentColor 를 지정 hex 로 치환
            temp_svg="$(mktemp --suffix=.svg)"
            sed "s|currentColor|$hex|g" "$svg" > "$temp_svg"
            for size in "${UI_SIZES[@]}"; do
                out_dir="$UI_OUT/$size/$color_name"
                mkdir -p "$out_dir"
                rsvg-convert -w "$size" -h "$size" "$temp_svg" -o "$out_dir/$name.png"
            done
            rm -f "$temp_svg"
        done
        icon_count=$((icon_count + 1))
    done
    echo "  ✓ $icon_count 아이콘 × ${#UI_SIZES[@]} 크기 × ${#UI_COLORS[@]} 색상 = $((icon_count * ${#UI_SIZES[@]} * ${#UI_COLORS[@]})) PNG"
    echo "  출력: $UI_OUT/{16|20|24|32}/{text|dim|accent}/*.png"
fi

echo
echo "✅ 모든 아이콘 자산 생성 완료"
echo "   트레이/앱: $OUT"
[[ -d "$UI_OUT" ]] && echo "   UI 세트:   $UI_OUT"
