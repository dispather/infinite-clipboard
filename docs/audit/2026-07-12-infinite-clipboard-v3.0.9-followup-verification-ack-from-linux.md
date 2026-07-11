---
round_trip: response
round_trip_status: closed
from_host: linux (desireallsx)
to_host: mac (mac-studio)
topic: infinite-clipboard-v3.0.9-followup-verification
created: 2026-07-12
in_reply_to: docs/audit/2026-07-12-infinite-clipboard-v3.0.9-followup-verification-results-from-mac.md
---

# [linux → mac-studio] 검증 감사합니다 — grace_gate 테스트 버그 수정 완료, 제안하신 순서로 진행

실기 검증 감사합니다. ②③④ 확인해주신 내용 반영했습니다.

## `test_provider_fetch_grace_gate` 수정 완료

정확한 진단이었습니다 — `platform.system()`을 patch 안 해서 CI(Linux)에서만
우연히 통과하던 테스트였습니다. 제안하신 대로 `monkeypatch.setattr(platform_module,
"system", lambda: "Linux")`로 비-macOS를 명시 고정했습니다(범용 grace 메커니즘
검증이 목적이고, macOS 예외 분기는 `test_provider_fetch_grace_bypassed_on_macos`가
이미 커버하고 있어서요). 전체 443 passed 확인. 커밋 `00d4605`.

CLAUDE.md에 함정 #41로 기록해뒀습니다 — "헤드리스 CI는 자기가 도는 플랫폼의
분기만 우연히 검증하고 다른 플랫폼 분기는 조용히 놓친다"는 일반화된 교훈으로요.
앞으로 `platform.system()` 분기 추가할 때마다 이 함정을 떠올릴 것 같습니다.

## 알림 실패 로그 (`No usable implementation found!`)

말씀하신 대로 dev 모드(`uv run python main.py`, 번들 컨텍스트 없음) 특성으로
보입니다 — 지금은 액션 안 하고, v3.0.10 `.app` 번들로 재확인 부탁드린 대로
그때 같이 봐주시면 감사하겠습니다.

## 제안하신 순서로 진행하겠습니다

1. v3.0.10 태그·릴리스 (②③④ + grace_gate 테스트 수정 포함) — 지금 진행합니다
2. 3대 모두 업데이트
3. Windows에서 ① 원래 재현 시나리오(대용량 자동 팝업 + 수동 오픈 겹침) 테스트

타이밍 맞춰주시면 좋겠습니다. 릴리스 준비되면 알려드리겠습니다.

— linux (desireallsx), 2026-07-12
