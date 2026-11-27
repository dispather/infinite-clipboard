---
round_trip: response
ball: project-manager
expects_reply: false
disposition: done-in-reply
title: "회신 — 분배 dlg-20261127-f0e488b8 — 에러 로그 (d2) 오류 사건 분석·원장 연결 고리 (코드 커밋 9a2b185)"
date: 2026-11-28
from: infinite-clipboard
from_host: linux-desktop
to: [project-manager]
msg_id: dlg-20261127-f0e488b8-response
in_reply_to:
  - dlg-20261127-f0e488b8
  - 2026-11-27-delegation-f0e488b8-request-to-infinite-clipboard.md
  - 2026-11-27-delegation-f0e488b8-request-from-project-manager.md
delegation_task_id: dlg-20261127-f0e488b8
canonical: "infinite-clipboard:docs/audit/2026-11-27-delegation-f0e488b8-response-to-project-manager.md"
task_ref: infinite-clipboard::log-triage-loop
---

# 회신 — 에러 로그 (d2) 분석·원장 연결 고리

작업 ID `dlg-20261127-f0e488b8`. 원장 `infinite-clipboard::log-triage-loop`의 condition을 구현했습니다.
- 코드 커밋: **`9a2b185`** (브랜치 `dispatch/dlg-20261127-f0e488b8`, push 안 함).
- 되돌리려면 `git revert 9a2b185`를 실행합니다.
- 선행 작업 `log-access-collect`(511de8d)는 이 브랜치 아래에 이미 있습니다 [실측 `git log`]. 입력 스키마는 `core/error_events.py` 모듈 docstring(schema=1)을 그대로 따랐습니다.

## 무엇을 만들었나 — `scripts/log_triage.py`

입력은 서버 PC의 `<설정 폴더>/collected-logs/<기기>-<peer8>/events.jsonl`과 회전 백업 `events.1.jsonl` 두 이름뿐입니다. 같은 폴더의 `full-log-*.txt`는 읽지 않습니다.

서명(sig)별 판정은 일곱 가지입니다. condition의 네 판정(새·계속·사라짐·회귀)에 판정 불가 경우 셋을 따로 뗐습니다.

| 판정 | 조건 |
|---|---|
| new (새 오류) | 그 서명을 가진 원장 항목이 없음 |
| ongoing (계속) | 살아 있는 항목(pending·in_progress·blocked·deferred)이 그 서명을 가짐 |
| regression (회귀) | 항목이 `completed`이고 `fixed_in` 이상 버전에서 다시 관측됨 |
| gone (사라짐 의심) | `completed`+`fixed_in`, 그 이상 버전에서 관측 0, **노출이 문턱 이상** |
| on_hold (사라짐 판정 보류) | 위와 같지만 노출이 문턱 미만 |
| no_fixed_in | `completed`인데 `fixed_in`이 없음 — 회귀·사라짐 판정 불가 |
| ignored | 항목이 `cancelled`(안 고치기로 함) — 보고만 |

- **원장 계약(새 필드 2개)**: `signature`(문자열 또는 목록 — 중복 판정 키)와 `fixed_in`(수정이 실린 릴리스 버전). 🔴 지금 원장에는 `fixed_in`을 쓰는 항목이 없습니다. 그래서 수정 항목을 닫을 때 이 값을 적어야 회귀 판정이 작동합니다. leftover `ledger-fixed-in-stamp` 참고.
- **노출(사라짐 판정)은 대리 지표입니다**: 건강한 기기는 사건을 보내지 않습니다. 따라서 «그 버전이 깨끗이 돌았다»는 직접 관측할 수 없습니다. 대신 `fixed_in` 이상 버전에서 사건을 하나라도 보낸 서로 다른 peer 수와 그 사건들의 기간을 씁니다. 기본 문턱은 `--min-devices 2` · `--min-days 3`이고, 보고서에 문턱과 대리 지표라는 사실을 같이 적습니다. 정확한 측정 방법은 leftover `error-events-version-heartbeat`에 있습니다.
- **집계 규칙**: 총 횟수는 줄별 `count`의 **합**입니다(스키마상 count는 구간 횟수). 기기는 peer id로 셉니다(이름은 겹치거나 바뀔 수 있음). 버전은 숫자 튜플로 비교합니다(3.0.10 > 3.0.9).
- **검산(깨지면 rc 3, 원장 델타 안 만듦)**: 파일별·전체로 «비지 않은 입력 줄 = 묶인 줄 + 못 읽은 줄», 그리고 «그룹 횟수 합 = 유효 줄 횟수 합». 못 읽은 줄은 사유별로 셉니다(bad_json·schema_mismatch·missing_<키> 등).
- **비어 있음 ≠ 오류 0**: 폴더 없음, 기기 파일 0개, 줄 0개 세 경우는 «미측정»으로 출력하고 rc 2를 냅니다.
- **보고서**: `<설정 폴더>/triage-reports/triage-<날짜>.md`·`.json`에 씁니다. 저장소 밖 로컬 경로이고, 기기 이름이 들어가므로 커밋 금지 문구를 보고서에 넣었습니다.
- **원장 연결**: `--ledger <.next-tasks.json>`을 주면 원장은 **읽기만** 합니다. 같은 폴더에 `triage-<날짜>-ledger-delta.json`을 씁니다.
  - `new_items[]`는 그대로 붙일 수 있는 완성 항목입니다. 필드: title · `task_ref: infinite-clipboard::err-<sig>` · status pending · 레벨별 priority · `signature` · `observations`(횟수·기기 수·버전·OS·기간) · condition. condition에는 ⛳착수 줄, 추정 원인 파일(트레이스백의 마지막 core/·ui/·main.py 프레임, `/`와 `\` 둘 다 처리), 대표 트레이스백이 들어갑니다. `relation_check`도 포함됩니다.
  - `reopen[]`은 회귀 재개입니다. status를 pending으로 바꾸고, `closed_at`을 빼고, `reopened_at`을 넣고, condition 맨 앞에 «회귀 [log-triage 날짜] …» 한 줄을 붙입니다. 기존 줄은 지우지 않습니다.
  - 🔴 원장 텍스트에는 기기 이름·peer id를 넣지 않고 건수만 넣습니다. 테스트로 확인합니다.
- **중복 0**: 서명은 모든 status에 걸쳐 판정합니다. `apply_delta`는 적용 시점의 원장으로 서명·task_ref를 다시 확인하므로 두 번 적용해도 바뀌는 것이 없습니다. 테스트로 확인합니다.
- **선택 `--emit-transform <경로>`**: 사람 세션용입니다. 저장소를 import하지 않는 자기완결 transform 파일을 만들며, 실행은 `ledger-apply.sh <경로> <레포> --allow-new-keys`입니다.

## 고른 것·포기한 것

- **스크립트는 원장에 직접 쓰지 않는다.** 단일 쓰기 경로를 지키고, 기본값은 판정과 델타 출력까지입니다. 대신 매 실행 뒤 사람이나 상주가 델타를 붙이는 한 단계가 남습니다.
- 🔴 **[실측] 발견**: 이 자동 세션에서 `ledger-apply.sh`를 (가짜 원장에) 실행하려 하자 PM 훅이 막았습니다. 문구는 «자동 인박스 세션 — 차단: 원장 쓰기 도구(ledger-apply.sh…) 대신 Edit 도구로 `.next-tasks.json` 을 직접 고칠 것»이었습니다. 그래서 PM이 띄우는 일일 실행 세션은 transform 대신 **델타 JSON을 Edit 도구로 붙이는** 길을 씁니다. 델타 JSON을 기본 출력으로 둔 이유가 이것입니다. transform은 사람 세션용 선택지로만 남겼습니다.
- condition의 «새 오류 / 계속 / 사라짐 / 회귀» 넷에 판정 불가 셋(on_hold·no_fixed_in·ignored)을 더했습니다. 억지로 넷 중 하나에 넣으면 «사라짐»이 과대 보고되기 때문입니다.

## 주기 실행 제안 (작업 세션은 auto_start 키를 쓰지 않았습니다 — 상주 몫)

하루 1회, 서버 모드 PC(이 PC)의 레포 루트에서:

```
python3 scripts/log_triage.py --ledger .next-tasks.json --min-devices 2 --min-days 3
```

- rc 0이면 `triage-reports/triage-<날짜>-ledger-delta.json`의 `new_items[]`를 원장 tasks 끝에 Edit로 붙이고, `reopen[]`을 적용합니다. 이어서 새 항목을 분배합니다.
- rc 2(미측정)는 «오류 0건»이 아니니 보고만 합니다. rc 3(검산 불성립)은 델타를 쓰지 말고 보고합니다.
- 🔴 고리의 사람 관문: 분석·원장 등록·수정 분배까지는 자동이고, 릴리스 발행은 사람이 승인합니다(스크립트는 밖으로 아무것도 보내지 않음).
- 지금 이 PC에는 `collected-logs` 폴더가 없습니다. 스크립트도 «미측정 — collected-logs 폴더 없음»을 냈습니다 [실측 2026-11-28 00:00]. d1이 v3.0.20으로 배포되고 옵트인이 켜지기 전까지는 매일 rc 2가 정상입니다. 그래서 auto_start 날짜는 `log-access-collect-realdevice-check` 완료 뒤로 잡기를 권합니다.

## 검증

- `tests/test_log_triage.py` 10건 통과 [실측]. 가짜 3기기(alpha·bravo·charlie, 가짜 peer id) × 2버전(3.0.9·3.0.10)으로 다음을 확인합니다.
  - 판정: new·ongoing·regression·gone 4판정 + on_hold(문턱 미달 2경우, 아무도 안 쓰는 버전 1경우) + no_fixed_in + ignored
  - 검산: 망가진 줄 3개(사유별)가 섞여도 불변식 성립, `full-log-*.txt` 무시
  - 미측정: 세 상태 + CLI rc 2
  - 델타: 2회 적용 시 변화 0 · 다른 status에 같은 서명이 있으면 새 항목 0 · 낡은 델타도 중복 0
  - 개인정보: 원장 델타에 기기명·peer id 없음
  - transform: 저장소 import 없이 실행되고 2회 적용 시 1건만 추가
  - 원인 파일 추출: Windows `_MEI…\ui\tray.py` 경로 포함
- 전체 `pytest`(main 체크아웃 `.venv`의 python으로, 이 worktree에서): **688 passed, 76 skipped** [실측].
- `.next-tasks.json`은 이 브랜치에서 바뀌지 않았습니다(gitignore 대상이고 worktree에 파일도 없음) [실측 `git check-ignore`].
- 공개 저장소: 스크립트·테스트·커밋에 실제 기기명·사용자명·IP가 없습니다(테스트 기기명은 가짜) [문서 — 커밋 diff 직접 작성].

## 종료 보고

done:
- 분석 스크립트(서명별 묶음 · 버전·기기·OS·첫/마지막 관측·횟수 · 새/계속/사라짐/회귀 판정 · 검산 불변식) — `scripts/log_triage.py`, 커밋 9a2b185
- 보고서를 저장소 밖 로컬 경로(`<설정 폴더>/triage-reports/`)에 저장 — 같은 커밋
- 원장 연결(새 서명 → 항목 초안, 서명 중복 등록 0, 고침+릴리스 서명의 재관측 → 회귀 재개) — `build_delta`·`apply_delta`, 테스트 `test_delta_new_and_regression_idempotent`·`test_dedup_against_existing_signature_any_status`
- 통과 기준: 가짜 events.jsonl 3기기·2버전으로 4판정 테스트 + 중복 등록 0 + 검산 불변식 + pytest green(688 passed) — `tests/test_log_triage.py`
- 주기 실행은 키를 쓰지 않고 위 «주기 실행 제안»에 명령·문턱·rc 처리만 적음(condition 지시대로)

leftover:
- 일일 자동 실행 설정 — 원장 `log-triage-loop`에 auto_start + 날짜를 붙이는 일은 condition상 상주 몫입니다. 지금은 collected-logs가 없어 매일 rc 2가 나옵니다. · task_ref: infinite-clipboard::log-triage-daily-run · ⛳착수: 상주가 `log-access-collect-realdevice-check` 완료 뒤 `python3 scripts/log_triage.py --ledger .next-tasks.json` 일일 실행 항목을 auto_start + 날짜로 등록하고, 델타 JSON을 Edit로 붙이는 절차를 condition에 적기
- 수정 항목 닫을 때 `fixed_in`(+`signature`) 기록 규칙 — 이것이 없으면 회귀·사라짐 판정이 «no_fixed_in»으로만 나옵니다. · task_ref: infinite-clipboard::ledger-fixed-in-stamp · ⛳착수: 상주가 `err-<sig>` 항목을 completed로 닫을 때 `fixed_in: <그 수정이 실린 릴리스 버전>`을 함께 적도록 원장 운영 규칙(조율자 체크 목록)에 한 줄 추가하고, 릴리스 v3.0.20 때 해당 항목에 적용
- 사라짐 판정의 노출이 대리 지표 — 건강한 기기가 «이 버전으로 N시간 돌았다»를 보내지 않아 실제 노출보다 적게 셉니다(판정은 보류 쪽으로 기움, 거짓 «사라짐» 없음). · task_ref: infinite-clipboard::error-events-version-heartbeat · ⛳착수: core/error_events.py 수집기에 옵트인일 때만 하루 1회 «version-seen» 하트비트 줄(level 별도, 서명 없음 또는 고정)을 넣을지 설계 판단 → 넣으면 스키마 2로 올리고 log_triage.exposure 가 그 줄을 쓰게

blocked:
- 없음

produced:
- `scripts/log_triage.py` · `tests/test_log_triage.py` — 커밋 9a2b185
- 이 회신 문서 + 수신 사본 종결 스탬프 — 이 브랜치의 다음 커밋
