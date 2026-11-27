"""에러 로그 (c)(e) 회귀 — 폴링 반복 로그 정리 + 클립보드 본문 로그 제거.

(c) «파일/폴더 클립보드 감지» INFO 가 폴링(약 0.5초)마다 반복(22초 44줄), 해석 못 하는
    이미지가 폴링마다 다시 해석돼 ERROR 반복(맥 30초 56줄).
(e) set_content 가 DEBUG «preview:» 로 클립보드 앞 50자를 파일 로그에 남겼다.
"""

import logging

from core import clipboard_manager as cm

BROKEN_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100


class _FakeResult:
    def __init__(self, returncode=0, stdout=b""):
        self.returncode = returncode
        self.stdout = stdout


def _make_linux_handler(monkeypatch, tool="wl-paste"):
    monkeypatch.setattr(cm.LinuxClipboard, "_init_klipper_dbus", lambda self: None)
    monkeypatch.setattr(cm.LinuxClipboard, "_init_cli_tools", lambda self: setattr(self, "tool", tool))
    monkeypatch.setattr(cm.LinuxClipboard, "_start_watch", lambda self: None)
    return cm.LinuxClipboard()


def _detect_lines(caplog):
    return [r for r in caplog.records if "파일/폴더 클립보드 감지" in r.getMessage()]


def test_same_file_list_polled_10_times_logs_info_once(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="core.clipboard_manager")
    handler = cm.MacClipboard()
    monkeypatch.setattr(handler, "get_files", lambda: ["/tmp/a.txt", "/tmp/b"])
    for _ in range(10):
        assert handler.get_content()[0] == "files"
    lines = _detect_lines(caplog)
    assert len(lines) == 1
    assert lines[0].levelno == logging.INFO


def test_changed_file_list_logs_again(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="core.clipboard_manager")
    handler = cm.MacClipboard()
    current = {"files": ["/tmp/a.txt"]}
    monkeypatch.setattr(handler, "get_files", lambda: current["files"])
    handler.get_content()
    handler.get_content()
    current["files"] = ["/tmp/c.txt"]
    handler.get_content()
    assert len(_detect_lines(caplog)) == 2


def test_same_files_after_other_content_logs_again(monkeypatch, caplog):
    """파일 → 다른 내용 → 같은 파일 재복사는 «바뀐 것» 이라 다시 1줄."""
    caplog.set_level(logging.DEBUG, logger="core.clipboard_manager")
    handler = cm.MacClipboard()
    current = {"files": ["/tmp/a.txt"]}
    monkeypatch.setattr(handler, "get_files", lambda: current["files"])
    monkeypatch.setattr(handler, "_get_image_from_clipboard", lambda: None)
    monkeypatch.setattr(cm.subprocess, "run", lambda *a, **k: _FakeResult(0, b"hello"))
    handler.get_content()
    current["files"] = None
    assert handler.get_content()[0] == "text"
    current["files"] = ["/tmp/a.txt"]
    handler.get_content()
    assert len(_detect_lines(caplog)) == 2


def test_same_broken_image_10_times_logs_error_once(monkeypatch, caplog):
    """리눅스 폴링 경로 — 같은 깨진 PNG 를 10회 읽어도 ERROR 1줄, 재해석 없음."""
    caplog.set_level(logging.DEBUG, logger="core.clipboard_manager")
    handler = _make_linux_handler(monkeypatch)

    def fake_run(cmd, **kw):
        if "text/uri-list" in cmd:
            return _FakeResult(1, "")
        if "image/png" in cmd:
            return _FakeResult(0, BROKEN_PNG)
        return _FakeResult(0, b"")

    monkeypatch.setattr(cm.subprocess, "run", fake_run)
    opens = []
    real_open = cm.Image.open
    monkeypatch.setattr(cm.Image, "open", lambda *a, **k: (opens.append(1), real_open(*a, **k))[1])

    for _ in range(10):
        handler._read_content_via_cli()

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "이미지 가져오기 오류" in errors[0].getMessage()
    assert len(opens) == 1


def test_new_broken_image_after_good_one_logs_again(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="core.clipboard_manager")
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (2, 2)).save(buf, format="PNG")
    good = buf.getvalue()

    holder = object.__new__(cm.MacClipboard)
    assert cm._decode_image_once(holder, BROKEN_PNG) is None
    assert cm._decode_image_once(holder, BROKEN_PNG) is None
    assert cm._decode_image_once(holder, good) is not None
    assert cm._decode_image_once(holder, BROKEN_PNG) is None
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 2


def test_windows_dib_broken_10_times_logs_error_once(caplog):
    caplog.set_level(logging.DEBUG, logger="core.clipboard_manager")
    holder = object.__new__(cm.WindowsClipboard)
    for _ in range(10):
        assert cm.WindowsClipboard._dib_to_image(holder, b"\x00" * 64) is None
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1


def test_text_copy_does_not_log_clipboard_text(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    body = "clipboard body marker " + "q7" * 30
    handler = _make_linux_handler(monkeypatch, tool="xclip")
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cm, "_write_via_subprocess", lambda *a, **k: True)

    assert handler.set_content("text", body) is True
    assert body[:20] not in caplog.text
    assert f"{len(body)}자" in caplog.text


def test_no_preview_logging_left_in_source():
    """4 OS 쓰기 경로 모두 — 본문 미리보기 로그가 소스에 남아 있지 않다."""
    import inspect
    src = inspect.getsource(cm)
    assert "preview: {data" not in src
