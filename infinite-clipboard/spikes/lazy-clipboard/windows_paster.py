"""Windows paster (별도 프로세스, 불변 규칙 1) — paste 트리거.

GetClipboardData(fmt) 호출이 owner 프로세스의 WM_RENDERFORMAT 를 유발해야 진짜
cross-process 라운드트립. owner 가 수동 HGLOBAL(4바이트 LE 길이 프리픽스 + data)로
제공하므로, 여기서 프리픽스를 읽어 정확히 그만큼만 취한 뒤 base64 로 stdout 에 반환한다.
부모(windows_spike.py)는 subprocess.run(capture_output) 으로 파이프를 드레인하므로
대용량 base64 stdout 도 데드락 없음.

read 를 수동 ctypes 로 하는 이유: 이전 iteration 에서 pywin32 GetClipboardData 가
대용량 등록 포맷에서 실패(rc=3)했음. 64-bit 핸들 안전을 위해 restype/argtypes 명시 필수.
"""
import sys
import struct
import base64
import ctypes

try:
    import win32clipboard
except Exception as e:  # noqa: BLE001
    sys.stderr.write(f"pywin32 import 실패: {e}\n")
    sys.exit(2)

# windows_spike.py 와 동일한 이름 → 동일 포맷 ID
FORMAT = win32clipboard.RegisterClipboardFormat("ICSpikeLazy")
# 이미지 Phase C 용 등록 포맷 "PNG"
FORMAT_PNG = win32clipboard.RegisterClipboardFormat("PNG")

# ── 64-bit 안전 ctypes 설정 (spike 와 동일 계약) ──
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


def main() -> int:
    # argv 가 ["image"] 면 등록 "PNG" 포맷, 아니면 기존 "ICSpikeLazy" 텍스트 포맷.
    fmt = FORMAT_PNG if sys.argv[1:] == ["image"] else FORMAT
    if not _u32.OpenClipboard(None):  # owner 가 아닌 별도 프로세스로 열기 (= paste)
        sys.stderr.write(f"OpenClipboard 실패: {_k32.GetLastError()}\n")
        return 3
    try:
        h = _u32.GetClipboardData(fmt)  # owner 의 WM_RENDERFORMAT 유발 → HGLOBAL 핸들
        if not h:
            sys.stderr.write(f"GetClipboardData NULL: {_k32.GetLastError()}\n")
            return 3
        p = _k32.GlobalLock(h)
        if not p:
            sys.stderr.write("GlobalLock 실패\n")
            return 3
        size = _k32.GlobalSize(h)
        buf = ctypes.string_at(p, size)  # 클립보드 open 중에만 핸들 유효
        _k32.GlobalUnlock(h)
    finally:
        _u32.CloseClipboard()

    if len(buf) < 4:
        sys.stderr.write(f"프레임 너무 짧음: {len(buf)} bytes\n")
        return 3
    n = struct.unpack("<I", buf[:4])[0]  # LE 길이 프리픽스
    raw = buf[4:4 + n]  # GlobalSize 라운딩 패딩 무시, 정확히 n 바이트만
    sys.stdout.write(base64.b64encode(raw).decode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
