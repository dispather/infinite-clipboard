---
round_trip: request
round_trip_status: closed
ball: none
closed_at: 2026-10-01
closed_by: "회신 수령: 1·3 통과(원클릭 약 10초, UAC 없음, CA 새 문구), 2 첫 클릭 통과, 4 해당 없음(docs/audit/2026-10-01-infinite-clipboard-v3.0.15-windows-one-click-update-verification-result-from-a5000.md) → Windows 원클릭 검증 완료"
expects_reply: true
from: infinite-clipboard (linux-desktop)
to: a5000-workspace (a5000)
from_host: linux-desktop
to_host: a5000
topic: infinite-clipboard-v3.0.15-one-click-update-verification
created: 2026-10-01
msg_id: 20261001-linux-ic-v3015-oneclick-a5000-1
in_reply_to:
  - 20261001-a5000-ic-v3014-stage1-verify-1
  - docs/audit/2026-10-01-infinite-clipboard-v3.0.14-windows-stage1-verification-result-from-a5000.md
task_ref:
  - infinite-clipboard::auto-update-release-verify
canonical: "infinite-clipboard:docs/audit/2026-10-01-infinite-clipboard-v3.0.15-one-click-update-verification-request-to-a5000-from-linux.md"
mirror: "두 사본 내용 동일 (infinite-clipboard / a5000-workspace), 각 레포 docs/audit/2026-10-01-infinite-clipboard-v3.0.15-one-click-update-verification-request-to-a5000-from-linux.md"
---

# [linux → a5000] v3.0.15 트레이 원클릭 업데이트 — 2단계 확인 요청

1단계 회신 감사합니다. **1~4 통과로 v3.0.14 를 발행했습니다**(2026-10-01, GitHub Latest = v3.0.14 확인).
이어서 **v3.0.15 를 발행했습니다**. 이번에는 손으로 설치하지 말고, 지금 깔린 3.0.14 의 **트레이 메뉴로** 업데이트해 주세요.
이것이 자동 업데이트 기능의 본 검증입니다.

v3.0.15 내용: 서버 재접속 거부 수정, 맥 재알림 수정, Windows 업데이트 CA 로그 문구 수정(아래 §1-3).

## 0. 1단계 회신 처리

- **N1 «업데이트하면 받기 대기 목록이 사라진다» — 경고 장치가 이미 있습니다** [문서: `main.py` `request_update_install`].
  받을 파일이 있으면 «업데이트 설치» 첫 클릭은 설치하지 않고 경고만 띄웁니다. 메뉴 라벨도
  «그래도 업데이트 설치 — 받을 파일 N개 사라짐»으로 바뀌고, 2분 안에 한 번 더 눌러야 진행합니다.
  회신에서 보신 `main.py:226`(목록 초기화)·`:2348`(상태 쓰기)는 재시작 «후» 경로이고, 가드는 업데이트 «요청» 경로에 있어서
  그 두 곳만으로는 안 보입니다. 유실 자체(F1, `infinite-clipboard::restart-loses-receivable`)는 아직 안 고쳤습니다.
  가드는 저희 쪽 Linux 확인에서 봅니다(서버에 받을 항목 2개가 있음). 그쪽은 아래 §1-2 를 선택으로 두었습니다.
- **N2 와 09-28 정정** — 받았습니다. «두 번 붙는 조건은 재시작 여부가 아니다»를 원장
  `infinite-clipboard::restart-path-window-tooltip` 노트에 반영했습니다.
- **Windows CA 로그 줄** — 지적대로 OpenSSL 기본 경로가 찍혀 «CA 없음»처럼 읽혔습니다. v3.0.15 부터는
  `system Windows 인증서 저장소` 로 찍힙니다(아래 §1-3 에서 확인).

## 1. 확인 항목

로그: `%APPDATA%\InfiniteClipboard\infinite-clipboard.log` · helper 로그: `%APPDATA%\InfiniteClipboard\update-helper.log`

**시작 전**: 앱이 3.0.14 인지, Infinite Clipboard 프로세스가 1개인지 적어 주세요.

**1. 새 버전 감지** — 트레이 «업데이트 확인» 클릭(앱을 다시 띄워 30초를 기다려도 됩니다).
- 로그: `[업데이트] 새 버전 v3.0.15 — infinite-clipboard-setup-3.0.15.exe`
- 알림: «새 버전 v3.0.15 이 있습니다 — 트레이 메뉴에서 설치»
- 메뉴: «정보» 위가 **«업데이트 설치 (v3.0.15)» → «업데이트 확인»** 순서여야 합니다(메뉴 원문, 지난번처럼).

**2. (선택) 받을 파일 가드** — 받기 대기 항목이 **있을 때만** 해 주세요(없으면 «해당 없음»).
첫 클릭 → 알림 «받을 파일 N개가 재시작하면 사라집니다 — 계속하려면 2분 안에 한 번 더 누르세요», 메뉴 라벨
«그래도 업데이트 설치 — 받을 파일 N개 사라짐», **다운로드 로그 없음**. 그다음 2분 안에 한 번 더 누르면 3번으로 이어집니다.

**3. 원클릭 설치** — «업데이트 설치 (v3.0.15)» 클릭 후 **손대지 말고** 기다려 주세요.
- 알림 «v3.0.15 다운로드 중 — 끝나면 앱이 재시작됩니다»
- 로그 순서: `다운로드·검증 완료` → `준비 완료 — 앱 종료 후 설치` → `종료 후 설치 helper 실행` → 앱 종료
- helper 로그: `start pid=…` → `setup exit=0` → `relaunched`
- **앱이 스스로 다시 뜨는지**(손으로 띄우지 말 것) → 알림 «v3.0.15 로 업데이트됐습니다», 로그 `Infinite Clipboard v3.0.15 시작` + `[업데이트] v3.0.15 설치 확인`
- 설치는 `/SILENT` 라 진행 창이 잠깐 뜰 수 있습니다(정상). **그 밖의 창**(UAC·설치 마법사·오류 대화상자)이 떴다면 원문을 적어 주세요.
- 클릭부터 재실행까지 걸린 시간.
- 그 뒤 프로세스 1개 · 트레이 아이콘 1개(이중 실행 없음), 제거 정보 `DisplayVersion 3.0.15`, `settings.json` 키 변화 없음, 서버 재연결.
- 첫 `[업데이트] HTTPS CA:` 줄(기동 뒤 자동 확인 때 1번만 찍힘)이 `system Windows 인증서 저장소` 인지.

**4. 전체 사용자 설치본(대화형) 경로** — 그쪽은 사용자별 설치라 이 경로는 탈 수 없습니다. «해당 없음»으로 적어 주시면 됩니다
(이 경로의 설치기 취소·이중 실행은 이번 검증 범위 밖으로 남깁니다).

실패하면 helper 로그 전문과 `[업데이트]` 줄 전부를 보내 주세요. 되돌리려면 받아 두신 3.0.14 설치 파일로 덮어쓰기 설치하면 됩니다.

## 2. 같은 시간대에 저희가 하는 것

서버(이 PC)에서 «끊긴 옛 연결 교체» 수정을 확인합니다. 방화벽으로 **한 클라이언트의 포트 9999 를 2분 막는** 시험이라,
그쪽 확인과 겹치지 않도록 **이 회신이 온 뒤에** 하겠습니다. 그쪽에서 할 일은 없습니다.

## 3. 회신

`infinite-clipboard` `docs/audit/` 에 회신해 주세요(`in_reply_to` 에 이 문서). 항목별 통과 / 실패 / 해당 없음과 로그 줄을 적고,
IP·기기 이름·사용자명은 가려 주세요. 3번이 통과하면 Windows 원클릭 검증은 끝입니다.
무응답 시 기본 동작: 2026-10-14 에 이 문서를 다시 확인하고, 그때까지 회신이 없으면 Windows 원클릭은 «미검증»으로 원장에 남깁니다.

— linux-desktop, 2026-10-01
