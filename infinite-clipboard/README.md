# Infinite Clipboard

Tailscale VPN으로 연결된 Windows/macOS/Linux PC 간 클립보드(텍스트/이미지)와 파일/폴더를 실시간 공유하는 트레이 상주 앱.

- **프로토콜**: TCP 소켓 + 4바이트 길이 헤더 + JSON/바이너리 프레임
- **인증**: 128비트 랜덤 공유 키 (`token_urlsafe(16)`) + SHA-256 해시 검증 + peer_id 핸드셰이크
- **파일/이미지 전송 (lazy)**: 복사 시 0바이트 offer만 전파, 받는 PC가 붙여넣기(또는 전송창 "받기" 버튼)로 fetch를 트리거해야 실제 전송 시작 — 1MB 청크 + xxHash64(청크) + SHA-256(전체) 이중 검증 + 체크포인트 이어받기. 텍스트는 그대로 즉시 동기화
- **UI**: pystray 시스템 트레이 + customtkinter 설정·이력·전송 창

---

## 사용자 설치 (배포판)

GitHub Releases 페이지에서 OS 별 패키지를 받아 설치한다. 모든 PC의 `auth_key`는 **반드시 동일해야** 한다 (아래 "최초 1회 키 공유" 참조).

| OS | 파일 (X.Y.Z = 버전) | 설치 방법 |
|----|---------------------|-----------|
| Linux (Arch/CachyOS) | `infinite-clipboard-X.Y.Z-1-x86_64.pkg.tar.zst` | `sudo pacman -U <파일>` |
| macOS (Apple Silicon) | `Infinite Clipboard X.Y.Z.dmg` | DMG 열기 → `/Applications`로 드래그 → **아래 Gatekeeper 우회 필수** |
| Windows | `infinite-clipboard-setup-X.Y.Z.exe` | 설치 프로그램 실행 → 안내 따르기 (per-user, 관리자 권한 불필요) |

설치 후:
- Linux: 애플리케이션 메뉴 → Infinite Clipboard
- macOS: 런치패드 또는 `open "/Applications/Infinite Clipboard.app"`. 아래 Gatekeeper 우회를 먼저 수행해야 정상 실행됨
- Windows: 시작 메뉴 → Infinite Clipboard (설치 시 "자동 시작" 체크 가능)

### macOS Gatekeeper 우회 (1회 필수)

DMG 가 코드 서명되어 있지 않아 macOS 가 quarantine 속성을 붙여 차단한다 ("Apple은 ... 악성 코드가 없음을 확인할 수 없습니다"). 다음 명령으로 1회 해제한다:

```sh
xattr -dr com.apple.quarantine "/Applications/Infinite Clipboard.app"
```

해제 후엔 일반 앱처럼 실행 + 자동 시작 가능. 새 버전을 받아 덮어쓸 때마다 같은 명령을 1회 다시 실행해야 한다.

> 우클릭 → 열기 방식은 unsigned ARM64 빌드에서 차단 다이얼로그가 그대로 떠 작동하지 않을 수 있다. `xattr` 명령이 가장 확실하다.

### 최초 1회 키 공유

앱은 첫 실행 시 각 PC별로 랜덤 `auth_key`를 자동 생성한다. PC 간 연결이 되려면 모든 PC가 같은 키를 가져야 한다.

```
1. PC A에서 앱 실행 → 설정 폴더의 settings.json 에 auth_key 생성
2. PC A의 auth_key 값을 복사
3. 다른 PC의 settings.json 의 auth_key 에 동일한 값 붙여넣기 + 앱 재시작
   (또는 트레이 → 설정 → Auth Key 에 직접 입력)
```

설정 파일 위치:

| OS | 경로 |
|----|------|
| Linux | `~/.config/InfiniteClipboard/settings.json` |
| macOS | `~/Library/Application Support/InfiniteClipboard/settings.json` |
| Windows | `%APPDATA%\InfiniteClipboard\settings.json` |

---

## 사용법

- **텍스트 공유**: 아무 PC에서 Ctrl+C → 연결된 모든 PC에 즉시 동기화 (별도 조작 불필요)
- **파일/이미지 전송 (lazy)**: 파일/폴더/이미지를 복사하면 다른 PC들에 "받을 수 있다"는 알림만 감 — 실제 전송은 그 PC에서 **Ctrl+V 하거나, 트레이 → Transfers 창의 "받기" 버튼을 눌러야** 시작됨(복사만 해도 자동으로 전송되지 않음). 완료되면 `~/Downloads/` (또는 설정한 경로)에 저장
- **설정 변경**: 트레이 아이콘 우클릭 → Settings
- **이력 확인**: 트레이 아이콘 우클릭 → Clipboard History
- **전송 진행률/받기**: 트레이 아이콘 우클릭 → Transfers
- **자동 시작**: 설정창에 "OS 시작 시 자동 실행" 스위치

### 트레이 아이콘 색상

| 색상 | 의미 |
|------|------|
| 초록 | 서버: 클라이언트 연결됨 / 클라이언트: 서버 연결됨 |
| 노랑 | 서버 대기 중 (클라이언트 없음) |
| 빨강 | 클라이언트 연결 끊김 |
| 회색 | 초기 상태 |

---

## 빌드 가이드

각 OS에서 네이티브로 실행되는 배포판을 만드는 방법. 소스 코드 변경 후 재배포하거나, 새 PC에 패키지를 뿌릴 때 사용.

### 공통 전제

- Python 3.10 이상
- Git으로 클론된 프로젝트 폴더 또는 클라우드 동기화된 폴더
- 인터넷 연결 (첫 빌드 시 의존성 다운로드)

### 공통 워크플로우

```
┌─────────────────────────┐
│ assets/icon.svg (소스)   │
└───────────┬─────────────┘
            │ generate_icons.sh
            ▼
┌─────────────────────────┐
│ assets/generated/        │  ← 각 OS에 공통. 한 PC에서 생성해 클라우드 동기화로 공유 가능
│   tray-{green|amber|..}  │
│   icon.png / ico / icns  │
└───────────┬─────────────┘
            │ build_{linux|mac|win}
            ▼
┌─────────────────────────┐
│ dist/InfiniteClipboard   │  ← PyInstaller onedir 번들
└───────────┬─────────────┘
            │ make_dmg / makepkg / iscc
            ▼
┌─────────────────────────┐
│ 설치 가능한 배포 패키지     │
│  .pkg.tar.zst / .dmg /   │
│  Setup.exe                │
└─────────────────────────┘
```

---

### 0. 자동 빌드 (GitHub Actions) — 권장

로컬 PC 를 거치지 않고 클라우드 러너에서 **3 OS 패키지를 동시에 빌드**한다.
`.github/workflows/build.yml` 이 정의돼 있다.

**트리거**

| 방법 | 동작 |
|------|------|
| `git tag vX.Y.Z && git push origin vX.Y.Z` | 3 OS 빌드 + GitHub Releases **draft** 생성 + installer/.pkg/.dmg 자동 첨부 |
| GitHub Actions 웹 UI → "Run workflow" | 빌드만 수행 (artifact 에 남음, Release 미생성) |

**산출물**

- Windows: `infinite-clipboard-setup-X.Y.Z.exe` (Inno Setup per-user installer)
- Linux: `infinite-clipboard-X.Y.Z-1-x86_64.pkg.tar.zst` (Arch/CachyOS pacman 패키지)
- macOS: `Infinite Clipboard X.Y.Z.dmg` (Apple Silicon ARM64, 드래그 설치형)

**Release 절차**

1. `version.py` 와 `pyproject.toml` 의 버전 동기화 후 commit
2. `git tag vX.Y.Z && git push origin vX.Y.Z`
3. Actions 탭에서 3 OS 빌드 완료 확인 (Windows/Linux 각 1-2분, macOS 첫 빌드 15-20분 / 캐시 후 5분)
4. Releases 페이지에서 draft 의 changelog 검토 → **Publish**

**제약**

- **macOS 코드 서명 없음** → DMG 다운로드 시 Gatekeeper 차단. 사용자가 1회 수동 우회 필요:
  ```bash
  # /Applications/Infinite\ Clipboard.app 으로 끌어다 놓은 후
  xattr -dr com.apple.quarantine "/Applications/Infinite Clipboard.app"
  ```
  정식 해결은 Apple Developer Program ($99/년) 등록 후 워크플로우에 서명 + 공증 step 추가 (별도 트랙)
- macOS 빌드는 **Apple Silicon (ARM64) only**. Intel Mac 사용 시 `macos-13` 으로 별도 matrix 필요 (현재 빠짐)

이하 "1. 아이콘 자산 생성" ~ "4. Windows 빌드" 는 **로컬 빌드 시** 참조한다.

---

### 1. 아이콘 자산 생성 (최초 1회, 또는 `assets/icon.svg` 수정 시)

아이콘 SVG를 4가지 상태색(green/amber/red/gray) PNG와 `.ico` (Windows), `.icns` (macOS), `icon-512.png` (Linux)로 일괄 변환한다.

```bash
# 의존성 (Linux/macOS 중 한 곳에서 한 번 설치)
#   Arch/CachyOS:  sudo pacman -S librsvg imagemagick
#   macOS:         brew install librsvg imagemagick
#   Debian/Ubuntu: sudo apt install librsvg2-bin imagemagick

./build/generate_icons.sh
```

결과물은 `assets/generated/`에 생성되며, **git에 포함**되므로 Windows 등 다른 PC에서 별도 변환 없이 바로 사용 가능하다. Windows에서는 이 스크립트를 돌릴 필요가 없다(이미 생성된 파일 그대로 씀).

변환 도구가 없을 때 자동 안내:
```
❌ 'rsvg-convert' 명령을 찾을 수 없습니다.
   Arch/CachyOS:  sudo pacman -S librsvg
   Debian/Ubuntu: sudo apt install librsvg2-bin
   macOS:         brew install librsvg
```

---

### 2. Linux 빌드 (CachyOS/Arch)

#### (a) PyInstaller onedir 번들 생성

```bash
# 시스템 의존성 (최초 1회)
sudo pacman -S python-gobject libayatana-appindicator wl-clipboard xclip

# 빌드 스크립트 실행
chmod +x build/build_linux.sh
./build/build_linux.sh
```

수행 내용:
1. `assets/generated/icon-512.png` 없으면 `generate_icons.sh` 자동 실행
2. `.venv` 없으면 `--system-site-packages` 로 생성 (AppIndicator 접근용)
3. `requirements_linux.txt` 설치
4. PyInstaller 6.0+ 설치
5. `build/infinite-clipboard.spec` 로 빌드

결과: `dist/InfiniteClipboard/InfiniteClipboard` (실행 파일, 약 129MB 번들)

바로 테스트:
```bash
./dist/InfiniteClipboard/InfiniteClipboard --no-tray --debug
```

#### (b) pacman 패키지(.pkg.tar.zst) 만들기

```bash
cd build
makepkg -f          # PKGBUILD 를 읽어 패키지화
```

결과: `build/infinite-clipboard-X.Y.Z-1-x86_64.pkg.tar.zst`

설치/제거:
```bash
sudo pacman -U infinite-clipboard-X.Y.Z-1-x86_64.pkg.tar.zst
sudo pacman -R infinite-clipboard
```

패키지 내용:
- `/opt/infinite-clipboard/` — 실행 파일 + `_internal/` 런타임
- `/usr/bin/infinite-clipboard` — 터미널 실행용 심볼릭 링크
- `/usr/share/applications/infinite-clipboard.desktop` — 애플리케이션 메뉴
- `/usr/share/icons/hicolor/512x512/apps/infinite-clipboard.png`
- `/usr/share/licenses/infinite-clipboard/LICENSE`

의존성(`libayatana-appindicator`, `python-gobject`, `wl-clipboard`, `xclip`)은 pacman이 자동 설치.

---

### 3. macOS 빌드

#### (a) `.app` 번들 생성

```bash
# 시스템 의존성 (최초 1회)
brew install librsvg imagemagick

# 빌드
chmod +x build/build_mac.sh
./build/build_mac.sh
```

수행 내용:
1. 아이콘 자산 확인 (`.icns` 없으면 자동 생성)
2. `.venv` 없으면 생성 + `requirements_mac.txt` 설치
3. PyInstaller 로 `.app` 번들 생성
4. `codesign --force --deep --sign -` 로 ad-hoc 서명

결과: `dist/InfiniteClipboard.app`

속성:
- `LSUIElement=1` — Dock 아이콘 없이 메뉴바 전용
- `console=False` — 터미널 창 안 뜸
- `bundle_identifier=com.dispather.infinite-clipboard`
- `CFBundleVersion=X.Y.Z`

바로 실행:
```bash
open dist/InfiniteClipboard.app
```

첫 실행 시 Gatekeeper 경고가 뜨면 Finder에서 해당 `.app`을 우클릭 → 열기로 1회 통과.

#### (b) DMG 패키징

```bash
chmod +x build/make_dmg.sh
./build/make_dmg.sh
```

결과: `dist/InfiniteClipboard-X.Y.Z.dmg`

DMG를 열면 `/Applications` 심볼릭 링크가 함께 있어 드래그 설치 UX 제공.

---

### 4. Windows 빌드

#### (a) PyInstaller onedir 번들

```cmd
:: 전제: Python 3.10+ 설치됨 (PATH 에 등록), assets\generated\ 가 동기화됨
build\build_win.bat
```

수행 내용:
1. `assets\generated\icon.ico` 존재 확인 (없으면 에러로 중단. Linux/macOS에서 먼저 생성 필요)
2. `.venv` 없으면 생성 + `requirements_win.txt` 설치
3. PyInstaller 설치
4. 공통 spec 으로 빌드

결과: `dist\InfiniteClipboard\InfiniteClipboard.exe`

#### (b) Inno Setup 인스톨러

1. **Inno Setup 설치**: https://jrsoftware.org/isdl.php (무료)
2. `iscc` 를 PATH 에 등록하거나 Inno Setup Compiler GUI 실행
3. 컴파일:

```cmd
iscc build\installer.iss
```

결과: `build\Output\InfiniteClipboard-Setup-X.Y.Z.exe`

인스톨러 기능:
- Program Files 에 설치 (UAC 거부 시 `%LOCALAPPDATA%` 로 폴백)
- 시작 메뉴 바로가기
- 옵션 체크박스: 바탕화면 바로가기 / **Windows 시작 시 자동 실행**
- 제거 시 설정 유지/삭제 선택 페이지
- 한국어 + 영어 2개 언어

---

### 5. 배포판 업데이트 체크리스트

버전을 올릴 때 수동 동기화해야 하는 위치:

| 파일 | 값 |
|------|----|
| `version.py` | `__version__ = "x.y.z"` |
| `pyproject.toml` | `version = "x.y.z"` |
| `build/installer.iss` | `#define AppVersion "x.y.z"` |
| `build/PKGBUILD` | `pkgver=x.y.z` |
| `build/infinite-clipboard.spec` | `"CFBundleShortVersionString": "x.y.z"`, `"CFBundleVersion": "x.y.z"`, `version="x.y.z"` |

모두 반영한 뒤 각 OS에서 빌드 스크립트를 다시 실행하면 된다.

---

## 개발자 실행 (빌드 없이 uv run)

소스 수정하면서 돌려보는 방식. 배포판을 만들 필요 없을 때 유용하다.

### 1. uv 설치 (각 PC 최초 1회)

```bash
# Arch/CachyOS
sudo pacman -S uv

# macOS
brew install uv

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. 실행

```bash
cd infinite-clipboard

# 최초 실행 시 GUI 설정 다이얼로그가 뜸 (모드/서버 IP/포트 선택)
python3 start.py

# 또는 직접 CLI 옵션으로
uv run main.py --mode server
uv run main.py --mode client --host 100.64.0.1
```

> Linux에서 Wayland 세션 사용 시: `sudo pacman -S wl-clipboard python-gobject libayatana-appindicator`
> X11 세션: `sudo pacman -S xclip python-gobject libayatana-appindicator`

### 3. 동기화 폴더에서 공유할 때

프로젝트 폴더를 Syncthing/Nextcloud 등으로 동기화하면 모든 PC에서 같은 코드를 사용할 수 있다.

**동기화 제외 대상:**
```
__pycache__/
.venv/
dist/
build/InfiniteClipboard/
build/Output/
```

`assets/generated/` 는 **동기화 포함** 권장 — 각 PC가 rsvg-convert 없이 바로 빌드 가능.

---

## CLI 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--mode` | `server` 또는 `client` | 설정 파일 값 |
| `--host` | 서버 IP (클라이언트 모드) | `100.64.0.1` |
| `--port` | 포트 번호 | `9999` |
| `--no-tray` | 트레이 없이 콘솔 모드 (디버그 용) | - |
| `--debug` | 상세 로그 (DEBUG 레벨) | - |
| `--version` | 버전 출력 후 종료 | - |

> `--key` 옵션은 v2.0.0 부터 **제거됨**. 프로세스 목록(`ps`, Task Manager)에서 키가 노출되는 보안 문제를 피하려고 `settings.json` 에서만 읽도록 변경.

---

## 네트워크 구성

```
[서버 PC — 항상 켜짐]              [클라이언트 PC들]
  Tailscale IP: 100.64.0.1         Tailscale IP: 100.64.0.x
       │                                  │
       └──────── Tailscale VPN ────────────┘
                    (WireGuard 암호화)
```

- 서버 PC 1대 고정, 나머지는 클라이언트
- Tailscale 의 WireGuard 가 암호화를 담당 → 앱 레벨 암호화 없음
- 서버 재시작 시 클라이언트 자동 재연결 (기본 5초 간격)
- 기본 포트 `9999`

### 방화벽

Tailscale 만 사용한다면 대부분 추가 설정 없이 동작. LAN에서 직접 연결하는 경우:

```bash
# Linux (ufw)
sudo ufw allow 9999/tcp

# Windows PowerShell (관리자)
New-NetFirewallRule -DisplayName "Infinite Clipboard" -Direction Inbound -Protocol TCP -LocalPort 9999 -Action Allow

# macOS
# 시스템 설정 → 네트워크 → 방화벽 → 앱 허용에 InfiniteClipboard 추가
```

---

## 트러블슈팅

### 빌드 시 `rsvg-convert: command not found`
아이콘 생성 스크립트는 librsvg가 필요. 위 "아이콘 자산 생성" 섹션의 설치 안내 참조.

### macOS 첫 실행 시 "확인되지 않은 개발자" 경고
Gatekeeper가 ad-hoc 서명된 앱을 막음. Finder에서 `.app` 을 **우클릭 → 열기** 로 1회 통과하면 이후 정상 실행.

### Linux 트레이 아이콘이 안 보임 (KDE Plasma Wayland)
```bash
sudo pacman -S libayatana-appindicator python-gobject
```
`--system-site-packages` 로 venv 를 생성해야 GI 바인딩 접근 가능.

### 연결이 안 됨 — "인증 실패"
모든 PC의 `auth_key` 값이 완전히 동일한지 확인. 한 글자라도 다르면 실패. 가장 확실한 방법: 서버 PC의 `settings.json` 을 복사해 클라이언트 PC에 덮어쓰기.

### 파일 전송이 도중에 멈춤
자동 이어받기가 구현되어 있음. 앱을 재시작하면 체크포인트(`~/.config/InfiniteClipboard/checkpoints/` 또는 각 OS 설정 폴더)에서 재개. 이어받기도 실패하면 임시 디렉토리(`/tmp/ic_transfer_<id>/` 등)를 수동 삭제 후 재전송.

### Windows 인스톨러에서 SmartScreen 경고
정식 코드 사이닝 인증서가 없어 발생. "추가 정보" → "실행" 클릭. 코드 사이닝이 필요하면 별도 인증서 구매 후 `signtool` 로 서명.

### 버전 업데이트 확인
```bash
# 설치된 실행 파일
infinite-clipboard --version
# 또는
/opt/infinite-clipboard/InfiniteClipboard --version
```

---

## 라이선스

MIT License — 자세한 내용은 `LICENSE` 파일 참조.

## 개발 기여

- 프로토콜 변경 시 `core/protocol.py` 의 `MSG_*` 상수 + `main.py` 핸들러 + 서버 `broadcast` 분기까지 모두 업데이트 필요
- 테스트: `pytest tests/`
- 코드 스타일: 한국어 주석, PEP 8, 타입 힌트 권장

## 요구사항 요약

- Python 3.10+ (빌드 전용 — 배포판은 Python 포함)
- Tailscale 권장 (없으면 동일 LAN 에서 직접 IP 연결 가능)
- Linux: `wl-clipboard` (Wayland) 또는 `xclip`/`xsel` (X11), `libayatana-appindicator`, `python-gobject`
- Windows: 추가 의존성 없음 (빌드 시 pywin32 자동 설치)
- macOS: 추가 의존성 없음 (빌드 시 pyobjc 자동 설치)
