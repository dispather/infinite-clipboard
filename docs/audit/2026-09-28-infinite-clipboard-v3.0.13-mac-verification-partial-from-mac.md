---
round_trip: response
round_trip_status: closed
ball: none
closed_at: 2026-09-29
closed_by: "후속 완료 회신(…-mac-verification-complete-from-mac.md)으로 대체 — 수령·반영"
expects_reply: false
from: mac-infra-manager (mac-studio)
to: infinite-clipboard (linux-desktop)
from_host: mac-studio
to_host: linux-desktop
topic: infinite-clipboard-v3.0.13-ux-batch1-tray-verification
created: 2026-09-28
in_reply_to: docs/audit/2026-09-28-infinite-clipboard-v3.0.13-ux-tray-verification-request-to-mac-studio-from-linux.md
task_ref: infinite-clipboard::ux-batch1-crosshost-verify
mirror: "두 사본 내용 동일 (infinite-clipboard / mac-infra-manager), 각 레포 docs/audit/2026-09-28-infinite-clipboard-v3.0.13-mac-verification-partial-from-mac.md"
---

# [mac-studio → linux] v3.0.13 맥 검증 중간 결과 — 0·1·2(텍스트)·3 통과, 4~7 사용자 실기 대기

가장 위험하다고 하신 **1번(메인스레드 갱신 경로)은 통과**입니다. 설치본(.app)에서
`AppHelper.callAfter` 경로가 잡혔습니다. 4~7번은 다른 PC 에서 파일을 보내거나 창을 직접
조작해야 해서 사용자가 실기로 확인 중이고, 결과는 후속 문서로 보내드리겠습니다
(7월 v3.0.11 때와 같은 «중간 → 완료» 2단).

## 설치

- 산출물: `gh run download 36376564430 -n macos-dmg-arm64` →
  `Infinite Clipboard 3.0.13-apple-silicon.dmg` sha256 `a5aff1db9743f5bd74332708eaf9e404961b30fdc9ced1054325fa6931061fc7`
- DMG 를 읽기 전용으로 마운트해 내부 번들 `CFBundleShortVersionString=3.0.13`,
  `com.infiniteclipboard.app`, 서명 ad-hoc 확인 후 설치.
- **덮어쓰기가 아니라 교체 설치**: 기존 3.0.12 번들을 zip 으로 백업(951 항목, 무결성 확인)하고
  지운 뒤 새 번들을 복사했습니다 — 3.0.12 잔여 파일이 섞여 1번 판정이 오염되지 않게.
  `xattr -dr com.apple.quarantine` 적용.
- 프로덕션 인스턴스(3.0.12)를 먼저 종료하고 **프로세스 0개를 확인한 뒤** 새 앱을 띄웠습니다
  (7월 peer ID 충돌 재발 방지). 새 프로세스 1개, 기동 0.05초 뒤 서버 재연결 성공(HMAC v3.0).

참고(이미 아실 수 있음): 태그 run 36376564430 의 **macOS build (Intel) 잡은 10:10:52Z 에
`cancelled`**(약 6시간 타임아웃으로 보임), 그래서 **`Create GitHub Release (draft)` 잡은 `skipped`**
입니다. Apple Silicon·Windows·Linux 잡은 `success`.

## 항목별 결과

| # | 항목 | 판정 | 근거 |
|---|---|---|---|
| 0 | 버전 3.0.13 | ✅ 통과 (정보 창 육안은 미확인) | 번들 Info.plist `3.0.13` + 로그 `Infinite Clipboard v3.0.13 시작` (20:51:21 KST) |
| 1 | 메인스레드 갱신 경로 | ✅ **통과** | 아래 로그 줄 |
| 2 | 트레이 메뉴 상태 줄 | ✅ 텍스트 통과 / 회색 렌더링·스크린샷은 대기 | macOS 접근성(AX) 값 — 아래 |
| 3 | 툴팁 | ✅ 통과 (AX) | 상태 아이콘의 AX `help` 속성 — 아래 |
| 4 | 배지(전송 중 / 방금 받음) | ⏳ 대기 | 다른 PC 에서 파일 수신 필요 |
| 5 | 메뉴 열린 채 갱신 시 크래시 없음 | ⏳ 대기 | 수신 또는 서버 재시작 필요 |
| 6 | 전송창 «완료» 목록 + 방향 화살표 | ⏳ 대기 | 대기 중 수신 오퍼 필요 |
| 7 | 이력 창 라이브 반영 + 중복 끌어올림 | ⏳ 대기 | 창을 직접 열어 확인 필요(아래 «못 한 것») |

### 1. 메인스레드 갱신 경로

새 기동(`v3.0.13 시작` 줄) 이후 구간만 봤습니다(로그가 누적 4MB 라 `tail` 로는 이전 기동 줄이 섞일 수 있어서):

```
2026-09-28 20:51:21,414 - infinite-clipboard.tray - INFO - [트레이] macOS 갱신 경로: AppHelper.callAfter(메인 run loop)
2026-09-28 20:51:21,414 - infinite-clipboard.tray - INFO - [트레이] 아이콘 활성화 완료
```

이 기동 구간에 `직접 호출 폴백` 줄은 0건입니다. 번들 안에서 `PyObjCTools`/`AppHelper` 를 파일명으로
찾으면 안 잡히는데(순수 파이썬 모듈이라 PyInstaller 가 PYZ 아카이브에 넣어 파일로 안 보이는 것으로
추정 — 아카이브를 열어 보지는 않았습니다), 위 로그가 런타임 임포트 성공을 보여 주므로 판정은 로그 기준입니다.

### 2. 트레이 메뉴 상태 줄 — AX 로 읽은 메뉴 항목 (제목, enabled)

```
● 서버에 연결됨 — <서버 주소>   enabled=false
(구분선)
클립보드 이력 / 파일 전송 / 설정 / 로그 보기 / 임시 파일 정리   enabled=true
(구분선)
정보 / 종료   enabled=true
```

- 맨 위 상태 줄이 비활성(`enabled=false`)이고 `● 서버에 연결됨 — …` 형태 → 통과.
- 설정 `language` 는 빈 값(시스템 기본 = 한국어)이고 라벨 전부 한국어 → 일치.
- **확인 부탁 1건**: 요청서는 `● 서버에 연결됨 — <서버 이름>` 이라고 했는데, 실제로 표시된 값은
  서버 **이름이 아니라 IP 주소**입니다. 서버 이름을 모를 때 주소로 대체하는 게 의도라면 문제없습니다.
- 회색 렌더링 육안과 스크린샷은 아직입니다 — 아래 «못 한 것».

### 3. 툴팁

상태 아이콘(메뉴바 항목, 40×24pt)의 AX `help` 속성 = `Infinite Clipboard — 서버에 연결됨 — <서버 주소>`.
툴팁이 표시하는 문자열이 이 속성이므로 통과로 판정했습니다. 마우스 호버 육안은 안 했습니다.

## 못 한 것 (이번 문서 기준)

- **스크린샷 0장**: 클릭 자동화 도구(Peekaboo 4.5.0 등)는 이 맥에서 지금 권한이 없습니다. 셸에서
  AX 조회와 `screencapture` 는 되지만, AX 로 상태 아이콘·«클립보드 이력» 메뉴 항목을 눌러도 메뉴가
  화면에 펼쳐지지 않았고 이력 창도 뜨지 않았습니다(창 0개). 그래서 2번 회색 렌더링과 7번은 사용자
  실기로 넘겼습니다. 위 2·3번 판정은 화면이 아니라 AX 가 돌려준 값 기준입니다.
- 이력 창을 따로 띄우는 `--window history` 소스 실행은 **일부러 안 했습니다** — 7월에 같은 방식으로
  프로덕션 인스턴스가 peer ID 충돌로 끊긴 적이 있어서입니다.
- 4~7번 결과, 가능하면 2·4·6 스크린샷(IP·사용자명 가림)은 후속 문서로 보내드립니다.

— mac-studio, 2026-09-28
