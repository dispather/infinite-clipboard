"""macOS paster (별도 프로세스, 불변 규칙 1) — paste 트리거.

NSPasteboard.dataForType_(UTI) 호출이 owner 프로세스의 provideDataForType 콜백을
유발해야 진짜 cross-process 라운드트립. 받은 NSData 의 raw 바이트를 **인자로 받은 temp
파일**에 쓰면 부모(macos_spike.py)가 읽어 원본 페이로드와 비교한다.

대용량(이미지/512KB) 회귀 수정: 예전엔 base64 를 stdout 으로 반환했는데, 부모가 run loop
펌핑 중 stdout 파이프를 드레인하지 않아 ~64KB 파이프 버퍼가 차면 paster 가 write 에서
블록 → 데드락(got=49152 증상). raw 를 파일로 쓰면 파이프를 안 쓰므로 데드락이 사라진다.

사용법: python macos_paster.py <outfile> [image]
"""
import sys

UTI = "com.infiniteclipboard.spike"  # macos_spike.py 와 동일해야 함
PNG_UTI = "public.png"  # Phase C 이미지 lazy 검증용 (실제 이미지 UTI)

try:
    from AppKit import NSPasteboard, NSApplicationLoad
except Exception as e:  # noqa: BLE001
    sys.stderr.write(f"pyobjc import 실패: {e}\n")
    sys.exit(2)


def main() -> int:
    args = sys.argv[1:]
    if not args:
        sys.stderr.write("usage: macos_paster.py <outfile> [image]\n")
        return 2
    outpath = args[0]
    # 두 번째 인자가 "image" 면 public.png, 아니면 기존 텍스트 custom UTI
    uti = PNG_UTI if "image" in args[1:] else UTI
    NSApplicationLoad()
    pb = NSPasteboard.generalPasteboard()
    data = pb.dataForType_(uti)  # owner 의 provideDataForType 를 유발
    if data is None:
        sys.stderr.write("dataForType_ None (제공자 미응답)\n")
        return 3
    with open(outpath, "wb") as f:
        f.write(bytes(data))  # raw 바이트 → 파일 (stdout 파이프 미사용)
    return 0


if __name__ == "__main__":
    sys.exit(main())
