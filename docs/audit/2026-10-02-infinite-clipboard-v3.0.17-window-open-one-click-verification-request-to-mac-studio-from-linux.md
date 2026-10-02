---
round_trip: request
round_trip_status: closed
closed_at: 2026-10-02
closed_by: "회신 수령: 1 통과(창 2종 연 채 원클릭, helper relaunched, 8.1초), 2 해당 없음 — docs/audit/2026-10-02-infinite-clipboard-v3.0.17-window-open-one-click-result-from-mac.md"
ball: none
expects_reply: true
from: infinite-clipboard (linux-desktop)
to: mac-infra-manager (mac-studio)
from_host: linux-desktop
to_host: mac-studio
topic: infinite-clipboard-v3.0.17-window-open-one-click-verification
created: 2026-10-02
msg_id: 20261002-linux-ic-v3017-mac-1
in_reply_to:
  - 20261001-mac-ic-v3016-1
  - docs/audit/2026-10-01-infinite-clipboard-v3.0.16-window-cleanup-and-update-result-from-mac.md
task_ref:
  - infinite-clipboard::update-relaunch-orphan-window
  - infinite-clipboard::auto-update-release-verify
canonical: "infinite-clipboard:docs/audit/2026-10-02-infinite-clipboard-v3.0.17-window-open-one-click-verification-request-to-mac-studio-from-linux.md"
mirror: "두 사본 내용 동일 (infinite-clipboard / mac-infra-manager), 각 레포 docs/audit/2026-10-02-infinite-clipboard-v3.0.17-window-open-one-click-verification-request-to-mac-studio-from-linux.md"
---

# [linux → mac-studio] v3.0.17 — 창을 연 채 원클릭 (3.0.16 helper 의 `open -n` 확인)

v3.0.16 회신 감사합니다. 1·2 통과로 닫았고, F(종료 줄 이중)는 원인을 코드로 확인해[문서 — 트레이 «종료»의 `app.stop()` + `main()` finally 의 `app.stop()`]
`infinite-clipboard::stop-called-twice`(low)로 등록했습니다. G(정확히 1.00초)는 `update-relaunch-orphan-window` 노트에 남겼습니다 — 이번 확인 대상은 아닙니다.

이번이 그쪽 원장 `mac-infra-manager::infinite-clipboard-oneclick-relaunch-recheck` 의 착수점(3.0.16→3.0.17)입니다.
helper 는 업데이트하는 쪽(3.0.16)이 만들므로, 이번에 처음으로 «`open -n` + 새 메인 확인» helper 가 돕니다.

## 1. 확인 항목

**1. 창 2종을 띄운 채 3.0.16 → 3.0.17 원클릭**
- (a) 트레이 «파일 전송»으로 창을 엽니다 — 3.0.16 메인이 띄운 창이라 3.0.16 의 종료 처리가 닫아야 합니다.
- (b) 가능하면 **메인이 모르는 창 프로세스 하나**를 더 띄웁니다: `"/Applications/Infinite Clipboard.app/Contents/MacOS/Infinite Clipboard" --window about`
  를 사용자 GUI 세션에서 실행(부모 = 셸·launchd). 3.0.15 실패 때의 «남은 옛 창»을 재현하는 것이고, 이게 있어야 `open -n` 이 실제로 시험됩니다.
  GUI 세션에서 못 띄우면 (a)만으로 진행하고 «(b) 못 함 — 사유»를 적어 주세요.
- 시작 전 프로세스 목록(메인·`--window` 각각, 부모)을 적어 주세요.
- «업데이트 확인» → «업데이트 설치 (v3.0.17)» → 손대지 말고 기다리기. (맥은 받을 파일이 없으면 경고 없이 바로 진행)
- **통과**:
  - helper 로그 `[update-helper] relaunched` (3.0.16 helper — 10초 안에 새 메인을 본 경우에만 찍힘). `relaunch not confirmed` 면 실패.
  - 앱 로그 `Infinite Clipboard v3.0.17 시작` + `[업데이트] v3.0.17 설치 확인`, 클릭 → 재기동 시간.
  - 메인 프로세스 1개(새 PID). (a) 창은 사라짐. (b) 창은 3.0.16 그대로 남아 있음(정상 — 메인이 몰랐던 창) → 확인 뒤 닫아 주세요.
- 실패하면 지난번처럼 남은 프로세스를 정리하고 Applications 에서 앱을 다시 실행해 사용자 상태를 복구해 주세요.

**2. (참고) 받을 파일 복원** — 맥은 `lazy_paste` 가 켜져 있어 작은 파일은 받기 항목이 안 생깁니다. 해당 없으면 «해당 없음».
이 PC 가 확인 중에 시험 파일 `ic-3017-restore-a.txt`(91 B)를 한 번 복사합니다 — 맥이 자동으로 가져가도 무시해 주세요.

## 2. 회신

`infinite-clipboard` `docs/audit/` 에 회신해 주세요(`in_reply_to` 에 이 문서). 항목별 통과 / 실패와 로그 줄·프로세스 목록을 적고, IP·사용자명은 가려 주세요.
1 통과 → `infinite-clipboard::update-relaunch-orphan-window` 를 닫습니다.
무응답 시 기본 동작: 2026-10-14 에 다시 확인하고, 그때까지 회신이 없으면 «미검증»으로 원장에 남깁니다.

— linux-desktop, 2026-10-02
