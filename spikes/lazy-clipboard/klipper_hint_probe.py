"""klipper_hint_probe.py — '복사 즉시(지연) 전송' 버그의 ②번 수정안 실측 스파이크.

## 목적 (성공 기준)
받는 PC 의 lazy placeholder 에 `x-kde-passwordManagerHint` 힌트를 달면 Klipper(KDE
클립보드 매니저)가 **우리 offer 의 데이터 mime(text/uri-list 등)을 read 하지 않게** 되는가?
- 힌트가 데이터 read 자체를 막으면 (B) → 우리 lazy fetch 가 트리거 안 됨 → 버그 해결.
- 힌트가 히스토리 저장만 막고 데이터는 여전히 read 하면 (A) → 무효 → 다른 방향 필요.

## 어떻게 측정하나
이 프로세스가 ext-data-control-v1 data source 로 클립보드 selection 을 소유한 뒤,
**사용자는 아무것도 안 하고(복사/붙여넣기 금지) 그냥 지켜본다**. idle 상태에서 들어오는
모든 `send(mime, fd)` 는 사용자 paste 가 아니라 **매니저/DE 의 자동 peek** 이다.
프로덕션과 달리 네트워크 fetch 를 호출하지 않고 상수 바이트만 내주므로 안전하다.

  - control 모드(--hint 없음): 데이터 mime(text/uri-list)이 곧 요청됨 → 버그 재현 확인
  - 힌트 모드(--hint):        데이터 mime 이 **요청 안 되면** → (B) 확정 → ②안 유효

## 실행 (실제 KDE Plasma Wayland 세션에서)
  # 1) pywayland 설치된 venv 준비 (한 번만)
  python3 -m venv /tmp/icw_probe && /tmp/icw_probe/bin/pip install "pywayland>=0.4.17"
  # 2) 저장소 루트(infinite-clipboard/)에서 실행 — core/_wayland_proto 를 import 하므로
  cd /path/to/infinite-clipboard/infinite-clipboard
  /tmp/icw_probe/bin/python spikes/lazy-clipboard/klipper_hint_probe.py            # control
  /tmp/icw_probe/bin/python spikes/lazy-clipboard/klipper_hint_probe.py --hint     # 처방

⚠️ 부작용: 측정 동안 현재 selection 이 잠시 이 테스트 mime(text/uri-list 의 가짜 file://
URI)으로 덮인다. 측정 후 평소 텍스트를 한 번 복사하면 원상복구된다.
"""
import argparse
import os
import select
import sys
import time

# 저장소 루트(infinite-clipboard/)를 path 에 — 프로덕션과 동일한 vendored 바인딩 사용
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

from pywayland.client import Display  # noqa: E402
from core._wayland_proto.wayland import WlSeat  # noqa: E402
from core._wayland_proto.ext_data_control_v1 import ExtDataControlManagerV1  # noqa: E402

# 프로덕션 lazy_wayland.py 와 동일한 mime 집합
MIME_URILIST = "text/uri-list"
MIME_GNOME = "x-special/gnome-copied-files"
MIME_HINT = "x-kde-passwordManagerHint"   # KeePassXC 가 쓰는 그 힌트
HINT_VALUE = b"secret"

DATA_MIMES = {MIME_URILIST, MIME_GNOME}    # "데이터 read" 로 카운트할 mime
FAKE_URI = "file:///tmp/ic_klipper_probe_FAKE.txt"


def _serve(mime: str) -> bytes:
    """요청 mime 에 맞는 상수 바이트 (fetch 콜백 호출 안 함 — 안전)."""
    if mime == MIME_HINT:
        return HINT_VALUE
    if mime == MIME_URILIST:
        return (FAKE_URI + "\r\n").encode()
    if mime == MIME_GNOME:
        return ("copy\n" + FAKE_URI).encode()
    return b""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hint", action="store_true",
                    help="offer 에 x-kde-passwordManagerHint 추가 (처방 모드)")
    ap.add_argument("--seconds", type=float, default=25.0,
                    help="측정 시간(초). 기본 25")
    ap.add_argument("--self-paste", action="store_true",
                    help="측정 중간(기본 t=8s)에 wl-paste 로 직접 데이터 mime 을 읽어 "
                         "'진짜 paste 는 힌트가 있어도 데이터를 받는가' 확인")
    args = ap.parse_args()
    PASTE_AT = 8.0  # self-paste 시 데이터 mime 을 요청하는 시각 (이전은 idle 로 집계)

    if not os.environ.get("WAYLAND_DISPLAY"):
        print("✗ WAYLAND_DISPLAY 미설정 — 실제 Wayland 세션에서 실행하세요.")
        return 2

    offered = [MIME_URILIST, MIME_GNOME] + ([MIME_HINT] if args.hint else [])
    events = []  # (elapsed_sec, mime)

    dpy = Display()
    dpy.connect()
    state = {"seat": None, "manager": None}
    registry = dpy.get_registry()

    def on_global(reg, name, iface, version):
        if iface == "wl_seat":
            state["seat"] = reg.bind(name, WlSeat, min(version, 1))
        elif iface == "ext_data_control_manager_v1":
            state["manager"] = reg.bind(name, ExtDataControlManagerV1, min(version, 1))

    registry.dispatcher["global"] = on_global
    dpy.roundtrip()
    if not state["seat"] or not state["manager"]:
        print("✗ ext_data_control_manager_v1 미광고 — 이 컴포지터는 미지원 "
              f"(manager={state['manager'] is not None} seat={state['seat'] is not None})")
        return 2

    device = state["manager"].get_data_device(state["seat"])
    source = state["manager"].create_data_source()
    t0 = time.monotonic()

    def on_send(_src, mime, fd):
        elapsed = time.monotonic() - t0
        events.append((elapsed, mime))
        tag = "DATA" if mime in DATA_MIMES else ("HINT" if mime == MIME_HINT else "?")
        print(f"  [{elapsed:6.2f}s] send  mime={mime:<32} ({tag})")
        try:
            os.write(fd, _serve(mime))
        finally:
            os.close(fd)

    def on_cancelled(_src):
        elapsed = time.monotonic() - t0
        print(f"  [{elapsed:6.2f}s] cancelled (다른 source 가 selection 가져감)")

    source.dispatcher["send"] = on_send
    source.dispatcher["cancelled"] = on_cancelled
    for m in offered:
        source.offer(m)
    device.set_selection(source)
    dpy.roundtrip()

    mode = "HINT(처방: x-kde-passwordManagerHint 포함)" if args.hint else "CONTROL(힌트 없음)"
    print(f"\n=== klipper hint probe — {mode} ===")
    print(f"offer mime: {offered}")
    print(f"⏱  {args.seconds:.0f}초 동안 측정 — 지금부터 **복사/붙여넣기 하지 말고** 기다리세요.")
    print(f"   (idle 중 들어오는 send = 매니저/DE 의 자동 peek)\n")

    import subprocess
    fd = dpy.get_fd()
    paste_proc = None
    paste_started = False
    while time.monotonic() - t0 < args.seconds:
        try:
            el = time.monotonic() - t0
            if args.self_paste and not paste_started and el >= PASTE_AT:
                paste_started = True
                print(f"  [{el:6.2f}s] (self-paste) wl-paste -t text/uri-list 실행 "
                      "— 진짜 paste 모사")
                paste_proc = subprocess.Popen(
                    ["wl-paste", "-t", MIME_URILIST],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )
            dpy.flush()
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                dpy.dispatch(block=True)
        except Exception as e:
            print(f"  (dispatch 예외 무시: {e})")
            break

    # self-paste 결과 수거 (진짜 paste 가 데이터를 받았는가)
    paste_ok = None
    if paste_proc is not None:
        try:
            out, _ = paste_proc.communicate(timeout=5)
            paste_ok = FAKE_URI in out.decode("utf-8", "replace")
        except Exception:
            paste_ok = False

    # idle peek 집계 — self-paste 모드면 PASTE_AT 이전 read 만 "매니저 자동 peek"
    idle_cutoff = PASTE_AT if args.self_paste else args.seconds
    idle_data = [(t, m) for (t, m) in events if m in DATA_MIMES and t < idle_cutoff]
    data_hits = [(t, m) for (t, m) in events if m in DATA_MIMES]
    hint_hits = [(t, m) for (t, m) in events if m == MIME_HINT]

    print("\n=== 판정 ===")
    print(f"idle 데이터 read 횟수 : {len(idle_data)}  {[f'{t:.2f}s/{m}' for t, m in idle_data]}")
    print(f"힌트 mime read 횟수   : {len(hint_hits)}  {[f'{t:.2f}s' for t, _ in hint_hits]}")
    if paste_ok is not None:
        print(f"self-paste(진짜 paste): {'✅ 데이터 받음' if paste_ok else '❌ 데이터 못 받음'} "
              f"(전체 데이터 read {len(data_hits)}회)")
    if args.hint:
        if not idle_data:
            print("\n✅ (B) 확정 — 힌트 모드에서 idle 데이터 read 0회.")
            print("   → Klipper 가 데이터 mime 을 읽지 않음 → ②안 유효 (lazy fetch 트리거 안 됨).")
            if paste_ok:
                print("   → 그러면서도 self-paste 는 데이터를 받음 = 진짜 paste 정상. 완벽.")
        else:
            print("\n❌ (A) — 힌트가 있어도 idle 중 데이터 mime 이 read 됨.")
            print("   → 히스토리 저장만 막고 read 는 그대로 → ②안 무효, ③안(받기 fallback)으로.")
    else:
        if idle_data:
            print("\n☑ control 에서 데이터 read 발생 = 버그 재현 확인 "
                  "(이제 --hint 로 재실행해 비교).")
        else:
            print("\n⚠ control 에서도 데이터 read 0회 — 이 세션에 Klipper 가 안 떠 있거나 "
                  "peek 안 함. (klipper 활성 여부 확인 필요)")

    try:
        source.destroy()
        dpy.disconnect()
    except Exception:
        pass
    print("\n(측정 종료 — 평소 텍스트를 한 번 복사하면 클립보드 원상복구)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
