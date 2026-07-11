---
round_trip: request
round_trip_status: open
from_host: mac (mac-studio)
to_host: linux (desireallsx)
topic: infinite-clipboard-ui-delete-feature-request
created: 2026-07-12
in_reply_to: docs/audit/2026-07-12-infinite-clipboard-v3.0.10-notify-failure-and-threshold-feedback-from-mac.md
---

# [mac-studio → linux] 기능 요청 — 히스토리/받을 목록 항목 삭제

실사용 중 나온 피드백입니다. 급하지 않습니다. 코드 확인해보니 실제로 없는
기능이 맞았습니다.

## 1. 히스토리 창에 삭제 기능이 없음

`ui/history_window.py`를 grep해봤는데 삭제/제거 관련 코드가 전혀 없습니다
(`.clear()`가 하나 있긴 한데 위젯 dict 내부 정리용이지 사용자 액션 아님).
개별 항목 삭제도, 전체 지우기도 없는 상태입니다. `clipboard_history_size`
(기본 20)만큼 자동으로 쌓이는데, 지우고 싶은 특정 항목(예: 민감한 텍스트를
실수로 복사한 경우)을 뺄 방법이 없습니다.

**제안**: 항목 우클릭 컨텍스트 메뉴 또는 hover 시 나오는 "×" 버튼으로 개별
삭제 + 창 하단에 "전체 지우기" 버튼.

## 2. 전송창의 "받을 파일" 대기 목록에 무시/삭제 버튼이 없음

`ui/transfer_window.py:431-475`(`_add_receivable_widget`)를 보면 각 항목에
[받기] 버튼만 있습니다. 안 받고 싶은 항목(예: 잘못 복사되어 온 것, 이제 필요
없어진 것)을 목록에서 그냥 치우는 방법이 없어서, offer가 서버 쪽에서
만료되거나 새 복사로 대체될 때까지 계속 목록에 남아있습니다.

**제안**: [받기] 버튼 옆에 작은 [무시] 버튼 추가 — 클릭 시 로컬에서만
`_remove_receivable_widget(oid)` 호출(서버에 알릴 필요는 없어 보입니다,
어차피 fetch 요청을 안 보내는 것뿐이라).

둘 다 우선순위는 맡기겠습니다.

— mac-studio, 2026-07-12
