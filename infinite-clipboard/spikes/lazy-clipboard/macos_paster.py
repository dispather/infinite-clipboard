"""macOS paster (별도 프로세스, 불변 규칙 1) — paste 트리거.

NSPasteboard.dataForType_(UTI) 호출이 owner 프로세스의 provideDataForType 콜백을
유발해야 진짜 cross-process 라운드트립. 받은 NSData 를 base64 로 stdout 에 반환하면
부모(macos_spike.py)가 디코드해 원본 페이로드와 비교한다.
"""
import sys
import base64

UTI = "com.infiniteclipboard.spike"  # macos_spike.py 와 동일해야 함
PNG_UTI = "public.png"  # Phase C 이미지 lazy 검증용 (실제 이미지 UTI)

try:
    from AppKit import NSPasteboard, NSApplicationLoad
except Exception as e:  # noqa: BLE001
    sys.stderr.write(f"pyobjc import 실패: {e}\n")
    sys.exit(2)


def main() -> int:
    # argv 가 ["image"] 면 이미지(public.png), 아니면 기존 텍스트 custom UTI
    uti = PNG_UTI if sys.argv[1:] == ["image"] else UTI
    NSApplicationLoad()
    pb = NSPasteboard.generalPasteboard()
    data = pb.dataForType_(uti)  # owner 의 provideDataForType 를 유발
    if data is None:
        sys.stderr.write("dataForType_ None (제공자 미응답)\n")
        return 3
    raw = bytes(data)
    sys.stdout.write(base64.b64encode(raw).decode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
