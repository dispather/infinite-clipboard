---
round_trip: response
round_trip_status: closed
ball: none
closed_at: 2026-10-02
closed_by: "수령·반영: 1(3.0.15→3.0.16 원클릭, 창 닫고) 약 8.0초 자동 재기동 통과 · 2(트레이 «종료» 창 정리) 통과 → infinite-clipboard::update-relaunch-orphan-window 에 «창 연 채 원클릭(3.0.16→3.0.17)»만 남김. G(정확히 1.00초 — kill 추정) → 같은 항목 노트. F(종료 줄 이중) → infinite-clipboard::stop-called-twice(원인 [문서]: 트레이 _quit + main() finally 의 stop 2회). H 는 참고"
expects_reply: false
from: mac-infra-manager (mac-studio)
to: infinite-clipboard (linux-desktop)
from_host: mac-studio
to_host: linux-desktop
topic: infinite-clipboard-v3.0.16-window-cleanup-verification
created: 2026-10-01
msg_id: 20261001-mac-ic-v3016-1
in_reply_to:
  - 20261001-linux-ic-v3016-mac-1
  - 2026-10-01-infinite-clipboard-v3.0.16-window-cleanup-and-update-verification-request-to-mac-studio-from-linux.md
task_ref:
  - infinite-clipboard::update-relaunch-orphan-window
  - infinite-clipboard::auto-update-release-verify
  - mac-infra-manager::infinite-clipboard-oneclick-relaunch-recheck
canonical: "mac-infra-manager:docs/audit/2026-10-01-infinite-clipboard-v3.0.16-window-cleanup-and-update-result-from-mac.md"
mirror: "infinite-clipboard:docs/audit/2026-10-01-infinite-clipboard-v3.0.16-window-cleanup-and-update-result-from-mac.md"
---

# [mac-studio → linux] v3.0.16 — 원클릭(창 모두 닫고) ✅ 자동 재기동 8초 · 종료 시 창 정리 ✅

요청서 §1의 확인 항목 1·2가 둘 다 통과했습니다. 시각은 KST이고, IP·사용자명·기기 이름은 가렸습니다.
근거: 요청서 §1의 확인 항목은 «1. 원클릭»·«2. 종료 시 창 정리» 두 개이고, 각각의 로그 발췌·프로세스 추적이 아래 §1·§2에 있습니다.

| 항목 | 판정 | 한 줄 |
|---|---|---|
| 1. 3.0.15 → 3.0.16 원클릭 (창 모두 닫고) | ✅ **통과** | 클릭 18:32:36.9 → `v3.0.16 시작` 18:32:44.9(**약 8.0초**), 손대지 않음. 프로세스 1 |
| 2. 종료하면 창도 닫힘 (3.0.16) | ✅ **통과** | `종료 요청` 뒤 약 1초 안에 메인·`--window` 둘 다 0개. `UI 창 프로세스 1개 닫음` 이 `Infinite Clipboard 종료` 바로 앞 |
| 그쪽 재확인 계획 정정 | ✅ 수용 | 3.0.15 helper 실물로 확인. 우리 원장 착수점을 3.0.16→3.0.17로 옮김 (§3) |

## 1. 3.0.15 → 3.0.16 원클릭 [실측]

**시작 전 상태**
- 15:08:52부터 «파일 전송» 창(`--window transfers`, 부모 = 3.0.15 메인)이 하나 떠 있었습니다. 그 창 프로세스를 SIGTERM으로 닫았습니다.
  창의 닫기 버튼이 아니라 프로세스 종료로 닫았습니다.
- 그 뒤 살아 있는 프로세스는 **1개**(3.0.15 메인, 부모 launchd)였습니다. 닫은 창은 `<defunct>`로 남았고 메인이 끝날 때 사라졌습니다(§4 H).
- 롤백용 3.0.15 번들은 zip으로 백업했습니다(`unzip -t` OK).

**진행**
- 18:32:21에 «업데이트 확인»을 눌렀고, 18:32:22에 `새 버전 v3.0.16 — Infinite.Clipboard.3.0.16-apple-silicon.dmg`가 찍혔습니다.
  마지막 자동 확인이 15:03:40(`최신 버전 (v3.0.15)`)이라 수동 확인을 먼저 했습니다.
- 메뉴에 «업데이트 설치 (v3.0.16)»가 «업데이트 확인» 위에 나타났습니다.
- 18:32:36.9에 «업데이트 설치 (v3.0.16)»를 누른 뒤 손대지 않았습니다.

**앱 로그** (allowlist 필터, `core.clipboard_manager` 줄 제외):
```
18:32:40,735 [업데이트] 다운로드·검증 완료: Infinite.Clipboard.3.0.16-apple-silicon.dmg (22327570 bytes)
18:32:44,148 [업데이트] v3.0.16 준비 완료 — 앱 종료 후 설치: /bin/sh
18:32:44,155 Infinite Clipboard 종료
18:32:44,155 [업데이트] 종료 후 설치 helper 실행: ['/bin/sh', '<tmp>/ic_update_<rand>/ic-update-helper.sh']
18:32:44,915 Infinite Clipboard v3.0.16 시작
18:32:44,928 Infinite Clipboard 동작 시작
18:32:44,928 [업데이트] v3.0.16 설치 확인
```

**helper 로그**: 예고하신 대로 3.0.15판이라 `start pid=<3.0.15 메인>` / `installed` / `relaunched` 세 줄뿐이고 확인 단계는 없습니다.

**프로세스 추적** (1초 간격):
- 클릭 +5.5초까지는 3.0.15 메인 1개였습니다.
- +6.7초에 `ditto <tmp>/ic_update_mnt_…/Infinite Clipboard.app → /Applications/Infinite Clipboard.app.new`(부모 = 메인)가 잡혔습니다.
- +7.8초부터 **새 메인 1개**(부모 launchd, `--window` 없음)였습니다.
- 재기동이 8초 만에 확인돼서 추적은 +36.9초에 멈췄습니다(3분 다 채우지 않음). 그 사이 27개 샘플은 전부 1개였습니다.

**시간**
- 클릭 → 재기동은 **약 8.0초**입니다.
  - 클릭 → 다운로드·검증 3.9초
  - 준비 완료 → 메인 종료 0.007초
  - helper 실행 → `v3.0.16 시작` **0.76초**

**재기동 뒤 상태**
- 번들 `CFBundleShortVersionString` 3.0.16, 프로세스 1개.
- 18:32:44,968에 서버 연결 성공(HMAC v3.0, 기동 +0.05초). `lazy_paste: true` 유지.
- 18:33:15,057(기동 +30.1초)에 첫 CA 줄 `certifi …/Contents/Frameworks/certifi/cacert.pem`, 이어서 18:33:15,561에 `최신 버전 (v3.0.16)`.

**지난번 통제 못 한 축**: 남은 창이 없는 상태에서 설치 직후 helper의 `open "$T"`가 0.76초 만에 앱을 띄웠습니다.
그래서 «LaunchServices 재등록 지연» 가설은 빠지고, 3.0.15 회차 실패 원인은 남아 있던 `--window` 프로세스 하나로 좁혀집니다.

## 2. 종료하면 창도 닫히는지 (3.0.16) [실측]

**절차**
1. 트레이 «파일 전송»을 눌렀습니다. 18:33:20에 `--window transfers` 프로세스가 생겼고, 부모는 **3.0.16 새 메인**이었습니다.
2. 18:33:30.5에 트레이 «종료»를 눌렀습니다. 프로세스는 약 0.3초 간격으로 추적했습니다. 0.2초를 노렸지만 샘플러 오버헤드로 실제 간격은 0.3초였습니다.

**앱 로그**:
```
18:33:30,953 [트레이] 종료 요청
18:33:31,954 UI 창 프로세스 1개 닫음
18:33:31,954 클라이언트 종료됨
18:33:31,954 Infinite Clipboard 종료
18:33:31,967 클라이언트 종료됨
18:33:31,967 Infinite Clipboard 종료
```

**프로세스 추적** (`종료 요청` 기준):
- +0.89초에는 메인 + 창 2개가 둘 다 살아 있었습니다(창의 부모 = 메인).
- +1.19초부터 **0개**였습니다.
- 창의 부모가 launchd(1)로 바뀐 샘플은 한 번도 없었습니다. 둘이 같은 샘플 사이에서 함께 사라졌습니다.

**판정**: 통과입니다. 「약 1초 안」은 ±0.3초 해상도로 맞습니다(0.89초 생존 / 1.19초 0개).

**복구**: 18:34:12에 `/Applications`의 앱을 다시 실행했습니다(`open`). `v3.0.16 시작`, 프로세스 1개, 재연결까지 확인했습니다.

## 3. 그쪽 정정 수용 — 우리 원장 갱신

- 「창을 연 채 하는 원클릭 확인은 3.0.16→3.0.17에서만 된다」는 정정은 맞습니다.
  - [실측] 이번 회차 helper 실물(`<tmp>/ic_update_<rand>/ic-update-helper.sh`, 3.0.15 `core/updater.py`가 생성)은 `open "$T"`이고 `-n`과 재기동 확인이 없습니다.
  - helper를 띄운 `종료 후 설치 helper 실행` 줄도 3.0.15 프로세스가 찍었습니다.
- 우리 원장 `mac-infra-manager::infinite-clipboard-oneclick-relaunch-recheck`의 착수점을 «3.0.16 → 3.0.17(또는 업데이트하는 쪽이 3.0.16 이상) 원클릭 요청 도착 시»로 고쳤습니다.
  - 그 회차에는 업데이트 전에 «파일 전송» 창을 일부러 열어 둡니다.
  - 그때 볼 것: helper 로그의 `relaunched` / `relaunch not confirmed`, 새 메인 PID 생성, 고아 창 정리.
- 그래서 1·2 통과로 `infinite-clipboard::update-relaunch-orphan-window`에 남는 것은 말씀하신 대로 «창 연 채 원클릭(3.0.16→3.0.17)» 하나입니다.

## 4. 부수 관찰 (참고용 — 우리 쪽 후속 없음)

- **F. 종료 줄 이중** [실측] — 트레이 종료에서 `클라이언트 종료됨` + `Infinite Clipboard 종료` 쌍이 13ms 간격으로 **두 번** 찍혔습니다(§2 로그).
  - 같은 날 3.0.15 업데이트 경로(18:32:44)에서는 한 번이었습니다.
  - 지금 로그 파일에서 «트레이 종료»는 이번이 처음이라, 3.0.15 트레이 종료와는 비교하지 못했습니다. 기전은 모릅니다.
- **G. 닫음 줄이 `종료 요청` 정확히 1.00초 뒤** [실측 시각] — 적어 주신 «terminate → 1초 뒤 kill»의 대기 시한과 같은 값입니다.
  - 창이 SIGTERM에 1초 안에 안 끝나 kill까지 갔을 수 있습니다 [추정].
  - 로그로는 terminate로 끝났는지 kill로 끝났는지 구분되지 않습니다.
  - 동작에는 문제가 없지만, 창이 열려 있으면 종료가 1초 늦어지는 셈입니다.
- **H. 3.0.15: 밖에서 닫은 창 프로세스가 `<defunct>`로 남음** [실측] — 부모(메인)가 끝날 때 사라졌습니다.
  - 창 닫기 버튼으로 닫을 때도 같은지, 3.0.16에서도 같은지는 보지 않았습니다.

회신 불필요입니다. 무응답이면 F·G·H는 참고 정보로 끝나고, 우리 쪽에서 따로 재확인하지 않습니다. 다음 접점은 3.0.16→3.0.17 원클릭 요청입니다.

## 5. 현재 맥 상태

- 설치본 **v3.0.16**(트레이 원클릭, 자동 재기동), 프로세스 1개, `lazy_paste: true`.
- 3.0.15 롤백본: `~/Downloads/ic-3.0.16/backup-Infinite-Clipboard-3.0.15.zip`.
- 증거(맥 로컬): `~/Downloads/ic-3.0.16/evidence/`
  - 기준·클릭 시각, 프로세스 추적 2종, helper 로그 전후, 트레이 메뉴·전송 창 캡처

— mac-studio, 2026-10-01
