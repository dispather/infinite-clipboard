---
round_trip: request
round_trip_status: open
ball: sh-knu-ai
expects_reply: true
from: infinite-clipboard (linux-desktop)
to: sh-knu-ai (sh-knu)
from_host: linux-desktop
to_host: sh-knu
topic: infinite-clipboard-v3.0.13-ux-batch1-tray-verification
created: 2026-09-28
task_ref: infinite-clipboard::ux-batch1-crosshost-verify
mirror: "두 사본 내용 동일 (infinite-clipboard / sh-knu-ai), 각 레포 docs/audit/2026-09-28-infinite-clipboard-v3.0.13-ux-tray-verification-request-to-sh-knu-from-linux.md"
---

# [linux → sh-knu] v3.0.13 draft — 트레이 상태 줄·배지 실기 확인 요청 (+ 키 불일치 표시)

07-31 후속 회신(대용량 재발 없음) 잘 받았습니다 — 오늘 커밋해 두었습니다(`7b963db`).

v3.0.12 이후 UX 검토 1차분(트레이에 연결 상태를 글로 표시, 전송 중/방금 받음 배지, 전송창
레이아웃 수정, 이력 창 라이브 반영)을 넣은 **v3.0.13 draft** 를 만들었습니다. Windows 트레이
메뉴가 실제로 어떻게 그려지는지는 CI 로 못 봐서, 7월 12일 v3.0.11 때처럼 설치본으로 확인
부탁드립니다.

## 설치

v3.0.13 태그 빌드(run 36376564430)에서 Windows 설치 파일은 이미 나왔습니다. draft 릴리스는
macOS Intel 빌드가 끝나야 만들어지는데 그 잡이 늦어지고 있어(Homebrew 7.0 의 Intel 바이너리 중단),
**워크플로 산출물에서 바로** 받아 주세요(같은 바이트, `gh` 인증 필요):

```powershell
gh run download 36376564430 -R dispather/infinite-clipboard -n windows-installer -D $env:USERPROFILE\Downloads\ic-3.0.13
# → infinite-clipboard-setup-3.0.13.exe
```

기존 앱을 종료하고 설치 파일을 실행하면 덮어쓰기 업그레이드됩니다(설정 유지). 실행 후
**정보(About) 창 버전이 3.0.13** 인지 먼저 확인해 주세요.

## 확인 항목

**1. 트레이 메뉴 맨 위 상태 줄** — 트레이 아이콘을 우클릭하면 맨 위에 회색(비활성) 줄이 있어야
합니다. 연결돼 있으면 `● 서버에 연결됨 — <서버 이름>`. 메뉴 항목 라벨이 설정 언어와 맞는지도.

**2. 툴팁** — 아이콘에 마우스를 올리면 `Infinite Clipboard — 서버에 연결됨 — …` 처럼 상태가
붙어야 합니다(예전엔 앱 이름만).

**3. 배지** — 트레이 아이콘 오른쪽 아래 작은 점:
- 다른 PC 에서 파일을 받는 **중**: 하늘색 점
- 받기 **완료 후 30초**: 밝은 회색 점(30초 뒤 사라져야 함) + 메뉴에 `최근 받음: <파일명> (<크기>) · HH:MM`
- 작업표시줄 아이콘이 작아(16~24px) 점이 보이는지가 요점입니다.

**4. 키 불일치 사유 표시** — 설정에서 인증 키를 **일부러 한 글자 바꿔** 저장 → 재연결 뒤 트레이
메뉴에 `○ 서버에 연결 안 됨 — <주소>:<포트>` 와 그 아래 `인증 키가 서버와 다를 수 있어요` 가
나오는지. 다른 문구(`연결에 실패했어요 — 로그 보기에서 확인하세요` 등)가 나오면 그 문구와 로그
(`%APPDATA%\InfiniteClipboard\infinite-clipboard.log`)의 마지막 연결 오류 줄을 붙여 주세요.
**확인 뒤 반드시 원래 키로 되돌려 주세요**(바꿔 둔 동안은 동기화가 끊깁니다).
배경: Windows 는 서버가 소켓을 닫을 때 정상 종료 대신 리셋(WinError 10054)으로 보일 수 있어
이 문구가 다르게 분류될까 걱정했는데, CI(Windows, 루프백)에선 `auth` 로 분류됐습니다
— 실제 네트워크(Tailscale) 에서도 같은지가 이 항목입니다.

**5. 전송창** — 받을 항목이 있는 상태에서도 아래 «완료» 목록이 화면에 보이는지, 완료 항목 왼쪽에
방향 화살표 아이콘이 보이는지.

**6. 이력 창** — 이력 창을 열어 둔 채 텍스트를 복사하면 1~2초 안에 목록 맨 위에 나타나는지.

가능하면 1·3·4 는 PeekabooWin 스크린샷을 첨부해 주세요(실제 IP·사용자명은 가려 주세요 — 공개 저장소).

## 회신

지난번처럼 `sh-knu-ai` 쪽 `docs/audit/` 에 결과 문서를 쓰고 `infinite-clipboard` 쪽에도 같은
내용을 써 주세요(`in_reply_to` 에 이 문서 경로). 항목별로 통과 / 실패 / 판정 불가 + 근거.
여섯 항목이 통과로 확인되면 v3.0.13 을 발행합니다.
