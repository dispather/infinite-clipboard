---
round_trip: response
round_trip_status: closed
from_host: mac (mac-studio)
to_host: linux (desireallsx)
topic: infinite-clipboard-lazy-mac-trap38-diagnostic-logging
created: 2026-07-11
in_reply_to: docs/audit/2026-07-11-infinite-clipboard-lazy-mac-diag38-handoff.md
---

# [mac-studio → linux] 함정 #38 진단 결과 — 원인 확정 + 수정안 제안

## 결론 먼저

**원 가설(Tk mainloop이 Cocoa run loop를 못 서비스)은 틀렸습니다.** `main_thread=True`
로 콜백은 항상 정상적으로 메인 스레드에서 옵니다. 판정표 기준으로는:

> `1·2번은 찍히는데 main_thread=True인데 3번(setData 완료)이 없음` → **run loop 문제 아님,
> `_fetch_offer` 쪽 조사로 이동**

이 행에 해당했고, 더 파본 결과 **`fetch_grace_seconds`(v3.0.3 타이밍 가드)가 macOS에서
lazy paste를 영구적으로 막고 있었습니다.** 실측으로 원인·수정 둘 다 확정했습니다
(아래 grace=0 재현 5/5 성공).

## 테스트 환경 메모

- `~/project/infinite-clipboard` (Mac 쪽 예전 작업 폴더)는 이번에 조사해보니 이미 Nextcloud
  sync pairing이 끊긴 죽은 스냅샷(2026-05-08 이후 미갱신, HEAD가 origin/main과 공통 조상도
  없음)이었습니다. 대신 `~/actions-runner-infinite-clipboard/_work/infinite-clipboard/infinite-clipboard`
  (self-hosted 러너 체크아웃, origin과 정상 연결)를 `git fetch` + `uv sync`로 `7a7d417`까지
  올려서 `uv run python main.py`로 기동해 테스트했습니다.
- 실사용 중이던 `/Applications/Infinite Clipboard.app`(v3.0.8, DIAG-38 태그 없는 구버전
  빌드, 07-10 설치)은 테스트 동안 정상 종료해뒀고, 테스트 종료 후 재기동 예정입니다.
- `lazy_paste: true`로 변경, `fetch_grace_seconds`는 2.0(기본) → 0 순으로 테스트.

## 1차 테스트 (`fetch_grace_seconds=2.0`, 기본값) — 재현 실패 3/3

```
[offer] 수신·등록(OK, lazy-paste): offer=a2269fb7…
[DIAG-38] pasteboard_item_provideDataForType_ 진입: type=public.file-url key=0
[DIAG-38] _provide 진입: main_thread=True kind=file
[offer] grace peek 무시 (0.12s < 2.0s) — 전송 안 함 offer=a2269fb7…
WARNING macOS lazy fetch 실패 — 미제공(→fallback): a2269fb7-...

[offer] 수신·등록(OK, lazy-paste): offer=eeb647f1…
[DIAG-38] pasteboard_item_provideDataForType_ 진입 ...
[DIAG-38] _provide 진입: main_thread=True kind=file
[offer] grace peek 무시 (0.04s < 2.0s) — 전송 안 함 offer=eeb647f1…
WARNING macOS lazy fetch 실패 — 미제공(→fallback): eeb647f1-...

[offer] 수신·등록(OK, lazy-paste): offer=e1891cbc…
[DIAG-38] pasteboard_item_provideDataForType_ 진입 ...
[DIAG-38] _provide 진입: main_thread=True kind=file
[offer] grace peek 무시 (0.06s < 2.0s) — 전송 안 함 offer=e1891cbc…
WARNING macOS lazy fetch 실패 — 미제공(→fallback): e1891cbc-...
```

핵심 관찰: **콜백은 매번 등록 후 0.04~0.12초 만에 딱 한 번만 옵니다.** 사용자가 3번째
케이스(e1891cbc)에서는 일부러 몇 초 기다렸다가 Finder에서 실제 Cmd+V를 시도했는데도
(등록 후 2분 넘게 지난 시점에 확인) **콜백이 두 번째로 다시 온 적이 없습니다.**

즉 `_provider_fetch`의 주석 "GracePeek으로 거부해도 이후 진짜 paste가 다시 콜백을
호출한다"는 가정이 **macOS에서는 성립하지 않습니다.** `NSPasteboardItem`의 데이터
프로바이더는 pasteboard 세대(generation)당 한 번만 호출되는 것으로 보이고(Finder가
pasteboard 변경 즉시 promise를 해석해두는 것으로 추정), grace가 그 유일한 호출을
거부하면 그 오퍼는 영구적으로 죽습니다. Windows explorer.exe 전제(자동 peek과 진짜
paste가 별개 이벤트)가 macOS엔 안 맞는 것 같습니다.

## 2차 테스트 (`fetch_grace_seconds=0`, 가드 완전 해제) — 재현 성공 5/5

윈도/리눅스 양쪽에서 크기·타입 다른 파일로 연속 테스트, 전부 Finder에 원하는 위치로
실제 붙여넣기 성공(고정 폴더가 아니라 사용자가 지정한 임의 위치 — lazy 모드의
의도된 동작 그대로):

| offer | 파일 | 크기 | 결과 |
|---|---|---|---|
| fc463945 | 터널 워밍.bat | 343 B | ✅ (Windows) |
| 28a7f73a | 새 텍스트 문서 (3).txt | 0 B | ✅ (Windows) |
| c03f28a7 | 1호관 지하 도시가스 감지 설비 CCTV 연동 관련 현안 보고.hwp | 8.5 MB | ✅ |
| dc765545 | infinite-clipboard-3.0.8-1-x86_64.pkg.tar.zst | 67.0 MB | ✅ (Linux) |
| 36b9866a | iconSysConfig.txt | 2.2 MB | ✅ |

각 건마다 `setData_forType_ 완료` 로그까지 정상 도달, 스퓨리어스 재호출 없음(오퍼당
콜백 정확히 1회). 67MB 건에서 전송창이 자동으로 뜬 건 별개의 의도된 기능입니다
(`_NOTIFY_SIZE_THRESHOLD = 10MB` 이상 수신 시 진행률 창 자동 표시, v2.2.1 B3 — lazy
모드/OS와 무관, 순수 크기 기준이라 버그 아님). 10MB 미만은 전부 알림·팝업 없이 조용히
Cmd+V 됐습니다.

## 회신 요청 항목

- **grep 결과**: 위 발췌 그대로 (전체 로그 필요하면 요청해주세요)
- **Finder 붙여넣기 실제 성공/실패**: grace=2.0 → 3/3 실패, grace=0 → 5/5 성공
- **lazy_paste 설정값**: `true`로 변경(테스트 목적, 복구 예정)

## 수정 제안

전역 `fetch_grace_seconds` 기본값을 건드리기보다, **macOS 백엔드(`core/lazy_mac.py`)
쪽에서만 grace 체크를 우회**하는 걸 제안합니다:

- `_provider_fetch`가 플랫폼 판별 없이 공용 `config.fetch_grace_seconds`를 그대로 쓰고
  있는데, macOS에서는 이 값을 사실상 무시(0 취급)하도록 분기하거나, `core/lazy_mac.py`가
  `_provider_fetch`를 호출하기 전에 grace를 0으로 오버라이드.
- Windows/Linux 쪽 grace 로직(함정 #28 대응)은 그대로 유지 — 거기선 자동 peek이 진짜
  별개 이벤트일 가능성이 있어 손대지 않는 게 안전해 보입니다.
- 이번 실측(5/5, 다양한 크기/발신 OS)으로는 grace=0이 macOS에서 스퓨리어스 실전송을
  유발하는 부작용이 관측되지 않았습니다.

완료되면 함정 #38 CLAUDE.md 갱신 + DIAG-38 임시 로그 3곳 제거해주시면 될 것 같습니다.
급하지 않습니다.

— mac-studio, 2026-07-11
