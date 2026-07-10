"""tests/_mac_paste_helper.py — macOS 별도 프로세스 paste (provideDataForType 트리거).

MacLazyProvider 가 지연 등록한 NSPasteboard 를 **다른 프로세스**에서 읽으면 owner 의
provideDataForType 콜백이 cross-process 로 유발된다(= 진짜 paste).

대용량 stdout 파이프 데드락 회피(스파이크 got=49152 교훈): 받은 raw 바이트/경로를
**인자로 받은 temp 파일**에 쓴다. 부모(테스트)는 run loop 를 펌핑하느라 stdout 을
드레인하지 않으므로 파이프를 쓰면 막힌다.

사용법: python _mac_paste_helper.py <outfile> [png|fileurl]
언더스코어 접두 파일명이라 pytest 가 테스트로 수집하지 않는다.
"""
import sys

from AppKit import NSPasteboard, NSApplicationLoad
from Foundation import NSURL


def main() -> int:
    args = sys.argv[1:]
    if not args:
        sys.stderr.write("usage: _mac_paste_helper.py <outfile> [png|fileurl]\n")
        return 2
    outpath = args[0]
    kind = args[1] if len(args) > 1 else "png"
    NSApplicationLoad()
    pb = NSPasteboard.generalPasteboard()

    if kind == "fileurl":
        urls = pb.readObjectsForClasses_options_([NSURL], None)  # lazy file-url 유발
        if not urls:
            sys.stderr.write("readObjectsForClasses_ None/empty\n")
            return 3
        paths = [u.path() for u in urls if u.isFileURL()]
        with open(outpath, "wb") as f:
            f.write("\n".join(p for p in paths if p).encode("utf-8"))
        return 0

    data = pb.dataForType_("public.png")  # owner 의 provideDataForType 유발
    if data is None:
        sys.stderr.write("dataForType_ None (제공자 미응답)\n")
        return 3
    with open(outpath, "wb") as f:
        f.write(bytes(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
