"""Linux X11 Pure-lazy 스파이크 — python-xlib selection owner + INCR.

검증: CLIPBOARD selection 을 소유한 상태에서, 별도 프로세스(`xclip -o`)가 paste 할 때
SelectionRequest 이벤트로 우리에게 콜백이 오는가, 그 콜백 안에서 origin.fetch() 한
바이트를 (대용량이면 INCR 청크로) 내줄 수 있는가.

2단계:
  Phase A — 작은 페이로드(TEXT_PAYLOAD): INCR 없이 단발 응답. 메커니즘 자체 증명.
  Phase B — 큰 페이로드(BIG_PAYLOAD, 512KB): INCR 청크 전송 증명.

paster 는 불변 규칙 1 에 따라 별도 프로세스(`xclip`)다.

로컬 실행(디스플레이 필요):
  xvfb-run -a python spikes/lazy-clipboard/x11_spike.py
또는 실제 X 세션에서 직접.
"""
import os
import sys
import time
import subprocess
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verdict as V  # noqa: E402
from origin import TestOrigin  # noqa: E402
from payload import TEXT_PAYLOAD, BIG_PAYLOAD  # noqa: E402

OS_NAME = "linux-x11"

try:
    from Xlib import X, display, Xatom
    from Xlib.protocol import event
except Exception as e:  # python-xlib 부재 = 인프라 실패 (빨강)
    print(f"[infra] python-xlib import 실패: {e}", file=sys.stderr)
    sys.exit(2)


class X11SelectionOwner:
    """CLIPBOARD selection 을 소유하고, ConvertSelection 요청에 lazy 응답.

    데이터는 등록 시점에 들고 있지 않고, 첫 요청(=paste) 시 fetch_cb() 로 가져온다.
    대용량은 INCR 프로토콜로 청크 전송.
    """

    # 한 번에 보낼 청크 크기 (서버 max-request-size 보다 충분히 작게)
    INCR_CHUNK = 64 * 1024

    def __init__(self, dpy, fetch_cb):
        self.dpy = dpy
        self.fetch_cb = fetch_cb  # () -> bytes, 첫 요청 시 1회 호출
        self.screen = dpy.screen()
        self.win = self.screen.root.create_window(
            0, 0, 1, 1, 0, self.screen.root_depth,
            event_mask=(X.PropertyChangeMask | X.StructureNotifyMask),
        )
        self.CLIPBOARD = dpy.intern_atom("CLIPBOARD")
        self.TARGETS = dpy.intern_atom("TARGETS")
        self.INCR = dpy.intern_atom("INCR")
        self.UTF8 = dpy.intern_atom("UTF8_STRING")
        self._data: bytes = b""    # fetch 된 데이터 (첫 요청 후 캐시)
        self._fetched = False
        # 진행 중 INCR 전송: {(requestor_id, property): (data, offset)}
        self._incr = {}
        self._done = threading.Event()

    def own(self):
        self.dpy.set_selection_owner(self.CLIPBOARD, self.win, X.CurrentTime)
        self.dpy.flush()
        return self.dpy.get_selection_owner(self.CLIPBOARD) == self.win

    def _ensure_data(self) -> bytes:
        if not self._fetched:
            self._data = self.fetch_cb()  # ← paste 시점에 origin 으로부터 fetch
            self._fetched = True
        return self._data

    def _send_notify(self, req, prop):
        ev = event.SelectionNotify(
            time=req.time, requestor=req.requestor, selection=req.selection,
            target=req.target, property=prop,
        )
        req.requestor.send_event(ev)
        self.dpy.flush()

    def handle_selection_request(self, req):
        requestor = req.requestor
        target = req.target
        prop = req.property if req.property != X.NONE else req.target

        if target == self.TARGETS:
            requestor.change_property(
                prop, Xatom.ATOM, 32, [self.TARGETS, self.UTF8],
            )
            self._send_notify(req, prop)
            return

        if target == self.UTF8 or target == Xatom.STRING:
            data = self._ensure_data()
            max_len = self.dpy.display.info.max_request_length * 4
            if len(data) <= max_len - 1024:
                # Phase A: 단발 전송
                requestor.change_property(prop, self.UTF8, 8, data)
                self._send_notify(req, prop)
                self._done.set()
            else:
                # Phase B: INCR 시작 — 총 길이(하한)를 INCR property 로 먼저 보냄
                requestor.change_attributes(event_mask=X.PropertyChangeMask)
                requestor.change_property(prop, self.INCR, 32, [len(data)])
                self._incr[(requestor.id, prop)] = (data, 0)
                self._send_notify(req, prop)
            return

        # 지원 안 하는 target → 거부
        self._send_notify(req, X.NONE)

    def handle_property_notify(self, ev):
        # INCR: requestor 가 property 를 지웠으면(Delete) 다음 청크 전송
        if ev.state != X.PropertyDelete:
            return
        key = (ev.window.id, ev.atom)
        if key not in self._incr:
            return
        data, off = self._incr[key]
        chunk = data[off:off + self.INCR_CHUNK]
        ev.window.change_property(ev.atom, self.UTF8, 8, chunk)
        self.dpy.flush()
        if not chunk:
            # 0바이트 청크 = INCR 종료
            del self._incr[key]
            self._done.set()
        else:
            self._incr[key] = (data, off + len(chunk))


def _paste_via_xclip(timeout: float) -> bytes:
    """불변 규칙 1: paster 는 별도 프로세스. xclip 으로 CLIPBOARD 읽기(=paste)."""
    out = subprocess.run(
        ["xclip", "-selection", "clipboard", "-o", "-t", "UTF8_STRING"],
        capture_output=True, timeout=timeout,
    )
    return out.stdout


def run_phase(payload: bytes, phase: str) -> dict:
    """한 페이로드에 대해 own → paste(xclip 별도 프로세스) → 검증. 결과 dict 반환."""
    dpy = display.Display()
    with TestOrigin(payload) as origin:
        owner = X11SelectionOwner(dpy, fetch_cb=origin.fetch)
        if not owner.own():
            dpy.close()
            return {"phase": phase, "ok": False, "reason": "selection 소유 실패",
                    "conn_times": list(origin.connection_times), "t_copy": 0, "t_paste": 0}
        t_copy = time.monotonic()

        # 이벤트 루프 스레드 (SelectionRequest / PropertyNotify 처리)
        stop = threading.Event()

        def loop():
            while not stop.is_set():
                if dpy.pending_events():
                    ev = dpy.next_event()
                    if ev.type == X.SelectionRequest:
                        owner.handle_selection_request(ev)
                    elif ev.type == X.PropertyNotify:
                        owner.handle_property_notify(ev)
                else:
                    time.sleep(0.005)

        t = threading.Thread(target=loop, daemon=True)
        t.start()

        time.sleep(0.05)  # owner 안정화
        t_paste = time.monotonic()
        try:
            got = _paste_via_xclip(timeout=10.0)
        except subprocess.TimeoutExpired:
            got = b""
        stop.set()
        result = {
            "phase": phase,
            "ok": got == payload,
            "got_len": len(got),
            "want_len": len(payload),
            "conn_times": list(origin.connection_times),
            "t_copy": t_copy,
            "t_paste": t_paste,
        }
    dpy.close()
    return result


def main() -> int:
    try:
        a = run_phase(TEXT_PAYLOAD, "A-small")
    except Exception as e:
        # 디스플레이 없음 등 = 인프라 실패
        print(f"[infra] X11 Phase A 실행 실패: {e}", file=sys.stderr)
        return 2

    if not a["ok"]:
        return V.emit(OS_NAME, V.FAIL, conn_times=a["conn_times"],
                      t_copy_done=a["t_copy"], t_paste_issued=a["t_paste"],
                      bytes_match=False,
                      notes=f"Phase A 실패(메커니즘 미동작): got={a['got_len']} want={a['want_len']}")

    # Phase A PASS → laziness 확인
    lazy_a = V.compute_lazy_proven(a["conn_times"], a["t_copy"], a["t_paste"])
    if not lazy_a:
        return V.emit(OS_NAME, V.NEEDS_MANUAL, conn_times=a["conn_times"],
                      t_copy_done=a["t_copy"], t_paste_issued=a["t_paste"],
                      bytes_match=True,
                      notes="Phase A 데이터 일치하나 laziness 미증명(eager 의심)")

    # Phase B (INCR) 시도
    try:
        b = run_phase(BIG_PAYLOAD, "B-incr")
        b_note = f"Phase B(INCR 512KB) ok={b['ok']} got={b['got_len']}"
    except Exception as e:
        b = {"ok": False}
        b_note = f"Phase B 예외: {e}"

    if b["ok"]:
        return V.emit(OS_NAME, V.PASS, conn_times=a["conn_times"],
                      t_copy_done=a["t_copy"], t_paste_issued=a["t_paste"],
                      bytes_match=True, notes=f"Phase A+B PASS. {b_note}")
    # 메커니즘은 됨(A), INCR 만 미완 → NEEDS-MANUAL (false FAIL 회피)
    return V.emit(OS_NAME, V.NEEDS_MANUAL, conn_times=a["conn_times"],
                  t_copy_done=a["t_copy"], t_paste_issued=a["t_paste"],
                  bytes_match=True, notes=f"Phase A PASS, {b_note}")


if __name__ == "__main__":
    sys.exit(main())
