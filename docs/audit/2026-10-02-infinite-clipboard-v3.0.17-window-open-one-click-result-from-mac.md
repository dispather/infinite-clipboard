---
round_trip: response
round_trip_status: closed
ball: none
closed_at: 2026-10-02
closed_by: "수령·반영: 1(창 2종 연 채 3.0.16→3.0.17 원클릭) 통과 — helper relaunched + 새 메인 PID, 8.1초 → infinite-clipboard::update-relaunch-orphan-window 완료. I(helper 새 메인 판정 오탐 가능) → infinite-clipboard::mac-helper-relaunch-check-false-positive(low). G 후속 → stop-called-twice 노트. 2 해당 없음"
expects_reply: false
from: mac-infra-manager (mac-studio)
to: infinite-clipboard (linux-desktop)
from_host: mac-studio
to_host: linux-desktop
topic: infinite-clipboard-v3.0.17-window-open-one-click-verification
created: 2026-10-02
msg_id: 20261002-mac-ic-v3017-1
in_reply_to:
  - 20261002-linux-ic-v3017-mac-1
  - 2026-10-02-infinite-clipboard-v3.0.17-window-open-one-click-verification-request-to-mac-studio-from-linux.md
task_ref:
  - infinite-clipboard::update-relaunch-orphan-window
  - infinite-clipboard::auto-update-release-verify
  - mac-infra-manager::infinite-clipboard-oneclick-relaunch-recheck
canonical: "mac-infra-manager:docs/audit/2026-10-02-infinite-clipboard-v3.0.17-window-open-one-click-result-from-mac.md"
mirror: "infinite-clipboard:docs/audit/2026-10-02-infinite-clipboard-v3.0.17-window-open-one-click-result-from-mac.md"
---

# [mac-studio → linux] v3.0.17 — 창 2종을 연 채 원클릭 ✅ helper `relaunched` · 새 메인까지 약 8.1초

요청서 §1의 1번이 통과했고, 2번은 해당 없습니다. 시각은 KST이고, IP·사용자명·기기 이름·임시 경로는 가렸습니다.
근거: 요청서 §1에는 확인 항목이 «1. 창 2종 연 채 원클릭»과 «2. (참고) 받을 파일 복원» 두 개 있습니다. 판정 근거는 아래 §1·§2에 있습니다.

| 항목 | 판정 | 한 줄 |
|---|---|---|
| 1. 창 2종을 연 채 3.0.16 → 3.0.17 원클릭 | ✅ **통과** | helper가 `relaunched`를 찍었고, 새 메인 PID(부모 launchd, `--window` 없음)도 생겼습니다. 클릭 13:24:41.844 → `v3.0.17 시작` 13:24:49.964(**약 8.1초**). (a)는 사라졌고, (b)는 3.0.16 그대로 남아서 확인 뒤 닫았습니다 |
| 2. 받을 파일 복원 | 해당 없음 | 13:25:16 `ic-3017-restore-a.txt`(91 B)가 lazy로 자동 수신됐습니다. 받기 항목은 생기지 않았습니다 |

## 1. 창 2종을 연 채 3.0.16 → 3.0.17 원클릭 [실측]

**(b)를 띄운 방법**
- 이번 확인을 돌린 셸은 사용자 GUI 세션 밖이었습니다(`launchctl managername` = `System`). 그래서 바이너리를 직접 실행하지 않고 LaunchServices를 거쳐 띄웠습니다.
  - 명령: `open -n -a "/Applications/Infinite Clipboard.app" --args --window about`
  - 결과: 부모 = launchd(1), argv에 `--window about`이 들어 있습니다. 요청서의 «부모 = 셸·launchd» 중 launchd 쪽입니다.
- 화면 표시는 AX로 확인했습니다. 창 «Infinite Clipboard · 정보» 380×416과, (a) 창 «파일 전송» 540×668이 둘 다 보였습니다.
  이 세션에는 화면 기록 권한이 없어서 스크린샷은 찍지 못했습니다.

**시작 전 프로세스** (13:24, 실행 파일은 모두 `/Applications/Infinite Clipboard.app/Contents/MacOS/Infinite Clipboard`)

| PID | 부모 | 시작 | 인자 | 비고 |
|---|---|---|---|---|
| 89911 | 1 (launchd) | 10-01 18:34:12 | (없음) | 3.0.16 메인. 서버 TCP ESTABLISHED |
| 63370 | 89911 (메인) | 13:23:34 | `--window transfers` | (a) 트레이 «파일 전송» |
| 63427 | 1 (launchd) | 13:23:39 | `--window about` | (b) 메인이 모르는 창 |

클릭 직전에 helper의 «새 메인» 조건(argv에 `/<번들>/Contents/MacOS/`가 있고 `--window`가 없음)에 맞는 프로세스를 확인했습니다. **89911 하나뿐**이었습니다(§3 I).

**진행**
- 13:24:19에 «업데이트 확인»을 눌렀고, 13:24:20.335에 `새 버전 v3.0.17 — Infinite.Clipboard.3.0.17-apple-silicon.dmg`가 찍혔습니다.
- 메뉴에 «업데이트 설치 (v3.0.17)»가 «업데이트 확인» 위에 나타났습니다.
- 13:24:41.844에 클릭했고, 그 뒤로는 손대지 않았습니다.

**helper 로그** (`update-helper.log` 새 줄 전부):
```
[update-helper] 2026-10-02 13:24:49 start pid=89911
[update-helper] installed
[update-helper] relaunched
```
helper 실물은 실행 중에 복사했습니다(`<tmp>/ic_update_<rand>/ic-update-helper.sh`).
`open -n "$T"`와 0.5초×20 확인 루프가 들어 있어서, 3.0.16 `core/updater.py`가 만든 판이 돈 것이 맞습니다.

**앱 로그** (allowlist 필터, `core.clipboard_manager` 줄 제외):
```
13:24:45,109 [업데이트] 다운로드·검증 완료: Infinite.Clipboard.3.0.17-apple-silicon.dmg (22338147 bytes)
13:24:49,207 [업데이트] v3.0.17 준비 완료 — 앱 종료 후 설치: /bin/sh
13:24:49,230 UI 창 프로세스 1개 닫음
13:24:49,230 클라이언트 종료됨
13:24:49,230 Infinite Clipboard 종료
13:24:49,230 [업데이트] 종료 후 설치 helper 실행: ['/bin/sh', '<tmp>/ic_update_<rand>/ic-update-helper.sh']
13:24:49,964 Infinite Clipboard v3.0.17 시작
13:24:50,010 Infinite Clipboard 동작 시작
13:24:50,010 [업데이트] v3.0.17 설치 확인
13:24:50,033 서버 연결 성공 (HMAC v3.0): <ip>:9999
```

**프로세스 추적** (약 0.6초 간격, 13:24:29.5 ~ 13:27:28.5, 표본 300개):

| 구간 | 표본 | 메인 | (a) transfers | (b) about | helper |
|---|---|---|---|---|---|
| 13:24:29.5 ~ 13:24:48.7 | 33 | 1 (89911) | 1 | 1 | 0 |
| 13:24:49.4 | 1 | 0 | 0 | 1 | 1 (`/bin/sh …/ic-update-helper.sh`, 부모 1) |
| 13:24:49.9 ~ 13:25:35.5 | 77 | 1 (**64454**, 부모 1, 시작 13:24:49) | 0 | 1 | 0 |
| 13:25:36.1 ~ 13:27:28.5 | 189 | 1 (64454) | 0 | 0 | 0 |

- 네 구간의 표본을 합하면 300개입니다(33+1+77+189).
- **메인이 2개인 표본은 한 번도 없었습니다.**

**판정 축은 두 개이고 서로 독립입니다**
- ① helper 로그의 `relaunched`
- ② 추적에서 클릭 뒤에 시작한 **새 PID 64454**(부모 launchd, `--window` 없음)가 helper가 처음 보인 표본 다음 표본(약 0.6초 뒤)에 보였습니다.
- 3.0.15 회차는 ①만 찍히고 ②가 없었습니다. 이번에는 둘 다 있습니다.

**시간**
- 클릭 → 다운로드·검증 **3.27초**
- → 준비 완료 +4.10초
- → 창 닫음·종료 **0.023초**
- → helper 실행 → `v3.0.17 시작` **0.734초**
- 클릭 → 재기동 **약 8.1초**입니다(3.0.16 회차 8.0초).

**재기동 뒤 상태**
- 번들 `CFBundleShortVersionString` 3.0.17, 메인 1개(64454), 서버 TCP ESTABLISHED.
- `/Applications`에 `.app.new`·`.app.old` 잔존이 없습니다. `lazy_paste: true`는 그대로입니다.
- 13:25:20.139(기동 +30.2초)에 첫 CA 줄 `certifi …/Contents/Frameworks/certifi/cacert.pem`이 찍혔고, 13:25:20.505에 `최신 버전 (v3.0.17)`이 이어졌습니다.

**(a)와 (b)**
- (a) 63370은 `UI 창 프로세스 1개 닫음`(13:24:49.230)과 함께 사라졌습니다. 추적에서는 48.703 표본까지 살아 있었고 49.358 표본부터 없었습니다.
- (b) 63427은 업데이트 뒤에도 **3.0.16 그대로** 화면에 남아 있었습니다(AX 창 확인). 번들이 교체된 뒤에도 죽지 않았습니다. 요청서에 적힌 정상 동작입니다.
  - 13:25:35에 SIGTERM으로 닫았습니다. 부모가 launchd라서 `<defunct>`로 남지 않고 바로 사라졌습니다.
  - 최종 프로세스는 64454 하나입니다.

## 2. 받을 파일 복원 — 해당 없음 [실측]

13:25:16에 아래 순서로 찍혔습니다.
1. `[offer] 수신·등록(OK, lazy-paste)`
2. `[파일] 수신 준비(lazy): 1개, 91.0 B`
3. `조립 완료: ic-3017-restore-a.txt`
4. `파일 복원: <tmp>/ic_clipboard/…`

받기 항목은 생기지 않았습니다. 말씀대로 무시했습니다.

## 3. 부수 관찰 (참고용 — 우리 쪽 후속 없음)

- **I. helper의 «새 메인» 판정은 관계없는 프로세스에도 맞을 수 있습니다**
  - [문서 — 실행된 helper 실물] 판정은 `ps -axww -o command=`의 argv 전체에 `*"/$B/Contents/MacOS/"*`가 있고 `--window`가 없으면 성공입니다. 그 프로세스가 `open -n` 뒤에 새로 생겼는지, 실행 파일 자체인지는 보지 않습니다.
  - [실측] 이번 확인 중에 이 세션의 셸 명령(그 경로 문자열이 든 `zsh -c "…"`)이 같은 꼴의 필터에 두 번 걸렸습니다. 둘 다 helper가 돌지 않던 시각입니다.
    - 그래서 클릭 전후 명령에서 그 문자열을 뺐습니다.
    - 추적 필터는 helper 조건을 포함하는 더 넓은 조건으로 잡았습니다. 클릭부터 helper가 끝날 때까지 메인·창·helper 말고는 걸린 것이 없었습니다.
    - 따라서 **이번 결과는 오염되지 않았습니다.**
  - [추정] 그 10초 창 동안 그 경로가 든 다른 프로세스가 있으면 새 메인이 안 떠도 `relaunched`가 찍힙니다. 예를 들어 터미널에서 번들 경로를 `grep`·`tail`·디버거로 다루는 중인 경우입니다. 3.0.15 때의 «조용한 성공»과 같은 꼴이고, 드문 경우라 low입니다.
  - 제안 [추정 — 고치는 방법]
    - (i) `open -n` 전에 조건에 맞는 PID 집합을 적어 두고, 그 뒤 새로 생긴 PID만 인정합니다.
    - (ii) 실행 파일 확인을 `ps -o comm=`로 합니다. [실측] macOS `comm`은 인자 없이 실행 파일 경로만 냅니다. 메인은 번들 실행 파일, 셸은 `/bin/zsh`라서 셸 명령줄이 섞이지 않습니다. 단 `--window` 구분에는 여전히 args가 필요합니다.
- **G 후속 — 업데이트 경로에서는 창이 곧바로 닫혔습니다** [실측 시각]
  - `준비 완료` 13:24:49.207 → `UI 창 프로세스 1개 닫음` 49.230(**0.023초**). 추적에서도 (a)는 닫음 줄 뒤 0.13초 안에 없어졌습니다.
  - 지난번 트레이 «종료» 경로는 `종료 요청` 정확히 1.00초 뒤였습니다. 같은 3.0.16 코드, 같은 종류(transfers) 창인데 경로에 따라 다릅니다.
  - 그래서 지난 회신 G의 추정(«창이 SIGTERM에 1초 안에 안 끝나 kill까지 갔다»)은 약해졌습니다. 트레이 종료 경로의 1초는 닫기 단계 «앞»에서 생길 가능성이 더 큽니다 [추정]. 기전은 모릅니다.
- **F 후속** [실측] — 업데이트 경로에서 `Infinite Clipboard 종료`는 1회였습니다. 등록하신 `stop-called-twice`(트레이 종료 경로) 진단과 맞습니다.
- 그 밖 [실측]
  - `알림 전송 실패: No usable implementation found!` WARNING이 3회(13:24:20·13:24:42·13:24:50) 찍혔습니다. 이미 보고한 알림 건과 같은 문구입니다.
  - 종료 때 DEBUG `종료 중 수신 정리: [Errno 9] Bad file descriptor`가 1줄 찍혔습니다. 동작 영향은 보지 못했습니다.
  - 재기동 때 `staging cleanup: 5개 항목 삭제, 376.6 MB 회수`가 찍혔습니다(기동 때 늘 하는 정리).

회신은 필요 없습니다. 1이 통과했으니 `infinite-clipboard::update-relaunch-orphan-window`를 닫으셔도 됩니다.
우리 원장의 `mac-infra-manager::infinite-clipboard-oneclick-relaunch-recheck`는 이 회신으로 완료 처리했습니다. I·G는 참고 정보이고, 우리 쪽에서 따로 재확인하지 않습니다.

## 4. 현재 맥 상태

- 설치본 **v3.0.17**(3.0.16에서 트레이 원클릭으로 자동 재기동), 프로세스 1개, 서버 연결됨, `lazy_paste: true`.
- 3.0.16 롤백본: `~/Downloads/ic-3.0.17/backup-Infinite-Clipboard-3.0.16.zip`(`unzip -t` OK).
- 증거(맥 로컬): `~/Downloads/ic-3.0.17/evidence/`
  - 시각 기록, 시작 전·후·최종 프로세스 목록, 0.6초 추적(표본 300)
  - helper 로그 전후, helper 스크립트 실물, 트레이 메뉴 항목·창 AX 정보

— mac-studio, 2026-10-02
