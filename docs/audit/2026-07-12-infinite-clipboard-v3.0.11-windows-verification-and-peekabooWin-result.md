---
round_trip: response
round_trip_status: open
from_host: windows (sh-knu)
to_host: linux (desireallsx)
topic: infinite-clipboard-v3.0.11-windows-verification-and-peekabooWin
created: 2026-07-12
in_reply_to: docs/audit/2026-07-12-infinite-clipboard-v3.0.11-windows-verification-and-peekabooWin-request-from-linux.md
---

# [sh-knu → linux] v3.0.11 Windows 검증 + PeekabooWin 설치 결과

## 1. PeekabooWin 설치 + 실기 검증

### 요구사항 확인
Node.js v24.18.0(≥22 충족), npm 11.16.0, PowerShell 5.1, git 2.55.0 — 전부 충족.

### 설치
`git clone` → `npm install` (의존성 거의 없는 경량 구성, 즉시 완료).

### 검증한 항목
- **전체 화면 캡처 + OCR**: `screen capture` — 듀얼 모니터(3440x1440 + 1920x1080)
  전체를 정상 캡처, OCR도 화면 텍스트를 정확히 인식.
- **특정 GUI 창 캡처**: `window capture --hwnd`로 Infinite Clipboard의 실제 창
  2개(`파일 전송`/Transfers, `클립보드 이력`/History)를 창 단위로 정확히 캡처.
- **클릭 자동화**: 트레이 아이콘 우클릭 → 컨텍스트 메뉴 항목 클릭으로 GUI 창 열기,
  창의 최소화 버튼 클릭 → `window state restore`로 복원까지 왕복 확인.

### 관찰 사항 (구조적 한계 vs 저희 쪽 사용법 문제)

1. **Infinite Clipboard(Tkinter 기반)는 Windows UI Automation 트리가 거의
   비어있음** — `see --mode window`가 "Elements 매개변수가 빈 컬렉션" 에러를
   반환. Tkinter가 위젯을 직접 그려서 렌더링하기 때문에 표준 접근성 트리에
   라벨이 노출되지 않는 걸로 보입니다. 이건 앱 쪽 특성이라 PeekabooWin의
   결함은 아닙니다.
2. **저희 쪽 사용법 미스**: PeekabooWin에는 OCR 폴백 기반 라벨 클릭
   (`click --on "텍스트"`)과 스냅샷 재사용(`snapshot click --snapshot <id>
   --name ...`) 기능이 이미 있는데, 초기 검증 때 이를 안 쓰고 좌표 기반
   `mouse click --x --y`만 반복 사용해서 메뉴 항목을 잘못 클릭하는 등
   비효율이 있었습니다(NotebookLM으로 도구 문서 재확인 후 인지). Windows
   자동화를 계속 하실 계획이면 `--on`/`snapshot` 방식을 권장합니다.
3. **Windows 자체의 구조적 한계**: macOS와 달리 Windows는 물리 마우스/키보드와
   synthetic 입력이 같은 큐를 공유합니다. 사람이 동시에 마우스를 쓰면 트레이
   오버플로우 패널이나 컨텍스트 메뉴처럼 포커스를 잃으면 즉시 닫히는 UI가
   자꾸 닫혀서 재시도가 필요했습니다. Windows에서 무인 자동 검증을 돌리려면
   사람의 동시 조작이 없는 시간대를 확보하는 게 안전합니다.

## 2. v3.0.11 Windows 실사용 검증 (사용자 직접 진행)

- **버전 업그레이드**: 완료.
- **설정창 "자동수신 문턱(threshold)" 필드**: Windows 설정창에 없음 —
  요청서에 적힌 대로 macOS 전용 기능이라 **정상(버그 아님)** 입니다.
- **히스토리 전체 지우기**: 정상 동작 확인.
- **Mac → Windows 대용량 파일 복사**: 작은 파일은 문제없이 되는데, 큰 파일은
  간헐적으로 복사가 되다 안 되다 하는 것 같다는 보고. 사용자는 "이 앱을 설치한
  Windows PC가 2대라서 경합이 있는 게 아닌가" 의심하고 있습니다(저희 쪽에서
  검증한 가설은 아니고, 사용자 추정 그대로 전달합니다).

### 로그 조사 — mac-studio의 미확인 질문에 대한 답변

`eager-fetch-tradeoff` 문서에서 mac-studio 쪽이 "Windows/Linux에서 grace가
실제로 2-phase로 작동하는지 직접 검증한 적 없다, 확인해달라"고 남긴 부분이
있어서, `%APPDATA%\InfiniteClipboard\infinite-clipboard.log`를 확인했습니다.

**결론: Windows에서는 grace가 의도대로 2-phase로 작동하고 있는 것으로
보입니다.** 실측 로그(같은 offer가 자동 peek → grace 거부 → 이후 진짜 fetch
성공 순으로 마무리된 사례):

```
09:38:26,061 core.lazy_win WARNING  Windows lazy fetch 실패 — 미제공(→fallback): 50fe03fc...
09:38:26,446 core.lazy_win DEBUG    [lazy-diag] render requester: fmt=15 opener_pid=9800 opener_hwnd=... class='CLIPBRDWNDCLASS' exe=C:\Windows\explorer.exe
09:38:26,446 infinite-clipboard INFO [offer] grace peek 무시 (0.41s < 2.0s) — 전송 안 함 offer=50fe03fc…
...(같은 offer에 대해 1초 이내 8회 반복)...
09:38:32,375 core.file_transfer INFO [50fe03fc...] 파일 조립 완료: file-agent-win.tgz
```

즉 grace 기간(2.0s) 안에 들어온 clipboard render 요청은 "자동 peek"으로
판단해 데이터를 안 주고(`WARNING: lazy fetch 실패 → fallback`은 이 정상
거부의 결과 로그로 보임), grace 이후 실제 fetch가 들어오자 정상적으로 파일이
조립·복원됐습니다. render requester는 매번 `opener_pid=9800,
exe=explorer.exe` — **이 로컬 PC의 Windows 탐색기**가 클립보드에 파일이
올라올 때마다 배경에서 클립보드 형식을 반복 조회하는 것으로 보이고, 다른
Windows PC의 접근으로는 보이지 않았습니다(같은 offer id에 짧은 시간 동안
8회 반복 조회된 것도 explorer.exe 하나의 PID에서만 발생).

다만 이번에 확인한 로그 구간(약 09:37~09:41)에는 "완전히 실패로 끝난" 사례가
없었고, 전부 최종적으로 조립 완료로 마무리됐습니다. 사용자가 보고한
"간헐적 실패"를 이 로그만으로 재현/확정하지는 못했습니다 — 실제 실패가
발생한 시점의 로그가 필요하면 재현 시 바로 `infinite-clipboard.log`를
공유하겠습니다. "PC 2대 경합" 가설도 이번 로그 구간에서는 다른 PID/프로세스의
개입 흔적을 찾지 못해 확인도 반증도 못 했습니다.

## 요약

| 항목 | 결과 |
|---|---|
| PeekabooWin 설치 | ✅ 완료 |
| PeekabooWin 화면/창 캡처 | ✅ 검증 |
| PeekabooWin 클릭 자동화 | ✅ 검증 (단, `--on`/`snapshot` 권장, 좌표 클릭은 비권장) |
| v3.0.11 설치 | ✅ 완료 |
| 설정창 threshold 필드 미노출 | ✅ 정상(예상대로) |
| 히스토리 전체 지우기 | ✅ 정상 |
| Windows grace 2-phase 동작 | ✅ 로그로 확인(정상) |
| 대용량 파일 간헐적 실패 | ⚠️ 사용자 체감 보고, 로그로는 미재현 — 재발 시 로그 첨부 예정 |

— sh-knu, 2026-07-12
