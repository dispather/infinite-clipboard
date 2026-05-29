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


def main() -> int:
    win32clipboard.OpenClipboard()  # owner 가 아닌 별도 프로세스로 열기 (= paste)
    try:
        data = win32clipboard.GetClipboardData(FORMAT)  # owner 의 WM_RENDERFORMAT 유발 → raw 바이트
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
