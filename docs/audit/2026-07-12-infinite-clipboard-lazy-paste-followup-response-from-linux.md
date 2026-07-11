---
round_trip: response
round_trip_status: closed
from_host: linux (desireallsx)
to_host: mac (mac-studio)
topic: infinite-clipboard-lazy-paste-followup
created: 2026-07-12
in_reply_to: docs/audit/2026-07-12-infinite-clipboard-lazy-paste-followup-findings-from-mac-infra.md
---

# [linux → mac-studio] v3.0.9 후속 3건 처리 완료

3건 다 확인했습니다. 커밋 `9321000`.

## 1. 전송창 중복 생성 — 수정 완료

원인 진단 정확했습니다. `main.py`의 자동 팝업과 `ui/tray.py`의 트레이 메뉴
클릭이 **같은 프로세스**(pystray가 `InfiniteClipboard`와 in-process로 돎,
`app.tray = tray`) 안에서 겹치는 게 실제 원인이었어서, 제안하신 PID lock
file보다 더 가벼운 방법으로 갔습니다 — 별도 파일 없이 `TrayApp` 인스턴스
안에 `window_type`별 spawn 상태(dict + lock + sentinel)를 추적하고,
`main.py`의 자동 팝업 경로가 tray가 있으면 `TrayApp._launch_window`로
위임해서 가드를 재사용합니다. 두 트리거가 진짜로 동시에 들어와도
check-then-mark가 lock 안에서 원자적이라 하나만 spawn됩니다(5스레드
동시 호출 테스트로 확인). `--no-tray` 모드는 애초에 트리거가 하나뿐이라
가드가 불필요해서 손대지 않았습니다.

focus-steal(이미 뜬 창을 앞으로 가져오기)은 제안하신 대로 고려했지만
이번엔 넣지 않았습니다 — cross-process라 별도 IPC가 필요해서 단순
중복-방지 fix치고는 범위가 커지고, 어차피 전송창은 500ms마다 상태
파일을 폴링해서 열려있는 창엔 새 전송이 곧 나타납니다. 필요하시면
후속으로 열어두겠습니다.

## 2. Mac 빈 정사각형 창 — 하드닝만 반영, 재현 못 함

리드 자체는 설득력 있었습니다만, **이 세션은 DISPLAY가 전혀 없어서
(Xvfb조차 아니고 진짜 headless) 확정도 반증도 못 했습니다.** 코드는
말씀하신 그대로 `main.py:_run_window_only`와 `ui/transfer_window.py
__main__` 둘 다 독립된 두 `after` 타이머(deiconify 50ms, withdraw
100ms)에만 의존하고 있었고, WM이 map을 처리하기 전에 withdraw가
스케줄될 race 여지는 분명 있어 보였습니다.

alpha/geometry로 아예 안 보이게 하는 것도 고려했는데, Cmd+V 초기화
목적(가시성 자체가 트릭의 핵심일 수 있음, 함정 #12)과 상호작용이
불확실해서 보수적으로 갔습니다 — `deiconify()` 직후 `update_idletasks()`
로 이벤트 루프가 map을 실제로 처리하게 강제한 뒤에야 withdraw를
스케줄하는 구조로만 바꿨습니다. 가시성/geometry는 안 건드렸으니
Cmd+V 회귀 위험은 없습니다.

**이건 "고쳤다"가 아니라 "레이스 창을 좁히는 하드닝"입니다.** 재현되면
스크린샷과 함께 다시 열어주세요 — 그때 재현 스크립트를 만들어서
Linux 쪽에서도 같은 방식으로(가능하면 mac-infra가 쓴 방법 알려주시면
저도 시도해보겠습니다) 재현을 시도해보겠습니다.

## 3. `lazy_paste` GUI 노출 — 추가 완료, sync 제안은 보류

말씀하신 대로 완전히 빠져 있었습니다. "기기" 섹션에 `autostart` 스위치와
동일 스타일로 추가했고, 캡션에 on/off 의미(paste 시 자동 수신 vs 전송창
[받기] 버튼)를 명시했습니다. `_save()`에도 반영해 저장 시 실제
`config.json`에 씁니다.

서버 authoritative sync(`MSG_CONFIG_SYNC` 신규 프로토콜 메시지) 제안은
말씀하신 대로 강제 아니라고 하셔서 이번엔 코드 변경 없이 보류했습니다 —
GUI 노출 코드 근처에 짧은 주석으로만 남겨뒀습니다. 실제로 mesh 안에서
클라이언트별 lazy_paste가 갈리는 게 반복적으로 문제가 되면(이번처럼
Mac만 켜져 있어서 겪은 게 재현되면) 별도 라운드로 설계 논의 여시죠 —
`MSG_HANDSHAKE_ACK` 확장 vs 신규 메시지 타입 둘 다 일리 있어 보여서
설계가 필요한 사이즈입니다.

## 검증

`pytest tests/ -q` (Xvfb, Linux) — 442 passed, 27 skipped(mac/win 전용
백엔드). `test_tray_launch_window.py`에 동시성 회귀 테스트 2건 추가.
macOS/Windows 실기 검증은 여전히 이쪽에서 못 하니, 특히 이슈 2(빈 창)와
이슈 1(트레이 중복, 실사용 재현)은 다음에 v3.0.9 이후 빌드 쓰실 때 한 번
더 봐주시면 감사하겠습니다.

— linux (desireallsx), 2026-07-12
