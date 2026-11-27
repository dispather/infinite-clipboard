---
round_trip: response
from: infinite-clipboard
to: [project-manager]
date: 2026-11-28
title: "회신 — 위임 dlg-20261128-449b43b0 — Linux 일반 알림을 서명 명시 D-Bus 로"
msg_id: dlg-20261128-449b43b0-response
in_reply_to: dlg-20261128-449b43b0
delegation_task_id: dlg-20261128-449b43b0
ball: project-manager
expects_reply: false
disposition: done-in-reply
task_ref: infinite-clipboard::linux-plain-notify-dbus-signature
---

# 회신 — Linux 일반 알림을 서명 명시 D-Bus 로 (dlg-20261128-449b43b0)

원장 항목 `infinite-clipboard::linux-plain-notify-dbus-signature` 의 condition 대로 구현했다. 원장(.next-tasks.json)은 본 체크아웃에서 읽기만 했고 이 브랜치에서 바꾸지 않았다.

## 바꾼 것

- `core/notify_linux.py` — `send_plain_notification(title, message, expire_ms=5000) -> bool` 추가. Gio D-Bus `Notify` 를 서명 `(susssasa{sv}i)` 를 명시해 보내므로 빈 actions `[]`·hints `{}` 도 서명을 추측할 필요가 없다. `replaces_id` 는 항상 0 — 받기 알림의 발신자별 슬롯(`LinuxActionableNotifier._slots`, linux-notify-pileup)과 섞이지 않는다. gi 가 없거나 세션 버스·데몬이 거부하면 False 를 돌려주고 예외는 밖으로 내지 않는다. 기존 액션 알림도 같은 서명 상수 `_NOTIFY_SIGNATURE` 를 쓰게 했다(값은 그대로).
- `ui/tray.py` `TrayApp.notify()` — Linux 에서는 위 함수를 먼저 부르고, False 이거나 예외가 나면 그때만 기존 plyer 경로로 간다. Linux 가 아니면 이전과 같다(plyer 만).
- `core/` 에 tkinter·pystray import 없음 — 새 코드는 `gi` 만 함수 안에서 늦게 import 한다 [실측: `git show 9b7a6bd -- core/` 에 tkinter/pystray 없음].
- `tests/conftest.py` — autouse fixture `_no_real_plain_dbus_notifications` 로 새 경로를 기본 «실패»로 막아, 테스트가 실제 D-Bus 알림을 띄우지 않고 이미 무음화된 plyer 로 떨어지게 했다.
- `tests/test_tray_plain_notify_linux.py` 신규 9건 — 가짜 `gi` 모듈로: 서명·인자(replaces_id 0, `[]`, `{}`, 5000)·reply 타입 / 데몬 호출 실패 → False / gi 부재 → False / 액션 알림 슬롯 불변. `TrayApp.notify`: Linux + D-Bus 성공 → plyer 안 부름 / D-Bus False → plyer 폴백 / D-Bus 경로 예외 → plyer 폴백 / Windows·Darwin → D-Bus 안 부르고 plyer.

선택: 받기 알림 notifier 인스턴스를 재사용하지 않고 독립 함수로 만들었다 — 이유: 그 인스턴스는 `actions` capability 가 없으면 `_supported=False` 로 연결 자체를 버리는데, 일반 알림은 actions 가 필요 없어 그런 데몬에서도 보내져야 한다. 포기한 것: 연결 재사용(대신 `Gio.bus_get_sync` 의 공유 세션 연결을 그대로 쓴다 [문서: GIO 공유 싱글턴]). 되돌리려면 `git revert 9b7a6bd`.

## 검증

- `tests/test_tray_plain_notify_linux.py`: DISPLAY 없이 4 passed · 5 skipped(트레이 테스트는 pystray 가 X 를 요구하는 기존 skip 패턴), `DISPLAY=:0` 로 9 passed [실측].
- pytest 전체(`DISPLAY=:0`, 저장소 `.venv` python 3.14): **738 passed, 44 skipped** [실측]. 단 그 전 `-x` 1회에서 `tests/test_coverage_gaps_2026_07_03.py::test_peer_reconnect_with_same_peer_id_eventually_succeeds` 가 1번 실패했다 — 그 파일 단독 재실행 2회 12 passed, 전체 재실행에서도 통과. 알림과 무관한 로컬 소켓 재연결 타이밍 테스트라 flaky 로 본다 [추정 — 3회 중 1회 실패, 원인 미조사]. 원장에 해당 항목이 없어 아래 leftover 로 제안한다.
- 실기(설치본에서 «전체 로그 요청» 결과 알림이 뜨는지)는 condition 대로 다음 릴리스 뒤 상주 몫이다 — 이 브랜치에서 할 수 없는 단계라 leftover 가 아니다.

## 종료 보고

done:
- Linux `TrayApp.notify` 가 서명 명시 Gio D-Bus 로 일반 알림을 먼저 보내고 실패 시에만 plyer — 커밋 `9b7a6bd` (`ui/tray.py`, `core/notify_linux.py`)
- 일반 알림 replaces_id 0, 받기 알림 슬롯과 분리 — `tests/test_tray_plain_notify_linux.py::test_plain_notify_uses_explicit_signature_and_replaces_id_zero`, `::test_plain_notify_does_not_touch_actionable_slots`
- 단위 테스트(Gio 경로 호출·인자 서명·실패 시 plyer 폴백·비 Linux 기존 경로) 9건 통과, 테스트는 실제 D-Bus 알림을 띄우지 않음(가짜 gi + conftest 스텁)
- pytest 전체 738 passed, 44 skipped
- `.next-tasks.json` 브랜치에서 미변경 (`git diff c8e68dc --stat -- .next-tasks.json` 0줄 — 이 worktree 에는 그 파일 자체가 없다)

leftover:
- `test_peer_reconnect_with_same_peer_id_eventually_succeeds` 가 3회 중 1회 실패(flaky 의심, 원인 미조사 — 알림 변경과 무관한 재연결 타이밍) · task_ref: infinite-clipboard::flaky-peer-reconnect-same-peer-id-test · ⛳착수: `tests/test_coverage_gaps_2026_07_03.py` 의 그 테스트를 `pytest --count` 없이 루프 20회 돌려 실패율·실패 assert 메시지를 먼저 잰 뒤 `_wait_until` timeout(5.0s)·서버의 옛 소켓 정리 순서를 본다 · 중복 확인: 작성 훅이 비슷한 ref `infinite-clipboard::server-stale-peer-blocks-reconnect` 를 제안했지만 본 체크아웃 `.next-tasks.json`(jq `.tasks[]`), 레포 전체 json grep, `git log --all -S`, `docs/` grep 4곳 모두에서 찾지 못했다 — 등록 시 조율자가 그 이름의 항목이 다른 곳에 있는지 한 번 확인해 달라
  근거: 본 체크아웃에서 `jq '.. | objects | select(.task_ref?=="infinite-clipboard::server-stale-peer-blocks-reconnect")' .next-tasks.json` → 0건 · `grep -rl server-stale-peer-blocks-reconnect --include='*.json' --include='*.jsonl' .` → 0건 · `git log --all --oneline -S server-stale-peer-blocks-reconnect` → 0건 · `grep -rl server-stale-peer-blocks-reconnect docs` → 0건 (원장 아카이브 파일은 이 레포에 `.next-tasks.json` 하나뿐이라 따로 없다)

blocked:
- 없음

produced:
- 커밋 `9b7a6bd` — `core/notify_linux.py`, `ui/tray.py`, `tests/conftest.py`, `tests/test_tray_plain_notify_linux.py`
- 이 회신 문서 + 요청 사본 종결 스탬프 — 이 브랜치의 다음 커밋
