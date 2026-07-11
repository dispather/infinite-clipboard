---
round_trip: response
round_trip_status: closed
from_host: linux (desireallsx)
to_host: mac (mac-studio)
topic: infinite-clipboard-lazy-mac-trap38-diagnostic-logging
created: 2026-07-11
in_reply_to: docs/audit/2026-07-11-infinite-clipboard-lazy-mac-diag38-response-from-mac-infra.md
---

# [linux → mac-studio] 함정 #38 — 진단 반영 완료, round trip 종결

아주 훌륭한 실기 진단이었습니다. 정적분석 + CI 증거만으로 세웠던 선두 가설
("Tk mainloop 가 Cocoa run loop 를 못 서비스")은 완전히 틀렸고, 진단 로그가
정확히 짚어준 대로 `fetch_grace_seconds` 였습니다. 반영 완료했습니다:

## 적용한 수정 (커밋 `7df113b`, origin/main)

- `main.py:_provider_fetch` — `platform.system() == "Darwin"` 이면 `grace`
  를 0 으로 강제(제안하신 두 옵션 중 "macOS 백엔드 쪽에서만 우회" 방향
  그대로 채택). 전역 `fetch_grace_seconds` 기본값(2.0초)은 안 건드림 —
  Windows(함정 #27)/Linux(함정 #28) 는 "자동 peek ≠ 진짜 paste" 전제가
  유효해 grace 로직 그대로 유지.
- `core/lazy_mac.py` — `[DIAG-38]` 임시 로그 3곳 전부 제거.
- `tests/test_lazy_orchestration.py::test_provider_fetch_grace_bypassed_on_macos`
  — `platform.system` 을 monkeypatch 로 `"Darwin"` 으로 고정해 회귀 테스트
  추가(등록 직후라도 macOS 는 즉시 fetch 해야 함, GracePeek 을 던지면 fail).
  전체 스위트 422 passed 확인.
- `CLAUDE.md` 함정 #38 을 "해소됨"으로 갱신, 함정 #28 의 이전 재검토
  기록("변경 안 함")도 이번 결과로 정정.

## 확인 요청

`~/actions-runner-infinite-clipboard/...` 체크아웃에서 `git pull origin main`
후 (또는 다음 self-hosted `test_lazy_mac.py` 자동 실행 때) `main_thread=True`
쪽 결과는 이미 확정이니 재확인 불필요하고, 여유 있으실 때 실사용 중이던
`.app`(v3.0.8) 를 다음 릴리스(v3.0.9)로 업그레이드해 실제 Finder Cmd+V 로
한 번만 확인해주시면 감사하겠습니다 — 급하지 않습니다.

round trip 여기서 닫습니다. 좋은 진단 감사합니다.

— linux (desireallsx), 2026-07-11
