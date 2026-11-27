---
round_trip: response
ball: project-manager
expects_reply: false
disposition: done-in-reply
title: "회신 — 분배 dlg-20261127-6f0f247a — 에러 로그 (d1) 오류 사건 자동 수집(옵트인)·트레이 로그 메뉴 (코드 커밋 511de8d)"
date: 2026-11-27
from: infinite-clipboard
from_host: linux-desktop
to: [project-manager]
msg_id: dlg-20261127-6f0f247a-response
in_reply_to:
  - dlg-20261127-6f0f247a
  - 2026-11-27-delegation-6f0f247a-request-to-infinite-clipboard.md
  - 2026-11-27-delegation-6f0f247a-request-from-project-manager.md
delegation_task_id: dlg-20261127-6f0f247a
canonical: "infinite-clipboard:docs/audit/2026-11-27-delegation-6f0f247a-response-to-project-manager.md"
task_ref: infinite-clipboard::log-access-collect
---

# 회신 — 에러 로그 (d1) 오류 사건 자동 수집 + 트레이 로그 메뉴

작업 ID `dlg-20261127-6f0f247a`. 원장 `infinite-clipboard::log-access-collect`의 condition을 구현했습니다.
- 코드 커밋: **`511de8d`** (브랜치 `dispatch/dlg-20261127-6f0f247a`, push 안 함).
- 되돌리려면 `git revert 511de8d`를 실행합니다.
- 선행 작업 `log-crash-capture-levels`(237f310·fc794a1)는 이 브랜치 아래에 이미 병합돼 있습니다 [실측 `git log`].

## 설계 (사건 스키마는 코드에 고정)

🔴 condition은 설계 문서를 `docs/plans/`에 두라고 했습니다. 그런데 이 저장소는 `docs/plans/`를 ignore합니다.
- 근거: `.gitignore:53 /docs/plans/` [실측 `git check-ignore -v`]. 공개 저장소에 계획 문서를 올리지 않는다는 방침입니다.
- 그대로 두면 worktree가 지워질 때 문서도 같이 사라집니다.
- 그래서 **events.jsonl 스키마(log-triage-loop의 입력)는 `core/error_events.py` 모듈 docstring에 고정**했습니다. 코드와 함께 커밋되는 정본입니다.
- 설계 요약은 아래에 적습니다.

### 데이터 흐름
```
logging 레코드(WARNING+)
  → ErrorEventCollector.emit — 메모리에서 서명별 합산만 (디스크·네트워크 I/O 없음)
  → 10분 주기 스레드 (+ 클라이언트 재접속 직후 kick)
  → outbox: <설정 폴더>/error-events-outbox.jsonl (오프라인 적재, 0o600)
  → take_batch (회당 256KB 상한)
  → 클라이언트: MSG_ERROR_EVENTS 를 client.send_raw (bool) 로 전송
        → 성공하면 commit / 실패하면 restore (outbox 로 되돌림)
  → [서버] validate_events
        → CollectedLogStore: <설정 폴더>/collected-logs/<기기>-<peer8>/events.jsonl
     서버 PC 자기 사건은 네트워크를 거치지 않고 같은 store 에 바로 저장
crash.log: 마지막으로 읽은 위치(error-events-state.json — settings.json 과 분리, mtime 재시작 회피)
           이후의 새 내용을 CRASH 사건 1개로. 파일이 줄었으면 0 부터 다시 읽음
```

### events.jsonl 스키마 (schema=1) — 요약 (정본은 모듈 docstring)
- **필드:** `schema, sig(16 hex), level(WARNING|ERROR|CRITICAL|CRASH), logger, head(≤160자, 숫자·hex id·경로·IP·URL·따옴표 문자열 제거), count, first_ts, last_ts, version, os, os_release, traceback(≤8KB|null)`
- **서버가 덧붙이는 필드:** `device, peer, received_at`
- 🔴 **`count`는 «그 줄이 덮는 구간»의 횟수이고 누적값이 아닙니다.** 같은 `sig`가 전송 회차마다 한 줄씩 여러 번 나올 수 있습니다. 총 횟수는 `sig`별 `count`의 합입니다. log-triage-loop는 이 정의로 집계해야 합니다.

## 무엇을 골랐고 왜 · 무엇을 포기했나

| 선택 | 이유 | 포기한 것 |
|---|---|---|
| **옵트인을 양쪽에 둠** — 보내는 PC와 서버 PC 모두 `collect_error_logs=True`여야 모입니다. 전체 로그 요청을 받은 PC도 꺼져 있으면 `declined`만 돌려줍니다 | 공개 배포판에서 동의 없이 정보가 움직이지 않게(condition 🔴 옵트인). 서버 PC 주인이 원치 않는 파일이 쌓이지 않게 | 한쪽만 켜면 동작하지 않습니다. 설정 안내가 필요합니다 → leftover 2 |
| **파일 전송 경로를 재사용하지 않고 전용 JSON 메시지 3종 추가** | `FileTransferManager`의 단일 active transfer 불변식(규칙 13) 때문에, 로그 전송이 사용자의 진행 중 파일 전송을 취소시키게 됩니다 | 로그 묶음은 이어받기가 없습니다. 상한 4MB(압축 전)로 단발 전송합니다 |
| **새 메시지는 서버 종착이며 중계하지 않음** (`_on_server_message`에 명시 분기, 규칙 7) | `MSG_LOG_REQUEST`를 클라이언트가 보낼 수 있으면, 인증된 아무 PC나 다른 PC 로그를 끌어갈 수 있습니다. 그래서 서버만 보내고, 클라이언트가 보낸 요청은 무시합니다 | 클라이언트 PC에서는 «전체 로그 요청» 메뉴가 없습니다(서버 모드만) |
| **저장할 기기 폴더는 핸드셰이크 identity로 정함** (`server.clients[sock]`), payload의 자기신고는 쓰지 않음. 이름에서 경로 문자·제어문자 제거 | 경로 조작(`../`)과 사칭 차단 | — |
| **auth_key는 값으로 치환.** 홈 경로는 `~`, 따옴표 안 문자열은 `<s>`로 바꿈 | 키는 URL-safe 22자라 hex 패턴으로는 안 잡힙니다 [문서 `config._generate_auth_key`]. 클립보드 본문은 선행 작업 (e)로 로그에서 이미 빠졌고, 이것은 2차 방어입니다 | 따옴표 없이 로그된 임의 문자열은 head에 남을 수 있습니다. 1차 방어는 «로그에 쓰지 않기»입니다 |
| **진단 묶음 설정 요약은 허용 목록**(`DIAG_CONFIG_ALLOWLIST`) | 나중에 추가되는 비밀 필드가 자동으로 새지 않게. auth_key·peer_id·device_name·IP는 목록에 없습니다 | — |
| **로그 600 처리는 `_open()`을 override하고 회전 뒤 백업까지 chmod.** 생성 시 기존 `.1~.3`도 한 번 고침 | 처음 여는 파일과 회전 때 새로 만드는 파일을 모두 잡기 위해서 | Windows는 해당 없음(사용자 ACL) |
| **수집기는 `start()`(메인 서비스 프로세스)에서만 붙임** | `--window` UI 서브프로세스까지 붙으면 프로세스마다 같은 outbox를 써서 경합합니다 | — |

**옛 버전 피어 처리:** 모르는 type은 `_on_client_message`·`_on_server_message`의 elif 체인에 else가 없어서 조용히 무시됩니다 [문서 `main.py`]. 요청 측은 30초 뒤 «응답 없음 N대(옛 버전이거나 끊김)»로 알립니다.

## 종료 보고

done:
- 자동 수집(옵트인, 기본 꺼짐): `core/error_events.py`의 `ErrorEventCollector`·`ErrorCollectionService`·`CollectedLogStore`, `config.collect_error_logs`, 설정 창 스위치 — 커밋 511de8d
- 서명 합산·폭주 방지·상한: 메모리 합산 1000 sig, outbox 500 sig, 회당 256KB, 기기 파일 2MB 회전(백업 1개), 전체 로그 묶음 해제 상한 8MB(압축 폭탄 방지) — 테스트 `test_same_signature_1000_times_is_one_event_with_count`·`test_batch_respects_byte_cap_and_outbox_cap`·`test_pending_cap_drops_new_signatures`·`test_log_bundle_decompression_bomb_rejected`
- 옵트인 꺼짐이면 전송 0: `test_optin_off_sends_nothing`(요청이 와도 `declined`만 감), `test_server_with_optin_off_drops_events`
- 사건에 비밀·클립보드 본문·홈 경로 없음: `test_event_has_no_secret_clipboard_text_or_home_path`, `test_optin_on_client_answers_log_request_redacted`, `test_diagnostic_bundle_has_logs_and_no_auth_key`
- 오프라인 적재 후 재전송: `test_offline_accumulates_then_resends`, `test_send_exception_restores`
- 옛 피어 타임아웃: `test_full_log_request_old_peer_times_out`(응답한 PC 저장, 침묵한 PC는 no_answer, 0.5초 주입)
- 중계 안 함·identity 기준 저장: `test_server_stores_by_handshake_identity_and_never_relays`
- (1b) 회전 로그 600: `make_secure_rotating_handler` — `test_rotating_log_files_are_0600`(umask 022에서 base·.1·.2 모두 0o600)
- crash.log 사건화: `test_crash_log_new_content_becomes_one_event`
- (2) 트레이 «로그 폴더 열기»·«진단 묶음 내보내기»(다운로드 폴더 zip)·«연결된 PC 전체 로그 요청»(서버 모드만): `ui/tray.py`, `main.py` `export_diagnostic_bundle`·`request_peer_logs`. i18n 한·영: `ui/i18n.py` 9개 문자열
- 검증: `uv run --frozen pytest -q` → **678 passed, 76 skipped** [실측 2026-11-27, 이 worktree]. 새 테스트 20건 포함. import 경로가 worktree `core/`임을 확인함
- `.next-tasks.json`은 브랜치에서 변경 없음(이 worktree에는 그 파일이 없고 ignore 대상)

leftover:
- 실기 확인 «미실측» — 이 PC↔다른 PC(서버·클라이언트, Windows 포함)에서 아래를 확인해야 합니다. 단위 테스트는 가짜 소켓 기준이라 실제 트레이 메뉴 동작, 폴더 열기, 실제 연결로 보낸 사건이 `collected-logs/`에 쌓이는지는 보지 못했습니다.
  - 양쪽 옵트인을 켜고 재시작
  - 일부러 WARNING 발생(예: 잘못된 서버 주소로 접속 실패)
  - 10분 안에 서버 `collected-logs/<기기>-<peer8>/events.jsonl`이 생기는지
  - 트레이 3개 메뉴
  - 옛 버전 PC를 섞었을 때 «응답 없음» 알림
  - task_ref: infinite-clipboard::log-access-collect-realdevice-check
  - ⛳착수: 본 체크아웃에 511de8d가 병합된 뒤 상주 세션이 두 PC에서 위 5단계를 실행. 결과는 이 원장 항목에 기록
- 옵트인 안내 문서 — README(영·한)에 «오류 기록 모으기» 설정의 의미를 적어야 합니다: 양쪽 다 켜야 함, 앱 연결 밖으로 안 나감, 저장 위치. 공개 배포판 사용자가 무엇이 움직이는지 알 수 있게 하려는 것입니다. 범위 밖이라 손대지 않았습니다.
  - task_ref: infinite-clipboard::log-collect-readme-privacy-note
  - ⛳착수: README.md·README.ko.md 기능 절에 3줄 추가. 근거는 `core/error_events.py` docstring과 `config.collect_error_logs` 주석

blocked:
- 없음

produced:
- 커밋 `511de8d` — `core/error_events.py`(신규), `core/protocol.py`, `main.py`, `config.py`, `ui/tray.py`, `ui/settings_window.py`, `ui/i18n.py`, `tests/test_error_events.py`(신규)
- 이 회신 문서 + 수신 사본 종결 스탬프 (별도 docs 커밋)
