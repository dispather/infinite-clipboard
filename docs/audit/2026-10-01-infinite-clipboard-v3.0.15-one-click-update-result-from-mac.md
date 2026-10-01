---
round_trip: response
round_trip_status: closed
ball: none
closed_at: 2026-10-01
closed_by: "수령·반영: 관찰 A 수정 통과 → mac-lazy-rebroadcast-after-deferred-clear 완료. 2b 자동 재기동 실패 확인(종료 시 창 프로세스 정리 코드 없음 [문서 ui/tray.py stop·_quit]) → infinite-clipboard::update-relaunch-orphan-window. E(파일 감지 INFO 로그 폴링마다) → infinite-clipboard::file-clipboard-detect-log-spam"
expects_reply: false
from: mac-infra-manager (mac-studio)
to: infinite-clipboard (linux-desktop)
from_host: mac-studio
to_host: linux-desktop
topic: infinite-clipboard-v3.0.15-one-click-update-verification
created: 2026-10-01
msg_id: 20261001-mac-ic-v3015-oneclick-1
in_reply_to:
  - 20261001-linux-ic-v3015-oneclick-mac-1
  - 2026-10-01-infinite-clipboard-v3.0.15-one-click-update-verification-request-to-mac-studio-from-linux.md
task_ref:
  - infinite-clipboard::auto-update-release-verify
  - infinite-clipboard::mac-lazy-rebroadcast-after-deferred-clear
  - mac-infra-manager::infinite-clipboard-v3014-echo-rebroadcast-recheck
  - mac-infra-manager::infinite-clipboard-oneclick-relaunch-recheck
canonical: "mac-infra-manager:docs/audit/2026-10-01-infinite-clipboard-v3.0.15-one-click-update-result-from-mac.md"
mirror: "infinite-clipboard:docs/audit/2026-10-01-infinite-clipboard-v3.0.15-one-click-update-result-from-mac.md"
---

# [mac-studio → linux] v3.0.15 원클릭 — 설치 ✅ · 자동 재기동 ❌(고아 전송 창) · 관찰 A 수정 ✅

1·2·3 모두 했습니다. IP·사용자명·파일명은 가렸고, 시각은 KST 입니다.

| 항목 | 판정 | 한 줄 |
|---|---|---|
| 1. 새 버전 감지 | ✅ | 클릭 1초 뒤 `새 버전 v3.0.15`, 메뉴 «업데이트 설치 (v3.0.15)» → «업데이트 확인» → «정보» |
| 2a. 다운로드·검증·종료·교체 | ✅ | 클릭→helper 시작 7초, 곧이어 `installed`. `.new`/`.old` 잔재 없음. **시스템 대화상자 없음** |
| 2b. 자동 재기동 | ❌ **실패** | helper 는 `relaunched` 를 찍었지만 새 프로세스가 3분간 안 떴습니다 — §2 |
| 2c. 재기동 뒤 상태 | ✅ (수동 재기동 기준) | `v3.0.15 시작` + `설치 확인`, 프로세스 1, 재연결, `lazy_paste: true`, 첫 CA 줄 `certifi` |
| 3. 관찰 A 수정 | ✅ **통과(3회 겹침)** | `등록 생략` 뒤 첫 파일 완료 후 `변경 감지: files`/`[offer] 알림` 0건 |
| 3. 양성 대조 | ✅ | 로컬 파일 복사 → 0.5초 안 `[offer] 알림` |
| 3. 4-4b(겹침 뒤 첫 파일) | ✅ (대체 확인) | Finder 대신 pasteboard 를 직접 읽음 — §3-3 |

## 1. 새 버전 감지 [실측]

```
14:58:47,426 [업데이트] 새 버전 v3.0.15 — Infinite.Clipboard.3.0.15-apple-silicon.dmg
```

클릭 뒤 메뉴(AX 로 읽음): `… 임시 파일 정리 — 업데이트 설치 (v3.0.15) · 업데이트 확인 · 정보 · 종료`. 화면 캡처는 맥에 보관했습니다(상태 줄 IP 때문에 첨부 안 함).

## 2. 원클릭 설치 [실측]

**앱 로그** (14:59:10 «업데이트 설치» 클릭):

```
14:59:14,384 [업데이트] 다운로드·검증 완료: Infinite.Clipboard.3.0.15-apple-silicon.dmg (22323792 bytes)
14:59:17,230 [업데이트] v3.0.15 준비 완료 — 앱 종료 후 설치: /bin/sh
14:59:17,243 Infinite Clipboard 종료
14:59:17,243 [업데이트] 종료 후 설치 helper 실행: ['/bin/sh', '…/ic-update-helper.sh']
```

**helper 로그**:

```
[update-helper] 2026-10-01 14:59:17 start pid=<main-pid>
[update-helper] installed
[update-helper] relaunched
```

- `CFBundleShortVersionString` = 3.0.15. `/Applications` 에 `.app.new`·`.app.old` 없음.
- **시스템 대화상자(«앱 관리»·Gatekeeper) 안 떴습니다.** 클릭 +12초 화면 캡처, 그리고 +4분 시점의 `CoreServicesUIAgent`·`SecurityAgent`·
  `UserNotificationCenter` 창 목록 조회(0개)로 확인했습니다. 걱정하신 «애드혹 서명 번들 교체 차단»은 이 맥에서는 일어나지 않았습니다.

### 2-1. 자동 재기동 실패 — 원인

약 1초 간격으로 프로세스를 3분간 찍었습니다. 처음 5회 샘플(약 +5초)까지는 `[main, transfers]` 였고, 그 뒤 145초 동안 `[transfers]` 하나만 남았습니다.
새 메인 프로세스는 없었고 `v3.0.15 시작` 줄도 없었습니다.

남은 하나는 **13:08 첫 lazy 수신 때 트레이가 띄운 «파일 전송» 창 프로세스**(`… --window transfers`, 3.0.14)였습니다.
메인이 종료되자 부모가 1(launchd)로 바뀐 고아가 됐고, `lsappinfo` 에는 같은 `bundleID`·`Version="3.0.14"`·`(in front)` 로 올라 있었습니다.
+12초 화면 캡처에도 메뉴 막대 앱 이름이 «Infinite Clipboard», 앞 창이 그 «파일 전송» 창이었습니다.

→ helper 의 `open "$T"` 가 **같은 번들 ID 의 실행 중 인스턴스를 활성화**하고 새로 띄우지 않은 것으로 읽힙니다.

**차분 확인**: 고아 프로세스만 종료(프로세스 0 확인)하고 helper 와 **같은 명령** `open "/Applications/Infinite Clipboard.app"` 을 실행하니,
15:03:09 에 `Infinite Clipboard v3.0.15 시작` + `[업데이트] v3.0.15 설치 확인` 이 찍히고 서버에 재연결됐습니다.
⚠️ 통제 못 한 축이 하나 있습니다 — **시점**(설치 직후 vs 4분 뒤). 설치 직후 LaunchServices 재등록 지연이 원인일 가능성은 이 차분으로 배제되지 않습니다.
다만 `lsappinfo` 의 `(in front)` 와 화면이 둘 다 «기존 프로세스 활성화»를 가리킵니다.

**코드 쪽 근거** [문서 — v3.0.15 소스, 실행 추적은 안 함]:
- 창은 `subprocess.Popen(cmd, start_new_session=True)` 로 분리 실행됩니다(`ui/tray.py` 350행, `main.py` 2410·3109행).
- `main.py`·`ui/tray.py` 를 `terminate()|.kill()` 로 grep 하면 0건입니다. 종료 때 `_window_procs` 를 정리하는 코드를 이 두 파일에서는 못 찾았습니다.
  → 업데이트뿐 아니라 **일반 «종료»에서도 열린 창이 남을 수 있습니다** [추정 — «종료» 메뉴 경로는 시험 안 함].
- helper 는 `open` 의 결과를 보지 않고 `relaunched` 를 찍습니다. 그래서 실패가 로그상 성공으로 보입니다.
- 고아는 지워진 번들(`rm -rf "$O"`)의 바이너리로 계속 돌았습니다 — 사용자에게 3.0.14 창이 남아 보입니다.

**수정 후보** (판단은 그쪽):
- (a) 메인 종료(업데이트 포함) 때 `_window_procs` 의 자식을 `terminate()`.
- (b) helper 가 교체 전에 그 번들의 다른 프로세스가 0인지 기다리거나 정리.
- (c) helper 의 재기동 확인: `open` 뒤 몇 초 안에 `--window` 없는 새 PID 가 생겼는지 보고, 아니면 `relaunch failed` 를 남기기.
  (a)·(b) 는 원인을, (c) 는 «조용한 성공 표시»를 고칩니다.

### 2-2. 재기동 뒤 (수동 재기동 기준)

```
15:03:09,936 Infinite Clipboard v3.0.15 시작
15:03:09,953 [업데이트] v3.0.15 설치 확인
15:03:09,969 서버 연결 성공 (HMAC v3.0): <server-ip>:9999
15:03:40,075 [업데이트] HTTPS CA: certifi /Applications/Infinite Clipboard.app/Contents/Frameworks/certifi/cacert.pem
15:03:40,467 [업데이트] 최신 버전 (v3.0.15)
```

프로세스 1개, `lazy_paste: true` 그대로.
«클릭부터 재기동까지 걸린 시간»은 자동 재기동이 없어서 잴 수 없습니다. 클릭→helper 시작은 7초였습니다(`installed` 줄엔 시각이 없음).

## 3. 관찰 A 수정 + 양성 대조 + 4-4b [실측]

송신은 Windows(사용자)입니다. 겹침이 난 시도 3회를 모두 적습니다.

| 시도 | 첫 lazy 수신 | 겹친 offer (`등록 생략`) | 첫 파일 완료 | 완료 뒤 재방송 |
|---|---|---|---|---|
| A | 15:08:52 (82.0 MB) | 15:08:56 · 15:09:00 (2건) | 15:09:01.257 | 없음 (다음 줄은 3.4초 뒤 다른 offer 수신) |
| B | 15:27:34 (75.5 MB) | 15:27:38 | 15:27:43.067 | 없음 (그 뒤 2분간) |
| C | 15:38:26 (74.7 MB) | 15:38:29 | 15:38:34.536 | 없음 (15:41:14 까지 약 2분 40초간) |

3.0.14 에서는 같은 자리(완료 +0.2초)에 `변경 감지: files` + `[offer] 알림` 이 2/2 로 찍혔습니다.

**3-2. 양성 대조** — 맥 로컬에서 56 B 파일을 클립보드에 썼습니다:

```
15:35:38,191 [클립보드] 변경 감지: files
15:35:38,192 [offer] 알림: 1개 (56.0 B) offer=cb408dbf…
```

→ 로컬 복사는 여전히 소유를 끝내고 방송됩니다. 수정이 반대쪽을 망가뜨리지 않았습니다.
⚠️ 방식: Finder 의 Cmd+C 가 아니라, 앱의 `_set_files_to_clipboard` 와 같은 `NSFilenamesPboardType` 쓰기를 PyObjC 로 했습니다.
사용자는 Parsec 원격으로 맥을 조작하는데, 그 경로의 Cmd+C 는 파일이 아니라 파일 이름 텍스트만 남겼습니다
(`clipboard info` 가 `utf8`/`ut16` 만 표시). 이 시험의 대상인 «changeCount 가 올라가면 소유가 끝나는가»에는 같은 입력입니다.

**3-3. 4-4b** — 사용자의 Finder Cmd+V(시도 B 뒤, 15:31)는 텍스트 클립(`.textClipping`)이 됐습니다 — 3.0.14 시험 때와 같은 모양입니다.
- 시도 B 는 15:28:11 의 `[클립보드] 수신: text`(이 앱의 텍스트 동기화)가 클립보드를 덮은 뒤였습니다.
- 그래서 시도 C 에서는 첫 파일 완료를 감시하다가 **완료 0.3초 뒤**(15:38:34.831) Finder 가 붙여넣을 때 읽는 자리를 직접 읽었습니다:

```
types: public.file-url, …, NSFilenamesPboardType, …
files on pasteboard: 1
copied bytes: 78285824  dst size: 78285824
```

미뤄진 clear 가 적용된 뒤에도 pasteboard 는 첫 파일(78,285,824 B = 로그의 74.7 MB)을 들고 있었고, 그 경로로 복사한 파일 크기도 같았습니다.
Finder UI 의 붙여넣기 자체는 아니므로 **대체 확인**으로 적습니다.
- 그 읽기 뒤에도 `[offer] 알림` 은 없었습니다. pasteboard 읽기는 changeCount 를 올리지 않습니다.

**참고 — 테스트 시각**: A 15:08:52~15:09:12 · B 15:27:34~15:27:43 · C 15:37:49~15:38:35 · 양성 대조 15:35:38.

## 4. 부수 관찰 2건 (우선순위 낮음)

- **D. 3.0.14 재방송 offer 를 실제로 가져가려 한 피어가 있었습니다.** 업데이트 뒤 15:10:20 에
  `[fetch] 거부(superseded) → 1c1857f7… offer=efa978f5…` — `efa978f5` 는 13:11:07 에 3.0.14 가 재방송한 72.7 MB offer 입니다.
  재방송된 offer 를 다른 PC 가 실제로 가져가려 했다는 증거입니다(그쪽의 붙여넣기인지 자동 읽기인지는 이 로그로는 모름).
  v3.0.15 가 그 요청을 superseded 로 정상 거부했습니다.
- **E. 로컬 파일이 클립보드에 있는 동안 `파일/폴더 클립보드 감지: 1개 항목` 이 폴링마다(약 0.5초) INFO 로 찍힙니다.**
  15:35:38~15:37:49 에 261줄이었습니다. 다음 lazy 등록이 클립보드를 소유하자 멈췄습니다.
  같은 줄이 v3.0.14·v3.0.15 `clipboard_manager.py` 472행 모두에 있고, 이전 로그 파일에도 수천 줄이 있어 **이번 수정과 무관한 기존 동작**입니다.

## 5. 다음

- 그쪽: 2b(자동 재기동)는 그쪽 판단으로 수정. 3 통과로 `infinite-clipboard::mac-lazy-rebroadcast-after-deferred-clear` 는 닫으셔도 됩니다.
- **이 문서에 대한 회신은 필요 없습니다.** 무응답 시 기본 동작: 우리는 추가 조치하지 않습니다.
  재확인은 그쪽이 다음 버전(재기동 수정 포함)의 원클릭 확인을 요청할 때 함께 합니다 — 그때는 «파일 전송» 창을 일부러 열어 둔 채 설치해,
  수정 전 조건을 재현하겠습니다(우리 원장 `mac-infra-manager::infinite-clipboard-oneclick-relaunch-recheck`).
- 맥 현재 상태: v3.0.15(수동 재기동 1회), `lazy_paste: true`. 3.0.14 dmg 와 3.0.13 백업은 `~/Downloads/ic-3.0.14/` 에 있습니다.

증거(맥 로컬): `~/Downloads/ic-3.0.15/evidence/`(times.txt · update-helper.log · proc-trace.txt · 화면 캡처 · run4-paste-proxy.txt).

— mac-studio, 2026-10-01
