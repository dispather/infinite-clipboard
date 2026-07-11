---
round_trip: request
round_trip_status: open
from_host: linux (desireallsx)
to_host: mac (mac-studio)
topic: infinite-clipboard-peekaboo-macos-verification-tooling
created: 2026-07-12
---

# [linux → mac-studio] macOS 실기 UI 검증용 Peekaboo 설치 요청 (급하지 않음)

## 배경

리눅스 쪽에서 v3.0.10 후속(커밋 `49758a0`, 문턱설정화/히스토리삭제/무시버튼) 코드 리뷰를
하다가, 앞으로 실기 UI 검증(스크린샷+클릭 자동화)을 리눅스 에이전트 세션이 직접 할 방법을
찾아봤습니다. 결과:

- `usecomputer`(X11 기반) → 이 리눅스 데스크톱(KDE Plasma Wayland)에선 화면 캡처가 항상
  검은 화면(KWin이 XWayland 루트윈도우에 전체 데스크톱을 합성 안 해줌 — 구조적 한계).
- `nordbyte/PeekabooX`(Rust, KDE/PipeWire 네이티브 지원) → 설치·검증 완료. `spectacle`
  폴백으로 실제 데스크톱 캡처 성공, `uinput` 백엔드로 클릭도 성공.

같은 계열(openclaw/Peekaboo)이 macOS 전용으로도 있습니다:
https://github.com/openclaw/Peekaboo — macOS CLI + 선택적 MCP 서버, 애플리케이션/전체
시스템 스크린샷 캡처.

## 요청

macOS는 이 리눅스 세션이 직접 다룰 수 없는 플랫폼이라(에이전트가 리눅스에서만 Bash를
실행함), mac-studio에서 도는 세션/사용자분께서 여유 있으실 때:

1. `Peekaboo`(openclaw/Peekaboo) 설치
2. mac-studio의 infinite-clipboard 로컬 체크아웃에서 실행 중인 GUI(설정창/전송창/히스토리창
   등)를 대상으로 실제 스크린샷 캡처가 되는지, 클릭 자동화(버튼 클릭 등)가 되는지 간단히
   검증
3. 결과를 회신 문서로 알려주시면(성공/실패, 설치 이슈 등) 감사하겠습니다

## 목적

앞으로 v3.0.11 이후 실기 UI 검증(예: 이번에 추가한 히스토리 삭제 버튼, 무시 버튼 등)을
사람이 직접 클릭해보지 않고도 자동화된 스크린샷/클릭으로 확인할 수 있는 수단을 3 OS 각각에
마련해두려는 목적입니다. 급하지 않으니 편하실 때 진행해주시면 됩니다.

— linux (desireallsx), 2026-07-12
