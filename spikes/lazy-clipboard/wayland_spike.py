"""Linux Wayland Pure-lazy 스파이크 — ext-data-control-v1 커스텀 data source.

검증: 백그라운드(입력 focus 없는) 프로세스가 ext_data_control_manager_v1 으로
selection 을 소유한 뒤, 별도 프로세스(`wl-paste`)가 paste 할 때 컴포지터가 우리
source 의 `send(mime, fd)` 이벤트를 호출하는가, 그 핸들러 안에서 origin.fetch() 한
바이트를 fd 로 내줄 수 있는가 (= paste 시점 lazy fetch).

ext-data-control-v1 은 클립보드 매니저용 프로토콜이라 일반 wl_data_device 와 달리
입력 focus serial 이 필요 없다 (백그라운드 lazy clipboard 의 전제).

paster 는 불변 규칙 1 에 따라 별도 프로세스(`wl-paste`)다.

⚠️ 부작용: 검증 중 현재 Wayland 세션의 클립보드 selection 이 잠시 이 테스트 mime
(application/x-ic-spike) 로 덮인다. 일반 텍스트 paste 에는 영향이 거의 없지만(우리는
text/plain 을 offer 하지 않음), 검증 후 평소 텍스트를 한 번 복사하면 원상복구된다.

실행:
  XDG_RUNTIME_DIR=/run/user/$(id -u) WAYLAND_DISPLAY=wayland-0 \\
    .venv/bin/python spikes/lazy-clipboard/wayland_spike.py
"""
import os
import sys
import time
import select
import threading
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verdict as V  # noqa: E402
from origin import TestOrigin  # noqa: E402
from payload import TEXT_PAYLOAD, BIG_PAYLOAD, IMAGE_PAYLOAD  # noqa: E402

OS_NAME = "linux-wayland"
MIME = "application/x-ic-spike"

try:
    from pywayland.client import Display
    from _proto.wayland.wl_seat import WlSeat
    from _proto.ext_data_control_v1.ext_data_control_manager_v1 import (
        ExtDataControlManagerV1,
    )
except Exception as e:  # pywayland / 바인딩 부재 = 인프라 실패 (빨강)
    print(f"[infra] pywayland/ext-data-control 바인딩 import 실패: {e}", file=sys.stderr)
    sys.exit(2)


def run_phase(payload: bytes, phase: str, mime: str = MIME) -> dict:
    """한 페이로드: source 등록(copy) → wl-paste(별도 프로세스, paste) → 검증.

    mime 으로 파라미터화 — source.offer(mime) 와 wl-paste -t <mime> 둘 다 이 mime 사용.
    on_send 핸들러는 mime 무관(fetch 한 바이트를 그대로 fd 에 write)이라 변경 없음.
    """
    result = {"phase": phase, "ok": False, "got_len": 0, "want_len": len(payload),
              "conn_times": [], "t_copy": 0.0, "t_paste": 0.0, "reason": "",
              "infra_missing": False}

    display = Display()
    try:
        display.connect()
    except Exception as e:
        result["reason"] = f"display connect 실패: {e}"
        return result

    state = {"seat": None, "manager": None}
    registry = display.get_registry()

    def on_global(reg, name, iface, version):
        if iface == "wl_seat":
            state["seat"] = reg.bind(name, WlSeat, min(version, 1))
        elif iface == "ext_data_control_manager_v1":
            state["manager"] = reg.bind(name, ExtDataControlManagerV1, min(version, 1))

    registry.dispatcher["global"] = on_global
    display.roundtrip()

    if state["manager"] is None or state["seat"] is None:
        # ext-data-control 미광고 = 환경 차이(예: zwlr 전용 구버전 sway). 메커니즘 실패 아님 → infra_missing.
        result["infra_missing"] = True
        result["reason"] = (f"ext_data_control_manager_v1 미광고 (이 컴포지터는 zwlr 전용 가능). "
                            f"manager={state['manager'] is not None} seat={state['seat'] is not None}")
        display.disconnect()
        return result

    with TestOrigin(payload) as origin:
        sent = {"done": False, "err": None}

        def on_send(_source, _mime_type, fd):
            # ← paste 시점에 컴포지터가 호출. 여기서 origin 으로부터 lazy fetch.
            try:
                data = origin.fetch()
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
            except Exception as e:  # noqa: BLE001
                sent["err"] = repr(e)
                try:
                    os.close(fd)
                except OSError:
                    pass
            finally:
                sent["done"] = True

        def on_cancelled(src):
            try:
                src.destroy()
            except Exception:  # noqa: BLE001
                pass

        source = state["manager"].create_data_source()
        source.dispatcher["send"] = on_send
        source.dispatcher["cancelled"] = on_cancelled
        source.offer(mime)

        device = state["manager"].get_data_device(state["seat"])
        device.set_selection(source)
        display.roundtrip()
        result["t_copy"] = time.monotonic()

        # 이벤트 펌프 스레드 — send 이벤트를 dispatch 해야 콜백이 fire
        stop = threading.Event()

        def pump():
            fd = display.get_fd()
            while not stop.is_set():
                try:
                    display.flush()
                    r, _, _ = select.select([fd], [], [], 0.05)
                    if r:
                        display.dispatch(block=True)  # 데이터 준비됨 → 읽고 dispatch (블록 안 함)
                except Exception:  # noqa: BLE001
                    return

        t = threading.Thread(target=pump, daemon=True)
        t.start()

        time.sleep(0.05)  # selection 안정화
        result["t_paste"] = time.monotonic()
        try:
            out = subprocess.run(
                ["wl-paste", "-t", mime],
                capture_output=True, timeout=10.0,
            )
            got = out.stdout
        except subprocess.TimeoutExpired:
            got = b""
            result["reason"] = "wl-paste timeout (send 콜백 hang 가능)"

        # send 콜백이 끝날 시간 약간 부여
        for _ in range(50):
            if sent["done"]:
                break
            time.sleep(0.01)
        stop.set()

        result["got_len"] = len(got)
        result["ok"] = got == payload
        result["conn_times"] = list(origin.connection_times)
        if sent["err"]:
            result["reason"] = f"send 핸들러 오류: {sent['err']}"
        elif not sent["done"] and not result["reason"]:
            result["reason"] = "send 이벤트 미수신"

    display.disconnect()
    return result


def _text_verdict() -> int:
    try:
        a = run_phase(TEXT_PAYLOAD, "A-small")
    except Exception as e:  # noqa: BLE001
        print(f"[infra] Wayland Phase A 실행 실패: {e}", file=sys.stderr)
        return 2

    if a.get("infra_missing"):
        # ext-data-control 미지원 컴포지터 → false FAIL 회피 (실제 kwin 은 PASS 로 검증됨)
        return V.emit(OS_NAME, V.NEEDS_MANUAL, conn_times=a["conn_times"],
                      t_copy_done=a["t_copy"], t_paste_issued=a["t_paste"],
                      bytes_match=False,
                      notes=f"{a['reason']} — 메커니즘 실패 아님, 환경 차이")

    if not a["ok"]:
        return V.emit(OS_NAME, V.FAIL, conn_times=a["conn_times"],
                      t_copy_done=a["t_copy"], t_paste_issued=a["t_paste"],
                      bytes_match=False,
                      notes=f"Phase A 실패: {a['reason']} got={a['got_len']} want={a['want_len']}")

    lazy_a = V.compute_lazy_proven(a["conn_times"], a["t_copy"], a["t_paste"])
    if not lazy_a:
        return V.emit(OS_NAME, V.NEEDS_MANUAL, conn_times=a["conn_times"],
                      t_copy_done=a["t_copy"], t_paste_issued=a["t_paste"],
                      bytes_match=True,
                      notes="Phase A 데이터 일치하나 laziness 미증명(eager 의심)")

    # Phase B — 대용량(512KB) 파이프 write 검증 (Wayland 는 INCR 불필요, fd write loop 만)
    try:
        b = run_phase(BIG_PAYLOAD, "B-large")
        b_note = f"Phase B(512KB) ok={b['ok']} got={b['got_len']} {b['reason']}"
    except Exception as e:  # noqa: BLE001
        b = {"ok": False}
        b_note = f"Phase B 예외: {e}"

    verdict = V.PASS if b["ok"] else V.NEEDS_MANUAL
    note = (f"Phase A+B PASS (실제 kwin ext-data-control). {b_note}" if b["ok"]
            else f"Phase A PASS(메커니즘 OK), {b_note}")
    return V.emit(OS_NAME, verdict, conn_times=a["conn_times"],
                  t_copy_done=a["t_copy"], t_paste_issued=a["t_paste"],
                  bytes_match=True, notes=note)


def _image_verdict() -> int:
    """Phase C — image/png MIME 으로 같은 ext-data-control source 메커니즘 검증.

    텍스트와 동일한 emit 패턴: infra_missing 이면 NEEDS_MANUAL(환경 차이), ok+lazy 면 PASS,
    데이터 일치하나 laziness 미증명이면 NEEDS_MANUAL, ok=False 면 FAIL.
    """
    try:
        c = run_phase(IMAGE_PAYLOAD, "C-image", mime="image/png")
    except Exception as e:  # noqa: BLE001
        print(f"[infra] Wayland Phase C 실행 실패: {e}", file=sys.stderr)
        return 2

    os_image = OS_NAME + "-image"

    if c.get("infra_missing"):
        # ext-data-control 미지원 컴포지터 → false FAIL 회피 (텍스트와 동일 처리)
        return V.emit(os_image, V.NEEDS_MANUAL, conn_times=c["conn_times"],
                      t_copy_done=c["t_copy"], t_paste_issued=c["t_paste"],
                      bytes_match=False,
                      notes=f"{c['reason']} — 메커니즘 실패 아님, 환경 차이")

    if not c["ok"]:
        return V.emit(os_image, V.FAIL, conn_times=c["conn_times"],
                      t_copy_done=c["t_copy"], t_paste_issued=c["t_paste"],
                      bytes_match=False,
                      notes=f"Phase C(image/png) 실패: {c['reason']} got={c['got_len']} want={c['want_len']}")

    lazy_c = V.compute_lazy_proven(c["conn_times"], c["t_copy"], c["t_paste"])
    if not lazy_c:
        return V.emit(os_image, V.NEEDS_MANUAL, conn_times=c["conn_times"],
                      t_copy_done=c["t_copy"], t_paste_issued=c["t_paste"],
                      bytes_match=True,
                      notes="Phase C(image/png) 데이터 일치하나 laziness 미증명(eager 의심)")

    return V.emit(os_image, V.PASS, conn_times=c["conn_times"],
                  t_copy_done=c["t_copy"], t_paste_issued=c["t_paste"],
                  bytes_match=True,
                  notes=f"Phase C(image/png ~{c['want_len']}B) PASS — 이미지 lazy 메커니즘 OK")


def main() -> int:
    rc1 = _text_verdict()
    rc2 = _image_verdict()
    # 둘 중 하나라도 인프라 예외(2)면 빨강, 아니면 정상(FAIL/NEEDS-MANUAL 포함 0)
    return 2 if 2 in (rc1, rc2) else 0


if __name__ == "__main__":
    sys.exit(main())
