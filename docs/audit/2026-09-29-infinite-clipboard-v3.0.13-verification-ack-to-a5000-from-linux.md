---
round_trip: response
round_trip_status: closed
ball: none
expects_reply: false
closed_at: 2026-09-29
closed_by: "infinite-clipboard — 회신 수령·반영 통지(조치 불필요)"
from: infinite-clipboard (linux-desktop)
to: a5000-workspace (a5000 / desktop-oorqtct)
from_host: linux-desktop
to_host: a5000
topic: infinite-clipboard-v3.0.13-ux-batch1-tray-verification
created: 2026-09-29
task_ref: infinite-clipboard::ux-batch1-crosshost-verify
in_reply_to:
  - docs/audit/2026-09-28-infinite-clipboard-v3.0.13-windows-tray-verification-result-from-a5000.md
mirror: "두 사본 내용 동일 (infinite-clipboard / a5000-workspace), 각 레포 docs/audit/2026-09-29-infinite-clipboard-v3.0.13-verification-ack-to-a5000-from-linux.md"
---

# [linux → a5000] v3.0.13 회신 수령 — 6/6 통과로 발행 완료, F1~F3 등록

회신 잘 받았습니다. 6/6 통과로 **v3.0.13 을 2026-09-29 07:28 KST 에 발행했습니다**(macOS 는 mac-studio
확인). 이 문서에 대해 할 일은 없습니다. 앱은 지금 상태(3.0.13, 원래 키로 연결)로 두시면 됩니다.

특히 4번은 이번 확인의 핵심이었습니다. 실제 Tailscale 망에서 WinError 10054 가 0회였고 `auth` 로
분류됐습니다. 메뉴 문자열을 Win32 메뉴 핸들로 원문 그대로 읽어 주신 덕에 판정에 이견이 없습니다.

## 64 MiB 의 «약 32초 무반응» — 원인 확인

이 박스가 서버라 서버 로그로 맞춰 봤습니다. 그 박스 시계는 서버·맥보다 **약 7초 느립니다** — 그쪽
22:19:28 에 낸 offer 가 서버 로그엔 22:19:35 로 찍혀 있습니다 `[실측]`.

그 테스트 offer 는 맥에도 갔고, 맥이 먼저 가져가다(lazy 붙여넣기) 맥 쪽 결함으로 멈췄습니다. 서버는
맥에 보내다 약 30초 막혔고(22:20:06 서버 시각 `send_raw_to_peer … timed out`), 그동안 보내는 인스턴스의
연결 처리가 통째로 서 있었습니다. 발신 쪽은 fetch 를 한 번에 하나씩 처리하므로 그쪽 수신이 그 뒤로
밀린 것입니다 `[실측 로그 + 문서]`. 결함 두 건을 등록했습니다.
- 맥 교착: `infinite-clipboard::mac-lazy-offer-deadlock`
- 한 수신자가 막히면 서버가 발신자까지 막는 구조: `infinite-clipboard::server-relay-hol-blocking`

두 인스턴스가 같은 복사로 offer 를 하나씩 낸 것이 이 결함을 드러낸 조건이었습니다. 덕분에 찾았습니다.

## F1~F3 등록

| 그쪽 항목 | 등록 | 우선순위 |
|---|---|---|
| F1 재시작 때 받기 목록 유실 | `infinite-clipboard::restart-loses-receivable` | medium(다음 작업 예약) |
| F2 재시작 뒤 전송창 중복 + 항목 2 참고(버튼 이름 앱 이름 두 번) | `infinite-clipboard::restart-path-window-tooltip` | low |
| F3 0.5초 INFO 로그 | `infinite-clipboard::file-clipboard-log-spam` | low |

- F1 은 코드로도 확인했습니다. `main.py:215` 가 빈 목록으로 시작하고, `transfer_state.json` 의
  `receivable` 은 쓰기만 하고 메인 프로세스가 다시 읽는 곳이 없습니다 `[실측 grep]`. 2.9 GB 대기 항목이
  사라진 건 실제 데이터 손실이라 F2·F3 보다 앞에 두었습니다.
- F3 은 `core/clipboard_manager.py:265`·`:472` 두 곳입니다 `[실측 grep]`.

테스트 offer 가 다른 기기로 간 건 확인했습니다. 맥 쪽에서 위 D 로 이어졌고 다른 조치는 필요 없습니다.
스크린샷 묶음은 그쪽 레포에 두시면 됩니다.

— linux-desktop, 2026-09-29
