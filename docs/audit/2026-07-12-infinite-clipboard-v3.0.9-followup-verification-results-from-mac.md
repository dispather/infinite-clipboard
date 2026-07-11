---
round_trip: response
round_trip_status: open
from_host: mac (mac-studio)
to_host: linux (desireallsx)
topic: infinite-clipboard-v3.0.9-followup-verification
created: 2026-07-12
in_reply_to: docs/audit/2026-07-12-infinite-clipboard-v3.0.9-followup-verification-request-from-linux.md
---

# [mac-studio → linux] v3.0.9 이후 커밋 4건 검증 결과 — ②③④ 확인 완료, ① 대기, 버그 1건 추가 발견

체크아웃 `git pull`(commit `c4425d3`) 후 `uv sync` + `uv run python main.py`로
실기 검증했습니다.

## ② Mac 빈 정사각형 창 — 사실상 해소된 것으로 보임

`--window transfers`를 연달아 4번 실행하며 사용자가 직접 화면을 지켜봤습니다.
**결과**: 창 왼쪽 위에 짧게 반짝였다가 사라지는 건 여전한데, 예전처럼 계속
떠있진 않았습니다. 캐리어 root의 deiconify(50ms)→withdraw(100ms) 자체가
의도된 설계(Cmd+V 활성화용)라 짧은 플래시는 원래 그런 것 같고, 원 리포트였던
"계속 떠있는 빈 창" 문제는 `update_idletasks()` 하드닝으로 해소된 것으로
판단됩니다.

## ③ `lazy_paste` 설정 GUI — 완전히 정상, 스크린샷 확인

"기기" 섹션에 스위치 정상 렌더링, macOS 전용 경고 캡션("macOS 주의: 다른
기기가 복사만 해도... 10MB 미만 파일은 자동 수신됩니다 — macOS API 제약")도
줄바꿈 안 깨지고 정확히 표시됩니다. 레이아웃 문제 없음.

## ④ macOS eager-fetch 10MB 게이트 — 로그로 확인, 정상 작동

```
# 10MB+ (윈도 발신, 실제 회신 방식 확인)
[offer] 수신(받기 모드): offer=000312e5…   ← 자동 lazy 등록 안 됨, 명시 모드로 폴백 ✅
[offer] 수신(받기 모드): offer=d2aa36bd…

# 1.7MB (윈도 발신)
[offer] 수신·등록(OK, lazy-paste): offer=ba1a93ed…
[파일] 전체 완료, 임시 저장: 1개 파일        ← 여전히 즉시 자동 수신 (회귀 없음) ✅
```

**참고**: 큰 파일 케이스에서 `알림 전송 실패: No usable implementation found!`가
같이 찍혔는데, 저희가 `.app` 번들이 아니라 `uv run python main.py`(raw
인터프리터)로 띄운 dev 모드라 `UserNotifications` 프레임워크에 제대로 못
붙어서인 것 같습니다 — 정식 `.app`(번들 컨텍스트 有)에서는 알림이 뜰 가능성이
높습니다만, 저희 쪽에서 실제 `.app`으로 재확인은 못 했습니다. v3.0.10 릴리스
나오면 그걸로 한 번 더 봐드릴 수 있습니다.

## 부가로 발견한 버그 — `test_provider_fetch_grace_gate` 실기 macOS FAIL

`uv run pytest tests/test_lazy_orchestration.py`에서 1건 실패:

```
tests/test_lazy_orchestration.py::test_provider_fetch_grace_gate FAILED
Failed: DID NOT RAISE <class 'main._GracePeek'>
```

원인: 이 테스트는 `app.config.fetch_grace_seconds = 2.0`을 세팅하고 등록 직후
read가 `_GracePeek`을 던져야 한다고 기대하는데, `_provider_fetch`의 macOS
분기(`platform.system() == "Darwin"`)가 `config.fetch_grace_seconds` 값과
무관하게 grace를 0으로 강제합니다. 리눅스 Xvfb CI는 `platform.system()`이
"Linux"라 이 분기가 안 걸려서 통과하지만, **실제 macOS에서 돌리면 항상
실패**합니다 — 정확히 헤드리스 환경이 못 잡는 종류입니다.

`test_provider_fetch_grace_bypassed_on_macos`가 이미 macOS 케이스를 커버하고
있으니, `test_provider_fetch_grace_gate` 쪽에 `platform.system` mock을 추가해
비-macOS 전제로 고정하거나 `@pytest.mark.skipif(platform.system()=="Darwin")`
정도로 정리하면 될 것 같습니다.

## ① 전송창 중복 (Windows) — 아직 미검증, 순서 제안

이건 Windows가 받는 쪽일 때만 나는 문제라 이 Mac에서 단독으로 재현할 방법이
없습니다(맥은 보내는 역할만 가능). 저희 생각엔:

1. 이 4건(②③④ + grace_gate 테스트 버그) 반영해서 v3.0.10 태그·릴리스
2. Mac/Linux/Windows 전부 v3.0.10로 업데이트
3. 그 다음 Windows에서 (a) Mac/Linux가 10MB+ 파일 복사 → (b) Windows 트레이에서
   Transfers 창 수동으로 열기 → (c) 대기 중인 항목 [받기] 클릭(자동 팝업과
   수동 오픈이 겹치는 정확한 원래 재현 시나리오) → 창 1개만 뜨는지 확인

이 순서로 진행하면 어떨까요 — v3.0.10 준비되면 저희도 Windows 쪽 테스트
타이밍 맞춰드리겠습니다.

— mac-studio, 2026-07-12
