---
round_trip_status: closed
from_host: linux (desireallsx)
to_host: mac (mac-studio)
topic: infinite-clipboard-lazy-mac-trap38-diagnostic-logging
created: 2026-07-11
in_reply_to: docs/2026-07-11-infinite-clipboard-gh-runner-setup-handoff.md
---

# [linux → mac-studio] infinite-clipboard 함정 #38 진단 로그 실행 요청

## 배경

`infinite-clipboard`의 macOS lazy 파일 붙여넣기 미작동(CLAUDE.md 함정 #38, 조사 중·미확정)
원인 후보는 다음과 같습니다:

- `provideDataForType:` 콜백은 **앱 메인 스레드 Cocoa run loop**에서만 발화한다(과거 CI run
  26632380131로 실증됨).
- CI 테스트(`test_lazy_mac.py::test_mac_file_url_roundtrip`)는 `NSRunLoop` 를 **직접 수동
  펌핑**해서 이 콜백을 검증하는데, 실제 앱은 Tk `mainloop()`가 이 역할을 대신 해준다고
  **가정만 하고 검증한 적이 없습니다**.
- 이 가정이 틀렸다면 — Tk 이벤트 루프가 크로스 프로세스 파스트보드 콜백(Mach/XPC
  라운드트립)에 필요한 방식으로 Cocoa run loop를 서비스하지 못한다면 — self-hosted 러너에서
  CI는 계속 green이면서 실제 앱에서만 붙여넣기가 안 될 수 있습니다.

## 조치 (완료, push됨)

`core/lazy_mac.py`에 `[DIAG-38]` 태그를 붙인 임시 진단 로그 3곳을 추가해 커밋·푸시했습니다
(커밋 `7a7d417`, origin/main):

1. `pasteboard_item_provideDataForType_` 진입 — **OS가 콜백을 실제로 발화했는지**
2. `_provide()` 진입 시 `NSThread.isMainThread()` — **메인 스레드에서 발화했는지**
3. `item.setData_forType_()` 완료 직후 — **OS에 데이터를 실제로 넘겼는지**

로그는 `config.LOG_FILE`(RotatingFileHandler)로 가므로 `.app` 번들로 띄워도 파일에 남습니다.

## 요청 — Mac 실기 검증

```bash
cd ~/project/infinite-clipboard   # 또는 실제 clone 경로
git pull origin main               # 7a7d417 이후 확인
```

1. `config.lazy_paste = True` 로 설정(설정 파일 또는 GUI 설정창)
2. `main.py` 를 **Tk mainloop 로 실제 기동**(PyInstaller 번들 `.app` 또는
   `python main.py` 터미널 실행 둘 다 무방 — 다만 `.app` 이 실제 배포 형태에 더 가까움)
3. 다른 PC(또는 같은 PC 다른 clone)에서 파일 복사 → mac-studio 에서 **실제 Finder Cmd+V**로
   붙여넣기 시도
4. 결과와 무관하게 로그 파일에서 확인:
   ```bash
   grep "DIAG-38" "$(python3 -c 'from config import LOG_FILE; print(LOG_FILE)')"
   ```

## 판정 기준

| 로그 결과 | 의미 |
|---|---|
| `[DIAG-38]` 세 줄 다 안 찍힘 | OS가 콜백 자체를 안 부름 — Tk가 등록은 했지만 콜백 전달 경로가 아예 안 됨 |
| 1번만 찍히고 2번부터 없음 | 콜백은 오는데 `_provide` 진입 전에 예외/분기 이슈(코드 로직 문제, run loop와 무관) |
| 1·2번은 찍히는데 `main_thread=False` | **가설 확정** — Tk가 메인 스레드가 아닌 곳에서 콜백을 받고 있음(비정상) |
| 1·2번 `main_thread=True` 인데 3번(setData 완료)이 없음 | fetch 실패/타임아웃 등 다른 원인 — run loop 문제 아님, `_fetch_offer` 쪽 조사로 이동 |
| 3줄 다 정상 찍히는데 Finder에 실제로 안 붙여짐 | run loop/콜백은 정상, OS 파스트보드 소비 측(Finder) 문제로 용의선 이동 |

## 회신 요청

- 위 grep 결과 원문 (또는 "로그 없음")
- Finder 붙여넣기 실제 성공/실패 여부
- `lazy_paste` 설정값(True로 바꿨는지)

완료되면 함정 #38 CLAUDE.md 항목 업데이트하고, 조사 종결 시 이 3개 진단 로그는 제거
예정입니다(임시 목적). 급하지 않습니다.

— linux (desireallsx), 2026-07-11
