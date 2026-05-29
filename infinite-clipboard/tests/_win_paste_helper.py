"""tests/_win_paste_helper.py — Windows 별도 프로세스 paste (delayed-render 트리거).

WindowsLazyProvider 가 지연 등록한 클립보드를 **다른 프로세스**에서 GetClipboardData
하면 owner 의 WM_RENDERFORMAT 가 cross-process 로 유발된다(= 진짜 paste). 같은
프로세스에서 읽으면 메커니즘이 다르므로 반드시 subprocess.

argv:
  "png"   → 등록 "PNG" 포맷 raw 바이트를 base64 로 stdout (ctypes — 대용량 안전)
  "hdrop" → CF_HDROP 경로 목록을 줄당 하나로 stdout (pywin32 가 DROPFILES 파싱)

언더스코어 접두 파일명이라 pytest 가 테스트로 수집하지 않는다.
"""
import base64
import ctypes
import sys

import win32clipboard
import win32con

_k32 = ctypes.windll.kernel32
_u32 = ctypes.windll.user32
_u32.OpenClipboard.argtypes = [ctypes.c_void_p]
_u32.GetClipboardData.restype = ctypes.c_void_p
_u32.GetClipboardData.argtypes = [ctypes.c_uint]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalLock.argtypes = [ctypes.c_void_p]
_k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_k32.GlobalSize.restype = ctypes.c_size_t
_k32.GlobalSize.argtypes = [ctypes.c_void_p]

_FMT_PNG = win32clipboard.RegisterClipboardFormat("PNG")


def _paste_png() -> int:
    if not _u32.OpenClipboard(None):
        sys.stderr.write(f"OpenClipboard 실패: {_k32.GetLastError()}\n")
        return 3
    try:
        h = _u32.GetClipboardData(_FMT_PNG)  # owner 의 WM_RENDERFORMAT 유발
        if not h:
            sys.stderr.write(f"GetClipboardData NULL: {_k32.GetLastError()}\n")
            return 3
        p = _k32.GlobalLock(h)
        if not p:
            sys.stderr.write("GlobalLock 실패\n")
            return 3
        size = _k32.GlobalSize(h)
        buf = ctypes.string_at(p, size)
        _k32.GlobalUnlock(h)
    finally:
        _u32.CloseClipboard()
    sys.stdout.write(base64.b64encode(buf).decode("ascii"))
    return 0


def _paste_hdrop() -> int:
    win32clipboard.OpenClipboard()
    try:
        files = win32clipboard.GetClipboardData(win32con.CF_HDROP)  # DROPFILES → 경로 tuple
    finally:
        win32clipboard.CloseClipboard()
    sys.stdout.write("\n".join(files))
    return 0


def main() -> int:
    kind = sys.argv[1] if len(sys.argv) > 1 else "png"
    return _paste_hdrop() if kind == "hdrop" else _paste_png()


if __name__ == "__main__":
    sys.exit(main())
