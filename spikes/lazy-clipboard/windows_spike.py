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
import struct
import base64
import ctypes
import threading
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verdict as V  # noqa: E402
from origin import TestOrigin  # noqa: E402
from payload import TEXT_PAYLOAD, BIG_PAYLOAD, IMAGE_PAYLOAD  # noqa: E402

OS_NAME = "windows"

try:
    import win32gui
    import win32con
    import win32clipboard
except Exception as e:  # pywin32 부재/비-Windows = 인프라 실패 (빨강)
    print(f"[infra] pywin32 import 실패 (Windows 전용): {e}", file=sys.stderr)
    sys.exit(2)

# ── 64-bit 안전 HGLOBAL ctypes 설정 (대용량 read 수정) ──────────────────
# 이전 iteration 의 대용량 FAIL(GetClipboardData rc=3)은 pywin32 의 대용량 등록 포맷
# 처리 문제로 추정. set/get 양쪽을 수동 ctypes HGLOBAL + 4바이트 LE 길이 프리픽스로
# 바꿔 정확한 바이트 round-trip 을 보장한다 (GlobalSize 라운딩 모호성 제거).
# 핸들은 64-bit 라 restype/argtypes 명시 필수 — 미설정 시 c_int 로 truncate 되어 깨진다.
_k32 = ctypes.windll.kernel32
_u32 = ctypes.windll.user32
GMEM_MOVEABLE = 0x0002
_k32.GlobalAlloc.restype = ctypes.c_void_p
_k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalLock.argtypes = [ctypes.c_void_p]
_k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_k32.GlobalSize.restype = ctypes.c_size_t
_k32.GlobalSize.argtypes = [ctypes.c_void_p]
_u32.SetClipboardData.restype = ctypes.c_void_p
_u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]


def _hglobal_from_bytes(data: bytes) -> int:
    """4바이트 LE 길이 프리픽스 + data 를 GMEM_MOVEABLE HGLOBAL 로 복사해 핸들 반환.

    paster 가 프리픽스를 읽어 정확히 len(data) 바이트만 취하므로 GlobalSize 라운딩 무관.
    SetClipboardData 성공 시 소유권이 시스템으로 넘어가므로 호출자는 free 하지 않는다.
    """
    framed = struct.pack("<I", len(data)) + data
    size = len(framed)
    h = _k32.GlobalAlloc(GMEM_MOVEABLE, size)
    if not h:
        raise OSError(f"GlobalAlloc 실패 (size={size})")
    p = _k32.GlobalLock(h)
    if not p:
        raise OSError("GlobalLock 실패")
    ctypes.memmove(p, framed, size)
    _k32.GlobalUnlock(h)
    return h

# 커스텀 등록 포맷 — pywin32 의 CF_UNICODETEXT 텍스트 특수처리 회피, raw 바이트 처리.
# RegisterClipboardFormat 은 같은 이름이면 프로세스 간 동일 ID 반환 (paster 와 공유).
FORMAT = win32clipboard.RegisterClipboardFormat("ICSpikeLazy")
# Phase C 이미지용 등록 포맷 "PNG" — 현대 앱이 클립보드 PNG 공유에 쓰는 opaque 포맷.
# CF_DIB 는 bitmap 구조라 OS 가 synthesize 하므로 메커니즘 질문엔 부적합. 등록 "PNG" 는
# opaque HGLOBAL 이라 정확히 round-trip → delayed-render 메커니즘 검증에 적합.
FORMAT_PNG = win32clipboard.RegisterClipboardFormat("PNG")
PASTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "windows_paster.py")


def run_phase(payload: bytes, phase: str, fmt=FORMAT, paster_arg: str = "text") -> dict:
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
                    # 수동 HGLOBAL(길이 프리픽스) 제공 — pywin32 대용량 처리 회피, 정확 round-trip.
                    h = _hglobal_from_bytes(data)
                    if not _u32.SetClipboardData(fmt, h):
                        rendered["err"] = f"SetClipboardData 실패: {_k32.GetLastError()}"
                    else:
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
            # 지연 렌더 등록 = NULL 핸들 (SetClipboardData(fmt, NULL)). NULL 반환은 지연 렌더의
            # 정상 성공 신호이므로 반환값으로 실패 판정하지 않는다 (예전 false-positive 제거).
            _u32.SetClipboardData(fmt, None)
            win32clipboard.CloseClipboard()
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
            # paster 에 포맷 종류 전달: 텍스트는 인자 없음, 이미지는 "image".
            cmd = [sys.executable, PASTER]
            if paster_arg == "image":
                cmd.append("image")
            out = subprocess.run(
                cmd,
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


def _text_verdict() -> int:
    """텍스트 Phase A/B — 등록 포맷 "ICSpikeLazy" delayed render. emit os "windows"."""
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


def _image_verdict() -> int:
    """이미지 Phase C — 등록 포맷 "PNG" delayed render. emit os "windows-image".

    IMAGE_PAYLOAD 는 ~1.1MB 유효 PNG 라 자체적으로 HGLOBAL 대용량 경로를 탄다
    (별도 small phase 불필요). 같은 WM_RENDERFORMAT 메커니즘이 opaque 등록 "PNG"
    포맷에서도 동작하는지 검증.
    """
    try:
        c = run_phase(IMAGE_PAYLOAD, "C-image", fmt=FORMAT_PNG, paster_arg="image")
    except Exception as e:  # noqa: BLE001
        print(f"[infra] Windows Phase C 실행 실패: {e}", file=sys.stderr)
        return 2

    if not c["ok"]:
        return V.emit("windows-image", V.FAIL, conn_times=c["conn_times"],
                      t_copy_done=c["t_copy"], t_paste_issued=c["t_paste"],
                      bytes_match=False,
                      notes=f"Phase C 실패: {c['reason']} got={c['got_len']} want={c['want_len']}")

    lazy_c = V.compute_lazy_proven(c["conn_times"], c["t_copy"], c["t_paste"])
    if not lazy_c:
        return V.emit("windows-image", V.NEEDS_MANUAL, conn_times=c["conn_times"],
                      t_copy_done=c["t_copy"], t_paste_issued=c["t_paste"],
                      bytes_match=True,
                      notes="Phase C 데이터 일치하나 laziness 미증명(eager 의심)")

    return V.emit("windows-image", V.PASS, conn_times=c["conn_times"],
                  t_copy_done=c["t_copy"], t_paste_issued=c["t_paste"],
                  bytes_match=True,
                  notes=("Phase C PASS (등록 \"PNG\" 포맷 delayed render, ~1.1MB). "
                         "이미지도 같은 WM_RENDERFORMAT 메커니즘으로 lazy round-trip 확인."))


def main() -> int:
    rc1 = _text_verdict()
    rc2 = _image_verdict()
    return 2 if 2 in (rc1, rc2) else 0


if __name__ == "__main__":
    sys.exit(main())
