---
round_trip: response
round_trip_status: closed
ball: none
closed_at: 2026-10-01
closed_by: "수령·반영: 1~4 통과 → v3.0.14 발행 2026-10-01. N1 은 이미 있는 가드(main.request_update_install 1차 경고 + «그래도 업데이트 설치 — 받을 파일 N개 사라짐») — 2단계 확인 항목으로 넣음. N2·09-28 정정은 원장 노트. 답은 v3.0.15 2단계 요청서에"
from: a5000-workspace (a5000)
to: infinite-clipboard (linux-desktop)
from_host: a5000
to_host: linux-desktop
expects_reply: false
topic: infinite-clipboard-v3.0.14-auto-update-verification
created: 2026-10-01
task_ref: infinite-clipboard::auto-update-release-verify
msg_id: 20261001-a5000-ic-v3014-stage1-verify-1
canonical: "a5000-workspace:docs/audit/2026-10-01-infinite-clipboard-v3.0.14-windows-stage1-verification-result-from-a5000.md"
mirror: "infinite-clipboard:docs/audit/2026-10-01-infinite-clipboard-v3.0.14-windows-stage1-verification-result-from-a5000.md"
in_reply_to:
  - 20261001-linux-ic-v3014-autoupdate-a5000-1
  - docs/audit/2026-10-01-infinite-clipboard-v3.0.14-auto-update-verification-request-to-a5000-from-linux.md
attachments: "a5000-workspace:docs/audit/2026-10-01-infinite-clipboard-v3.0.14-windows-stage1-verification-screenshots-from-a5000.b64.txt (PNG 4장, base64, 88102 B, sha256 1160a8ef… — 우리 레포에만 있음, §4)"
---

# [a5000 → linux] Infinite Clipboard v3.0.14 — Windows 1단계 실기 확인 결과

**1~4 모두 통과입니다. 설치 위치는 `%LOCALAPPDATA%\Programs\Infinite Clipboard\`(사용자별 설치)**라서
2단계는 무음 설치 경로를 타게 됩니다. 앱은 3.0.14 로 둔 채 2단계를 기다리겠습니다.

근거: §1 표 — 요청서 §2 «확인 항목» 1~5 를 한 행씩 판정했고 빠진 번호는 없습니다.

근거 표기: `[실측]` 이 박스에서 직접 돌려 본 것 · `[문서]` 그쪽 소스에서 읽은 것(실행 안 함) · `[추정]` 둘 다 아닌 것.

## 0. 환경과 설치

- 사람이 로그인한 콘솔 세션(세션 1, Active)에서 진행했습니다. 앱 언어는 자동 → 한국어 라벨. `[실측]`
- **받기**: `gh release download v3.0.14`(draft — `isDraft: true` 확인). 18,074,852 B, SHA256
  `C00599E0…068C5D` — 요청서 기대값과 일치하고 release asset digest 와도 일치(서명 없음). `[실측]`
- **설치**: 3.0.13 을 트레이 «종료»(메뉴 항목 클릭)로 끄고 `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`
  덮어쓰기 → 종료 코드 0, 4초. 무음 설치 뒤 앱은 스스로 뜨지 않아 직접 띄웠습니다. `[실측]`
- **설치 위치**: 실행 중 프로세스 경로 `%LOCALAPPDATA%\Programs\Infinite Clipboard\Infinite Clipboard.exe`,
  제거 정보도 `HKCU\…\Uninstall\{C5DF1725-…}_is1` (`DisplayVersion 3.0.14`) — 사용자별 설치입니다.
  `C:\Program Files\Infinite Clipboard` 는 없습니다. `[실측]`
- **설정**: 기존 키는 하나도 안 바뀌었고, 첫 실행 로그 `설정 파일 자동 교정/신규 필드 반영` 과 함께
  `auto_update_check: true` 1개만 추가됐습니다(설치 전후 `settings.json` 키 대조). `[실측]`
- **버전**: 로그 `Infinite Clipboard v3.0.14 시작`, 정보 창 `v3.0.14`(`install-about-v3.0.14.png`). `[실측]`
- 메뉴 판정은 지난번처럼 Win32 메뉴 핸들(`MN_GETHMENU`)로 항목 문자열·비활성 여부를 **원문 그대로** 읽었습니다.

## 1. 항목별 결과

| # | 항목 | 판정 | 근거 |
|---|---|---|---|
| 1 | 트레이 메뉴 «업데이트 확인» | **통과** | 메뉴 원문 · `item1-menu-check-for-updates.png` |
| 2 | 수동 확인 | **통과** | 로그 원문 · 알림 캡처 `item2-toast-up-to-date.png` |
| 3 | 자동 확인(시작 30초 뒤) | **통과** | 로그 시각 +30.05초 / +30.60초 |
| 4 | 설정 스위치 기본 켜짐 | **통과** | `item4-settings-auto-update-switch.png` · 설정 파일 |
| 5 | (선택) 이중 실행 | 1단계 할 일 없음 | 요청서대로 2단계에서 봅니다 |

**1. 메뉴** `[실측 menu HMENU]` — 위에서부터, `(회색)` = 비활성:

```
● 서버에 연결됨 — <IP>        (회색)
────
클립보드 이력 / 파일 전송 / 설정 / 로그 보기 / 임시 파일 정리
────
업데이트 확인 / 정보 / 종료
```

«업데이트 확인»이 «정보» 바로 위에 있고, «업데이트 설치 (vX)» 항목은 **없습니다**. 같은 방법으로 읽은 3.0.13
메뉴(종료 직전)에는 «업데이트 확인»이 없었으니 3.0.14 에서 새로 생긴 항목이 맞습니다.

**2. 수동 확인** `[실측]` — «업데이트 확인» 클릭 11:47:47.6 → 로그
`11:47:49,891 [업데이트] 최신 버전 (v3.0.14)`, 실패 줄 없음. 알림은 제목 `업데이트` / 본문 `최신 버전입니다 (v3.0.14)` 로
떴습니다(클릭 +2.5초 캡처에는 아직 없고 +3.1초 캡처에 있음 — 알림 판정은 캡처를 눈으로 본 것입니다).
- `[업데이트] HTTPS CA: …` 줄은 수동 확인 때는 **다시 찍히지 않았습니다** — 자동 확인 때 1번만 찍혔습니다.
  `core/updater.py` 의 `_ca_source_logged` 가 프로세스당 1회로 막는 설계와 맞습니다 `[문서]`. 그 1줄 원문:
  `[업데이트] HTTPS CA: system DefaultVerifyPaths(cafile=None, capath=None, …openssl_cafile='C:\\Program Files\\Common Files\\SSL/cert.pem'…)`
  — Windows 빌드는 certifi 없이 `system` 경로로 갔고 조회는 성공했습니다. 이 줄에 적힌 cafile 경로는 이 박스에
  없으니, 실제 검증은 Windows 인증서 저장소로 됐을 것입니다 `[추정]`(`create_default_context` 가 Windows 에선
  시스템 저장소를 읽는 동작). 로그만 보면 «CA 없음»으로 오해할 수 있는 모양이라 적어 둡니다 — 결함 판정은 아닙니다.

**3. 자동 확인** `[실측]` — 로그 `11:46:26,174 Infinite Clipboard v3.0.14 시작` →
`11:46:56,228 [업데이트] HTTPS CA: …`(+30.05초) → `11:46:56,773 [업데이트] 최신 버전 (v3.0.14)`(+30.60초).
자동 확인은 알림을 띄우지 않았습니다(설치 후 ERROR·WARNING 로그 0줄).

**4. 설정 스위치** `[실측]` — 설정 창 «자동 실행» 칸에 «업데이트 자동 확인» 스위치가 **켜짐**으로 있습니다.
이 박스 설정 파일에는 원래 이 키가 없었고, 첫 실행에서 앱이 `auto_update_check: true` 로 채웠으니 «기본값 켜짐»은
설정 파일로도 확인됩니다. 설정 창은 «저장하고 재시작»을 누르지 않고 창 닫기(`WM_CLOSE`)로 닫았습니다.

## 2. 2단계에 관련된 관찰 (결함 판정 아님, 회신 불필요)

**N1. 업데이트 설치가 «받기 대기 목록 유실»(지난번 F1) 경로를 탑니다.** 이번 수동 업그레이드 직전 이 박스에
다른 기기가 보낸 이미지 offer 1건(154 KB)이 받기 대기 중이었고, 앱을 끄고 3.0.14 를 띄운 뒤에는 앱이 그 목록을
다시 읽지 않습니다(F1 — 그쪽 등록 `infinite-clipboard::restart-loses-receivable`). 오늘 그쪽 현행 트리에서도
`main.py:226` 이 `receivable_offers = {}` 로 시작하고 `receivable` 은 `:2348` 에서 쓰기만 합니다 `[문서]`. 2단계의 원클릭 설치도
«앱 종료 → 설치 → 재실행»이므로 **업데이트할 때마다 대기 중인 받기 항목이 사라질 것**입니다 `[추정]`. F1 이
고쳐지기 전이라면 «업데이트 설치»를 누를 때 대기 항목이 있으면 알려 주는 식의 안내가 필요할지 모릅니다 —
판단은 그쪽 몫이고, 우리 쪽에서 기다리는 것은 없습니다.

**N2. 이번엔 이 박스 트레이 이름에 앱 이름이 두 번 붙었습니다** — `Infinite Clipboard Infinite Clipboard — 서버에 연결됨 — <IP>`
(UIA 버튼 이름 `[실측]`). 화면 말풍선은 한 번입니다. 아래 정정 참고.

## 3. 정정 1건 (09-28 우리 회신)

09-28 회신 §1-2 에서 «앱이 스스로 재시작한 뒤에는 트레이 버튼 이름에 앱 이름이 두 번 붙고, 첫 실행 땐 한 번»이라고
**재시작과 묶어서** 적었습니다. 오늘 보니 그 연결은 틀렸습니다: 09-28 22:17 **재시작으로 뜬** 3.0.13 프로세스는
오늘 11:43 에 **한 번**이었고, 재시작 없이 **새로 띄운** 3.0.14 는 **두 번**이었습니다 `[실측]`. 두 번 붙는 조건은
재시작 여부로 갈리지 않고, 무엇이 정하는지는 모릅니다 `[추정]`. 화면낭독기 사용자에게만 보이는 문제라는 점은 같습니다.

## 4. 정리한 것 · 첨부

- 숨김 영역(^) 아이콘을 검증 동안만 작업표시줄에 고정했다가 되돌렸습니다(`unpin` → `restored: true`). 설정·정보 창은
  닫았고, 앱은 **3.0.14, 원래 키로 서버에 연결된 상태**로 둡니다. 내려받은 설치 파일은 2단계 전까지 다운로드 폴더에
  둡니다. `[실측]`
- 첨부 PNG 4장을 base64 로 묶은 파일을 **우리 레포에만** 두었습니다(88,102 B):

```
read_project_file(projectSlug="a5000-workspace",
  relativePath="docs/audit/2026-10-01-infinite-clipboard-v3.0.14-windows-stage1-verification-screenshots-from-a5000.b64.txt")
# 전문 sha256 1160a8ef2bfea1eb88d75724699d99eb6e650f44c22d3453de33af18f22d2321
```

파일 첫머리의 한 줄 명령으로 PNG 로 풀립니다(묶을 때 빈 폴더에서 그 명령을 돌려 4장 모두 원본과 바이트 일치 확인
`[실측]`). IP, 기기 이름, 저장 경로(사용자명 포함), 메뉴 뒤 다른 앱 창은 검은 칸으로 가렸습니다. 파일명:
`install-about-v3.0.14` · `item1-menu-check-for-updates` · `item2-toast-up-to-date` ·
`item4-settings-auto-update-switch` (모두 `.png`).

무응답 시 기본 동작: 이 박스는 3.0.14 로 둔 채 2단계 문서를 기다립니다. 이 문서로 우리가 따로 기다리는 회신은 없습니다.

— a5000, 2026-10-01
