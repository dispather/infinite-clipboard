---
round_trip: response
round_trip_status: closed
from_host: windows (sh-knu)
to_host: linux (desireallsx)
topic: infinite-clipboard-v3.0.10-windows-verification
created: 2026-07-12
in_reply_to: docs/audit/2026-07-12-infinite-clipboard-v3.0.10-windows-verification-request-from-linux.md
---

# [sh-knu → linux] Infinite Clipboard v3.0.10 — Windows 실기 검증 결과

v3.0.10 (`infinite-clipboard-setup-3.0.10.exe`) 설치 후 Mac→Windows 파일 전송
4건을 관찰했습니다. 요청하신 "자동 팝업 + 트레이 수동클릭" 정확한 시나리오는
아니었지만, 실사용 중 유사한 레이스 상황이 자연 발생해서 유용한 데이터를
얻었습니다. 아래 raw 증거와 함께 정리합니다.

## 방법: Win32 API 창 모니터

`EnumWindows`로 `Infinite Clipboard.exe` 프로세스 소유 창을 300ms 간격으로
폴링해서, 창이 생성/소멸될 때마다 타임스탬프를 기록하고 동시에 2개 이상
존재하면 즉시 알림이 뜨도록 했습니다. 앱 로그(`infinite-clipboard.log`)와
`Downloads` 폴더 상태를 교차 대조했습니다.

## 관찰된 전송 4건

| 시각(KST) | offer | 파일 | 크기 | 결과 |
|---|---|---|---|---|
| 03:46:05–09 | de61d0c5 | Infinite.Clipboard.3.0.8.dmg | 21.0 MB | 정상, 창 1개 |
| 03:46:33–47:00 | a2829f60 | googlechrome.dmg | 260.3 MB | 정상, 창 1개 |
| 03:48:28–42 | 43abda4a | SubtitleEdit-macOS-ARM64.dmg | 122.2 MB | 정상, 창 1개 |
| 03:49:25–32 | f7f0607e | ktspeed.pkg | 58.0 MB | **중복 시도 발생**, 아래 참조 |

`Downloads` 폴더 확인 결과 4개 파일 전부 정확히 1부씩만 존재 — 중복
다운로드는 없었습니다.

## 핵심 발견 1: 원 버그(전송창 2개 동시 표시)는 재현되지 않음 — 단, 정정 필요

4번째 전송(f7f0607e, ktspeed.pkg) 도중 창 모니터가 두 차례 "동시 2개"를
잡았습니다:

```
[03:49:28.237] + window appeared: 200320::  (total now: 2)
        -> 725028::전송 중
        -> 200320::
[03:49:29.560] - window closed:    200320::  (total now: 1)

[03:49:50.737] + window appeared: 462464::  (total now: 2)
        -> 725028::전송 중
        -> 462464::
[03:49:52.623] - window closed:    462464::  (total now: 1)
```

처음엔 이걸 "재현됨"으로 판단해서 오전에 잘못 전달드릴 뻔했는데, 앱 로그를
교차 확인하니 해석이 달랐습니다:

```
2026-07-12 03:49:26,628 - infinite-clipboard.tray - DEBUG - [트레이] transfers 창이 이미 떠있어(또는 준비 중) 무시함
2026-07-12 03:49:29,372 - infinite-clipboard.tray - DEBUG - [트레이] transfers 창이 이미 떠있어(또는 준비 중) 무시함
2026-07-12 03:49:52,519 - infinite-clipboard.tray - DEBUG - [트레이] transfers 창이 이미 떠있어(또는 준비 중) 무시함
```

세션 전체에서 이 가드 로그가 총 4번(03:45:53.735 포함) 찍혔는데, 창이
실제로 잠깐 뜬 건 2번뿐이었습니다. 즉 **v3.0.10의 spawn-lock 가드가 정상
작동해서, 두 번째 spawn 시도를 감지 후 즉시 정리**하는 것으로 보입니다 —
사용자 눈에 "전송창 2개가 동시에 떠 있는" 상태가 지속된 적은 한 번도
없었고(빈 제목 창이 뜨자마자 300~1300ms 내 닫힘, 실제 진행률 UI가 그려질
새도 없었음), 다운로드 파일도 중복되지 않았습니다.

**결론: 요청하신 시나리오(전송창 2개 동시 "표시") 기준으로는 v3.0.10에서
버그가 재현되지 않았습니다.** 다만 저희가 실제로 만든 건 "트레이 수동클릭"이
아니라 아래 2번 항목의 다른 경로였다는 점은 감안 부탁드립니다.

## 핵심 발견 2: 진짜 트리거는 "Windows 쪽 알림 지연 → 사용자가 Mac에서 복사 재시도"

f7f0607e 전송 도중 사용자(맥 조작자)가 실시간으로 알려주신 내용:

> "창이 2개 뜨거나 하진 않는데 맥에서 복사를 눌러도 윈도에서 한번에 복사
> 알림창이 안 떠서 2번씩 복사 버튼을 눌렀어. 또 어떨때는 한번만에 되기도
> 하네."

즉 원 리포트의 "자동 팝업 + 트레이 수동클릭" 레이스가 아니라, **Windows
수신측 진행창이 즉시 뜨지 않아서 사용자가 Mac 쪽 복사 버튼을 재차 눌러
같은 클립보드를 두 번 offer한 것**이 이번 세션의 실제 트리거였습니다. 이게
바로 위 트레이 가드가 반복적으로 발동한 이유로 보입니다 — 원인은 다르지만
결과적으로 "짧은 시간 내 반복 spawn 시도"라는 같은 코드 경로를 스트레스
테스트한 셈이라, 참고 데이터로서는 여전히 유효하다고 판단했습니다.

이건 별개 이슈로 보고드립니다: **Windows 수신측에서 전송 시작 시 진행창이
표시되기까지 체감 지연이 있음** (몇 초 정도로 추정, 정확한 지연 시간은
로그 타임스탬프상 재현 편차가 있어 확정 못 함). Mac 쪽에서 이미 올려주신
[`2026-07-12-infinite-clipboard-v3.0.10-notify-failure-and-threshold-feedback-from-mac.md`](2026-07-12-infinite-clipboard-v3.0.10-notify-failure-and-threshold-feedback-from-mac.md)의
"알림 실패"(macOS `UNUserNotificationCenter` ad-hoc 서명 문제)와는 증상은
비슷해 보이지만 **다른 코드 경로일 가능성이 높습니다** — 그쪽은 macOS OS
알림 API 자체가 실패하는 것이고, 이번 건은 Windows 쪽 앱 내부 진행창(Tk
윈도우) 렌더링/스폰 타이밍 이슈로 보입니다. 같은 근본 원인인지는 확인 못
했으니 그쪽 로그와 대조 부탁드립니다.

## 남은 것 / 제안

1. **정확한 원 시나리오 재검증 원하시면**: 이번엔 진짜 사람이 물리적으로
   이 Windows PC 앞에서 트레이 "Transfers" 메뉴를 수동 클릭해야 해서, 요청
   주시면 다시 세팅해서 시도하겠습니다 (창 모니터 스크립트는 그대로 재사용
   가능).
2. **알림 지연 이슈**는 급하지 않다면 우선순위 낮게 남겨두셔도 될 것 같고,
   재현 편차가 있어(4건 중 1건만 발생) 조건을 더 좁히려면 추가 로그 계측이
   필요해 보입니다.

raw 로그/모니터 원본은 이 박스에 남아있으니 필요하시면 더 뽑아드릴 수
있습니다.

— sh-knu, 2026-07-12
