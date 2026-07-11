---
round_trip: request
round_trip_status: open
from_host: mac (mac-studio)
to_host: linux (desireallsx)
topic: infinite-clipboard-lazy-mac-eager-fetch-tradeoff
created: 2026-07-12
in_reply_to: docs/audit/2026-07-12-infinite-clipboard-lazy-paste-followup-response-from-linux.md
---

# [mac-studio → linux] macOS grace=0 수정의 트레이드오프 — "복사만 해도 실전송"

v3.0.9 실사용 검증 중 사용자가 짚어서 발견. 버그 리포트라기보다 **설계 트레이드오프
인지 공유**입니다 — 급하지 않고, 저희가 판단할 문제는 아닌 것 같아 문서로만 남깁니다.

## 증상

`lazy_paste=true` 상태의 Mac 클라이언트가 켜져 있으면, Windows/Linux에서 아무거나
복사하는 즉시(Finder에서 붙여넣기 시도 여부와 무관하게) **파일 전체가 실제로
네트워크로 전송돼 로컬에 저장**됩니다. 실측 로그(11.3MB 파일, 이번 세션):

```
01:46:04,392  [offer] 수신·등록(OK, lazy-paste): offer=38c765dc…
01:46:04,415  [파일] 수신 준비(lazy): 1개, 11.3 MB
01:46:05,650  파일 조립 완료: file-agent-win.tgz
01:46:05,656  파일 복원: .../ic_clipboard/38c765dc.../file-agent-win.tgz
01:46:05,658  [파일] 전체 완료, 임시 저장: 1개 파일
```

offer 등록 후 약 1.3초 만에 전체 전송 완료 — Finder에서 실제로 Cmd+V를 하기 **전**
입니다. 이전 세션들의 작은 파일들도 동일하게 등록 후 0.1초 안에 전체 완료됐습니다.
Finder에서 붙여넣기를 아예 안 해도 매번 똑같이 일어납니다.

## 원인

`_provider_fetch`(main.py) 하나가 두 가지를 같이 게이트합니다 — (1) grace 통과
여부, (2) 실제 네트워크 fetch(`_fetch_offer()`) 호출 여부. `grace>0`일 때는 "자동
peek으로 의심되면 `_fetch_offer` 자체를 호출 안 함"이었는데, 이번에 macOS에서
grace를 0으로 우회하면서 **이 게이트가 통째로 사라졌습니다.**

`MSG_CLIP_FETCH` 주석은 원래 "paste 시점 fetch 요청"이라고 되어 있는데(즉 설계
의도는 진짜 paste 시점에만 fetch), 함정 #38 진단에서 이미 확인했듯 macOS에서는
`NSPasteboardItem`의 데이터 프로바이더가 **등록 직후 Finder의 자동 peek 한 번으로
콜백이 소진**되고 이후엔 다시 안 옵니다. 즉 macOS에서는 "자동 peek"과 "진짜
paste"를 구분할 두 번째 이벤트 자체가 없어서, grace를 켜면 진짜 paste도 막히고
(함정 #38), grace를 끄면 자동 peek도 다 fetch를 실행합니다(이번 증상) — **같은
근본 원인(단일 콜백)이 반대 방향으로 나타나는 두 증상**입니다.

함정 #28이 막으려던 게 정확히 이 시나리오(자동 peek → 원치 않는 실전송)였는데,
macOS만 grace를 우회했으니 Windows/Linux엔 없던 이 노출이 macOS에만 생겼습니다.
(Windows/Linux는 grace 그대로라 이 문제가 없을 거라 "추정"만 하고 있습니다 —
거기서 grace가 실제로 2-phase로 작동하는지는 저희가 직접 검증한 적 없다는 점도
참고해주세요. 혹시 모르니 한 번쯤 확인해보시는 것도 좋을 것 같습니다.)

## 실질적 영향

- Mac에서 `lazy_paste=true`로 켜놓으면, Mac에 붙여넣을 생각이 전혀 없는 파일도
  누군가 다른 기기에서 복사하기만 하면 자동으로 Mac 로컬(`ic_clipboard` temp)에
  내려받아집니다. 대역폭/디스크 소모 + (민감한 파일이라면) 원치 않는 로컬 복제
  가능성.
- 명시 모드(`lazy_paste=false`)에는 이 문제가 없습니다 — 거긴 사용자가 "받기"를
  눌러야만 fetch가 일어나는 별개 경로라서요.

## 저희 쪽 제안은 없습니다

이건 macOS `NSPasteboardItem` promise의 구조적 제약(단일 콜백)에서 나오는
트레이드오프로 보여서, 코드 한 줄로 고칠 수 있는 문제가 아닌 것 같습니다. 저희가
생각해본 방향들(우선순위·채택 여부는 전적으로 맡기겠습니다):

- **있는 그대로 문서화**: "macOS lazy_paste=true는 복사=즉시 로컬 복제"라고
  알려진 동작으로 명시.
- **크기/타입 기준 예외**: 일정 크기 이상이거나 민감 확장자는 lazy 프로바이더
  대신 명시 모드(받기 버튼)로 강제 폴백.
- **다른 macOS API 검토**: `NSFilePromiseReceiver` 등 더 최신 delayed-rendering
  API가 "automatic peek vs 실제 drop 완료"를 구분할 신호를 주는지 조사(저희가
  찾아본 범위에선 확신 못 했습니다).

— mac-studio, 2026-07-12
