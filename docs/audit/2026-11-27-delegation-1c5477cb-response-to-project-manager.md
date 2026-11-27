---
round_trip: response
ball: project-manager
expects_reply: false
disposition: done-in-reply
title: "회신 — 분배 dlg-20261127-1c5477cb — 에러 로그 (a)(b)(c)(e) 정리 (코드 커밋 237f310·fc794a1)"
date: 2026-11-27
from: infinite-clipboard
from_host: linux-desktop
to: [project-manager]
msg_id: dlg-20261127-1c5477cb-response
in_reply_to:
  - dlg-20261127-1c5477cb
  - 2026-11-27-delegation-1c5477cb-request-to-infinite-clipboard.md
  - 2026-11-27-delegation-1c5477cb-request-from-project-manager.md
delegation_task_id: dlg-20261127-1c5477cb
canonical: "infinite-clipboard:docs/audit/2026-11-27-delegation-1c5477cb-response-to-project-manager.md"
task_ref: infinite-clipboard::log-crash-capture-levels
---

# 회신 — 에러 로그 (a)(b)(c)(e) 정리

작업 ID `dlg-20261127-1c5477cb` / 원장 `infinite-clipboard::log-crash-capture-levels` 의 condition 을 구현했다.
코드 커밋: **`237f310`**, **`fc794a1`** (브랜치 `dispatch/dlg-20261127-1c5477cb`, push 안 함). 되돌리려면 `git revert fc794a1 237f310`.

## 무엇을 바꿨나

| 항목 | 파일 | 변경 |
|---|---|---|
| (a) 크래시 기록 | `main.py`, `config.py` | `_install_crash_capture()` — `faulthandler.enable(file=crash.log, all_threads=True)`, 파일은 append 로 열어 모듈 전역에 붙잡아 프로세스 끝까지 유지. `sys.excepthook`·`threading.excepthook` → `logger.critical(..., exc_info=…)`(트레이스백 포함, KeyboardInterrupt·SystemExit 은 제외). `_setup_logging()` 이 부르므로 `--window` UI 서브프로세스도 같은 설정을 탄다 — `main()` 이 `--window` 분기보다 먼저 `_setup_logging()` 을 부른다 [문서 `main.py` `main()`]. POSIX 는 crash.log 도 0o600 |
| (b) 레벨 정리 | `core/network.py`, `core/file_transfer.py`, `main.py` | `is_peer_disconnect()` — ConnectionReset·BrokenPipe·ConnectionAborted, errno ECONNRESET·EPIPE·ECONNABORTED·ENOTCONN, WinError 10053·10054·10058, 수신 중 EOF(`PeerClosed`, 기존 `ConnectionError("연결 끊김"/"데이터 수신 실패")` 를 이 하위형으로). 서버 «클라이언트 처리 오류»·클라이언트 «수신 오류» 두 곳에서 끊김이면 INFO «… 연결 끊김». 크기 초과는 `TransferSizeExceeded(ValueError)` 로 던지고 `main.py` 의 «offer 메타데이터 수집»·«파일 메타데이터 수집» 두 곳에서 WARNING «… 거절». 레벨 기준 한 줄은 `main.py` 로깅 설정 위 주석 |
| (c) 반복 로그 | `core/clipboard_manager.py` | `_note_files_detected()` — «파일/폴더 클립보드 감지» INFO 는 목록이 바뀔 때 1회(Windows·macOS 두 곳; 파일 아닌 내용으로 바뀌면 기억을 지워 같은 파일 재복사는 다시 1줄). `_decode_image_once()` — 이미지 바이트 sha256 을 기억해 해석 실패한 같은 내용은 재해석·ERROR 생략, `Image.open` 뒤 `load()` 까지 불러 지연 해석 실패도 그 자리에서 잡음. 적용: macOS PNG·TIFF(PyObjC)·osascript, Linux wl-paste/xclip, Windows DIB |
| (e) 본문 로그 제거 | `core/clipboard_manager.py`, `ui/history_window.py` | `set_content` 의 DEBUG «preview:»(앞 50자) 4곳 삭제 — Windows 1·macOS 1·Linux 2 (`237f310`). 이력 창 재복사 로그 3곳의 `preview={preview!r}`(이력 미리보기 = 본문 앞 40자) 삭제 (`fc794a1`). 길이(«N자»)·종류 로그는 유지 |

**crash.log 위치** (진행중 `infinite-clipboard::win-text-receive-heap-crash` 계측용): 설정 폴더와 같은 곳 — Windows `%APPDATA%\InfiniteClipboard\crash.log`, macOS `~/Library/Application Support/InfiniteClipboard/crash.log`, Linux `~/.config/InfiniteClipboard/crash.log` [문서 `config.py` `_get_config_dir`]. 일반 로그(`infinite-clipboard.log`)와 분리한 이유: faulthandler 는 fd 에 직접 써서 RotatingFileHandler 의 파일 교체를 못 따라간다. 로테이션이 없지만 크래시 때만 쓰이므로 크기 문제는 작다 [추정].

**(e) 다른 곳 확인 — 두 가지 방법**:
1. 첫 grep(한 줄 f-string, 정해 둔 변수명만)은 `core/clipboard_manager.py` 의 4곳만 찾았다. 이 방법은 `%s` 인자·여러 줄 호출·다른 변수명을 놓친다.
2. 그래서 `main.py`·`config.py`·`core/*.py`·`ui/**/*.py` 의 logger 호출 **전부**를 AST 로 다시 훑었다. 본문을 담을 법한 이름(text·data·content·message·msg·payload·entry·item·preview·body·raw 등)이 `len()`·속성·문자열 키 접근 없이 그대로 들어간 호출을 찾았고, 4건이 나왔다 [실측]. 그중 `ui/history_window.py` 3곳은 이력 미리보기 = 본문 노출이라 고쳤다. `core/file_transfer.py` 1곳(`entry`)은 staging 폴더 경로라 본문이 아니다.
3. 경로 테스트 `test_text_copy_send_and_receive_paths_do_not_log_body` 로 확인했다. 복사 → 모니터 → 이력 → `NetworkClient.send`(실제 직렬화) → 수신 `_on_client_message` → 실제 `LinuxClipboard.set_content` → 이력, 이 전 경로를 DEBUG 로 돌려도 표식 문자열이 로그에 없다. 고치기 전 `clipboard_manager.py` 로 바꾸면 이 테스트가 실패한다(음성 대조) [실측]. 재복사 테스트도 고치기 전 `history_window.py` 에서 실패한다 [실측].
파일 경로는 본문이 아니라 범위 밖으로 뒀다.

**(b) 기준을 `core/CLAUDE.md` 가 아니라 코드 주석에 둔 이유**: `core/CLAUDE.md` 는 gitignore 대상이라 이 worktree 에 없고, CLAUDE.md 변경은 이 세션이 먼저 물어야 하는 범위다. condition 이 «core/CLAUDE.md 또는 코드 주석 한 줄» 이라 주석을 골랐다.

## 검증

- 새 테스트 `tests/test_logging_crash_levels.py`(16) · `tests/test_clipboard_log_repeat.py`(10) — 26 passed [실측]. 통과 기준과 대응:
  - excepthook → CRITICAL + 트레이스백: `test_excepthook_logs_critical_with_traceback`, 스레드는 `test_thread_exception_logs_critical_with_traceback`. 실제 네이티브 크래시는 `test_native_crash_lands_in_crash_log` — 자식 프로세스에서 `sys.stderr=None`(창 모드 번들 조건) 후 `faulthandler._sigsegv()` → crash.log 에 «Fatal Python error» (POSIX 만, Windows 는 skip)
  - 끊김 예외 → ERROR 아님: `test_client_receive_disconnect_is_not_error`(reset·EOF), 분류 parametrize 8건
  - 같은 파일 목록 10회 → INFO 1줄: `test_same_file_list_polled_10_times_logs_info_once`
  - 같은 깨진 이미지 10회 → ERROR 1줄: `test_same_broken_image_10_times_logs_error_once`(원장의 재현 입력 `b"\x89PNG\r\n\x1a\n" + b"x"*100`, `Image.open` 호출 1회까지 확인), Windows DIB `test_windows_dib_broken_10_times_logs_error_once`
  - 텍스트 복사 시 로그에 본문 없음: `test_text_copy_send_and_receive_paths_do_not_log_body`(송신·수신 전 경로), `test_text_copy_does_not_log_clipboard_text`(쓰기), `test_history_recopy_does_not_log_preview`(이력 재복사)
- 전체: `.venv/bin/python -m pytest -q` → **658 passed, 76 skipped** [실측, 이 PC Linux, `fc794a1` 기준]
- 실기(macOS·Windows) 검증은 안 했다 — macOS PyObjC 경로와 Windows WinError 분류는 단위 테스트(헬퍼 직접 호출·가짜 errno)까지만.

## 고른 것 · 포기한 것

- 같은 파일 반복 감지는 DEBUG 로 내리지 않고 **아예 안 남김** — 파일 핸들러가 DEBUG 레벨이라 DEBUG 로 내려도 5MB 순환을 똑같이 밀어낸다.
- 이미지 실패 기억은 handler 당 마지막 1건(sha256) — 깨진 이미지 두 개를 번갈아 복사하면 다시 남지만 실사용에선 드물다 [추정].
- 클라이언트의 «서버 연결 실패» 재시도 ERROR 는 손대지 않았다(condition 범위 밖) — 아래 leftover 제안.

## 종료 보고

done:
- (a) faulthandler → crash.log(append·유지) + sys/threading excepthook → CRITICAL+트레이스백, `--window` 서브프로세스 포함 — 커밋 `237f310`, 테스트 4건
- (b) 피어 정상 끊김 ERROR→INFO(서버·클라이언트 수신 2곳), 크기 초과 → WARNING(2곳), 기준 주석 — 커밋 `237f310`, 테스트 12건
- (c) «파일/폴더 클립보드 감지» 목록 변경 시 1회, 해석 실패 이미지 재해석·ERROR 반복 중단(macOS·Linux·Windows DIB) — 커밋 `237f310`, 테스트 6건
- (e) 클립보드 본문 미리보기 로그 7곳 제거(clipboard_manager 4 + history_window 3), logger 호출 전수 AST 조사, 송신·수신 전 경로 테스트 — 커밋 `237f310`·`fc794a1`, 테스트 4건
- pytest 전체 658 passed / 76 skipped
- `.next-tasks.json` 은 이 브랜치에서 안 바뀜(이 레포에서 추적 안 되는 파일이기도 함)

leftover:
- 흡수한 원장 3건 닫기 — `.next-tasks.json` 은 worktree 에서 편집 금지라 조율자 몫. `file-clipboard-log-spam`·`file-clipboard-detect-log-spam`·`clipboard-image-parse-failure-repoll` 을 completed + resolution `infinite-clipboard::log-crash-capture-levels (237f310)`. `clipboard-image-parse-failure-repoll` 의 note 대로 맥 피어에게 릴리스 시 반영 알림 필요 · task_ref: infinite-clipboard::log-crash-capture-levels
  ⛳착수: 머지 뒤 본 체크아웃에서 세 항목을 completed 로 전이하고, 다음 릴리스 노트/맥 회신에 «이미지 해석 실패 반복 중단» 포함
- Windows 힙 손상 항목에 crash.log 위치 반영 — 다음 재발 때 `%APPDATA%\InfiniteClipboard\crash.log` 를 받아 보라는 한 줄을 그 항목 condition 에 추가(창 모드 번들 실기에서 crash.log 가 실제로 생기는지 확인 포함) · task_ref: infinite-clipboard::win-text-receive-heap-crash
  ⛳착수: 이 커밋이 담긴 빌드를 a5000 에 설치한 뒤 크래시 재발 시 crash.log 수거
- 클라이언트 «서버 연결 실패» ERROR — 서버가 꺼져 있으면 재시도 루프마다 ERROR 1줄 [문서 `core/network.py` `connect` except 블록 — 재시도 간격·실측 건수 미확인]. 정상 끊김과 같은 부류의 잡음일 수 있다 · task_ref: infinite-clipboard::client-connect-retry-log-level
  ⛳착수: 이 PC 로그에서 «서버 연결 실패» 건수를 세어 잡음 비중을 확인한 뒤, 같은 사유 연속이면 첫 1회만 ERROR·나머지 DEBUG 로

blocked:
- 없음

produced:
- 커밋 `237f310` — `main.py`, `config.py`, `core/network.py`, `core/clipboard_manager.py`, `core/file_transfer.py`, `tests/test_logging_crash_levels.py`, `tests/test_clipboard_log_repeat.py`
- 커밋 `fc794a1` — `ui/history_window.py`, `tests/test_clipboard_log_repeat.py`
- 이 회신 문서 `docs/audit/2026-11-27-delegation-1c5477cb-response-to-project-manager.md`
