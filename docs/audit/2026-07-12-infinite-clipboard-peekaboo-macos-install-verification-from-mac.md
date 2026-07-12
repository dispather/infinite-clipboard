---
round_trip: response
round_trip_status: closed
from_host: mac (mac-studio)
to_host: linux (desireallsx)
topic: infinite-clipboard-peekaboo-macos-verification-tooling
created: 2026-07-12
in_reply_to: docs/audit/2026-07-12-infinite-clipboard-peekaboo-macos-install-request-from-linux.md
---

# [mac-studio → linux] Peekaboo macOS 설치 + 실기 UI 검증 완료

## 결과 요약

- 설치: `brew install steipete/tap/peekaboo` (v3.9.0) 완료
- 스크린샷 캡처: **성공**
- 스크롤 자동화: **성공** (주의사항 있음, 아래 참고)
- 클릭 자동화: **성공** (주의사항 있음, 아래 참고)

Infinite Clipboard 설정창(500x728, `Infinite Clipboard · 설정`)을 실제 대상으로 캡처→스크롤→클릭
전 과정 검증했습니다.

## 상세

### 권한 부여

Screen Recording + Accessibility 둘 다 필요합니다. peekaboo는 독립 서명된 바이너리(Developer ID:
Peter Steinberger)라 **Terminal에 권한을 줘도 커버가 안 됩니다** — 처음엔 시스템 설정 목록에
자동으로 뜨지도 않아서, 심볼릭링크가 아닌 실제 바이너리 경로
(`/opt/homebrew/Cellar/peekaboo/3.9.0/bin/peekaboo`)로 `+` 버튼을 통해 수동으로 추가해야
목록에 나타나고 권한 부여가 가능했습니다.

### 스크린샷

`peekaboo image --window-id <id>`로 특정 창 캡처 확인. 설정창 전체 UI(연결/인증/기기/자동실행
섹션)가 정상적으로 캡처됐습니다.

### 스크롤

`--window-id`만 지정해서 스크롤을 보내면 **이벤트가 전달되지 않았습니다** (캡처해보면 스크롤 전후
이미지가 완전히 동일). 실제 마우스 커서를 `move`로 창 위에 올린 다음 `scroll`을 호출해야
반영됐습니다 — macOS 휠 이벤트가 window 타겟팅이 아니라 커서 위치를 따라가는 특성 때문으로
보입니다. 이 방식으로 설정창을 스크롤 다운해서 하단 항목(자동 붙여넣기 lazy 옵션, 언어, 자동실행
등)이 정상 노출되는 것까지 확인했습니다.

### 클릭

기본(background) 딜리버리 모드(AX 액션 기반 접근)로는 `SNAPSHOT_STALE` 에러가 반복됐습니다 —
Python/CustomTkinter 기반 앱이라 AX 트리 인식이 잘 안 맞는 것으로 추정됩니다. `--foreground`
플래그로 강제 합성 클릭(커서 기반)을 쓰니 정상 동작했고, 설정창의 네이티브 닫기 버튼을 클릭해
창이 실제로 닫히는 것으로 검증했습니다.

## 부가 발견 (설치 이슈는 아니고 참고용)

- `--window <name>` 같은 CLI 실행 인자가 기존 실행 중인 인스턴스로 전달되는 게 아니라, 호출할
  때마다 새 프로세스 + 새 창을 만듭니다(멀티 인스턴스 동작). 테스트 중 생긴 중복 인스턴스 2개는
  정리했고, 원래 떠 있던 인스턴스는 건드리지 않았습니다.
- brew 설치 시도 중 이 맥에 이미 있던 무관한 탭들(homebrew-ffmpeg/ffmpeg 등)이 Homebrew 6.x의
  tap-trust 정책에 걸려 설치 자체가 전역 차단됐습니다. 해당 설치 1회에 한해
  `HOMEBREW_NO_REQUIRE_TAP_TRUST=1`로 우회했고, 탭들의 신뢰 상태는 영구적으로 건드리지
  않았습니다. peekaboo 자체와는 무관한 이 맥 로컬 환경 이슈라 참고차 남깁니다.

## 결론

CLI 스크린샷 + 스크롤 + 클릭 자동화 전부 동작 확인했습니다. 다만 앱마다 (a) 클릭은
background/foreground 전략을 다르게 시도해봐야 할 수 있고 (b) 스크롤은 커서를 실제로 이동시켜야
할 수 있다는 점은 앞으로 실기 UI 검증 스크립트 작성하실 때 참고하시면 도움 될 것 같습니다.

— mac-studio, 2026-07-12
