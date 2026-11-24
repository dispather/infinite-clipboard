---
round_trip: response
ball: project-manager
expects_reply: false
disposition: done-in-reply
title: "회신 — 분배 dlg-20261124-a13cd741 — Dependabot Pillow 13 + setuptools 84 (코드 커밋 08d41da)"
date: 2026-11-24
from: infinite-clipboard
from_host: linux-desktop
to: [project-manager]
msg_id: dlg-20261124-a13cd741-response
in_reply_to:
  - dlg-20261124-a13cd741
  - 2026-11-24-delegation-a13cd741-request-to-infinite-clipboard.md
  - 2026-11-24-delegation-a13cd741-request-from-project-manager.md
delegation_task_id: dlg-20261124-a13cd741
canonical: "infinite-clipboard:docs/audit/2026-11-24-delegation-a13cd741-response-to-project-manager.md"
task_ref: infinite-clipboard::dependabot-setuptools
---

# 회신 — Dependabot 경고 45건 해소 (Pillow 13.0.0 + setuptools 84.2.1)

작업 ID `dlg-20261124-a13cd741` / 원장 `infinite-clipboard::dependabot-setuptools` 의 condition 을 그대로 구현했다.
코드 커밋: **`08d41da`** (브랜치 `dispatch/dlg-20261124-a13cd741`, push 안 함). 되돌리려면 `git revert 08d41da`.

## 무엇을 바꿨나

| 파일 | 변경 |
|---|---|
| `pyproject.toml` | `pillow>=12.2.0` → `>=13.0.0`, `requires-python >=3.10` → `>=3.11` |
| `requirements.txt` | `pillow>=13.0.0` (Windows·Linux·mac 배포 빌드가 pip 으로 이 파일을 쓴다) |
| `.github/workflows/test.yml:105` | Windows 테스트 잡의 하드코딩 `"pillow>=12.2.0"` → `>=13.0.0` |
| `uv.lock` | pillow 12.3.0→13.0.0, setuptools 82.0.1→84.2.1. 3.10 전용 백포트 exceptiongroup·tomli·typing-extensions 가 빠짐(우리 코드 직접 import 0건 [실측 grep]) |
| `core/clipboard_manager.py` | PNG 저장 5곳을 `_image_to_png_bytes()` 하나로 모음 — 아래 «회귀 1건» |
| `tests/test_image_png_encoding.py` | 위 회귀를 막는 테스트 2개(신규) |
| `CONTRIBUTING.md`·`CONTRIBUTING.ko.md` | Python 3.10 → 3.11 (3곳씩) |

근거: 0건 주장에 쓴 명령과 결과(이 세션에서 실행)

```
grep -rn --include='*.py' -E '^\s*(import|from) (typing_extensions|tomli|exceptiongroup)' core ui tests main.py start.py config.py   → 0건 (rc=1)
grep -rn -iE 'sdist|python -m build|setup\.py' .github build packaging   → 0건
grep -n 'build-system' pyproject.toml   → 0건
```

## 무엇을 골랐고 왜 · 무엇을 포기했나

- **골랐다: `requires-python` 을 3.11 로 올림.** [실측] PyPI 에서 Pillow 13.0.0 의 `requires_python` 은 `>=3.11` 이다(릴리스 노트 «Pillow has dropped support for Python 3.10»). 모든 CI·빌드는 3.12 이상이라 배포물에는 영향이 없다 [실측 grep: `build.yml`·`test.yml` 의 `python-version: '3.12'`, `build_mac.sh` pyenv 3.12.13, Linux 빌드는 Arch 시스템 python].
- **포기했다: 마커로 나누는 안(`pillow>=13; python_version>='3.11'`).** 이렇게 하면 3.10 사용자를 남길 수 있지만 `uv.lock` 에 3.10 용 pillow 12.3.0 항목이 남는다. 그러면 Dependabot 경고가 열린 채로 남아 합격 관측 «open 0건»을 채우지 못한다 [추정 — Dependabot 이 lock 의 모든 해석 항목을 본다는 전제].
근거: 판단(명령 없음) — 마커 안은 실행하지 않았다.

## 회귀 1건 — Pillow 13 은 `I` 모드 이미지를 PNG 로 저장하지 못한다 (고침)

- [실측] Pillow 13.0.0 에서 32-bit 정수 TIFF 를 열면 `mode=I` 이고, PNG 저장은 `OSError: cannot write mode I as PNG` 로 실패한다. 12.3.0 에서는 DeprecationWarning 만 내고 16비트로 잘라 저장했다.
- 우리 경로: macOS 클립보드 TIFF 분기(`MacClipboard._get_image_from_clipboard`)가 이런 이미지를 받을 수 있다. 그러면 복사 감지 시 PNG 변환에서 예외가 나 그 클립보드 복사가 동기화되지 않는다.
- 조치: `_image_to_png_bytes()` 가 `mode == "I"` 이면 `convert("I;16")` 를 먼저 거쳐 12.x 와 같은 결과(16비트 잘라내기)를 낸다. 감지 쪽 4곳 + mac osascript 폴백의 임시 PNG 쓰기 1곳이 이 헬퍼를 쓴다.
- 16-bit PNG·16-bit TIFF 는 `I;16` 으로 열리고 원래부터 저장된다 [실측]. Windows DIB(BMP)·Linux `wl-paste/xclip -t image/png` 경로는 `I` 모드가 나오지 않는다 [추정 — 입력이 BMP/PNG].

## 검증

- **기준선**(Pillow 12.3.0, Python 3.14): `pytest` 619 passed · 76 skipped [실측].
- **업그레이드 후**(Pillow 13.0.0): Python 3.14 · 3.11.15 양쪽에서 **621 passed · 76 skipped** — 기존 619 + 새 테스트 2 [실측]. 3.11 은 uv 가 받은 managed 인터프리터와 임시 venv 로 돌렸다.
- **PyInstaller hidden imports smoke**: `import PIL.ImageTk, PIL._imagingtk, PIL._tkinter_finder, customtkinter` 이 Pillow 13 에서 성공 [실측]. 셋은 `build/infinite-clipboard.spec:79-81` 이 명시하는 모듈이다.
- **setuptools 82→84**: `uv tree --invert` 로 보면 의존 경로는 pyinstaller·pyinstaller-hooks-contrib 뿐이다(빌드 도구, 런타임 번들 아님) [실측]. 82.0.1·84.2.1 모두에서 pyinstaller 6.19.0 import 가 정상이다(`pkg_resources` 는 두 버전 모두 없음) [실측]. 83·84 changelog 의 제거 항목은 C 확장 컴파일·distutils API 다 [문서]. 이 레포는 C 확장을 빌드하지 않는다.
- **원장 note 의 [추정] 해소 — sdist 를 만드는가**: `.github`·`build`·`packaging` 에서 `sdist|python -m build|setup.py` 0건 [실측 grep], `pyproject.toml` 에 `[build-system]` 없음 [실측]. → sdist 를 만들지 않으므로 setuptools 취약점(MANIFEST.in 제외 우회)의 노출면은 없다.

## 정정 1건 — 요청서의 검증 명령

요청서는 `npm run lint`·`npm run typecheck` 를 지시했지만 이 레포는 Python(uv) 프로젝트이고 `package.json` 이 없다 [실측 `ls`]. pytest 전체 + import smoke + `I` 모드 실측으로 대신했다. PM 분배 템플릿이 JS 레포를 전제로 한 것으로 보인다 — 레포별 검증 명령을 원장 항목에서 가져오면 이런 어긋남이 없어진다.

## 종료 보고

done:
- Pillow 12.3.0→13.0.0, setuptools 82.0.1→84.2.1 — 커밋 `08d41da` (`uv.lock`·`pyproject.toml`·`requirements.txt`·`test.yml`)
- 13.0 API 변경과 우리 사용처 대조 → 회귀 1건(`I` 모드 PNG 저장) 발견·수정·테스트 — 커밋 `08d41da` (`core/clipboard_manager.py`, `tests/test_image_png_encoding.py`)
- 전체 pytest: Python 3.14·3.11.15 양쪽 621 passed · 76 skipped (기준선 619 + 신규 2)
- PyInstaller hidden imports 3종 import smoke 통과
- setuptools 의존 경로 확인(pyinstaller 전용) + sdist 미생성 확인 → 원장 note 의 [추정] 해소

leftover:
- 3 OS 빌드와 이미지 클립보드 실기 왕복 — 이 worktree 에서는 build 불가, 실기는 다른 호스트가 필요. Linux→mac 왕복에서 macOS TIFF 경로(`I` 모드 수정분)도 함께 본다. Windows 실기는 a5000(sh-knu 금지). · task_ref: infinite-clipboard::dependabot-pillow13-build-crosshost-verify
  ⛳착수: 머지 후 `gh workflow run build.yml` 로 3 OS 빌드 → 산출물 실행 → 이미지 복사 왕복 Linux→mac, Linux→Windows(a5000) 각 1회
- Dependabot open 0 관측 + 경고 #7 수동 dismiss — main push 뒤에만 관측할 수 있고 dismiss 는 GitHub 화면 조작(밖으로 나가는 일)이다. · task_ref: infinite-clipboard::dependabot-alerts-close-observe
  ⛳착수: main push 뒤 `gh api repos/dispather/infinite-clipboard/dependabot/alerts --jq '[.[]|select(.state=="open")]|length'` → 0 이 아니면 남은 번호 확인, #7(`infinite-clipboard/uv.lock`, 평탄화 전 경로)이 남았으면 GitHub 에서 dismiss

blocked:
- 없음

produced:
- 커밋 `08d41da8286bc64b608f95bd75299b036e903941` — 코드·lock·문서 8파일
- `docs/audit/2026-11-24-delegation-a13cd741-response-to-project-manager.md` (이 문서)

## 안 하기로 한 것

- `F` 모드(float) 이미지의 PNG 저장 실패는 고치지 않았다. [실측] Pillow 12.3.0 에서도 똑같이 `cannot write mode F as PNG` 로 실패한다. 이번 업그레이드의 회귀가 아니라 원래 있던 한계라서 이 작업(보안 업그레이드)의 범위 밖으로 둔다.

## 무응답 시 기본 동작

이 문서는 종결 보고다. 조율자가 위 4칸을 검사해 빠진 것이 없으면 회신하지 않아도 된다. 위 leftover 2건은 머지 후 조율자가 원장에 등록한다(우리 쪽 재확인 시점: 다음 `/inbox` 실행).
