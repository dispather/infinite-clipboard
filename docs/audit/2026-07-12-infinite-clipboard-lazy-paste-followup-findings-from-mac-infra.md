---
round_trip: request
round_trip_status: open
from_host: mac (mac-studio)
to_host: linux (desireallsx)
topic: infinite-clipboard-lazy-paste-followup
created: 2026-07-12
in_reply_to: docs/audit/2026-07-11-infinite-clipboard-lazy-mac-diag38-ack-from-linux.md
---

# [mac-studio → linux] v3.0.9 확인 중 추가로 나온 것 3건

함정 #38 수정(v3.0.9) 실사용 확인 중 별개의 이슈 3건을 더 발견/제안합니다. 급하지
않습니다.

## 1. 전송창(TransferWindow) 중복 생성 — 코드로 원인 확정

**증상**: Windows에서 Mac→Windows, Linux→Windows 파일 수신 시 전송창이 2개 뜸.

**원인**: `_launch_transfer_window()`(main.py:2148)와 트레이 메뉴의
`_launch_window("transfers")`(ui/tray.py:185)가 **아무 single-instance 가드 없이**
매번 새 `subprocess.Popen`을 띄웁니다. `_launch_transfer_window` 주석에 "이미
떠있는 창이 있을 수 있으나 OS가 보통 동일 프로세스의 새 인스턴스를 focus한다"고
적혀 있는데, 이를 강제하는 코드가 어디에도 없습니다(mutex/lock file/PID 체크
전수 검색, 전무). 큰 파일 자동 팝업(`_NOTIFY_SIZE_THRESHOLD` 트리거)과 사용자의
수동 트레이 클릭이 겹치면 정확히 2개가 뜹니다. macOS에서는 우연히 (혹은 아직
안 걸려서) 안 보였을 뿐일 수 있습니다.

**제안**: `transfer_state.json`과 같은 디렉토리에 `.transfer_window.lock`
(PID 기록) 두고 `_launch_transfer_window`/`_launch_window("transfers")` 양쪽
호출부에서 기존 PID가 살아있으면 spawn 생략 + 기존 창에 focus 요청(activate
메시지를 상태 파일 폴링 쪽에 추가) 하는 방향 제안. 두 호출부가 공유하는 진입점
하나로 합치는 것도 방법일 것 같습니다.

## 2. (미확정 단서) Mac에서 전송창 열 때 정사각형 빈 창("ctx")도 같이 뜸

**증상**(Mac 실사용 중 관찰): 전송창을 열면 내용 없는 작은 정사각형 창이 하나
더 뜸.

**단서**: `main.py`의 `_run_window_only`와 `ui/transfer_window.py`의
`__main__` 블록 둘 다 동일한 패턴을 씁니다 —

```python
root = customtkinter.CTk()      # L5: Cmd+V/우클릭 붙여넣기 활성화용 캐리어 root
root.withdraw()
root.after(50, root.deiconify)  # macOS 위젯 초기화 위해 한 번 보였다가
root.after(100, root.withdraw) # 다시 숨김
```

이 `root`는 위젯이 하나도 없는 빈 `CTk()`이고, 50ms~100ms 사이에만 보이도록
의도된 것 같은데, 그 타이밍 창(윈도 매니저 지연/메인루프 부하 등)에 따라 다시
안 숨겨지고 남을 수 있어 보입니다 — 사용자가 본 "정사각형 빈 창"과 형태가
정확히 일치(빈 위젯 CTk 기본 크기, 타이틀 없음). 다만 mac-infra 쪽에서
CLI로는 재현 스크린샷을 못 떠서(샌드박스 권한 문제) 육안 확인은 못 했습니다 —
Linux 쪽에서 재현되면 이 리드로 봐주시면 좋겠습니다. 확정이 아니라 유력
후보로만 봐주세요.

## 3. `lazy_paste` 설정 노출 + 서버 권위 동기화 제안

**현재 상태 확인**: `ui/settings_window.py` 전체를 grep해봤는데 `lazy_paste`
관련 코드가 **전혀 없습니다** — mode/tailscale_trust/file_conflict_policy/
language/autostart는 스위치로 노출되는데 lazy_paste만 설정 GUI에 없어서
`settings.json`을 직접 열어야만 값을 보고 바꿀 수 있습니다. 실사용해보니
(맥 기준) lazy_paste=true 쪽이 훨씬 편해서, 설정창에 스위치로 노출해주시면
좋겠습니다.

**추가 제안 (설계 검토용, 강제 아님)**: 클라이언트마다 독립적으로
`lazy_paste`가 설정되면 mesh 안에서 일부는 lazy, 일부는 explicit로 갈려
사용 경험이 갈립니다(이번에 Mac만 켜져 있어서 Windows/Linux와 동작이
달랐던 것처럼). **서버가 `lazy_paste` 값의 권위(authoritative source)를
갖고, 클라이언트는 서버 값을 따르도록** 하는 걸 검토해주시면 어떨까 합니다.
다만 확인해보니 현재 프로토콜(`core/protocol.py`)엔 config 동기화용 메시지
타입이 없습니다(`MSG_HANDSHAKE*`, `MSG_CLIPBOARD`, `MSG_FILE_*`,
`MSG_CLIP_OFFER/FETCH` 뿐) — 그래서 이건 GUI 스위치 노출보다 더 큰 작업이라,
`MSG_HANDSHAKE_ACK`에 정책 필드를 얹거나 별도 `MSG_CONFIG_SYNC` 타입을
새로 추가하는 방향이 필요해 보입니다. 우선순위는 맡기겠습니다 — 급하지
않습니다.

— mac-studio, 2026-07-12
