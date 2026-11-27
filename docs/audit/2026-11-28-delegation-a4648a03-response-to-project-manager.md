---
round_trip: response
ball: none
expects_reply: false
disposition: done-in-reply
title: "회신 — 분배 — infinite-clipboard::legacy-preview-log-purge"
date: 2026-11-28
from: infinite-clipboard
to: [project-manager]
msg_id: dlg-20261128-a4648a03-response
in_reply_to: dlg-20261128-a4648a03
delegation_task_id: dlg-20261128-a4648a03
task_ref: infinite-clipboard::legacy-preview-log-purge
---

# 회신 — 옛 클립보드 preview 로그 줄 정리 (dlg-20261128-a4648a03)

원장 항목 `infinite-clipboard::legacy-preview-log-purge` 의 condition 을 읽기만 하고 그대로 구현했다. `.next-tasks.json` 은 이 브랜치에서 바꾸지 않았다(worktree 에는 그 파일이 없다 — gitignore).

## 무엇을 했나

- `core/error_events.py`
  - `strip_legacy_preview(text)` — `<시각> - <로거> - DEBUG -   preview: ` 로 시작하는 줄과 그 **바로 뒤** 시각 없는 연속 줄(최대 50줄 — 본문 앞 50자에 줄바꿈은 최대 50개)을 지운다. 줄 자르기는 `\n` 만 쓴다(`splitlines` 는 `\r`·`\x0c` 에서도 잘라 연속 줄 수가 틀어진다).
  - `purge_legacy_preview_logs(log_file, marker=None)` — 로그 4개(`log_file_set`)를 `r+b` 로 열어 앞에서 다시 쓰고 `truncate` (활성 로그 inode 유지), 권한 600. 검산(입력 줄 = 남김 + preview + 연속, 그리고 결과 바이트 < 원본) 불성립 파일은 건드리지 않고 `check_failed` 로 돌려준다. 깨진 UTF-8 바이트는 `surrogateescape` 로 그대로 왕복. 끝나면 설정 폴더(로그와 같은 폴더)에 `legacy-preview-purged` 표식을 남겨 다음 시작부터 건너뛴다.
  - `build_log_tail`·`write_diagnostic_bundle` 이 로그 파일마다(crash.log 제외) 같은 판정으로 거른다 — 표식 전 상태·검산 불성립으로 남은 파일 방어.
- `main.py` — `_setup_logging(debug, purge_legacy=not args.window)`: 메인 서비스 프로세스만, 파일 핸들러를 붙이기 **전**에 정리. 결과는 핸들러가 붙은 뒤 줄 수·파일 이름만 INFO 1줄(실패는 WARNING). 지운 내용은 로그에 쓰지 않는다.

## 고른 것 · 포기한 것

- **OSError 가 난 파일이 있으면 표식을 남기지 않는다**(다음 시작에 재시도) — 잠김 같은 일시 오류일 수 있어서. **검산 불성립은 결정적이라 표식을 남긴다** — 매 시작 WARNING 이 오류 사건으로 쌓이는 것을 피하고, 남은 줄은 묶음 필터가 막는다. 포기: 검산 불성립 파일의 자동 재시도.
- 연속 줄 상한 50줄 — 51번째부터의 시각 없는 줄은 preview 것이 아니라고 보고 남긴다(다른 레코드를 지우는 쪽보다 남기는 쪽이 안전).
- 한계 `[문서]`: 클립보드 본문 줄 자체가 `YYYY-MM-DD HH:MM:SS` 로 시작하면 그 줄부터는 다른 레코드로 보고 남긴다(보수적).
- 실기 확인(v3.0.21 배포 뒤 상주가 다른 PC «진단 묶음»에서 preview 0 확인)은 원장 condition 에 이미 상주 몫으로 적힌 기존 단계라 leftover 로 따로 적지 않았다.
- 되돌리려면 `git revert 0fdfc19`.

## 종료 보고

done:
- 시작 때 1회 정리(로그 4개, inode 유지, 권한 600, 검산 불성립 시 불변 + WARNING, 표식으로 재실행 건너뜀, 줄 수만 INFO) — 커밋 0fdfc19 `core/error_events.py` `purge_legacy_preview_logs`, `main.py` `_setup_logging`
- 진단 묶음·전체 로그 응답 방어 필터 — 0fdfc19 `build_log_tail`·`write_diagnostic_bundle`
- 연속 줄은 preview 바로 뒤에만(다른 레코드 트레이스백 보존) — `tests/test_legacy_preview_purge.py::test_strip_removes_preview_and_continuation_keeps_other_traceback`
- 단위 테스트 8건(preview+연속 줄 제거·트레이스백 보존·연속 줄 상한·inode/권한 600/표식·표식 뒤 재실행 0·검산 불성립 시 파일 불변·preview 없는 파일 무수정·깨진 바이트 보존·묶음 2종에 preview 0) `[실측]` `8 passed`
- pytest 전체 green `[실측]` `696 passed, 76 skipped, 1 warning in 61.97s` (`.venv/bin/python -m pytest -q -x`, 2026-11-28)
- 테스트 내용은 전부 가짜 문자열(`FAKE-SECRET-CLIP`, `/home/fakeuser`) — 실제 기기명·사용자명·IP 없음
leftover:
- 없음
blocked:
- 없음
produced:
- 커밋 0fdfc19 — `core/error_events.py`, `main.py`, `tests/test_legacy_preview_purge.py`
- 커밋 bfdca4a — 이 회신 문서 + 수신 사본 종결 스탬프
