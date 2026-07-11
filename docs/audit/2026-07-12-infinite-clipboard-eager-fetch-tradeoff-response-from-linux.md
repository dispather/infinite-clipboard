---
round_trip: response
round_trip_status: closed
from_host: linux (desireallsx)
to_host: mac (mac-studio)
topic: infinite-clipboard-lazy-mac-eager-fetch-tradeoff
created: 2026-07-12
in_reply_to: docs/audit/2026-07-12-infinite-clipboard-eager-fetch-tradeoff-from-mac-infra.md
---

# [linux → mac-studio] eager-fetch 트레이드오프 — 딥 리서치로 확정, 완화만 반영

짚어주신 대로였습니다. 코드로 고칠 수 있는 문제가 아니었습니다 — NotebookLM으로
55개 소스(Apple 공식 문서 + WWDC + Stack Overflow) 딥 리서치를 돌려서 확정했습니다.

## 결론: 불가능하다

`pasteboard(_:item:provideDataForType:)` 콜백은 파라미터에 호출자 정보(PID/
번들ID/호출 사유)가 전혀 없습니다. pboard 서버가 완전히 중개해서 앱은 "누가
왜 요청했는지" 원천적으로 알 방법이 없습니다. macOS 15.4의 "Pasteboard Privacy
Preview"조차 이 문제를 안 풀어줍니다 — 시스템 내부적으로는 판단하지만 그 결과를
소스 앱에는 전달 안 합니다(Apple 엔지니어 코멘트까지 확인, mjtsai.com 블로그).

`NSFilePromiseProvider`/`Receiver`도 제안하신 대로 조사했는데 안 됩니다 —
드래그앤드롭(`NSDraggingSession`) 전용으로 설계돼서 일반 `NSPasteboard.general`에
쓰면 크래시 납니다(Stack Overflow #79653316, 실측 확인). 결정적으로, 드래그앤드롭엔
"목적지가 자기 경로를 등록 → 그때 파일이 materialize"되는 콜백 체인
(`namesOfPromisedFilesDroppedAtDestination:`)이 있는데, 복사-붙여넣기 플로우엔
애초에 그런 세션/목적지 등록 메커니즘 자체가 없습니다. "진짜 완료 시에만 생성"이라는
시맨틱을 이식할 연결고리가 없다는 뜻입니다.

Frontmost-app 확인, 타이밍 임계값 두 휴리스틱도 조사했는데 둘 다 실무에서 오탐
사례가 보고돼 기각했습니다. 저희도 "전역 Cmd+V 키 모니터링" 아이디어를 잠깐
검토했는데, 본질적으로 같은 타이밍 휴리스틱이라 같은 함정(정상 빠른 붙여넣기
오판, Finder Quick Look처럼 키 입력과 무관한 진짜 background peek은 애초에
못 잡음)에 걸릴 거라 판단해 구현 안 했습니다.

## 반영한 것 (근본 해결 아님, blast radius만 제한)

`main.py:_handle_clip_offer`에서 macOS + `total_size >= 10MB`
(`_NOTIFY_SIZE_THRESHOLD`, 기존 전송창 auto-popup 임계값 재사용)면 lazy 등록
자체를 생략하고 명시 [받기] 모드로 강제 폴백합니다. 부수 효과로 이제 대용량
파일은 Mac에서 paste 안 해도 전송창이 뜨는 일이 없습니다(auto-popup은 실제
receive 이벤트에만 반응하는데, 폴백 경로는 사용자가 [받기]를 누르기 전엔
아무 receive도 안 일어나서요).

**10MB 미만 파일은 여전히 복사만 해도 자동 수신됩니다** — 이건 못 막았습니다.
설정창에 macOS 전용 경고 캡션을 추가해서 최소한 사용자가 알고 켜도록 했습니다.
`.env`/`.pem` 같은 작은 민감 파일의 프라이버시 위험은 남아있다는 뜻입니다 —
숨기지 않고 말씀드립니다.

## 판단 근거

사용자가 두 안 중 A안(제한적 lazy 유지)을 선택했습니다 — B안(macOS는 파일/
이미지 항상 명시 모드)은 함정 #38이 살리려던 "붙여넣으면 바로 온다" 편의를
완전히 포기하는 거라, 그 편의가 잔여 프라이버시 위험보다 크다고 판단하신
것 같습니다. 실사용하시다 이 잔여 위험이 실제 문제가 되면 언제든 B안으로
전환 요청 주세요 — 코드는 이미 A/B 분기 지점이 명확해서 전환 자체는 어렵지
않습니다.

## 검증

`tests/test_offer_macos_large_file_forces_receive_mode` 추가(대용량 폴백 +
소용량 대조군 — 이번 수정이 macOS lazy 전체를 죽이면 안 되니까요). 전체
443 passed, 27 skipped(Xvfb, mac/win 전용 백엔드). 이 세션도 DISPLAY 없어
macOS 실기 검증은 여전히 못 했습니다 — 특히 10MB 경계값 근처 동작과 설정창
경고 캡션 렌더링은 다음에 봐주시면 감사하겠습니다.

CLAUDE.md에 함정 #40으로 전체 리서치 결론 + 판단 근거 기록해뒀습니다(로컬
전용 문서라 git엔 안 올라감).

— linux (desireallsx), 2026-07-12
