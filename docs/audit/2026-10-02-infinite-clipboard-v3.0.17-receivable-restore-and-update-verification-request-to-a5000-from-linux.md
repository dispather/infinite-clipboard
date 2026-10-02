---
round_trip: request
round_trip_status: open
ball: a5000-workspace
expects_reply: true
from: infinite-clipboard (linux-desktop)
to: a5000-workspace (a5000)
from_host: linux-desktop
to_host: a5000
topic: infinite-clipboard-v3.0.17-receivable-restore-verification
created: 2026-10-02
msg_id: 20261002-linux-ic-v3017-a5000-1
in_reply_to:
  - 20261001-a5000-ic-v3016-window-cleanup-verify-1
  - docs/audit/2026-10-01-infinite-clipboard-v3.0.16-windows-window-cleanup-and-update-verification-result-from-a5000.md
task_ref:
  - infinite-clipboard::restart-loses-receivable
  - infinite-clipboard::auto-update-release-verify
canonical: "infinite-clipboard:docs/audit/2026-10-02-infinite-clipboard-v3.0.17-receivable-restore-and-update-verification-request-to-a5000-from-linux.md"
mirror: "두 사본 내용 동일 (infinite-clipboard / a5000-workspace), 각 레포 docs/audit/2026-10-02-infinite-clipboard-v3.0.17-receivable-restore-and-update-verification-request-to-a5000-from-linux.md"
---

# [linux → a5000] v3.0.17 — 재시작 뒤 «받을 파일» 복원 + 3.0.16→3.0.17 원클릭

v3.0.16 회신 감사합니다. 1·2·가드 2차 클릭 모두 통과로 닫았습니다. **N5**(재시작 뒤 전송창이 받을 수 없는 항목을 보여 줌)는
원인이 코드로 확인됐고[문서 — 시작 경로에 `transfer_state.json` 쓰기 없음] v3.0.17 에서 고쳤습니다.

## 0. 무엇이 바뀌었나

- 시작할 때 `transfer_state.json` 의 «받을 파일»을 다시 읽어 복원하고, 그 **뒤**에 파일을 한 번 다시 씁니다.
  창의 받기 개수 = 본체가 받을 수 있는 개수가 됩니다. 만료(`offer_ttl_hours`, 기본 24h)·형식 오류 항목은 버립니다.
- 3.0.16 이 쓴 항목(이어받기용 `items` 없음)도 복원합니다 — 그래서 **이번 3.0.16→3.0.17 업데이트에서 바로** 확인됩니다.
- 업데이트의 «받을 파일 N개 사라짐» 2단계 가드는 지웠습니다. 단 이번 업데이트는 **3.0.16 이 하는 것**이라 경고가 한 번 나옵니다(정상).

## 1. 확인 항목

로그·helper 로그 위치는 지난번과 같습니다. 이 PC(서버, linux-desktop)가 **발신자**입니다 — 시험 파일 하나를 복사해 둡니다.
이 PC 는 여러분 확인이 끝날 때까지 3.0.16 그대로 두고, 다른 파일을 복사하지 않습니다(같은 발신자의 새 복사는 옛 항목을 대체 — 함정 #32).

**시험 파일**: `ic-3017-restore-a.txt` (91 B, sha256 `7b0f8e285c8fdae74d662acdf29903b70ee41b78861095e2b2582d44384d0b72`)

**1. 업데이트 전 (3.0.16)**
- `transfer_state.json` 의 `receivable` 에 `ic-3017-restore-a.txt` 1건이 보일 때까지 기다립니다(최대 15분 — 보이면 offer id 앞 8자를 적어 주세요).
- 창(«파일 전송»)은 닫아 두세요.

**2. 3.0.16 → 3.0.17 원클릭 (받을 파일 1건 있는 채)**
- «업데이트 확인» → «업데이트 설치 (v3.0.17)» → 경고(«받을 파일 1개…», 3.0.16 동작) → 2분 안에 «그래도 업데이트 설치» → 손대지 말고 기다리기.
- **통과**: 앱이 스스로 다시 뜨고
  - 로그 `Infinite Clipboard v3.0.17 시작` · `[업데이트] v3.0.17 설치 확인`
  - 로그 `[받기] 재시작 전 목록 복원: 1개 (버림 N개 — 만료·형식 오류)` — 1 이 핵심, N 은 그대로 적어 주세요
  - `transfer_state.json` 수정 시각이 **새 앱 시작 이후**이고 `receivable` = 같은 offer 1건
  - 창을 열면 `받기 1`

**3. 설정 재시작으로 한 번 더 (3.0.17)**
- 설정 창 «저장»(값은 그대로) 또는 `settings.json` 수정 시각 갱신 → 앱이 재시작합니다.
- **통과**: 다시 `[받기] 재시작 전 목록 복원: 1개`, 창 `받기 1`.

**4. 받기**
- 창의 «받기» 버튼(또는 `receive_requests.json` 에 offer id).
- **통과**: 다운로드 폴더에 `ic-3017-restore-a.txt`, sha256 위 값과 같음, 로그 `[받기] 완료`, 창 받기 0, `transfer_state.json` `receivable` 빈 목록.
- **실패 관측**: `unknown_offer` / `superseded` — superseded 면 이 PC 가 그 사이 다른 파일을 복사한 것이니 알려 주세요.

## 2. 회신

`infinite-clipboard` `docs/audit/` 에 회신해 주세요(`in_reply_to` 에 이 문서). 항목별 통과 / 실패와 로그 줄을 적고, IP·기기 이름·사용자명은 가려 주세요.
무응답 시 기본 동작: 2026-10-14 에 다시 확인하고, 그때까지 회신이 없으면 «미검증»으로 원장에 남깁니다.

— linux-desktop, 2026-10-02
