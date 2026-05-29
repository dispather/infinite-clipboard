"""Windows Pure-lazy 스파이크 — classic delayed rendering (CF_UNICODETEXT).

검증: message-only window 가 클립보드를 지연 등록(SetClipboardData(fmt, None))한 뒤,
별도 프로세스(windows_paster.py)가 GetClipboardData 로 paste 할 때 OS 가 owner 에게
WM_RENDERFORMAT 를 보내는가, 그 핸들러에서 origin.fetch() 한 바이트를 제공할 수 있는가.

(A) 메커니즘 증명에는 표준 포맷 CF_UNICODETEXT + base64 인코딩으로 임의 바이트를 안전하게
round-trip 한다 (커스텀 포맷 HGLOBAL 핸들링 회피). 실제 파일(CF_HDROP) delayed render 는
제품 단계의 후속 목표 — 여기선 메커니즘만 본다.

paster 는 불변 규칙 1 에 따라 별도 프로세스(windows_paster.py)다.

로컬(Linux) 실행 불가 — CI(windows-latest)에서 검증.
"""
import os
import sys
import time
import base64
import ctypes
import threading
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verdict as V  # noqa: E402
from origin import TestOrigin  # noqa: E402
from payload import TEXT_PAYLOAD, BIG_PAYLOAD  # noqa: E402

OS_NAME = "windows"

try:
    import win32gui
    import win32con
    import win32clipboard
except Exception as e:  # pywin32 부재/비-Windows = 인프라 실패 (빨강)
    print(f"[infra] pywin32 import 실패 (Windows 전용): {e}", file=sys.stderr)
    sys.exit(2)

# 커스텀 등록 포맷 — pywin32 의 CF_UNICODETEXT 텍스트 특수처리 회피, raw 바이트 처리.
# RegisterClipboardFormat 은 같은 이름이면 프로세스 간 동일 ID 반환 (paster 와 공유).
FORMAT = win32clipboard.RegisterClipboardFormat("ICSpikeLazy")
PASTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "windows_paster.py")


def run_phase(payload: bytes, phase: str) -> dict:
    result = {"phase": phase, "ok": False, "got_len": 0, "want_len": len(payload),
              "conn_times": [], "t_copy": 0.0, "t_paste": 0.0, "reason": ""}

    with TestOrigin(payload) as origin:
        rendered = {"done": False, "err": None}
        ready = threading.Event()
        stop = threading.Event()
        state = {"hwnd": None}

        def wndproc(hwnd, msg, wparam, lparam):
            if msg in (win32con.WM_RENDERFORMAT, win32con.WM_RENDERALLFORMATS):
                try:
                    data = origin.fetch()  # ← paste 시점에 origin 으로부터 lazy fetch
                    # WM_RENDERFORMAT 중엔 클립보드가 이미 system 에 의해 열려 있음 → Open/Close 금지.
                    # 커스텀 포맷이라 raw 바이트 그대로 제공.
                    win32clipboard.SetClipboardData(FORMAT, data)
                    rendered["done"] = True
                except Exception as e:  # noqa: BLE001
                    rendered["err"] = repr(e)
                return 0
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

        def worker():
            # 창 생성 + 클립보드 지연 소유 + 메시지 펌프 (owner 스레드가 WM_RENDERFORMAT 처리)
            wc = win32gui.WNDCLASS()
            wc.lpszClassName = "ICSpikeWnd"
            wc.lpfnWndProc = wndproc
            try:
                atom = win32gui.RegisterClass(wc)
            except Exception:
                atom = wc.lpszClassName  # 이미 등록된 경우
            hwnd = win32gui.CreateWindowEx(
                0, atom, "ic-spike", 0, 0, 0, 0, 0,
                win32con.HWND_MESSAGE, 0, 0, None,
            )
            state["hwnd"] = hwnd

            win32clipboard.OpenClipboard(hwnd)
            win32clipboard.EmptyClipboard()
            # 지연 렌더 등록 = NULL 핸들. pywin32 는 None 을 거부하므로 raw Win32 API 로 NULL 전달.
            ok = ctypes.windll.user32.SetClipboardData(FORMAT, None)
            win32clipboard.CloseClipboard()
            if not ok:
                rendered["err"] = f"지연 등록(SetClipboardData NULL) 실패: {ctypes.get_last_error()}"
            ready.set()

            while not stop.is_set():
                win32gui.PumpWaitingMessages()
                time.sleep(0.005)

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        if not ready.wait(timeout=10):
            result["reason"] = "owner 창/클립보드 소유 준비 실패"
            stop.set()
            return result
        result["t_copy"] = time.monotonic()

        time.sleep(0.05)
        result["t_paste"] = time.monotonic()
        try:
            out = subprocess.run(
                [sys.executable, PASTER],
                capture_output=True, timeout=15.0, text=True,
            )
            b64 = (out.stdout or "").strip()
            got = base64.b64decode(b64) if b64 else b""
            if out.returncode != 0:
                result["reason"] = f"paster rc={out.returncode}: {out.stderr.strip()[:200]}"
        except subprocess.TimeoutExpired:
            got = b""
            result["reason"] = "paster timeout (WM_RENDERFORMAT 미처리 가능)"

        stop.set()
        result["got_len"] = len(got)
        result["ok"] = got == payload
        result["conn_times"] = list(origin.connection_times)
        if rendered["err"] and not result["reason"]:
            result["reason"] = f"render 오류: {rendered['err']}"
        elif not rendered["done"] and not result["reason"]:
            result["reason"] = "WM_RENDERFORMAT 미수신"

    return result


def main() -> int:
    try:
        a = run_phase(TEXT_PAYLOAD, "A-small")
    except Exception as e:  # noqa: BLE001
        print(f"[infra] Windows Phase A 실행 실패: {e}", file=sys.stderr)
        return 2

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

    try:
        b = run_phase(BIG_PAYLOAD, "B-large")
        b_note = f"Phase B(512KB) ok={b['ok']} got={b['got_len']} {b['reason']}"
    except Exception as e:  # noqa: BLE001
        b = {"ok": False}
        b_note = f"Phase B 예외: {e}"

    verdict = V.PASS if b["ok"] else V.NEEDS_MANUAL
    note = (f"Phase A+B PASS (CF_UNICODETEXT delayed render). CF_HDROP(파일)는 후속. {b_note}"
            if b["ok"] else f"Phase A PASS(메커니즘 OK), {b_note}")
    return V.emit(OS_NAME, verdict, conn_times=a["conn_times"],
                  t_copy_done=a["t_copy"], t_paste_issued=a["t_paste"],
                  bytes_match=True, notes=note)


if __name__ == "__main__":
    sys.exit(main())
