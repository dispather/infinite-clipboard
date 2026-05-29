"""tests/_mac_paste_helper.py — macOS 별도 프로세스 paste (provideDataForType 트리거).

MacLazyProvider 가 지연 등록한 NSPasteboard 를 **다른 프로세스**에서 읽으면 owner 의
provideDataForType 콜백이 cross-process 로 유발된다(= 진짜 paste). 같은 프로세스에서
읽으면 메커니즘이 다르므로 반드시 subprocess.

argv:
  "png"     → public.png raw 바이트를 base64 로 stdout
  "fileurl" → public.file-url 들을 NSURL 로 읽어 경로를 줄당 하나로 stdout

언더스코어 접두 파일명이라 pytest 가 테스트로 수집하지 않는다.
"""
import base64
import sys

from AppKit import NSPasteboard, NSApplicationLoad
from Foundation import NSURL


def _paste_png() -> int:
    pb = NSPasteboard.generalPasteboard()
    data = pb.dataForType_("public.png")  # owner 의 provideDataForType 유발
    if data is None:
        sys.stderr.write("dataForType_ None (제공자 미응답)\n")
        return 3
    sys.stdout.write(base64.b64encode(bytes(data)).decode("ascii"))
    return 0


def _paste_fileurl() -> int:
    pb = NSPasteboard.generalPasteboard()
    urls = pb.readObjectsForClasses_options_([NSURL], None)  # lazy file-url 들 유발
    if not urls:
        sys.stderr.write("readObjectsForClasses_ None/empty\n")
        return 3
    paths = [u.path() for u in urls if u.isFileURL()]
    sys.stdout.write("\n".join(p for p in paths if p))
    return 0


def main() -> int:
    NSApplicationLoad()
    kind = sys.argv[1] if len(sys.argv) > 1 else "png"
    return _paste_fileurl() if kind == "fileurl" else _paste_png()


if __name__ == "__main__":
    sys.exit(main())
