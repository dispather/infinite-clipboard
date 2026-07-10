"""macOS Pure-lazy 스파이크 — NSPasteboard lazy data provider + file promise probe.

검증 (A) data lazy provide: NSPasteboardItem 에 setDataProvider:forTypes: 로 custom UTI 를
지연 등록한 뒤, 별도 프로세스(macos_paster.py)가 dataForType: 로 paste 할 때 owner 의
pasteboard:item:provideDataForType: 콜백이 fire 하는가, 그 안에서 origin.fetch() 한
바이트를 제공할 수 있는가.

⚠️ run-loop 펌핑 필수 (spec-review CRITICAL #1): data provider 콜백은 활성 run loop 가
있어야 fire 한다. NSApplicationLoad() 후 main thread 에서 NSRunLoop 를 펌핑하며 자식
paster 가 읽을 때까지 대기 → 콜백 미발화를 펌핑 누락과 혼동(false FAIL)하지 않는다.

(B) file promise probe: 파일은 URL 이라 단순 data lazy 가 아니라 NSFilePromiseProvider 가
필요한데, 이는 Finder drag-drop 으로만 fulfillment → CI 자동화 불가 → NEEDS-MANUAL 로 기록.

paster 는 불변 규칙 1 에 따라 별도 프로세스(macos_paster.py)다.

로컬(Linux) 실행 불가 — CI(macos-14)에서 검증.
"""
import os
import sys
import time
import subprocess
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verdict as V  # noqa: E402
from origin import TestOrigin  # noqa: E402
from payload import TEXT_PAYLOAD, BIG_PAYLOAD, IMAGE_PAYLOAD  # noqa: E402

OS_NAME = "macos"
UTI = "com.infiniteclipboard.spike"  # macos_paster.py 와 동일해야 함
PNG_UTI = "public.png"  # Phase C 이미지 lazy 검증용 (실제 이미지 UTI)
PASTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macos_paster.py")

try:
    import objc  # noqa: F401
    from Foundation import (
        NSObject, NSData, NSRunLoop, NSDate, NSDefaultRunLoopMode,
    )
    from AppKit import NSPasteboard, NSPasteboardItem, NSApplicationLoad
except Exception as e:  # pyobjc 부재/비-macOS = 인프라 실패 (빨강)
    print(f"[infra] pyobjc import 실패 (macOS 전용): {e}", file=sys.stderr)
    sys.exit(2)


# ─── Provider 클래스는 모듈 레벨에서 단 1회 정의 (하니스 버그 수정) ───
# ObjC 클래스는 이름으로 전역 등록되므로, run_phase 마다 재정의하면 두 번째 등록이
# 실패하거나 stale closure(Phase A 의 origin 을 캡처) 를 유발 → Phase B/C 가 깨진다.
# 해결: 클래스는 1회만 정의하고, origin 은 closure 가 아니라 인스턴스 상태로 주입한다.
# objc.protocolNamed 도 모듈 레벨에서 1회 해결 (실패 시 비공식 conform 으로 폴백).
try:
    _PROTO = objc.protocolNamed("NSPasteboardItemDataProvider")
    _BASES, _KW = (NSObject,), {"protocols": [_PROTO]}
except Exception:  # noqa: BLE001  (프로토콜 미발견 시 비공식 conform 으로 폴백)
    _BASES, _KW = (NSObject,), {}


class _Provider(*_BASES, **_KW):
    """origin.fetch() 를 paste 시점에 호출하는 NSPasteboardItemDataProvider.

    setDataProvider:forTypes: 는 provider 가 NSPasteboardItemDataProvider 프로토콜을
    '공식' 으로 conform 해야 성공(YES) 한다 → pyobjc protocols= 로 명시 선언.
    origin/state 는 closure 가 아니라 인스턴스 속성으로 주입 (모듈 레벨 1회 정의이므로).
    """

    def initWithOrigin_state_(self, origin, st):
        self = objc.super(_Provider, self).init()
        if self is None:
            return None
        self._origin = origin
        self._st = st
        return self

    # 셀렉터: pasteboard:item:provideDataForType:
    def pasteboard_item_provideDataForType_(self, pasteboard, item, type_):
        try:
            data = self._origin.fetch()  # ← paste 시점 lazy fetch
            ns = NSData.dataWithBytes_length_(data, len(data))
            item.setData_forType_(ns, type_)
            self._st["fired"] = True
        except Exception as e:  # noqa: BLE001
            self._st["err"] = repr(e)


def run_phase(payload: bytes, phase: str, uti: str = UTI) -> dict:
    result = {"phase": phase, "ok": False, "got_len": 0, "want_len": len(payload),
              "conn_times": [], "t_copy": 0.0, "t_paste": 0.0, "reason": ""}

    NSApplicationLoad()
    with TestOrigin(payload) as origin:
        st = {"fired": False, "err": None}
        # 모듈 레벨 1회 정의된 _Provider 사용 — origin 은 인스턴스 상태로 주입
        provider = _Provider.alloc().initWithOrigin_state_(origin, st)

        item = NSPasteboardItem.alloc().init()
        ok = item.setDataProvider_forTypes_(provider, [uti])
        if not ok:
            result["reason"] = "setDataProvider_forTypes_ 실패"
            return result

        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        wrote = pb.writeObjects_([item])  # = copy (selection 설정)
        if not wrote:
            result["reason"] = "writeObjects_ 실패"
            return result
        result["t_copy"] = time.monotonic()

        # 자식 paster spawn (= paste). owner 는 run loop 를 펌핑해 콜백 처리.
        # 대용량(이미지/512KB) stdout 파이프 데드락 회피: paster 가 raw 바이트를 temp 파일로 씀
        # (예전 base64 stdout 방식은 부모가 펌핑 중 파이프 미드레인 → got=49152 데드락).
        out_fd, out_path = tempfile.mkstemp(prefix="ic_spike_mac_")
        os.close(out_fd)
        paster_argv = [sys.executable, PASTER, out_path]
        if uti == PNG_UTI:
            paster_argv.append("image")
        result["t_paste"] = time.monotonic()
        proc = subprocess.Popen(
            paster_argv,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        rl = NSRunLoop.currentRunLoop()
        deadline = time.monotonic() + 15.0
        while proc.poll() is None and time.monotonic() < deadline:
            rl.runMode_beforeDate_(
                NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05),
            )

        if proc.poll() is None:
            proc.kill()
            result["reason"] = "paster timeout (provideDataForType 미발화 가능)"
        _out, err = proc.communicate()

        try:
            with open(out_path, "rb") as f:
                got = f.read()  # paster 가 쓴 raw 바이트
        except OSError:
            got = b""
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass
        if proc.returncode not in (0, None) and not result["reason"]:
            result["reason"] = f"paster rc={proc.returncode}: {(err or b'').decode('utf-8','replace')[:200]}"

        result["got_len"] = len(got)
        result["ok"] = got == payload
        result["conn_times"] = list(origin.connection_times)
        if st["err"] and not result["reason"]:
            result["reason"] = f"provider 오류: {st['err']}"
        elif not st["fired"] and not result["reason"]:
            result["reason"] = "provideDataForType 미발화"

    return result


def _probe_file_promise() -> str:
    """file promise 가능성 프로브. 거의 확실히 Finder 필요 → CI 자동화 불가."""
    try:
        from AppKit import NSFilePromiseProvider  # noqa: F401
        return "NSFilePromiseProvider 존재하나 fulfillment 는 Finder drag-drop 필요 → CI 자동화 불가"
    except Exception:  # noqa: BLE001
        return "NSFilePromiseProvider import 불가(이 pyobjc 버전) — 파일 경로 별도 검토"


def _text_verdict() -> int:
    """텍스트 data-provider 종합 판정 (custom UTI).

    data lazy 메커니즘은 PASS 근거지만, 우리 목표는 파일이라 file promise 가 CI 자동화
    불가 → 보수적으로 NEEDS-MANUAL 로 유지. file promise probe + Phase A/B 펌핑 그대로.
    """
    fp_note = _probe_file_promise()
    try:
        a = run_phase(TEXT_PAYLOAD, "A-small")
    except Exception as e:  # noqa: BLE001
        print(f"[infra] macOS Phase A 실행 실패: {e}", file=sys.stderr)
        return 2

    if not a["ok"]:
        return V.emit(OS_NAME, V.FAIL, conn_times=a["conn_times"],
                      t_copy_done=a["t_copy"], t_paste_issued=a["t_paste"],
                      bytes_match=False,
                      notes=f"Phase A(data) 실패: {a['reason']} got={a['got_len']}. file promise: {fp_note}")

    lazy_a = V.compute_lazy_proven(a["conn_times"], a["t_copy"], a["t_paste"])
    if not lazy_a:
        return V.emit(OS_NAME, V.NEEDS_MANUAL, conn_times=a["conn_times"],
                      t_copy_done=a["t_copy"], t_paste_issued=a["t_paste"],
                      bytes_match=True,
                      notes=f"data 일치하나 laziness 미증명(eager 의심). file promise: {fp_note}")

    try:
        b = run_phase(BIG_PAYLOAD, "B-large")
        b_note = f"Phase B(512KB) ok={b['ok']} got={b['got_len']} {b['reason']}"
    except Exception as e:  # noqa: BLE001
        b_note = f"Phase B 예외: {e}"

    # 종합: data lazy 는 PASS 근거지만, 파일이 목표라 보수적으로 NEEDS-MANUAL (file promise 미검증)
    return V.emit(OS_NAME, V.NEEDS_MANUAL, conn_times=a["conn_times"],
                  t_copy_done=a["t_copy"], t_paste_issued=a["t_paste"],
                  bytes_match=True,
                  notes=f"data lazy provide PASS (메커니즘 OK). {b_note}. 파일은 미검증 — {fp_note}")


def _image_verdict() -> int:
    """이미지 lazy 종합 판정 (Phase C, public.png).

    public.png 는 실제 이미지 UTI 라 같은 provideDataForType 메커니즘이 동작하면
    이미지 lazy 는 진짜 PASS — file promise 와 달리 보수적 강등 불필요.
    """
    try:
        c = run_phase(IMAGE_PAYLOAD, "C-image", uti=PNG_UTI)
    except Exception as e:  # noqa: BLE001
        print(f"[infra] macOS Phase C 실행 실패: {e}", file=sys.stderr)
        return 2

    if not c["ok"]:
        return V.emit("macos-image", V.FAIL, conn_times=c["conn_times"],
                      t_copy_done=c["t_copy"], t_paste_issued=c["t_paste"],
                      bytes_match=False,
                      notes=f"Phase C(public.png) 실패: {c['reason']} got={c['got_len']}")

    lazy_c = V.compute_lazy_proven(c["conn_times"], c["t_copy"], c["t_paste"])
    if not lazy_c:
        return V.emit("macos-image", V.NEEDS_MANUAL, conn_times=c["conn_times"],
                      t_copy_done=c["t_copy"], t_paste_issued=c["t_paste"],
                      bytes_match=True,
                      notes="이미지 data 일치하나 laziness 미증명(eager 의심)")

    # public.png 가 실제 이미지 타입이라 이미지 lazy 는 보수적 강등 없이 PASS
    return V.emit("macos-image", V.PASS, conn_times=c["conn_times"],
                  t_copy_done=c["t_copy"], t_paste_issued=c["t_paste"],
                  bytes_match=True,
                  notes="이미지 lazy provide PASS (public.png 실제 이미지 UTI 로 라운드트립)")


def main() -> int:
    rc1 = _text_verdict()
    rc2 = _image_verdict()
    return 2 if 2 in (rc1, rc2) else 0


if __name__ == "__main__":
    sys.exit(main())
