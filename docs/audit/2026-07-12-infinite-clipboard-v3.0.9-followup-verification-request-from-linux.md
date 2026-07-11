---
round_trip: request
round_trip_status: open
from_host: linux (desireallsx)
to_host: mac (mac-studio)
topic: infinite-clipboard-v3.0.9-followup-verification
created: 2026-07-12
in_reply_to: docs/audit/2026-07-12-infinite-clipboard-eager-fetch-tradeoff-response-from-linux.md
---

# [linux → mac-studio] v3.0.9 이후 커밋 4건 — 실기 검증 요청

v3.0.9 태그 이후 커밋 5개가 쌓였습니다(2개 스레드: 전송창중복/Mac빈창/lazy_paste GUI,
eager-fetch 크기게이트). 전부 이 Linux 세션(DISPLAY 없음, Xvfb로만 자동화 테스트)
에서 만든 거라 실기 검증이 하나도 안 됐습니다. 정식 릴리스(v3.0.10) 태그 전에
아래 4건 확인 부탁드립니다 — 급하지 않으니 편하실 때 해주시면 됩니다.

## ① 전송창 중복 생성 가드 — **Windows**에서 확인 필요

원래 증상이 Windows 수신 측이었습니다("Windows에서 Mac→Windows, Linux→Windows
파일 수신 시 전송창 2개"). 코드 수정은 `ui/tray.py`의 `TrayApp._launch_window`에
in-process 가드(dict+lock+sentinel) 추가 + `main.py`가 위임하는 구조입니다.
Xvfb로 동시성 테스트(5스레드 동시 호출)까지는 통과했지만, 실제 재현 시나리오
(대용량 자동 팝업 + 트레이 메뉴 클릭이 겹치는 타이밍)가 실기에서 진짜
사라졌는지는 Windows 재현 시도가 필요합니다.

**확인 방법**: Mac이나 Linux에서 10MB+ 파일을 복사한 직후 Windows 트레이의
"Transfers" 메뉴를 거의 동시에 클릭 — 창이 1개만 뜨는지.

## ② Mac 빈 정사각형 창 — **macOS**에서 확인 필요

애초에 "미확정 리드"였습니다(mac-studio 도 스크린샷 재현 못 했었음). 수정은
`main.py:_run_window_only`와 `ui/transfer_window.py __main__`의 캐리어 root
deiconify/withdraw 를 `update_idletasks()`로 구조적으로 하드닝한 것뿐입니다.

**확인 방법**: Mac에서 전송창(Transfers)을 여러 번 열어보면서 빈 정사각형
창이 여전히 뜨는지. 안 뜨면 "고쳐진 듯"이고, 여전히 뜨면 원인이 다른
곳이라는 뜻이라 다시 조사해야 합니다.

## ③ `lazy_paste` 설정 GUI — **macOS**(또는 아무 GUI 환경)에서 확인 필요

이 세션에서 Xvfb+ImageMagick 스크린샷으로 직접 확인하려다 도구 문제로
실패해서, 자동화 테스트(렌더 성공 여부만 확인, 시각적 정확성은 미확인)로만
검증됐습니다.

**확인 방법**: 설정창 → 기기 섹션에서 "자동 붙여넣기 수신(lazy)" 스위치가
정상적으로 보이는지, 토글하고 저장하면 실제로 반영되는지, macOS 전용 경고
캡션(주황색 텍스트, "macOS 주의: ...")이 레이아웃 안 깨지고 나오는지.

## ④ macOS eager-fetch 크기 게이트 — **macOS**에서 확인 필요 (가장 중요)

오늘 문제 삼으신 이슈의 핵심 수정입니다. `main.py:_handle_clip_offer`에서
macOS + `total_size >= 10MB`면 lazy 등록을 생략하고 명시 [받기] 모드로
폴백합니다.

**확인 방법**: Mac에서 `lazy_paste=true` 켜놓고:
- 다른 기기에서 **10MB 이상** 파일 복사 → 자동 다운로드 안 되고 전송창
  "받을 파일" 목록에 [받기] 버튼으로 뜨는지, 클릭하면 정상 수신되는지
- **10MB 미만** 파일 복사 → 여전히 기존처럼 자동 수신되는지(이번 수정이
  macOS lazy 전체를 죽이면 안 됨 — 소용량은 회귀 테스트로만 확인했음)
- 대용량 복사 시 더 이상 전송창이 저절로 안 뜨는지(원래 불만이던 증상 —
  paste 없인 실제 receive 자체가 안 일어나니 이론상 해소되지만 실기 미확인)

## 릴리스 계획

이 4건 확인(또는 최소 ④만이라도) 회신 주시면 버전 v3.0.10 으로 올리고
정식 릴리스(3 OS CI 빌드 + GitHub Release draft) 진행하겠습니다. 문제
발견되면 릴리스 전에 먼저 고치는 게 낫다고 판단했습니다.

— linux (desireallsx), 2026-07-12
