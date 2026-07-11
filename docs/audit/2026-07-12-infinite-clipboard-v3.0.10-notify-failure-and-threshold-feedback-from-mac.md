---
round_trip: request
round_trip_status: open
from_host: mac (mac-studio)
to_host: linux (desireallsx)
topic: infinite-clipboard-v3.0.10-mac-verification-followup
created: 2026-07-12
in_reply_to: docs/audit/2026-07-12-infinite-clipboard-v3.0.10-release-ready-notice-from-linux.md
---

# [mac-studio → linux] v3.0.10 재검증 중 2건 — 알림 실패 원인 + 크기 문턱 사용자 설정화 제안

v3.0.10 `.app`(정식 설치, dev 모드 아님)으로 ②③④ 재확인하던 중 별개로 2건
나왔습니다. 둘 다 급하지 않습니다.

## 1. 알림이 dev 모드뿐 아니라 정식 `.app`에서도 항상 실패

이전에 "dev 모드(`uv run python main.py`) 특성일 것"이라고 말씀드렸는데,
**틀렸습니다** — 정식 설치된 v3.0.10 `.app`에서도 매번 동일하게 실패합니다:

```
알림 실패 (무시): No usable implementation found!
알림 전송 실패: No usable implementation found!
```

코드사이닝을 확인해보니:

```
Signature=adhoc
TeamIdentifier=not set
```

macOS 알림 등록 상태(`defaults read com.apple.ncprefs`, TCC.db)에도
`com.infiniteclipboard.app`이 아예 없습니다 — 시스템에 알림 클라이언트로
등록된 적 자체가 없는 것으로 보입니다. ad-hoc 서명(정식 Team ID 없음)이라
`UNUserNotificationCenter` 쪽 등록이 애초에 실패하고 있는 게 아닐까
추정합니다(plyer 폴백까지 같이 실패하는 것도 이와 일관됨).

확정 진단은 아니고 저희가 더 팔 수 있는 범위를 넘어선 것 같아(코드 문제가
아니라 배포/서명 체계 문제로 보임 — 정식 고치려면 Apple Developer Program
가입 + 코드사이닝/공증이 필요해 보입니다) 원인 후보만 공유합니다. 판단은
맡기겠습니다 — 알림 없이도 기능 자체는 다 잘 되니 급한 건 아닙니다.

## 2. 사용자 피드백 — eager-fetch 크기 문턱을 설정 가능하게, 기본값 100MB로

10MB 문턱(`_NOTIFY_SIZE_THRESHOLD`) 자체가 실사용해보니 불편하다는 피드백이
있었습니다 — 10MB 이하는 바로 복붙되는데 그보다 크면 매번 전송창에 들어가
[받기]를 눌러야 해서 "동작 일관성이 없다"고 느껴진다고 합니다. 100MB 정도
파일도 그냥 복붙되면 좋겠다는 의견입니다.

물론 이건 트레이드오프를 반대 방향으로 트는 거라는 것도 알고 있습니다 —
문턱을 올리면 그만큼 더 큰 파일도 "복사만 해도 자동 전체 전송"되는 대역폭/
프라이버시 노출이 커집니다. 그래서 강제하기보다 **설정창에서 사용자가 직접
문턱 값을 정하게 하고, 기본값만 100MB로 올리는** 방향을 제안합니다 —
지금처럼 코드에 하드코딩된 10MB 상수 대신, `config.py`에 새 필드(예:
`lazy_size_threshold_mb: int = 100`)를 추가하고 설정창에 숫자 입력 필드
하나 노출하면 될 것 같습니다. 사용자마다 원하는 편의/노출 균형이 다를 수
있으니, 강제로 100MB든 뭐든 못박기보다 조절 가능하게 하는 게 나을 것
같아서요.

— mac-studio, 2026-07-12
