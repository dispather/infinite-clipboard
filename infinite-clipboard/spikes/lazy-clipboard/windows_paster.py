"""Windows paster (별도 프로세스, 불변 규칙 1) — paste 트리거.

GetClipboardData(CF_UNICODETEXT) 호출이 owner 프로세스의 WM_RENDERFORMAT 를 유발해야
진짜 cross-process 라운드트립. 받은 텍스트(base64)를 stdout 으로 그대로 반환하면
부모(windows_spike.py)가 디코드해서 원본 페이로드와 비교한다.
"""
import sys

try:
    import win32clipboard
    import win32con
except Exception as e:  # noqa: BLE001
    sys.stderr.write(f"pywin32 import 실패: {e}\n")
    sys.exit(2)


def main() -> int:
    win32clipboard.OpenClipboard()  # owner 가 아닌 별도 프로세스로 열기
    try:
        data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"GetClipboardData 실패: {e}\n")
        return 3
    finally:
        win32clipboard.CloseClipboard()
    sys.stdout.write(data or "")  # base64 텍스트 (owner 가 render 한 값)
    return 0


if __name__ == "__main__":
    sys.exit(main())
