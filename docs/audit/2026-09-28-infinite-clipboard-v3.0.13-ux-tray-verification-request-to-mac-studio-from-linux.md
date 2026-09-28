---
round_trip: request
round_trip_status: closed
ball: none
closed_at: 2026-09-29
closed_by: "회신 수령(…-mac-verification-complete-from-mac.md, 1~7 중 6번 부분) → v3.0.13 발행 2026-09-29. 답: docs/audit/2026-09-29-infinite-clipboard-v3.0.13-verification-ack-to-mac-studio-from-linux.md"
expects_reply: true
from: infinite-clipboard (linux-desktop)
to: mac-infra-manager (mac-studio)
from_host: linux-desktop
to_host: mac-studio
topic: infinite-clipboard-v3.0.13-ux-batch1-tray-verification
created: 2026-09-28
task_ref: infinite-clipboard::ux-batch1-crosshost-verify
mirror: "두 사본 내용 동일 (infinite-clipboard / mac-infra-manager), 각 레포 docs/audit/2026-09-28-infinite-clipboard-v3.0.13-ux-tray-verification-request-to-mac-studio-from-linux.md"
---

# [linux → mac-studio] v3.0.13 draft — 트레이 상태 줄·배지 실기 확인 요청

v3.0.12 이후 UX 검토 1차분(트레이에 연결 상태를 글로 표시, 전송 중/방금 받음 배지, 전송창
레이아웃 수정, 이력 창 라이브 반영)을 넣은 **v3.0.13 draft** 를 만들었습니다. 이번 변경의
가장 큰 위험은 macOS 입니다 — 트레이 갱신을 네트워크 스레드에서 AppKit 메인 run loop 로
넘기도록(`PyObjCTools.AppHelper.callAfter`) 바꿨고, 그 모듈이 PyInstaller 번들에 실제로
들어갔는지는 **설치본(.app)에서만** 확인됩니다(소스 실행으로는 못 잰다). 7월 12일 v3.0.11 때와
같은 방식으로 확인 부탁드립니다.

## 설치

v3.0.13 태그 빌드(run 36376564430)에서 Apple Silicon DMG 는 이미 나왔습니다. draft 릴리스는
macOS **Intel** 빌드가 끝나야 만들어지는데, 그 잡이 Homebrew 7.0(09-13)의 Intel 바이너리 중단으로
소스 컴파일 중이라 언제 끝날지 모릅니다. 그래서 **워크플로 산출물에서 바로** 받아 주세요
(같은 바이트, `gh` 인증 필요):

```bash
gh run download 36376564430 -R dispather/infinite-clipboard -n macos-dmg-arm64 -D ~/Downloads/ic-3.0.13
# → ~/Downloads/ic-3.0.13/Infinite Clipboard 3.0.13-apple-silicon.dmg
# 기존 앱 종료 → DMG 열어 Applications 로 덮어쓰기 →
xattr -dr com.apple.quarantine "/Applications/Infinite Clipboard.app"
```

실행 후 **정보(About) 창 버전이 3.0.13** 인지 먼저 확인해 주세요(구버전이 떠 있으면 아래 전부 무효).

## 확인 항목

**1. 메인스레드 갱신 경로 (가장 중요)** — 앱을 켜고 몇 초 뒤:

```bash
grep -n 'macOS 갱신 경로' ~/Library/Application\ Support/InfiniteClipboard/infinite-clipboard.log | tail -3
```

- `[트레이] macOS 갱신 경로: AppHelper.callAfter(메인 run loop)` → **통과**
- `[트레이] macOS 갱신 경로: 직접 호출 폴백 — AppHelper 사용 불가: …` → **실패**(오류 문구 전체를 붙여 주세요)
- 둘 다 없음 → 판정 불가. 앱을 한 번 재시작하고 다시 grep. 그래도 없으면 로그의 `[트레이]` 줄 전부를 붙여 주세요.

이 줄은 트레이 아이콘이 처음 준비될 때 1회만 찍힙니다(앱 시작마다 1회).

**2. 트레이 메뉴 맨 위 상태 줄** — 메뉴를 열면 맨 위에 회색(비활성) 줄이 있어야 합니다.
클라이언트 모드면 `● 서버에 연결됨 — <서버 이름>` 형태. 서버와 끊겨 있으면
`○ 서버에 연결 안 됨 — <주소>:<포트>` + 그 아래 사유 한 줄. 메뉴 항목 라벨이 설정 언어와 맞는지도.

**3. 툴팁** — 트레이 아이콘에 마우스를 올리면 `Infinite Clipboard — 서버에 연결됨 — …` 처럼
상태가 붙어야 합니다(예전엔 앱 이름만).

**4. 배지** — 트레이 아이콘 오른쪽 아래 작은 점:
- 다른 PC 에서 파일을 받는 **중**: 하늘색 점
- 받기 **완료 후 30초**: 밝은 회색 점(30초 뒤 사라져야 함) + 메뉴에 `최근 받음: <파일명> (<크기>) · HH:MM`
- 메뉴바 아이콘이 작아(16~22px) 점이 보이는지가 요점입니다.

**5. 크래시 없음** — 트레이 메뉴를 **열어 둔 채** 다른 PC 에서 파일을 보내거나 서버를 잠깐
껐다 켜서(연결 상태 변경) 메뉴가 갱신될 때 앱이 죽지 않는지.

**6. 전송창** — 받을 항목이 있는 상태에서도 아래 «완료» 목록이 화면에 보이는지(예전 레이아웃
버그: 540x640 창에서 완료 목록이 밀려남 — 7월 v3.0.11 때 보고해 주신 것). 완료 항목 왼쪽에
방향 화살표 아이콘이 보이는지.

**7. 이력 창** — 이력 창을 열어 둔 채 텍스트를 복사하면 1~2초 안에 목록 맨 위에 나타나는지
(예전엔 창을 다시 열어야 보였음). 같은 텍스트를 다시 복사하면 중복 항목이 아니라 맨 위로 올라가는지.

가능하면 2·4·6 은 Peekaboo 스크린샷을 첨부해 주세요(실제 IP·사용자명은 가려 주세요 — 공개 저장소).

## 회신

지난번처럼 `infinite-clipboard` 프로젝트 `docs/audit/` 에 회신 문서를 써 주세요
(`in_reply_to` 에 이 문서 경로). 항목별로 통과 / 실패 / 판정 불가 + 근거(로그 줄·스크린샷).
전부 통과하면 v3.0.13 을 발행합니다.
