"""
크로스 플랫폼 클립보드 관리 모듈
Windows, macOS, Linux 지원

핵심 기능:
- 클립보드 변경 감지 (해시 비교)
- 텍스트/이미지/파일 읽기·쓰기
- OS별 핸들러 자동 선택 (팩토리 패턴)
"""

import io
import os
import base64
import hashlib
import platform
import subprocess
from typing import Any, List, Optional, Tuple
from PIL import Image
import logging

logger = logging.getLogger(__name__)

# 현재 OS 확인
SYSTEM = platform.system()  # 'Windows', 'Darwin' (macOS), 'Linux'


def _decode_clipboard_bytes(data: bytes) -> str:
    """클립보드 바이트를 가능한 인코딩으로 복원.

    RDP(Windows 원격 접속) 세션이 CP949/CP1252/UTF-16 으로 텍스트를
    주입하는 경우가 있어 UTF-8 strict 만 믿으면 무한 디코드 에러 루프.
    BOM 검사 우선, 그 다음 인코딩 체인 순회, 최종 폴백은 replace.
    """
    if not data:
        return ""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "utf-16", "cp949", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class ClipboardManager:
    """크로스 플랫폼 클립보드를 관리하는 클래스 (OS 분기 팩토리)"""

    def __init__(self):
        self._last_content_hash: Optional[str] = None
        # 자기 자신의 변경으로 인한 재전송 방지 (타임스탬프 기반, 0.5초 윈도우)
        self._ignore_until: float = 0.0

        # OS별 클립보드 핸들러 초기화
        if SYSTEM == "Windows":
            self._handler = WindowsClipboard()
        elif SYSTEM == "Darwin":
            self._handler = MacClipboard()
        else:
            self._handler = LinuxClipboard()

    def get_clipboard_content(self) -> Tuple[str, Any]:
        """
        클립보드 내용을 가져옵니다.

        Returns:
            Tuple[str, Any]: (타입, 데이터)
            - 타입: 'text', 'image', 'files', 'empty'
            - 데이터: 텍스트 문자열, base64 인코딩된 이미지, 파일 경로 리스트
        """
        return self._handler.get_content()

    def get_clipboard_files(self) -> Optional[List[str]]:
        """
        클립보드의 파일/폴더 목록을 반환합니다.
        파일뿐 아니라 폴더 경로도 포함됩니다.

        Returns:
            Optional[List[str]]: 파일/폴더 경로 리스트, 없으면 None
        """
        if hasattr(self._handler, "get_files"):
            return self._handler.get_files()
        return None

    def set_clipboard_content(self, content_type: str, data: Any) -> bool:
        """
        클립보드에 내용을 설정합니다.

        Args:
            content_type: 'text', 'image', 'files'
            data: 텍스트 문자열, base64 인코딩된 이미지, 파일 경로 리스트

        Returns:
            bool: 성공 여부
        """
        import time
        self._ignore_until = time.monotonic() + 0.5

        success = self._handler.set_content(content_type, data)

        if success:
            self._last_content_hash = self._calculate_hash(content_type, data)
        else:
            self._ignore_until = 0.0

        return success

    def has_changed(self) -> Tuple[bool, str, Any]:
        """
        클립보드 내용이 변경되었는지 확인합니다.

        Returns:
            Tuple[bool, str, Any]: (변경됨, 타입, 데이터)
        """
        import time
        if time.monotonic() < self._ignore_until:
            content_type, data = self.get_clipboard_content()
            self._last_content_hash = self._calculate_hash(content_type, data)
            return (False, "", None)

        content_type, data = self.get_clipboard_content()

        if content_type == "empty":
            return (False, "", None)

        current_hash = self._calculate_hash(content_type, data)

        if current_hash != self._last_content_hash:
            self._last_content_hash = current_hash
            return (True, content_type, data)

        return (False, "", None)

    def _calculate_hash(self, content_type: str, data: Any) -> Optional[str]:
        """데이터의 해시를 계산합니다."""
        if data is None:
            return None

        import json
        if isinstance(data, (list, dict)):
            serialized = json.dumps(data, sort_keys=True, ensure_ascii=False)
        else:
            serialized = str(data)
        content = f"{content_type}:{serialized}"
        return hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()


# ---------------------------------------------------------------------------
# Windows 클립보드 핸들러
# ---------------------------------------------------------------------------

class WindowsClipboard:
    """Windows 클립보드 핸들러 (win32clipboard 사용)"""

    def __init__(self):
        import win32clipboard
        import win32con
        import time

        self.win32clipboard = win32clipboard
        self.win32con = win32con
        self.time = time
        self._clipboard_open = False
        self._max_retries = 5
        self._retry_delay = 0.1  # 100ms

    def _open_clipboard(self) -> bool:
        """클립보드를 안전하게 열기 (재시도 포함)"""
        for i in range(self._max_retries):
            try:
                self.win32clipboard.OpenClipboard()
                self._clipboard_open = True
                return True
            except Exception as e:
                if i < self._max_retries - 1:
                    self.time.sleep(self._retry_delay)
                else:
                    logger.warning(
                        f"클립보드 열기 실패 (재시도 {self._max_retries}회): {e}"
                    )
        return False

    def _close_clipboard(self):
        """클립보드를 안전하게 닫기"""
        if self._clipboard_open:
            try:
                self.win32clipboard.CloseClipboard()
            except Exception:
                pass
            finally:
                self._clipboard_open = False

    def get_files(self) -> Optional[List[str]]:
        """
        클립보드의 파일/폴더 목록을 반환합니다.
        CF_HDROP 형식을 사용하며, 파일과 폴더 모두 포함됩니다.
        """
        if not self._open_clipboard():
            return None

        try:
            CF_HDROP = 15
            if self.win32clipboard.IsClipboardFormatAvailable(CF_HDROP):
                try:
                    data = self.win32clipboard.GetClipboardData(CF_HDROP)
                    if data:
                        # data는 파일/폴더 경로 튜플 (폴더도 포함됨)
                        return list(data)
                except Exception:
                    pass
            return None
        except Exception:
            return None
        finally:
            self._close_clipboard()

    def get_content(self) -> Tuple[str, Any]:
        if not self._open_clipboard():
            return ("empty", None)

        try:
            # 파일/폴더 확인 (CF_HDROP)
            CF_HDROP = 15
            if self.win32clipboard.IsClipboardFormatAvailable(CF_HDROP):
                try:
                    data = self.win32clipboard.GetClipboardData(CF_HDROP)
                    if data:
                        files = list(data)
                        if files:
                            logger.info(f"파일/폴더 클립보드 감지: {len(files)}개 항목")
                            return ("files", files)
                except Exception as e:
                    logger.error(f"파일 클립보드 읽기 오류: {e}")

            # 텍스트 확인
            if self.win32clipboard.IsClipboardFormatAvailable(
                self.win32con.CF_UNICODETEXT
            ):
                data = self.win32clipboard.GetClipboardData(
                    self.win32con.CF_UNICODETEXT
                )
                return ("text", data)

            # 이미지 확인 (DIB 형식)
            if self.win32clipboard.IsClipboardFormatAvailable(self.win32con.CF_DIB):
                data = self.win32clipboard.GetClipboardData(self.win32con.CF_DIB)
                image = self._dib_to_image(data)
                if image:
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                    image_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
                    return ("image", image_data)

            return ("empty", None)

        except Exception as e:
            logger.error(f"클립보드 읽기 오류: {e}")
            return ("empty", None)
        finally:
            self._close_clipboard()

    def set_content(self, content_type: str, data: Any) -> bool:
        if not self._open_clipboard():
            return False

        try:
            self.win32clipboard.EmptyClipboard()

            if content_type == "text":
                self.win32clipboard.SetClipboardData(
                    self.win32con.CF_UNICODETEXT, data
                )
                # v2.2 R1 privacy: INFO 로그에선 길이만, content 는 DEBUG 만
                logger.info(f"텍스트 클립보드 설정: {len(data)}자")
                logger.debug(f"  preview: {data[:50]}{'...' if len(data) > 50 else ''}")

            elif content_type == "image":
                image_data = base64.b64decode(data)
                image = Image.open(io.BytesIO(image_data))

                output = io.BytesIO()
                image.convert("RGB").save(output, "BMP")
                bmp_data = output.getvalue()[14:]  # BMP 파일 헤더 14바이트 제거

                self.win32clipboard.SetClipboardData(self.win32con.CF_DIB, bmp_data)
                logger.info("이미지 클립보드 설정 완료")

            elif content_type == "files":
                self._set_files_to_clipboard(data)

            return True

        except Exception as e:
            logger.error(f"클립보드 설정 오류: {e}")
            return False
        finally:
            self._close_clipboard()

    def _set_files_to_clipboard(self, file_paths: List[str]) -> bool:
        """파일/폴더 목록을 클립보드에 설정 (CF_HDROP)"""
        try:
            import struct

            CF_HDROP = 15

            # DROPFILES 구조체 생성
            # DROPFILES 구조: 20바이트 헤더 + 파일 경로들 (널 종료, 더블 널로 끝)
            files_str = "\0".join(file_paths) + "\0\0"
            files_bytes = files_str.encode("utf-16-le")

            # DROPFILES 헤더 (20바이트)
            # pFiles: 파일 목록 시작 오프셋 (20)
            # pt.x, pt.y: 드롭 위치 (0, 0)
            # fNC: 비클라이언트 영역 여부 (0)
            # fWide: 유니코드 여부 (1)
            header = struct.pack("<IIIii", 20, 0, 0, 0, 1)

            dropfiles = header + files_bytes

            self.win32clipboard.SetClipboardData(CF_HDROP, dropfiles)
            logger.info(f"파일/폴더 클립보드 설정: {len(file_paths)}개 항목")
            return True

        except Exception as e:
            logger.error(f"파일 클립보드 설정 오류: {e}")
            return False

    def _dib_to_image(self, dib_data: bytes) -> Optional[Image.Image]:
        """DIB(Device Independent Bitmap) 데이터를 PIL Image로 변환"""
        try:
            bmp_header = (
                b"BM"
                + (len(dib_data) + 14).to_bytes(4, "little")
                + b"\x00\x00\x00\x00"
                + b"\x36\x00\x00\x00"
            )
            bmp_data = bmp_header + dib_data
            return Image.open(io.BytesIO(bmp_data))
        except Exception as e:
            logger.error(f"DIB 변환 오류: {e}")
            return None


# ---------------------------------------------------------------------------
# macOS 클립보드 핸들러
# ---------------------------------------------------------------------------

def _clean_env():
    """macOS 번들 환경용 subprocess env.

    - MallocStackLogging 경고 억제
    - LANG/LC_CTYPE 주입: LaunchServices 는 GUI .app 에 locale 을 주입하지 않아
      pbcopy 가 'C' locale 로 떨어지면 한글 UTF-8 바이트를 전부 버린다
      (utf8 타입에 빈 문자열만 등록되어 Linux→Mac 붙여넣기 실패).
    """
    env = os.environ.copy()
    env.pop("MallocStackLogging", None)
    env.pop("MallocStackLoggingNoCompact", None)
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_CTYPE", "UTF-8")
    return env


class MacClipboard:
    """macOS 클립보드 핸들러 (pbcopy/pbpaste 및 PyObjC 사용)"""

    def get_files(self) -> Optional[List[str]]:
        """
        macOS 클립보드에서 파일/폴더 경로 목록을 반환합니다.
        NSPasteboard의 filenames 타입을 사용합니다.
        """
        try:
            from AppKit import NSPasteboard, NSFilenamesPboardType

            pasteboard = NSPasteboard.generalPasteboard()
            file_urls = pasteboard.propertyListForType_(NSFilenamesPboardType)
            if file_urls:
                # 파일과 폴더 경로 모두 포함됨
                return list(file_urls)
        except ImportError:
            # PyObjC가 없으면 osascript로 대체
            try:
                script = """
                    try
                        set theFiles to the clipboard as «class furl»
                        return POSIX path of theFiles
                    on error
                        return ""
                    end try
                """
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True, text=True, env=_clean_env()
                )
                if result.returncode == 0 and result.stdout.strip():
                    path = result.stdout.strip()
                    if path:
                        return [path]
            except Exception as e:
                logger.error(f"macOS 파일 목록 가져오기 오류: {e}")
        except Exception as e:
            logger.error(f"macOS 파일 목록 가져오기 오류: {e}")

        return None

    def get_content(self) -> Tuple[str, Any]:
        try:
            # 파일/폴더 확인 먼저
            files = self.get_files()
            if files:
                logger.info(f"파일/폴더 클립보드 감지: {len(files)}개 항목")
                return ("files", files)

            # 이미지 확인 (PyObjC 사용)
            image = self._get_image_from_clipboard()
            if image:
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                image_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
                return ("image", image_data)

            # 텍스트 확인 (pbpaste 사용) — 바이트로 읽고 인코딩 자동 탐지
            # RDP 세션이 CP949/CP1252/UTF-16 을 주입하는 경우 대비
            result = subprocess.run(["pbpaste"], capture_output=True, env=_clean_env())
            if result.returncode == 0 and result.stdout:
                return ("text", _decode_clipboard_bytes(result.stdout))

            return ("empty", None)

        except Exception as e:
            logger.error(f"클립보드 읽기 오류: {e}")
            return ("empty", None)

    def set_content(self, content_type: str, data: Any) -> bool:
        try:
            if content_type == "text":
                process = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE, env=_clean_env())
                process.communicate(data.encode("utf-8"))
                # v2.2 R1 privacy: INFO 로그에선 길이만, content 는 DEBUG 만
                logger.info(f"텍스트 클립보드 설정: {len(data)}자")
                logger.debug(f"  preview: {data[:50]}{'...' if len(data) > 50 else ''}")
                return True

            elif content_type == "image":
                return self._set_image_to_clipboard(data)

            elif content_type == "files":
                return self._set_files_to_clipboard(data)

        except Exception as e:
            logger.error(f"클립보드 설정 오류: {e}")
            return False

        return False

    def _set_files_to_clipboard(self, file_paths: List[str]) -> bool:
        """macOS 클립보드에 파일 경로 설정 (NSPasteboard)"""
        try:
            from AppKit import NSPasteboard, NSFilenamesPboardType
            pasteboard = NSPasteboard.generalPasteboard()
            pasteboard.clearContents()
            pasteboard.setPropertyList_forType_(file_paths, NSFilenamesPboardType)
            logger.info(f"파일 클립보드 설정: {len(file_paths)}개 항목")
            return True
        except ImportError:
            # PyObjC 없으면 osascript 폴백 — 단일 파일만 지원
            try:
                if file_paths:
                    safe_path = file_paths[0].replace('\\', '\\\\').replace('"', '\\"')
                    script = f'set the clipboard to (POSIX file "{safe_path}")'
                    subprocess.run(["osascript", "-e", script], check=True, timeout=5, env=_clean_env())
                    logger.info("파일 클립보드 설정 (osascript, 단일 파일)")
                    return True
            except Exception as e:
                logger.error(f"osascript 파일 설정 오류: {e}")
        except Exception as e:
            logger.error(f"파일 클립보드 설정 오류: {e}")
        return False

    def _get_image_from_clipboard(self) -> Optional[Image.Image]:
        """macOS 클립보드에서 이미지 가져오기"""
        try:
            from AppKit import NSPasteboard, NSPasteboardTypePNG, NSPasteboardTypeTIFF

            pasteboard = NSPasteboard.generalPasteboard()

            # PNG 먼저 시도
            png_data = pasteboard.dataForType_(NSPasteboardTypePNG)
            if png_data:
                return Image.open(io.BytesIO(png_data.bytes()))

            # TIFF 시도
            tiff_data = pasteboard.dataForType_(NSPasteboardTypeTIFF)
            if tiff_data:
                return Image.open(io.BytesIO(tiff_data.bytes()))

        except ImportError:
            # PyObjC가 없으면 osascript 사용
            try:
                import tempfile
                import os

                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                    temp_path = f.name

                script = f"""
                    set theFile to POSIX file "{temp_path}"
                    try
                        set imgData to the clipboard as «class PNGf»
                        set fileRef to open for access theFile with write permission
                        write imgData to fileRef
                        close access fileRef
                        return "success"
                    on error
                        return "no image"
                    end try
                """

                result = subprocess.run(
                    ["osascript", "-e", script], capture_output=True, text=True, env=_clean_env()
                )

                if result.stdout.strip() == "success" and os.path.exists(temp_path):
                    image = Image.open(temp_path)
                    os.unlink(temp_path)
                    return image

                if os.path.exists(temp_path):
                    os.unlink(temp_path)

            except Exception as e:
                logger.error(f"osascript 이미지 가져오기 오류: {e}")

        except Exception as e:
            logger.error(f"이미지 가져오기 오류: {e}")

        return None

    def _set_image_to_clipboard(self, data: str) -> bool:
        """macOS 클립보드에 이미지 설정"""
        try:
            from AppKit import NSPasteboard, NSPasteboardTypePNG, NSData

            image_bytes = base64.b64decode(data)
            ns_data = NSData.dataWithBytes_length_(image_bytes, len(image_bytes))

            pasteboard = NSPasteboard.generalPasteboard()
            pasteboard.clearContents()
            pasteboard.setData_forType_(ns_data, NSPasteboardTypePNG)

            logger.info("이미지 클립보드 설정 완료")
            return True

        except ImportError:
            # PyObjC가 없으면 osascript 사용
            try:
                import tempfile
                import os

                image_bytes = base64.b64decode(data)
                image = Image.open(io.BytesIO(image_bytes))

                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                    image.save(f, format="PNG")
                    temp_path = f.name

                script = f"""
                    set theFile to POSIX file "{temp_path}"
                    set theImage to read theFile as «class PNGf»
                    set the clipboard to theImage
                """

                subprocess.run(["osascript", "-e", script], check=True, env=_clean_env())
                os.unlink(temp_path)

                logger.info("이미지 클립보드 설정 완료 (osascript)")
                return True

            except Exception as e:
                logger.error(f"osascript 이미지 설정 오류: {e}")
                return False

        except Exception as e:
            logger.error(f"이미지 설정 오류: {e}")
            return False


# ---------------------------------------------------------------------------
# Linux 클립보드 핸들러
# ---------------------------------------------------------------------------

class LinuxClipboard:
    """
    Linux 클립보드 핸들러

    변경 감지 전략 (우선순위):
    1. KDE Klipper D-Bus 시그널 (subprocess 0건, 이벤트 기반)
    2. wl-paste --watch (Wayland, 장기 실행 프로세스 1개, 이벤트 기반)
    3. 폴링 (X11 폴백, 주기적 subprocess 호출)

    wl-paste --watch 모드에서는 변경이 감지될 때만 콘텐츠를 읽으므로
    subprocess 호출이 최소화되고 커서 깜빡임/IME 간섭이 없다.
    """

    def __init__(self):
        self.session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        self.is_wayland = self.session_type == "wayland"
        self.tool: Optional[str] = None

        # Klipper D-Bus 사용 가능 여부 (KDE Plasma)
        self._klipper_iface = None
        self._klipper_change_detected = False
        self._klipper_cached_content: Tuple[str, Any] = ("empty", None)
        self._glib_ctx = None  # GLib MainContext (시그널 수동 처리용)

        # wl-paste --watch 프로세스
        self._watch_proc: Optional[subprocess.Popen] = None
        self._watch_change_detected = False
        self._watch_cached_content: Tuple[str, Any] = ("empty", None)
        self._watch_thread: Optional[Any] = None

        # Klipper D-Bus 초기화 시도 (KDE Plasma Wayland)
        if self.is_wayland:
            self._init_klipper_dbus()

        # Klipper가 안 되면 CLI 도구 탐색
        if not self._klipper_iface:
            self._init_cli_tools()

        # wl-paste를 찾았으면 wl-copy도 있다고 가정
        self._use_wl = self.tool == "wl-paste"

        # Klipper D-Bus가 없는 Wayland 환경에서 wl-paste --watch 시작
        if not self._klipper_iface and self._use_wl:
            self._start_watch()

        logger.info(
            f"Linux 클립보드 초기화: 세션={self.session_type or 'unknown'}, "
            f"도구={'klipper-dbus' if self._klipper_iface else ('wl-paste-watch' if self._watch_proc else self.tool)}, "
            f"wayland모드={self._use_wl}"
        )

    def _init_klipper_dbus(self):
        """KDE Klipper D-Bus 연결 + 시그널 구독 (GLib MainLoop 없음)

        별도 GLib MainLoop 스레드를 만들면 pystray의 Gtk 루프와 충돌하여
        CPU 스핀(199%)과 메모리 손상이 발생한다.
        대신 MainContext.iteration(False)으로 폴링 시점에 시그널을 수동 처리한다.
        트레이 모드에서는 pystray의 Gtk 루프가 시그널을 자동 처리해준다.
        """
        try:
            import dbus
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib

            DBusGMainLoop(set_as_default=True)
            bus = dbus.SessionBus()
            klipper = bus.get_object("org.kde.klipper", "/klipper")
            self._klipper_iface = dbus.Interface(
                klipper, "org.kde.klipper.klipper"
            )

            # 연결 테스트
            self._klipper_iface.getClipboardContents()

            # 변경 시그널 구독
            bus.add_signal_receiver(
                self._on_klipper_changed,
                signal_name="clipboardHistoryUpdated",
                dbus_interface="org.kde.klipper.klipper",
            )

            # GLib MainContext 참조 저장 (수동 iteration용)
            self._glib_ctx = GLib.MainContext.default()

            # 첫 읽기는 수행
            self._klipper_change_detected = True

            # wl-copy/wl-paste도 이미지/파일 전송용으로 확인
            import shutil
            if shutil.which("wl-paste"):
                self.tool = "wl-paste"
            else:
                self.tool = None

            logger.info("Klipper D-Bus 연결 성공 (시그널 + 수동 iteration 모드)")

        except Exception as e:
            logger.info(f"Klipper D-Bus 사용 불가 ({e}), CLI 도구로 전환")
            self._klipper_iface = None

    def _on_klipper_changed(self):
        """Klipper 클립보드 변경 D-Bus 시그널 핸들러"""
        self._klipper_change_detected = True

    def _drain_dbus_signals(self):
        """대기 중인 D-Bus 시그널을 처리한다 (non-blocking)."""
        if self._glib_ctx:
            # 대기 중인 이벤트를 모두 처리 (최대 10회로 제한)
            for _ in range(10):
                if not self._glib_ctx.iteration(False):
                    break

    def _init_cli_tools(self):
        """xclip/xsel/wl-paste CLI 도구 탐색"""
        if self.is_wayland:
            search_order = ["wl-paste", "xclip", "xsel"]
        else:
            search_order = ["xclip", "xsel"]

        for tool in search_order:
            try:
                subprocess.run(
                    [tool, "--version"], capture_output=True, timeout=5
                )
                self.tool = tool
                break
            except FileNotFoundError:
                continue
            except Exception:
                import shutil
                if shutil.which(tool):
                    self.tool = tool
                    break
                continue

        if not self.tool and not self._klipper_iface:
            import shutil
            pkg = "wl-clipboard" if self.is_wayland else "xclip"
            if shutil.which("pacman"):
                install_cmd = f"sudo pacman -S {pkg}"
            elif shutil.which("apt"):
                install_cmd = f"sudo apt install {pkg}"
            elif shutil.which("dnf"):
                install_cmd = f"sudo dnf install {pkg}"
            else:
                install_cmd = f"패키지 매니저로 {pkg}을 설치하세요"

            raise SystemExit(
                f"\n[오류] 클립보드 도구가 없습니다.\n"
                f"  세션 타입: {self.session_type or '알 수 없음'}\n"
                f"  설치 명령어: {install_cmd}\n"
            )

    def _start_watch(self):
        """wl-paste --watch 프로세스를 시작하여 클립보드 변경을 이벤트로 감지한다.

        --watch 모드는 클립보드가 변경될 때마다 지정한 명령을 실행한다.
        여기서는 'cat'을 실행하여 stdout으로 변경 알림을 받는다.
        변경이 감지되면 _watch_change_detected 플래그를 설정하고,
        get_content()에서 실제 콘텐츠를 읽는다.
        """
        import threading

        try:
            # --watch는 변경 시마다 명령을 실행. 'echo'로 변경 알림만 받음
            self._watch_proc = subprocess.Popen(
                ["wl-paste", "--watch", "echo", "CHANGED"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )

            def _reader():
                """stdout을 읽으며 변경 이벤트를 감지하는 백그라운드 스레드"""
                try:
                    while self._watch_proc and self._watch_proc.poll() is None:
                        line = self._watch_proc.stdout.readline()
                        if line:
                            self._watch_change_detected = True
                except Exception:
                    pass

            self._watch_thread = threading.Thread(target=_reader, daemon=True)
            self._watch_thread.start()
            # 첫 실행 시 현재 클립보드 읽기
            self._watch_change_detected = True
            logger.info("wl-paste --watch 프로세스 시작 (이벤트 기반 변경 감지)")

        except Exception as e:
            logger.warning(f"wl-paste --watch 시작 실패, 폴링 모드로 전환: {e}")
            self._watch_proc = None

    def cleanup(self):
        """리소스 정리"""
        if self._watch_proc:
            try:
                self._watch_proc.terminate()
                self._watch_proc.wait(timeout=2)
            except Exception:
                try:
                    self._watch_proc.kill()
                except Exception:
                    pass
            self._watch_proc = None

    # ── 읽기 ──────────────────────────────────────────────────────────

    def get_files(self) -> Optional[List[str]]:
        """클립보드에서 파일/폴더 URI 목록을 반환한다."""
        try:
            if self.tool == "wl-paste":
                result = subprocess.run(
                    ["wl-paste", "-t", "text/uri-list"],
                    capture_output=True, text=True, timeout=5,
                )
            elif self.tool == "xclip":
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-t", "text/uri-list", "-o"],
                    capture_output=True, text=True, timeout=5,
                )
            else:
                return None

            if result.returncode == 0 and result.stdout.strip():
                import urllib.parse
                paths = []
                for line in result.stdout.strip().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("file://"):
                        path = urllib.parse.unquote(
                            urllib.parse.urlparse(line).path
                        )
                        paths.append(path)
                if paths:
                    return paths
        except Exception as e:
            logger.error(f"파일 목록 가져오기 오류: {e}")
        return None

    def _get_image_from_clipboard(self) -> Optional[Image.Image]:
        """클립보드에서 이미지 가져오기"""
        try:
            if self.tool == "wl-paste":
                result = subprocess.run(
                    ["wl-paste", "-t", "image/png"],
                    capture_output=True, timeout=5,
                )
            elif self.tool == "xclip":
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                    capture_output=True, timeout=5,
                )
            else:
                return None
            if result.returncode == 0 and result.stdout:
                return Image.open(io.BytesIO(result.stdout))
        except Exception as e:
            logger.error(f"이미지 가져오기 오류: {e}")
        return None

    def _read_content_via_cli(self) -> Tuple[str, Any]:
        """subprocess로 클립보드 내용을 읽는다 (이미지/파일 포함)."""
        try:
            files = self.get_files()
            if files:
                return ("files", files)

            image = self._get_image_from_clipboard()
            if image:
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                return ("image", base64.b64encode(buffer.getvalue()).decode("utf-8"))

            # 바이트로 읽고 인코딩 자동 탐지 — RDP/Windows 원격 주입 대비
            if self.tool == "wl-paste":
                result = subprocess.run(
                    ["wl-paste", "--no-newline"],
                    capture_output=True, timeout=5,
                )
            elif self.tool == "xclip":
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True, timeout=5,
                )
            elif self.tool == "xsel":
                result = subprocess.run(
                    ["xsel", "--clipboard", "--output"],
                    capture_output=True, timeout=5,
                )
            else:
                return ("empty", None)

            if result.returncode == 0 and result.stdout:
                return ("text", _decode_clipboard_bytes(result.stdout))
            return ("empty", None)
        except Exception as e:
            logger.error(f"클립보드 읽기 오류: {e}")
            return ("empty", None)

    def get_content(self) -> Tuple[str, Any]:
        """
        클립보드 내용을 반환한다.

        Klipper D-Bus 모드: 시그널이 올 때만 읽기. 평상시 호출 0건.
        wl-paste --watch 모드: 변경 이벤트가 올 때만 읽기. 평상시 호출 0건.
        폴링 모드 (X11): 매번 subprocess로 읽기.
        """
        if self._klipper_iface:
            # 대기 중인 D-Bus 시그널 처리 (--no-tray 모드에서 필수)
            self._drain_dbus_signals()

            # D-Bus 모드: 변경 시그널이 올 때만 실제 읽기
            if not self._klipper_change_detected:
                return self._klipper_cached_content

            self._klipper_change_detected = False
            try:
                # 텍스트는 D-Bus로 직접 (subprocess 0건)
                text = str(self._klipper_iface.getClipboardContents())

                # 이미지/파일은 wl-paste로 (변경 시에만)
                if self.tool:
                    files = self.get_files()
                    if files:
                        self._klipper_cached_content = ("files", files)
                        return self._klipper_cached_content

                    image = self._get_image_from_clipboard()
                    if image:
                        buffer = io.BytesIO()
                        image.save(buffer, format="PNG")
                        img_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
                        self._klipper_cached_content = ("image", img_data)
                        return self._klipper_cached_content

                if text:
                    self._klipper_cached_content = ("text", text)
                else:
                    self._klipper_cached_content = ("empty", None)

            except Exception as e:
                logger.error(f"Klipper D-Bus 읽기 오류: {e}")

            return self._klipper_cached_content

        elif self._watch_proc:
            # wl-paste --watch 모드: 변경 이벤트가 올 때만 실제 읽기
            if not self._watch_change_detected:
                return self._watch_cached_content

            self._watch_change_detected = False
            self._watch_cached_content = self._read_content_via_cli()
            return self._watch_cached_content

        else:
            # 폴링 모드 (X11 또는 --watch 미지원)
            return self._read_content_via_cli()

    # ── 쓰기 ──────────────────────────────────────────────────────────

    def set_content(self, content_type: str, data: Any) -> bool:
        try:
            if content_type == "text":
                # Wayland/Plasma 에서 Klipper D-Bus setClipboardContents 는
                # Klipper 히스토리에는 들어가지만 실제 active 클립보드에는 반영
                # 안 되는 알려진 동작. 따라서 **wl-copy 를 1순위 쓰기 경로로**
                # 사용하고 Klipper D-Bus 는 최후 폴백으로만 유지.
                import shutil as _sh

                # 1순위: wl-copy (Wayland 표준)
                # v2.2 R1 privacy: INFO 로그에선 길이만, content preview 는 DEBUG 만
                if _sh.which("wl-copy"):
                    try:
                        process = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
                        process.communicate(data.encode("utf-8"))
                        logger.info(f"텍스트 클립보드 설정 (wl-copy): {len(data)}자")
                        logger.debug(f"  preview: {data[:50]}{'...' if len(data) > 50 else ''}")
                        return True
                    except Exception as e:
                        logger.warning(f"wl-copy 실패 ({e}), 다음 도구 시도")

                # 2순위: X11 tools
                if self.tool == "xclip":
                    cmd = ["xclip", "-selection", "clipboard"]
                elif self.tool == "xsel":
                    cmd = ["xsel", "--clipboard", "--input"]
                else:
                    cmd = None

                if cmd is not None:
                    process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                    process.communicate(data.encode("utf-8"))
                    logger.info(f"텍스트 클립보드 설정 ({cmd[0]}): {len(data)}자")
                    logger.debug(f"  preview: {data[:50]}{'...' if len(data) > 50 else ''}")
                    return True

                # 최후 폴백: Klipper D-Bus (히스토리에만 들어갈 수 있음)
                if self._klipper_iface:
                    self._klipper_iface.setClipboardContents(data)
                    logger.info("텍스트 클립보드 설정 (Klipper D-Bus 폴백)")
                    return True

                return False

            elif content_type == "image":
                return self._set_image_to_clipboard(data)

            elif content_type == "files":
                return self._set_files_to_clipboard(data)

        except Exception as e:
            logger.error(f"클립보드 설정 오류: {e}")
            return False
        return False

    def _set_files_to_clipboard(self, file_paths: List[str]) -> bool:
        """클립보드에 파일 경로를 text/uri-list로 설정"""
        try:
            import urllib.parse
            uri_list = "\n".join(
                "file://" + urllib.parse.quote(p, safe="/:")
                for p in file_paths
            ) + "\n"

            if self.tool == "wl-paste" or self._use_wl:
                cmd = ["wl-copy", "-t", "text/uri-list"]
            elif self.tool == "xclip":
                cmd = ["xclip", "-selection", "clipboard", "-t", "text/uri-list"]
            else:
                return False

            process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            process.communicate(uri_list.encode("utf-8"))
            logger.info(f"파일 클립보드 설정: {len(file_paths)}개 항목")
            return True
        except Exception as e:
            logger.error(f"파일 클립보드 설정 오류: {e}")
            return False

    def _set_image_to_clipboard(self, data: str) -> bool:
        """클립보드에 이미지 설정"""
        try:
            image_bytes = base64.b64decode(data)
            if self.tool == "wl-paste":
                cmd = ["wl-copy", "-t", "image/png"]
            elif self.tool == "xclip":
                cmd = ["xclip", "-selection", "clipboard", "-t", "image/png"]
            else:
                return False

            process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            process.communicate(image_bytes)
            logger.info("이미지 클립보드 설정 완료")
            return True
        except Exception as e:
            logger.error(f"이미지 설정 오류: {e}")
        return False
