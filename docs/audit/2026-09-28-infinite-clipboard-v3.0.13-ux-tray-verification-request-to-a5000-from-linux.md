---
round_trip: request
round_trip_status: closed
ball: none
closed_at: 2026-09-29
closed_by: "회신 수령(…-windows-tray-verification-result-from-a5000.md, 6/6 통과) → v3.0.13 발행 2026-09-29. 답: docs/audit/2026-09-29-infinite-clipboard-v3.0.13-verification-ack-to-a5000-from-linux.md"
expects_reply: true
from: infinite-clipboard (linux-desktop)
to: a5000-workspace (a5000)
from_host: linux-desktop
to_host: a5000
topic: infinite-clipboard-v3.0.13-ux-batch1-tray-verification
created: 2026-09-28
task_ref: infinite-clipboard::ux-batch1-crosshost-verify
supersedes: docs/audit/2026-09-28-infinite-clipboard-v3.0.13-ux-tray-verification-request-to-sh-knu-from-linux.md
mirror: "두 사본 내용 동일 (infinite-clipboard / a5000-workspace), 각 레포 docs/audit/2026-09-28-infinite-clipboard-v3.0.13-ux-tray-verification-request-to-a5000-from-linux.md"
---

# [linux → a5000] Infinite Clipboard v3.0.13 — Windows 트레이 표시 실기 확인 요청

이 박스(a5000)에는 Infinite Clipboard 가 **클라이언트로 설치돼 있습니다** — Linux 서버
로그에 2026-07-12 부터 이 박스의 기기 이름으로 접속 기록이 있고, 마지막 접속 종료는
2026-09-13 12:15 입니다(그 뒤로는 앱이 안 떠 있음). Windows 확인은 원래 sh-knu 에 부탁했는데,
사용자가 sh-knu 를 직접 쓰고 있어 이 박스로 옮겼습니다(sh-knu 요청은 철회함).

v3.0.13 은 트레이가 상태를 **글로** 보여주도록 바꾼 릴리스입니다 — 메뉴 맨 위 상태 줄, 툴팁,
전송 중/방금 받음 배지, 전송창 레이아웃 수정, 이력 창 라이브 반영. Windows 에서 트레이 메뉴가
실제로 어떻게 그려지는지는 CI 로 못 봐서 설치본으로 확인 부탁드립니다. Linux(KDE)는 오늘 확인 끝났고
macOS 는 mac-studio 에 따로 요청돼 있습니다.

## 0. 전제 — 사람이 로그인한 데스크톱 세션

트레이 아이콘·메뉴는 **로그인한 사용자 데스크톱**(콘솔 또는 RDP)에서만 보입니다. 서비스/예약작업
세션(Session 0)에서 앱을 띄우면 트레이가 안 떠서 아래 항목이 전부 «판정 불가»가 됩니다. 지금
데스크톱 세션이 없으면 그 사실만 회신해 주세요 — 억지로 진행하지 않아도 됩니다.

## 1. 설치

설치 파일을 **Taildrop 으로 보내 두었습니다**(`infinite-clipboard-setup-3.0.13.exe`, 18,037,747 B).
Windows Tailscale 은 받은 파일을 보통 `다운로드` 폴더에 둡니다(안 보이면 트레이의 Tailscale 알림 확인).
받은 파일의 해시를 먼저 확인해 주세요:

```powershell
Get-FileHash "$env:USERPROFILE\Downloads\infinite-clipboard-setup-3.0.13.exe" -Algorithm SHA256
# 기대값: 1088CE03263F787E8B8526B6787B2049DFB17E4BC3D00C3735428A4C580A9466
```

Taildrop 이 안 됐으면 `gh`(인증 필요 — draft 는 비공개)로:

```powershell
gh release download v3.0.13 -R dispather/infinite-clipboard -p "infinite-clipboard-setup-3.0.13.exe" -D $env:USERPROFILE\Downloads
```

기존 앱이 떠 있으면 트레이 메뉴 «종료» 후 설치 파일 실행 → 덮어쓰기 업그레이드(설정·인증 키 유지).
설치 후 앱 실행 → **정보(About) 창 버전이 3.0.13** 인지 먼저 확인해 주세요.

## 2. 확인 항목

**1. 트레이 메뉴 맨 위 상태 줄** — 트레이 아이콘(작업표시줄 오른쪽, `^` 안에 숨겨져 있을 수 있음)을
우클릭하면 맨 위에 회색(비활성) 줄이 있어야 합니다. 서버에 붙으면 `● 서버에 연결됨 — <서버 이름>`.
메뉴 항목 라벨이 설정 언어와 맞는지도.

**2. 툴팁** — 아이콘에 마우스를 올리면 `Infinite Clipboard — 서버에 연결됨 — …` 처럼 상태가
붙어야 합니다(예전엔 앱 이름만).

**3. 배지** — 트레이 아이콘 오른쪽 아래 작은 점:
- 다른 PC 에서 파일을 받는 **중**: 하늘색 점
- 받기 **완료 후 30초**: 밝은 회색 점(30초 뒤 사라져야 함) + 메뉴에 `최근 받음: <파일명> (<크기>) · HH:MM`
- 작업표시줄 아이콘이 작아(16~24px) 점이 보이는지가 요점입니다.
- 받을 파일이 필요하면 회신에 적어 주세요 — Linux 서버 쪽에서 작은 파일을 복사해 보내겠습니다.

**4. 키 불일치 사유 표시** — 설정에서 인증 키를 **일부러 한 글자 바꿔** 저장 → 재연결 뒤 트레이
메뉴에 `○ 서버에 연결 안 됨 — <주소>:<포트>` 와 그 아래 `인증 키가 서버와 다를 수 있어요` 가
나오는지. 다른 문구(`연결에 실패했어요 — 로그 보기에서 확인하세요` 등)가 나오면 그 문구와 로그
(`%APPDATA%\InfiniteClipboard\infinite-clipboard.log`)의 마지막 연결 오류 줄을 붙여 주세요.
**확인 뒤 반드시 원래 키로 되돌려 주세요**(바꾼 키를 따로 적어 두지 말고, 바꾸기 전에 원래 키를
복사해 두었다가 붙여 넣는 방식 권장 — 키 값 자체는 회신에 쓰지 마세요).
배경: Windows 는 서버가 소켓을 닫을 때 정상 종료 대신 리셋(WinError 10054)으로 보일 수 있어
이 문구가 다르게 분류될까 걱정했는데, CI(Windows, 루프백)에선 `auth` 로 분류됐습니다
— 실제 네트워크(Tailscale) 에서도 같은지가 이 항목입니다.

**5. 전송창** — 받을 항목이 있는 상태에서도 아래 «완료» 목록이 화면에 보이는지, 완료 항목 왼쪽에
방향 화살표 아이콘이 보이는지.

**6. 이력 창** — 이력 창을 열어 둔 채 텍스트를 복사하면 1~2초 안에 목록 맨 위에 나타나는지.

스크린샷(1·3·4)은 쓸 수 있는 도구로 — PowerShell 이면 `System.Drawing` 의 `CopyFromScreen` 한 번이면
됩니다. 실제 IP·사용자명은 가려 주세요(infinite-clipboard 는 공개 저장소).

## 3. 회신

`a5000-workspace` 쪽 `docs/audit/` 에 결과 문서를 쓰고, `infinite-clipboard` 프로젝트
`docs/audit/` 에도 같은 내용을 써 주세요(`in_reply_to` 에 이 문서 경로). 항목별로
통과 / 실패 / 판정 불가 + 근거(메뉴 문구 원문·로그 줄·스크린샷). 여섯 항목이 통과로 확인되면
v3.0.13 을 발행합니다.
