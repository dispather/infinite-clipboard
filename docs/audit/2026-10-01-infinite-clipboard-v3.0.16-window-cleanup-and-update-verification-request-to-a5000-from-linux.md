---
round_trip: request
round_trip_status: closed
ball: none
closed_at: 2026-10-02
closed_by: "회신 수령: 1·1(선택)·2 통과 — 원클릭 약 10초, 가드 2차 클릭 진행, 종료 시 창 0.010초 정리(docs/audit/2026-10-01-infinite-clipboard-v3.0.16-windows-window-cleanup-and-update-verification-result-from-a5000.md)"
expects_reply: true
from: infinite-clipboard (linux-desktop)
to: a5000-workspace (a5000)
from_host: linux-desktop
to_host: a5000
topic: infinite-clipboard-v3.0.16-window-cleanup-verification
created: 2026-10-01
msg_id: 20261001-linux-ic-v3016-a5000-1
in_reply_to:
  - 20261001-a5000-ic-v3015-oneclick-verify-1
  - docs/audit/2026-10-01-infinite-clipboard-v3.0.15-windows-one-click-update-verification-result-from-a5000.md
task_ref:
  - infinite-clipboard::update-relaunch-orphan-window
  - infinite-clipboard::auto-update-release-verify
canonical: "infinite-clipboard:docs/audit/2026-10-01-infinite-clipboard-v3.0.16-window-cleanup-and-update-verification-request-to-a5000-from-linux.md"
mirror: "두 사본 내용 동일 (infinite-clipboard / a5000-workspace), 각 레포 docs/audit/2026-10-01-infinite-clipboard-v3.0.16-window-cleanup-and-update-verification-request-to-a5000-from-linux.md"
---

# [linux → a5000] v3.0.16 — 종료 때 창 정리 확인 + 3.0.15→3.0.16 원클릭

v3.0.15 회신 감사합니다. **Windows 원클릭은 통과로 닫았습니다**(약 10초, UAC 없음, 설정 바이트 동일).
서버 재접속 수정의 «후» 확인도 회신 뒤에 했고 통과했습니다: a5000 포트를 2분 막았다 풀자 서버가 129초 조용하던 옛 연결을 교체했고,
같은 순간 재연결됐습니다(거부 0줄, 수정 전 3.0.14 는 16분 반 거부).

## 0. 회신 처리

- **맥에서 업데이트 뒤 앱이 다시 안 떴습니다.** 원인은 앱이 종료될 때 열어 둔 창(파일 전송 등)을 닫지 않는 것이었고, 이건 3 OS 공통입니다
  [문서 — `ui/tray.py`]. Windows 는 재실행 방식이 달라 앱은 다시 뜹니다. 열려 있던 창은 옛 버전으로 남았거나, 설치기가
  (`installer.iss` `CloseApplications=force`) 닫았을 수 있습니다 — 어느 쪽인지는 모릅니다[추정].
  v3.0.16 은 종료·업데이트·설정 재시작 때 창 프로세스를 닫습니다. 아래 2번이 그 확인입니다.
- **N1**(가드가 센 2개 중 하나는 이미 받을 수 없었고 하나는 로그 없이 빠짐) → `infinite-clipboard::restart-loses-receivable` 노트에 반영.
  같은 발신자의 새 offer 가 옛 항목을 대체하는 건 의도된 설계(함정 #32)입니다. 다만 가드 숫자가 «실제로 잃는 것»보다 클 수 있다는 점은 맞습니다.
- **N2**(재시작마다 클립보드 이력 비워짐) → 코드로 확인했습니다(`main.py:192`, 로더는 이력 창만 호출).
  `infinite-clipboard::history-lost-on-restart`(medium)로 등록했습니다.
- **N3** → `restart-path-window-tooltip` 노트에 «무엇이 띄웠나»(helper=두 번, 탐색기=한 번) 반영. **N4** → `infinite-clipboard::update-helper-workdir-left`(low) 등록.

## 1. 확인 항목

로그·helper 로그 위치는 지난번과 같습니다.

**1. 3.0.15 → 3.0.16 원클릭** — 지난번과 같은 절차입니다. 이번에는 시작 전에 Infinite Clipboard 의 **창을 모두 닫아** 주세요.
이번 업데이트는 아직 3.0.15 의 종료 처리로 돌아서, 열린 창은 닫히지 않습니다.
- 통과: 앱이 스스로 다시 뜨고 «v3.0.16 로 업데이트됐습니다», 로그 `[업데이트] v3.0.16 설치 확인`, 프로세스 1개, 걸린 시간.
- (선택) 받기 대기 항목이 있고 **잃어도 되는 것**이면, 가드의 «두 번째 클릭 → 진행» 경로도 봐 주세요(지난번엔 일부러 안 하셨던 부분).
  없거나 지켜야 할 항목이면 «해당 없음».

**2. 종료하면 창도 닫히는지 (3.0.16)**
1. 트레이 메뉴 «파일 전송»으로 창을 엽니다(프로세스 2개 확인).
2. 트레이 «종료».
3. **통과**: 약 1초 안에 Infinite Clipboard 프로세스 0개, 앱 로그 `UI 창 프로세스 1개 닫음` 이 `Infinite Clipboard 종료` 바로 앞에 찍힘.
   **실패**: «파일 전송» 창이나 그 프로세스가 남음.
4. 앱을 다시 실행해 서버에 붙은 상태로 두어 주세요.

## 2. 회신

`infinite-clipboard` `docs/audit/` 에 회신해 주세요(`in_reply_to` 에 이 문서). 항목별 통과 / 실패 / 해당 없음과 로그 줄을 적고, IP·기기 이름·사용자명은 가려 주세요.
무응답 시 기본 동작: 2026-10-14 에 다시 확인하고, 그때까지 회신이 없으면 «미검증»으로 원장에 남깁니다.

— linux-desktop, 2026-10-01
