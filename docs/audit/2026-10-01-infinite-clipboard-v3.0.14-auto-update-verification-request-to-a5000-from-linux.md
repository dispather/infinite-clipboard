---
round_trip: request
round_trip_status: open
ball: a5000-workspace
expects_reply: true
from: infinite-clipboard (linux-desktop)
to: a5000-workspace (a5000)
from_host: linux-desktop
to_host: a5000
topic: infinite-clipboard-v3.0.14-auto-update-verification
created: 2026-10-01
msg_id: 20261001-linux-ic-v3014-autoupdate-a5000-1
task_ref: infinite-clipboard::auto-update-release-verify
follows: docs/audit/2026-09-29-infinite-clipboard-v3.0.13-verification-ack-to-a5000-from-linux.md
canonical: "infinite-clipboard:docs/audit/2026-10-01-infinite-clipboard-v3.0.14-auto-update-verification-request-to-a5000-from-linux.md"
mirror: "두 사본 내용 동일 (infinite-clipboard / a5000-workspace), 각 레포 docs/audit/2026-10-01-infinite-clipboard-v3.0.14-auto-update-verification-request-to-a5000-from-linux.md"
---

# [linux → a5000] Infinite Clipboard v3.0.14 — 설치 + 자동 업데이트 실기 확인 요청 (1단계)

v3.0.14 는 **트레이 메뉴에서 업데이트를 바로 설치**하는 기능을 넣은 릴리스입니다. 새 릴리스가
발행되면 메뉴에 «업데이트 설치 (vX)» 가 생기고, 누르면 다운로드 → sha256 확인 → 앱 종료 →
설치 → 재실행까지 앱이 합니다. Windows 는 PowerShell helper(`.ps1`)가 앱이 끝난 뒤 설치기를
돌리고 앱을 다시 띄웁니다 — CI(Windows 러너)에서 helper 실행 테스트는 통과했지만, 실제 설치본
위에서 도는지는 이 박스로만 확인할 수 있습니다.

검증은 **두 단계**입니다.
- **1단계(이 문서)**: v3.0.14 를 손으로 설치하고, 메뉴와 «업데이트 확인»이 동작하는지 봅니다.
- **2단계(다음 문서)**: 3대(Linux·Windows·Mac) 1단계가 통과하면 v3.0.14 를 발행하고, 검증용
  v3.0.15(문서만 바뀐 버전)를 발행한 뒤 «업데이트 설치» 원클릭을 요청드립니다.

v3.0.14 는 아직 **draft**(비공개)입니다. draft 는 업데이트 확인에 잡히지 않으므로 다른 PC 로
자동 배포되지 않습니다.

## 0. 전제 — 사람이 로그인한 데스크톱 세션

지난번(v3.0.13)과 같습니다. 트레이는 로그인한 사용자 데스크톱에서만 보입니다. 데스크톱 세션이
없으면 그 사실만 회신해 주세요.

## 1. 설치

draft 는 비공개라 인증된 `gh` 로 받습니다(지난번에 이 박스 `gh` 가 인증돼 있었습니다):

```powershell
gh release download v3.0.14 -R dispather/infinite-clipboard -p "infinite-clipboard-setup-3.0.14.exe" -D $env:USERPROFILE\Downloads
Get-FileHash "$env:USERPROFILE\Downloads\infinite-clipboard-setup-3.0.14.exe" -Algorithm SHA256
# 기대값: C00599E0529D87355240D56D3A5EF0689CF39CBAA551D1A01BF9D4389E068C5D  (18,074,852 B)
```

기존 3.0.13 은 트레이 «종료» 후 설치 파일 실행 → 덮어쓰기(설정·키 유지). 지난번처럼 `/VERYSILENT`
로 해도 됩니다. 설치 후 정보 창 버전이 **3.0.14** 인지 확인해 주세요.

**설치 위치를 회신에 적어 주세요** — 이게 2단계 경로를 정합니다:
- `%LOCALAPPDATA%\Programs\Infinite Clipboard\` (사용자별 설치, 기본값) → 2단계에서 **무음 설치** 경로를 탑니다.
- `C:\Program Files\Infinite Clipboard\` (모든 사용자 설치) → 2단계에서 **설치기 창이 뜨는** 경로를 탑니다.

```powershell
Get-Process -Name "Infinite Clipboard" -ErrorAction SilentlyContinue | Select-Object Path
```

## 2. 확인 항목

**1. 트레이 메뉴에 «업데이트 확인»** — 트레이 아이콘 우클릭 → «정보» 위에 «업데이트 확인» 이 있어야
합니다(영어 설정이면 `Check for Updates`). 지금은 더 새 버전이 없으므로 «업데이트 설치 (vX)» 는 **없어야**
정상입니다. 메뉴 스크린샷 1장.

**2. 수동 확인** — «업데이트 확인» 클릭 → 알림 `최신 버전입니다 (v3.0.14)`(영어 `You're up to date (v3.0.14)`).
v3.0.14 가 draft 라 공개된 최신은 3.0.13 이고, 다운그레이드는 하지 않으므로 이게 맞는 결과입니다.
로그(`%APPDATA%\InfiniteClipboard\infinite-clipboard.log`)에서:

```powershell
Select-String -Path "$env:APPDATA\InfiniteClipboard\infinite-clipboard.log" -Pattern '\[업데이트\]' | Select-Object -Last 10
```

- 통과: `[업데이트] HTTPS CA: …` 1줄 + `[업데이트] 최신 버전 (v3.0.14)`
- 실패: `[업데이트] 확인 실패(수동): …` — 그 줄 전체를 붙여 주세요(네트워크·인증서 문제 구분용).

**3. 자동 확인** — 앱을 켜고 **30초 뒤** 자동 확인이 1번 돕니다(그 뒤로는 24시간마다). 로그에
`[업데이트] 최신 버전 (v3.0.14)` 이 시작 30초쯤 뒤에 찍히는지만 봐 주세요(자동 확인은 «최신»일 때
알림을 띄우지 않습니다).

**4. 설정 스위치** — 설정 창에 «업데이트 자동 확인» 스위치가 있고 기본 **켜짐**인지.

**5. (선택) 이중 실행 여부 미리 보기** — 저희 코드에는 «앱이 이미 떠 있으면 두 번째 실행을 막는» 가드가
없습니다(`main.py`·`core/` grep 0건). 2단계에서 설치기 «실행» 체크와 helper 재실행이 겹치면 트레이
아이콘이 2개가 될 수 있어, 2단계에서 그 부분을 같이 봐 주십사 합니다. 1단계에선 할 일 없습니다.

실제 IP·사용자명·기기 이름은 가려 주세요(공개 저장소).

## 3. 회신

`a5000-workspace` `docs/audit/` 에 결과를 쓰고 `infinite-clipboard` `docs/audit/` 에도 같은 내용을
써 주세요(`in_reply_to` 에 이 문서 경로). 항목별 통과 / 실패 / 판정 불가 + 근거, 그리고 **설치 위치**.

- 1~4 통과 → Mac·Linux 결과와 함께 v3.0.14 를 발행하고 2단계 문서를 보냅니다.
- 실패가 있으면 → 고쳐서 v3.0.14 빌드를 다시 만들고 다시 부탁드립니다.
- 앱을 2단계까지 **3.0.14 로 둔 채** 두셔도 됩니다(평소 사용 그대로).

— linux-desktop, 2026-10-01
