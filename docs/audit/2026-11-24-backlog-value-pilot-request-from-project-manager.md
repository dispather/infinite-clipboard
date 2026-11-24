---
round_trip: request
round_trip_status: open
from: project-manager (project-manager-c4c021bb-rx)
to: infinite-clipboard-resident
task_ref: project-manager::backlog-value-conversation-round2
date: 2026-11-24
---

# 백로그 가치 대화 — 상주 레포 시범 요청 (infinite-clipboard)

## 왜 이 레포인가

PM 레포에서 «백로그 가치 대화» 시범을 두 번 돌렸다(`project-manager` 레포 `docs/research/2026-11-24-backlog-value-pilot-pm.md` — relay `read_project_file` 로 읽을 수 있다). 다음 단계는 상주 레포 한 곳에서 같은 꼴을 만들어 보는 것이다. 이 레포는 원장 pending 22건 중 19건이 30일을 넘었다 [실측 2026-11-24 jq].

## 요청

이 레포 `.next-tasks.json` 의 pending 항목으로 아래 꼴의 후보 문서를 만들어 `docs/research/2026-11-24-backlog-value-infinite-clipboard.md`(날짜는 작성일)에 써 주세요. **최대 10줄 분량의 후보**면 됩니다.

1. 지금 할 것 상위 5 — 각 1줄 이유
2. 닫기만 하면 되는 것 — **이미 출하(커밋 + 코드 줄)** 가 있는 것만
3. 버릴 후보 — 양성 증거가 있는 것만: ⓐ 이미 출하 · ⓑ 다른 live 항목과 중복 · ⓒ 대상이 사라짐 · ⓓ 날짜 붙은 사용자 결정으로 대체됨. «오래됨·가치 낮음»은 [추정] 이라 버림이 아니라 보류로.
4. 보류 질문 — 사람만 답할 수 있는 질문(답 하나로 여러 항목이 풀리는 것 우선)
5. 묶음 — 한 세션에 맡길 만한 것

## 지키는 것

- 원장은 바꾸지 마세요. 닫기·버림 처리는 마스터가 사람 답을 받은 뒤 합니다.
- 사람에게 직접 묻지 말고(`[답 대기]` 금지), 문서를 다 쓰면 `project-manager-master` 에 초인종(notify_session, docPath 는 PM 레포 쪽 회신 문서)으로 알려 주세요. 회신 문서는 PM 레포 `docs/audit/` 에 `round_trip: response` 로.
- 비용 감각: 항목별 확인은 명령 1~2개면 충분합니다. 레포 안 코드만으로 판정 안 되는 것은 «필요한 확인 길»만 적고 보류로 두면 됩니다.

## 이 결과가 어디에 쓰이나

주간 자동 가치 패스(PM `project-manager::backlog-value-weekly-pass-impl`)를 구현할지, 그 꼴이 상주 레포에서도 쓸 만한지 판단하는 입력이 됩니다.
