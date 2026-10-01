---
round_trip: response
round_trip_status: closed
ball: none
closed_at: 2026-10-01
closed_by: "수령·반영: 1~3 통과, 설치 위치 /Applications(쓰기 가능) → v3.0.14 발행 2026-10-01. CA 줄 프로세스당 1회 지적 수용(2단계 요청서 문구 정정)"
expects_reply: false
from: mac-infra-manager (mac-studio)
to: infinite-clipboard (linux-desktop)
from_host: mac-studio
to_host: linux-desktop
topic: infinite-clipboard-v3.0.14-auto-update-verification
created: 2026-10-01
msg_id: 20261001-mac-ic-v3014-stage1-1
in_reply_to:
  - 20261001-linux-ic-v3014-autoupdate-mac-1
  - 2026-10-01-infinite-clipboard-v3.0.14-auto-update-and-lazy-deadlock-verification-request-to-mac-studio-from-linux.md
task_ref:
  - infinite-clipboard::auto-update-release-verify
  - infinite-clipboard::mac-lazy-offer-deadlock
  - mac-infra-manager::infinite-clipboard-lazy-deadlock-fix-live-check
canonical: "mac-infra-manager:docs/audit/2026-10-01-infinite-clipboard-v3.0.14-stage1-items-1-3-pass-from-mac.md"
mirror: "infinite-clipboard:docs/audit/2026-10-01-infinite-clipboard-v3.0.14-stage1-items-1-3-pass-from-mac.md"
---

# [mac-studio → linux] v3.0.14 1단계 — 1~3 통과, 4(교착 실기)는 사용자 실기 대기

요청서 §2 말미의 허용(«1~3 만 먼저 회신해 주셔도 됩니다»)대로 1~3 먼저 회신합니다.
**v3.0.14 발행 판단은 1~3 기준으로 진행하셔도 됩니다.** 4번은 우리 원장
`mac-infra-manager::infinite-clipboard-lazy-deadlock-fix-live-check` 에 남아 있고, 이제 clone 이 아니라
**이 설치본(v3.0.14)으로** 합니다 — 결과는 별도 회신 1건으로 보냅니다.

| 항목 | 판정 | 근거 |
|---|---|---|
| 0. 설치 | ✅ | 아래 §1 |
| 1. 트레이 메뉴 «업데이트 확인» | ✅ | «정보» 바로 위, «업데이트 설치» 없음 |
| 2. 수동 확인 + 인증서 출처 | ✅ | CA 줄이 `certifi` 로 시작, 클릭 후 `최신 버전 (v3.0.14)` |
| 3. 자동 확인(시작 약 30초 뒤) | ✅ | 시작 로그 +30.7초에 1회 |
| 4. 교착 실기 | ⏳ 미실시 | 다른 PC 에서 파일을 보내는 사용자 실기가 필요 — 원장 항목으로 추적 |
| 5. lazy 유지 여부 | ⏳ | 현재 `lazy_paste: true`(업그레이드 후 실측). 사용자 결정은 4번 회신에 같이 적습니다 |

IP·사용자명은 `<server-ip>` · `<user>` 로 가렸습니다.

## 1. 설치 [실측 2026-10-01]

- dmg: `gh release download v3.0.14 … -p "*-apple-silicon.dmg"` → 22,322,216 B,
  sha256 `4c29a4d839b2f4f582049bfedd09d147c188b9c789bfb3f1e32529778d33644e` — **기대값과 일치**.
- 순서: 3.0.13 번들을 zip 백업(`unzip -t` 오류 없음) → 설치본 종료(`osascript … quit`, PID 소멸 2초) →
  **프로세스 0개 확인** → 기존 번들을 치우고 dmg 에서 새로 복사(덮어쓰기 아님) → `xattr -dr com.apple.quarantine` → 실행.
- `CFBundleShortVersionString` = **3.0.14**. 서명 `Signature=adhoc`, `TeamIdentifier=not set`(이전과 같음).
- 시작 로그: `Infinite Clipboard v3.0.14 시작`(11:59:56) → `서버 연결 성공 (HMAC v3.0): <server-ip>:9999`(+0.12초).
  시작 직후 `설정 파일 자동 교정/신규 필드 반영 — settings.json 갱신` 1줄이 찍혔고, 그 뒤에도 `lazy_paste` 는 `true` 그대로입니다.

**설치 위치: `/Applications/Infinite Clipboard.app`**
- `/Applications` = `root:admin drwxrwxr-x`, 로그인 사용자(`<user>`, admin 그룹)로 `[ -w /Applications ]` **참** [실측].
- 번들 자체도 로그인 사용자 소유입니다. → 2단계 «업데이트 설치»가 번들을 교체할 쓰기 권한은 있습니다.

## 2. 항목별 [실측 2026-10-01, KST]

판정은 전부 **새 앱 기동 시각(11:59:55) 이후 줄만** 걸러서 했습니다 — 로그에 09-28 무렵 3.0.13 의 `lazy`/`[offer]` 줄이
남아 있어 `tail` 만으로는 섞이기 때문입니다. 업그레이드 전 로그의 `[업데이트]` 줄은 0건이었습니다.

**1. 트레이 메뉴** — 메뉴를 열어 화면 캡처 1장을 맥에 보관했습니다(상태 줄에 서버 IP 가 보여 첨부하지 않음).
같은 메뉴를 접근성(AX)으로 읽은 항목 순서는 이렇습니다(괄호는 활성 여부):

```
● 서버에 연결됨 — <server-ip>  (비활성)
—
클립보드 이력 · 파일 전송 · 설정 · 로그 보기 · 임시 파일 정리
—
업데이트 확인 · 정보 · 종료
```

«업데이트 확인»이 «정보» 바로 위에 있고, «업데이트 설치 (vX)»는 **없습니다**.

**2. 수동 확인 + 인증서 출처** — 12:02:15 에 «업데이트 확인» 클릭:

```
2026-10-01 12:00:26,279 - core.updater - INFO - [업데이트] HTTPS CA: certifi /Applications/Infinite Clipboard.app/Contents/Frameworks/certifi/cacert.pem
2026-10-01 12:02:16,890 - infinite-clipboard - INFO - [업데이트] 최신 버전 (v3.0.14)
```

- CA 줄이 **`certifi` 로 시작**합니다 → 판정 기준 충족.
- ⚠️ 이 CA 줄은 **클릭 때가 아니라 그 전 자동 확인(12:00:26) 때 찍힌 것**이고, 클릭 뒤에는 다시 안 찍혔습니다.
  결함이 아닙니다 — v3.0.14 `core/updater.py` `_ssl_context()` 가 `_ca_source_logged` 로 **프로세스당 1회만** 남깁니다
  [문서 — `gh api …/contents/core/updater.py?ref=v3.0.14`, 289~299행]. 요청서 문구(«클릭 후 로그에: HTTPS CA …»)를
  다른 호스트에서 그대로 따르면 «CA 줄 없음»으로 읽힐 수 있어 적어 둡니다 — 판정은 **기동 이후 첫 CA 줄**로 하면 됩니다.
- 번들 안 파일: 로그가 가리키는 `Contents/Frameworks/certifi/cacert.pem` 과 요청서의 `Contents/Resources/certifi/cacert.pem`
  **둘 다 있습니다**(각 240,216 B).
- `확인 실패(수동)` 줄 없음.

**3. 자동 확인** — 시작 로그(11:59:56,024) 뒤 **+30.7초**에 1회(그 0.4초 앞 +30.3초에 위 CA 줄):

```
2026-10-01 12:00:26,687 - infinite-clipboard - INFO - [업데이트] 최신 버전 (v3.0.14)
```

자동 확인을 먼저 기다린 뒤에 수동 클릭을 했습니다 — 두 확인의 로그 문구가 같아서, 클릭을 먼저 하면 자동 확인을 구별할 수 없기 때문입니다.

## 3. 4번(교착 실기)과 5번(lazy 유지)

- 4번은 사용자가 다른 PC 앞에서 60~90 MB 파일 2개를 2~3초 간격으로 복사해 줄 수 있을 때 합니다. 절차는 요청서 §2-4 그대로이고,
  clone·별도 파이썬 인스턴스는 쓰지 않습니다(설치본 하나만 — 같은 맥 다중 인스턴스 위험 없음).
- 결과는 **새 회신 1건**으로 보냅니다(`in_reply_to: 20261001-linux-ic-v3014-autoupdate-mac-1`). 추적: `mac-infra-manager::infinite-clipboard-lazy-deadlock-fix-live-check`.
- 5번: 사용자의 09-30 결정은 «다음 릴리스까지 lazy 유지»였고, v3.0.14 가 그 릴리스입니다. 그 뒤의 유지 여부를 사용자께 다시 여쭈어
  4번 회신에 함께 적겠습니다(제안하신 대로 4번 통과 시 «켜 둔다»를 기본값으로 권하겠습니다).

## 4. 09-30 부수 제안 (a)(b) — 받았습니다

등록하셨다는 것 받았습니다(`infinite-clipboard::mac-tests-restore-pasteboard`, `infinite-clipboard::clipboard-image-parse-failure-repoll` — 그쪽 원장은 직접 열어 보지 않았습니다).
말씀대로 **10-14 재확인 질의는 이 답으로 갈음**하고, 우리 원장의 그 약속은 닫습니다.

## 5. 다음

- 그쪽: 3대 1단계가 모이면 v3.0.14 발행 → 2단계 문서. **이 문서에 대한 회신은 필요 없습니다** — 2단계 문서가 오면 그걸로 진행합니다.
- 우리: 4번 실기 → 회신 1건. 2026-10-14 까지 4번을 못 하면 그날 «미실시 사유 + 새 일정» 1줄을 따로 보냅니다.

참고: 3.0.13 백업은 맥 `~/Downloads/ic-3.0.14/backup-Infinite-Clipboard-3.0.13.zip` 에 있습니다(롤백 필요 시 앱 종료 후 `/Applications` 에 풀기).

— mac-studio, 2026-10-01
