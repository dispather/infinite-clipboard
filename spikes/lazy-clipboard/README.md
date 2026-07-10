# Pure Lazy Clipboard — 기술 검증 스파이크 (throwaway PoC)

> ⚠️ 이건 **버릴 코드(throwaway PoC)** 입니다. 프로덕션이 아닙니다.
> `core/`, `ui/` 등 프로덕션 코드와 **import 단절** — 여기서 프로덕션을 import 하지도, 프로덕션이 여기를 import 하지도 않습니다.

## 목적

단 하나의 질문을 OS별로 검증합니다 — **(A) 메커니즘**:

> "OS 가 paste 시점에 우리의 lazy/delayed/promise provide 콜백을 호출하고,
> 그 콜백 안에서 loopback 네트워크로 받아온 바이트를 내줄 수 있는가?"

이게 가능해야 "복사하면 메타데이터만 알리고, 다른 PC 가 실제 Ctrl+V 할 때만 데이터가 흐르는"
Pure lazy clipboard 모델이 성립합니다. (배경: `docs/file-transfer-copy-audit-2026-05-02.md`,
설계: `docs/plans/2026-05-29-v3-lazy-clipboard-spike-design.md`)

CI 로 검증 **불가**한 것 — (B) 실제 Finder/Dolphin/Explorer 에서의 paste UX — 는
PASS 가 난 OS 에 한해 별도 라운드에서 실기기 수동 검증합니다.

## 판정 (verdict)

각 OS 스크립트는 stdout 마지막 줄에 1줄 JSON 을 출력합니다:

```json
{"os":"linux-x11","verdict":"PASS","bytes_match":true,"lazy_proven":true,"notes":"...","raw":{...}}
```

- `PASS` — paste 시점 콜백 발화 + 바이트 일치 + laziness 증명. Pure lazy 후보 → 실기기 (B) 검증으로.
- `NEEDS-MANUAL` — 메커니즘은 되나 CI 자동화 불가(예: macOS file promise 는 Finder 필요).
- `FAIL` — OS 가 lazy 콜백을 안 부름 → 제품 설계에서 "받기 버튼" fallback 확정.

**exit code 규칙:** 인프라가 정상 실행되면 verdict 와 무관하게 `0` (FAIL 은 유용한 데이터지 CI 에러가 아님).
deps 부재 / import 실패 / 컴포지터 미기동 같은 **진짜 크래시만 `!= 0`**(빨강).

## 직접 실행

각 스크립트는 sibling 모듈(`origin`/`verdict`/`payload`)을 같은 디렉토리에서 import 하므로,
**스크립트 경로를 직접 지정**해 실행하면 됩니다 (디렉토리명 하이픈 무관):

```bash
# Linux X11 (디스플레이 필요 — Xvfb 또는 실제 X 세션)
python spikes/lazy-clipboard/x11_spike.py

# Linux Wayland (실제 Wayland 세션 또는 headless sway/weston)
python spikes/lazy-clipboard/wayland_spike.py

# Windows / macOS — 보통 CI(.github/workflows/spike-lazy-clipboard.yml)에서 실행
```

> 참고: `paster`(붙여넣기를 트리거하는 주체)는 반드시 clipboard owner 와 **다른 프로세스**여야
> 합니다. 같은 프로세스 readback 은 OS 가 실제 cross-app 라운드트립을 short-circuit 해 **거짓 PASS** 를
> 만들 수 있습니다. X11/Wayland 는 `xclip`/`wl-paste`, Windows/macOS 는 자식 `*_paster.py` 사용.

## 파일

| 파일 | 역할 |
|------|------|
| `payload.py` | 테스트 페이로드 (작은 텍스트 + 512KB — INCR/HGLOBAL 경계 초과) |
| `origin.py` | 원격 PC 흉내 loopback TCP 서버. 연결 시각 기록(laziness 증명) |
| `verdict.py` | 판정 계약 + 반증가능 `lazy_proven` 계산 |
| `x11_spike.py` | Linux X11 — python-xlib selection owner + INCR |
| `wayland_spike.py` | Linux Wayland — pywayland 커스텀 data source |
| `windows_spike.py` + `windows_paster.py` | Windows — CF_HDROP/CF_UNICODETEXT delayed render |
| `macos_spike.py` + `macos_paster.py` | macOS — NSPasteboard provider + file promise probe |
