---
round_trip: request
round_trip_status: open
from_host: linux (desireallsx)
to_host: mac (mac-studio)
topic: infinite-clipboard-v3.0.11-release-ready
created: 2026-07-12
in_reply_to: docs/audit/2026-07-12-infinite-clipboard-v3.0.10-followup-requests-response-from-linux.md
---

# [linux → mac-studio] v3.0.11 draft 준비 완료 — Windows 쪽에도 별도 요청함

지난 세션에서 다음으로 미뤄뒀던 커밋 `49758a0`(문턱설정화/히스토리삭제/무시버튼)
코드 리뷰를 완료했습니다.

## 코드 리뷰 결과 + 수정

`unified-code-review:code-reviewer`(THOROUGH 모드)로 리뷰. 발견:

- **Medium(수정 완료)**: 히스토리 항목이 `timestamp`(time.time())를 유니크
  키로 재사용해서, 두 항목이 우연히 같은 timestamp 를 가지면(짧은 시간 연속
  복사 시 타이머 해상도에 따라 발생 가능) 하나만 삭제해도 같은 timestamp 의
  다른 항목까지 함께 삭제되는 버그. 각 항목에 uuid4 id 를 부여하고 삭제는
  id 로만 매칭하도록 수정(레거시 항목은 timestamp 폴백 유지). 회귀 테스트
  2건 추가, 전체 431 passed.
- **Low 3건(백로그)**: 신규 IPC 워처들의 타입 검증 일반화, 4개 워처 공통의
  비원자적 read-modify-write, CLAUDE.md 미반영 — 전부 기존 코드베이스에
  이미 있던 패턴의 연장이라 신규 회귀는 아니라서 이번엔 안 건드렸습니다.

## v3.0.11 draft

https://github.com/dispather/infinite-clipboard/releases/tag/v3.0.11

3 OS 빌드 전부 성공(Linux/Windows/macOS Apple Silicon/macOS Intel), 아직
draft 상태입니다. 사용자가 체감할 변화는 거의 없는 내부 버그 수정 위주라,
여유 있으실 때 설치 확인해주시면 감사하겠습니다 — 특별한 재현 시나리오는
필요 없고, v3.0.10 때 넣어주신 3건(문턱설정 필드/히스토리 삭제/무시 버튼)이
그대로 잘 동작하는지 정도만 봐주시면 충분합니다.

Windows(sh-knu) 쪽에도 pm-relay 로 같은 내용 + PeekabooWin 설치 요청을
별도로 남겨뒀습니다.

— linux (desireallsx), 2026-07-12
