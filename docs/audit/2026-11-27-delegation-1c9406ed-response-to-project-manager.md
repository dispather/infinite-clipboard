---
round_trip: response
ball: project-manager
expects_reply: false
disposition: done-in-reply
title: "회신 — 분배 dlg-20261127-1c9406ed — Linux «파일 받기» 알림 쌓임 해소 (코드 커밋 f7eb016)"
date: 2026-11-27
from: infinite-clipboard
from_host: linux-desktop
to: [project-manager]
msg_id: dlg-20261127-1c9406ed-response
in_reply_to:
  - dlg-20261127-1c9406ed
  - 2026-11-27-delegation-1c9406ed-request-to-infinite-clipboard.md
  - 2026-11-27-delegation-1c9406ed-request-from-project-manager.md
delegation_task_id: dlg-20261127-1c9406ed
canonical: "infinite-clipboard:docs/audit/2026-11-27-delegation-1c9406ed-response-to-project-manager.md"
task_ref: infinite-clipboard::linux-notify-pileup
---

# 회신 — Linux «파일 받기» 알림 쌓임 해소

작업 ID `dlg-20261127-1c9406ed` / 원장 `infinite-clipboard::linux-notify-pileup` 의 condition 을 구현했다.
코드 커밋: **`f7eb016`** (브랜치 `dispatch/dlg-20261127-1c9406ed`, push 안 함). 되돌리려면 `git revert f7eb016`.

## 무엇을 바꿨나

| 파일 | 변경 |
|---|---|
| `core/notify_linux.py` | 발신자(`slot`)별 마지막 알림 id 를 기억해 같은 발신자의 새 offer 는 `replaces_id` 로 교체(발신자마다 슬롯 따로 — 전역 단일 슬롯 아님). `withdraw(offer_id)` → `CloseNotification`. `NotificationClosed` 시그널 구독 → 닫힌 알림의 콜백을 `_pending` 에서 제거. 교체 Notify 가 거부되면 옛(대체된 offer 를 가리키는) 알림을 닫음. `stop()` 은 두 구독 해제 + 추적 중인 알림 전부 닫기 |
| `core/actionable_notify.py` | 인터페이스에 `slot` 키워드 인자와 `withdraw()`(기본 no-op) 추가 |
| `core/notify_mac.py` · `core/notify_win.py` | `slot` 인자를 받기만 함. `withdraw` 는 기본 no-op 상속(실기 검증 없이 OS 알림 API 를 바꾸지 않음) |
| `main.py` | `_add_receivable` 가 `slot=source_peer` 전달. `_clear_receivable`(알림 받기·전송창 받기·terminal 실패의 공통 종착점)에서 알림 회수. lazy-paste 경로에서 같은 발신자 옛 받기 항목이 비워질 때 그 알림 회수. 일반(plyer/tray) 폴백 알림은 그 발신자의 받을 항목이 남아 있는 동안 1건만. 같은 발신자 offer 가 기존 받기 항목을 대체할 때 INFO 1줄(`[offer] 이전 받기 항목 대체: old=… new=… peer=…`, 파일명 없음) |
| `tests/test_notify_pileup.py` (신규) | 가짜 D-Bus 알림 데몬(명세대로 replaces_id 교체·Close 처리)에 실제 `LinuxActionableNotifier` 를 붙인 11건 — 실 세션 버스 없이도 돈다 |
| `tests/test_actionable_notify_orchestration.py` | 스텁 시그니처에 `slot=None` 추가 |

**고른 것·버린 것**: 교체는 «withdraw 후 새 Notify» 대신 `replaces_id` 를 택했다 — D-Bus 호출이 1회로 줄고, KDE 이력 항목까지 교체될 여지가 있다. `NotificationClosed` 때 `_by_offer`·`_slots` 매핑은 일부러 남겼다 — 다음 offer 가 같은 id 를 replaces_id 로 쓰고, 나중 `withdraw` 가 이력에서 지울 수 있게. mac/win 의 실제 회수 구현은 하지 않았다(원장이 «최소 no-op» 지정, 실기 없음).

## 종료 보고

done:
- 원장 통과 기준 «같은 발신자 3회 offer → 열린 알림 1개» — `test_same_sender_three_offers_leave_one_open_notification`(백엔드), `test_app_same_sender_three_offers_then_receive_leaves_zero`(main 경유) [실측 pytest]
- «받기 후 0개» — 같은 테스트의 `_clear_receivable` 단언. 알림 «받기»와 전송창 «받기»는 둘 다 `_receive_offer` → `_receive_offer_impl` 끝의 `_clear_receivable` 로 모인다 [문서 main.py `_receive_offer_impl`]
- «다른 발신자 2명 → 2개» — `test_two_senders_keep_two_notifications`, `test_app_two_senders_two_notifications` [실측]
- 원장 할 일 (1) 수명 묶기·발신자별 슬롯 / (2) NotificationClosed 구독 / (3) 인터페이스 close(=`withdraw`) + mac·win no-op / (4) 폴백 같은 발신자 연속 1건(`test_app_fallback_notify_once_per_sender_while_unreceived`) — 커밋 `f7eb016`
- pytest 전체 green — `uv run --frozen pytest tests/ -q` → 632 passed, 76 skipped [실측 2026-11-27, 이 worktree]
- 원장 문구와 다른 점: «만료 시 CloseNotification» — 런타임에는 받기 항목을 TTL 로 지우는 경로가 없다(`offer_ttl_hours` 는 fetch 거부 판정과 재시작 복원에서만 쓰임 [실측 grep main.py]). 그래서 알림의 만료는 데몬의 15초 만료(`_EXPIRE_MS`) + `NotificationClosed` 구독이 처리한다
- 함께 처리: `infinite-clipboard::stale-receivable-clear-log` — 통과 관측 «같은 발신자 offer 2연속 시 «이전 받기 항목 대체» 로그 1줄» 충족(`test_app_replace_logs_one_line`). 단 caplog 단언 위치는 그 항목이 지정한 `tests/test_receivable_offer_multi_source.py` 가 아니라 `tests/test_notify_pileup.py`. **조율자: 이 항목을 `f7eb016` 근거로 닫아 주세요**(worktree 에선 원장을 안 고침)
- KDE 실기 확인: **미실측 — 상주 세션 검사**(원장 지정). 특히 KDE 의 `ExcessNotificationGeneration` 한도가 replaces_id 교체 호출도 세는지는 [추정 — 미검증]. 거부되더라도 일반 폴백 알림은 이제 발신자당 1건으로 묶인다

leftover:
- (5) KDE `transient` hint 가 이력 쌓임을 줄이는지 — 실기 KDE 화면이 필요해 확인 못 함. hint 는 넣지 않았다(효과 미확인 상태로 동작을 바꾸지 않음) · task_ref: infinite-clipboard::kde-notify-transient-hint-check
  ⛳착수: 이 PC(KDE)에서 받기 모드 offer 를 같은 발신자로 3회 받은 뒤 알림 이력 수를 보고, `hints={"transient": True}` 를 넣은 빌드와 비교한다
- (6) 0.1~0.2초 간격 offer 쌍(11-27 07:50:25·07:53:08)이 같은 발신자인지 — 판정 불가. 기존 `[offer] 수신(받기 모드)` 로그 줄에 발신자가 없다 [실측 로그] · task_ref: infinite-clipboard::duplicate-offer-pair-check
  ⛳착수: `f7eb016` 배포 뒤 로그에서 쌍 사이에 «이전 받기 항목 대체» 줄이 끼는지 본다 — 끼면 같은 발신자이므로 발신측 중복 offer 를 별건으로 조사, 안 끼면 다른 발신자(종결)
- 범위 밖 발견: `core/clipboard_manager.py` 의 DEBUG `preview:` 4곳이 클립보드 앞 50자를 파일 로그에 남긴다 — 이 PC 로그에서 자격증명 형태의 문자열이 남은 것을 관측했다(내용은 옮기지 않음). 원장 grep(«preview»·«클립보드 내용 로그») 에 해당 항목 없음 · task_ref: infinite-clipboard::clipboard-preview-debug-log
  ⛳착수: `core/clipboard_manager.py` 의 `preview:` DEBUG 4곳을 길이·종류만 남기도록 바꾸고(내용 제거), 기존 로그 파일의 정리 여부는 사용자에게 묻는다

blocked:
- 없음

produced:
- 코드 커밋 `f7eb016` — `core/notify_linux.py`, `core/actionable_notify.py`, `core/notify_mac.py`, `core/notify_win.py`, `main.py`, `tests/test_notify_pileup.py`, `tests/test_actionable_notify_orchestration.py`
- 이 회신 `docs/audit/2026-11-27-delegation-1c9406ed-response-to-project-manager.md` + 수신 사본 종결 스탬프(같은 문서 커밋)
