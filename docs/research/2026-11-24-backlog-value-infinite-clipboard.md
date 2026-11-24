---
title: 백로그 가치 대화 — infinite-clipboard 시범 후보
date: 2026-11-24
in_reply_to: docs/audit/2026-11-24-backlog-value-pilot-request-from-project-manager.md
task_ref: project-manager::backlog-value-conversation-round2
scope: .next-tasks.json status=pending 22건 (in_progress 1건 — Windows 힙 손상 — 제외)
ledger_changed: false
---

# 백로그 가치 대화 — infinite-clipboard (후보)

판정 근거는 2026-11-24 17:08 KST 의 jq·grep·git 실행 결과 [실측]. 항목은 원장 `task_ref`(없으면 제목)로 부른다.

## 1. 지금 할 것 상위 5

1. `stop-called-twice` — 설정 재시작 때 두 스레드가 `stop()` 을 동시에 실행 → 서버 포트가 아직 열린 채 재시작될 수 있음. `main.py:343` 에 락 없음 [실측 grep `_stop_lock` 0건].
2. `file-clipboard-log-spam` (+ 중복 `file-clipboard-detect-log-spam`) — 한 번 고치면 2건이 닫힘. `core/clipboard_manager.py:265·477` 이 아직 `logger.info` [실측].
3. `dependabot-setuptools` — 보안 경고. `uv.lock:455` setuptools 82.0.1, 요구치 ≥83 [실측]. 명령 2~3개짜리.
4. `ux-b3-pairing-auto-button` 의 A3 부분만 — 「자동」 버튼이 클라이언트 호스트칸에 자기 IP 를 넣는 사용자 체감 버그. `ui/settings_window.py:644` 에 그대로 있음 [실측]. B3 나머지(피어 선택)는 설계 판단이라 분리.
5. `mac-tests-restore-pasteboard` — 실사용 맥에서 테스트를 돌리면 사용자 클립보드가 사라지고 가짜 PNG 가 다른 PC 로 퍼짐. `tests/conftest.py` 에 Darwin/pasteboard fixture 0건 [실측].

## 2. 닫기만 하면 되는 것

- 커밋 + 코드 줄 기준으로는 **0건**. 확인한 15건(아래 3 의 중복 포함) 모두 대상 코드가 그대로 있음 [실측].
- 기준 밖 1건 참고: `claude-md-slim` — 목표(크기 ≤ 32,768B)는 달성. 루트 `CLAUDE.md` 12,241B [실측 wc -c]. 다만 `.gitignore:50 **/CLAUDE.md` 라 커밋이 없어 요청한 «커밋 + 코드 줄» 기준은 못 채움. 마스터 판단.

## 3. 버릴 후보 (양성 증거만)

- ⓑ 중복: `file-clipboard-detect-log-spam`(10-01) ≡ `file-clipboard-log-spam`(09-29) — 같은 로그 문구, 같은 파일 `core/clipboard_manager.py` [실측]. 앞의 것을 버리고 뒤의 것으로 합침.
- 그 밖에 ⓐ·ⓒ·ⓓ 양성 증거 없음. 오래됐을 뿐인 항목은 보류로 둠.

## 4. 보류 질문 (사람만 답함)

1. **Apple Developer Program($99/년)은 여전히 보류인가?** → `Apple Developer Program 등록`, `pending-receive-tray-signal`(맥 알림 실패의 원인이 ad-hoc 서명[문서: 원장 condition]) 두 건이 갈림. 보류면 후자는 «트레이 대기 배지» 쪽 설계로만 진행.
2. **Windows 실기 시험 호스트를 어디로 할까?** (a5000 계속 / pm-vm-win11 / 사람 직접) → `update-win-allusers-interactive-verify`, `restart-path-window-tooltip` 의 툴팁 부분, `tray-menu-closes-on-update` 의 Windows 확인이 한꺼번에 풀림.
3. **AUR 제출을 계속할 것인가?** (계정 가입·SSH 키는 사람만 가능) → `AUR 계정 가입` 1건. 안 하면 ⓓ 로 버릴 수 있게 됨.
4. **«공유할 기기 선택»의 범위** (받기만 거를지 / 보내기도 / 텍스트 포함) → `per-client-peer-filter`. 보내기를 넣으면 프로토콜 변경.
5. `Mac→Windows 대용량 간헐 실패` — 사람 질문은 아니고 확인 길만 있음: Windows 실사용 로그에서 `[diag-largefile]` 태그를 grep(v3.0.12, 07-13 이후 계측). 히트 0이면 «재발 없음»으로 닫기 후보.

## 5. 묶음 (한 세션)

- **로그 소음**: `file-clipboard-log-spam`(+중복) · `stale-receivable-clear-log` · `clipboard-image-parse-failure-repoll` — 전부 `core/clipboard_manager.py`·`main.py` 의 로그 지점, 테스트는 caplog.
- **업데이터**: `update-helper-workdir-left` · `mac-helper-relaunch-check-false-positive` — 둘 다 `core/updater.py` helper 스크립트.
- **수명주기**: `stop-called-twice` · `restart-path-window-tooltip` 의 전송창 중복 부분.
- **테스트·의존성 위생**: `dependabot-setuptools` · `mac-tests-restore-pasteboard` · `코드 리뷰 저심각도 3건`(CLAUDE.md 표 대신 이제 `docs/reference/file-map.md` 에 3개 키 미반영 — 0건 [실측]).
- **설정 창 UX**: `ux-b3-pairing-auto-button` · `ux-b4-settings-restructure` (+ 질문 4 답이 나오면 `per-client-peer-filter`) — `ui/settings_window.py` 한 파일.
- `ux-a2-i18n-leaks` 는 혼자 한 세션 (`main.py` FAIL_REASON_INFO·`_make_preview:885`·`components.py`).

## 이 시범에서 본 것 (주간 자동 패스 판단용)

- 항목당 grep 1개로 «출하됨/아님»이 갈렸다 — `condition` 에 파일·줄이 적힌 항목(09-29 이후 등록분)은 자동 판정이 쉬움. `task_ref`·`added_at` 이 없는 옛 4건은 condition 서사만 있어 사람 판단이 필요했음.
- 중복은 제목 유사도로 잡혔다(같은 로그 문구). 자동 패스에서 «같은 파일 + 같은 문자열 리터럴»을 중복 신호로 쓸 만함 [추정].
