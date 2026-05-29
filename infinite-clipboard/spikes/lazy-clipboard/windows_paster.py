"""Windows paster (별도 프로세스, 불변 규칙 1) — paste 트리거.

GetClipboardData(CF_UNICODETEXT) 호출이 owner 프로세스의 WM_RENDERFORMAT 를 유발해야
진짜 cross-process 라운드트립. 받은 텍스트(base64)를 stdout 으로 그대로 반환하면
부모(windows_spike.py)가 디코드해서 원본 페이로드와 비교한다.
"""
import sys
import base64

try:
    import win32clipboard
except Exception as e:  # noqa: BLE001
    sys.stderr.write(f"pywin32 import 실패: {e}\n")
    sys.exit(2)

# windows_spike.py 와 동일한 이름 → 동일 포맷 ID
FORMAT = win32clipboard.RegisterClipboardFormat("ICSpikeLazy")
# 이미지 Phase C 용 등록 포맷 "PNG" (spike 의 FORMAT_PNG 와 동일 ID)
FORMAT_PNG = win32clipboard.RegisterClipboardFormat("PNG")


def main() -> int:
    # argv 가 ["image"] 면 등록 "PNG" 포맷, 아니면 기존 "ICSpikeLazy" 텍스트 포맷.
    fmt = FORMAT_PNG if sys.argv[1:] == ["image"] else FORMAT
    win32clipboard.OpenClipboard()  # owner 가 아닌 별도 프로세스로 열기 (= paste)
    try:
        data = win32clipboard.GetClipboardData(fmt)  # owner 의 WM_RENDERFORMAT 유발 → raw 바이트
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"GetClipboardData 실패: {e}\n")
        return 3
    finally:
        win32clipboard.CloseClipboard()
    if data is None:
        sys.stderr.write("GetClipboardData None\n")
        return 3
    raw = data if isinstance(data, (bytes, bytearray)) else bytes(data, "latin-1")
    sys.stdout.write(base64.b64encode(raw).decode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
