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


def test_text_copy_send_and_receive_paths_do_not_log_body(tmp_path, monkeypatch, caplog):
    """복사 → 모니터 → 이력 → 네트워크 송신, 그리고 수신 → 클립보드 설정 → 이력 전 경로에서
    클립보드 본문이 어느 레벨 로그에도 안 남는다."""
    import main
    from config import AppConfig
    from core import network as net
    from core.protocol import Protocol, MSG_CLIPBOARD, generate_peer_id

    caplog.set_level(logging.DEBUG)
    body = "clipboard body marker " + "z9" * 30

    # ── 송신 측 ──
    sender = main.InfiniteClipboard(AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
    ))
    monkeypatch.setattr(sender, "_get_history_file", lambda: str(tmp_path / "h1.json"))
    sent = []

    class _CaptureSock:
        def sendall(self, b):
            sent.append(b)

    client = net.NetworkClient("127.0.0.1", 1, "k", "test-device")
    client.running = True
    client.connected = True
    client.socket = _CaptureSock()
    sender.client = client
    monkeypatch.setattr(sender, "_is_network_active", lambda: True)
    monkeypatch.setattr(sender.clipboard, "has_changed", lambda: (True, "text", body))
    sender.running = True

    def _stop_after_one(_s):
        sender.running = False

    monkeypatch.setattr(main.time, "sleep", _stop_after_one)
    sender._monitor_clipboard()

    assert len(sent) == 1
    frame = sent[0]
    message = client.protocol.parse_message(frame[Protocol.HEADER_SIZE:])
    assert message["type"] == MSG_CLIPBOARD and message["data"]["content"] == body

    # ── 수신 측 (실제 LinuxClipboard.set_content, 쓰기 도구만 가짜) ──
    receiver = main.InfiniteClipboard(AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
    ))
    monkeypatch.setattr(receiver, "_get_history_file", lambda: str(tmp_path / "h2.json"))
    handler = _make_linux_handler(monkeypatch, tool="xclip")
    receiver.clipboard._handler = handler
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cm, "_write_via_subprocess", lambda *a, **k: True)
    receiver._on_client_message(message)

    assert receiver.clipboard_history[0]["content"] == body
    assert any("[클립보드] 수신: text" in r.getMessage() for r in caplog.records)
    for r in caplog.records:
        rendered = r.getMessage() + (logging.Formatter().formatException(r.exc_info) if r.exc_info else "")
        assert body[:20] not in rendered, f"본문이 로그에 남음: {r.name} {r.levelname}"
    assert body[:20] not in caplog.text


def test_history_recopy_does_not_log_preview(caplog):
    """이력 창 재복사 로그가 미리보기(본문 앞 80자)를 남기지 않는다 — 창 생성 없이 메서드만."""
    import pytest
    pytest.importorskip("customtkinter")
    from ui.history_window import HistoryWindow

    caplog.set_level(logging.DEBUG)
    body = "clipboard body marker " + "h4" * 30

    class _FakeSelf:
        class clipboard_manager:
            @staticmethod
            def set_clipboard_content(kind, data):
                return True

    HistoryWindow._recopy(_FakeSelf(), {"type": "text", "content": body, "preview": body[:80]})
    HistoryWindow._recopy(_FakeSelf(), {"type": "image", "content": "[image]", "preview": body[:80]})
    HistoryWindow._recopy(_FakeSelf(), {"type": "text", "content": None, "preview": body[:80]})
    assert "재복사 성공=True" in caplog.text
    assert body[:20] not in caplog.text
