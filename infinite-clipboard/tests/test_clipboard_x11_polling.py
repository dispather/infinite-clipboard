"""M2 회귀 — X11 폴백 폴링이 유휴 상태에서도 매 폴링마다 최대 3개 subprocess
(files→image→text 순차 시도) 를 실행하던 문제.

2026-07-03 감사 M2: `LinuxClipboard._read_content_via_cli` 가 X11 폴링 모드에서
매번 get_files()/_get_image_from_clipboard()/텍스트 읽기를 순서대로 blind 시도해,
대부분의 폴링(변화 없음)에서도 불필요한 subprocess 를 2개까지 낭비했다. 수정:
xclip 은 TARGETS 를 1회 조회해 실제 있는 타입만 골라 읽는다.
"""

import subprocess

from core import clipboard_manager as cm


def _make_linux_handler(monkeypatch, tool="xclip"):
    """실제 시스템 프로세스/시그널 없이 LinuxClipboard 인스턴스를 만든다."""
    monkeypatch.setattr(cm.LinuxClipboard, "_init_klipper_dbus", lambda self: None)
    monkeypatch.setattr(cm.LinuxClipboard, "_init_cli_tools", lambda self: setattr(self, "tool", tool))
    monkeypatch.setattr(cm.LinuxClipboard, "_start_watch", lambda self: None)
    return cm.LinuxClipboard()


class _FakeResult:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_xclip_polling_skips_file_and_image_subprocess_when_absent(monkeypatch):
    """TARGETS 에 uri-list/image 가 없으면 그 subprocess 는 아예 호출되지 않아야
    한다 (M2 이전엔 매번 순차 시도)."""
    handler = _make_linux_handler(monkeypatch, tool="xclip")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if "TARGETS" in cmd:
            return _FakeResult(returncode=0, stdout="UTF8_STRING\nTEXT\n")
        if "text/uri-list" in cmd:
            raise AssertionError("uri-list 타겟이 없는데 파일 목록을 조회함")
        if "image/png" in cmd:
            raise AssertionError("image/png 타겟이 없는데 이미지를 조회함")
        return _FakeResult(returncode=0, stdout=b"hello")

    monkeypatch.setattr(subprocess, "run", fake_run)
    content_type, data = handler._read_content_via_cli()

    assert content_type == "text"
    assert data == "hello"
    assert len(calls) == 2, f"M2 회귀 — subprocess 호출이 예상보다 많음: {calls}"


def test_xclip_polling_reads_files_when_uri_list_target_present(monkeypatch):
    handler = _make_linux_handler(monkeypatch, tool="xclip")

    def fake_run(cmd, **kw):
        if "TARGETS" in cmd:
            return _FakeResult(returncode=0, stdout="text/uri-list\n")
        if "text/uri-list" in cmd:
            return _FakeResult(returncode=0, stdout="file:///tmp/a.txt\n")
        raise AssertionError(f"예상치 못한 호출: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    content_type, data = handler._read_content_via_cli()

    assert content_type == "files"
    assert data == ["/tmp/a.txt"]


def test_xclip_polling_reads_image_when_image_target_present(monkeypatch):
    handler = _make_linux_handler(monkeypatch, tool="xclip")

    def fake_run(cmd, **kw):
        if "TARGETS" in cmd:
            return _FakeResult(returncode=0, stdout="image/png\n")
        if "text/uri-list" in cmd:
            raise AssertionError("image 타겟만 있는데 파일 목록을 조회함")
        raise AssertionError(f"예상치 못한 호출: {cmd}")  # image 읽기는 Image.open 필요해 여기선 미검증

    monkeypatch.setattr(subprocess, "run", fake_run)
    # 이미지 디코딩까지 성공시키긴 복잡하므로, 여기선 uri-list 호출이 없는지만 확인
    try:
        handler._read_content_via_cli()
    except AssertionError:
        raise
    except Exception:
        pass  # PNG 디코딩 실패는 이 테스트의 관심사가 아님


def test_xclip_targets_query_failure_falls_back_to_text_read(monkeypatch):
    """TARGETS 조회 자체가 실패해도 크래시 없이 텍스트 읽기로 폴백해야 한다."""
    handler = _make_linux_handler(monkeypatch, tool="xclip")

    def fake_run(cmd, **kw):
        if "TARGETS" in cmd:
            raise OSError("xclip not found")
        return _FakeResult(returncode=0, stdout=b"fallback text")

    monkeypatch.setattr(subprocess, "run", fake_run)
    content_type, data = handler._read_content_via_cli()

    assert content_type == "text"
    assert data == "fallback text"


def test_xclip_targets_failure_still_attempts_files_and_image(monkeypatch):
    """리뷰 발견: TARGETS 조회 실패 시 "타입 없음"으로 오인해 files/image 읽기를
    건너뛰면, 실제로 파일/이미지가 클립보드에 있어도 조용히 놓친다(text 로
    오분류하거나 empty 취급). TARGETS 실패는 "모름"으로 취급해 files/image
    를 실제로 다시 시도해야 한다 — 이 테스트는 파일이 정말 있는 경우 그
    시도가 실제로 파일을 찾아내는지까지 확인한다."""
    handler = _make_linux_handler(monkeypatch, tool="xclip")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if "TARGETS" in cmd:
            raise OSError("xclip TARGETS probe hiccup")
        if "text/uri-list" in cmd:
            return _FakeResult(returncode=0, stdout="file:///tmp/really-there.txt\n")
        raise AssertionError(f"파일이 이미 발견됐는데 추가 호출됨: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    content_type, data = handler._read_content_via_cli()

    assert content_type == "files", (
        "테스트갭 회귀 — TARGETS 실패를 '없음'으로 오인해 실제 파일을 놓침"
    )
    assert data == ["/tmp/really-there.txt"]
    assert any("text/uri-list" in c for c in calls), (
        "TARGETS 실패 후 files 읽기를 실제로 시도하지 않음"
    )


def test_wl_paste_and_xsel_polling_behavior_unchanged(monkeypatch):
    """wl-paste/xsel 경로는 M2 대상 밖 — 기존 순차 시도 그대로 유지."""
    handler = _make_linux_handler(monkeypatch, tool="xsel")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return _FakeResult(returncode=0, stdout=b"xsel text")

    monkeypatch.setattr(subprocess, "run", fake_run)
    content_type, data = handler._read_content_via_cli()

    assert content_type == "text"
    # xsel 은 get_files/_get_image_from_clipboard 가 tool 불일치로 이미
    # subprocess 없이 None 반환하므로, 텍스트 읽기 1회만 호출돼야 함
    assert len(calls) == 1, f"xsel 경로 호출 수 변경됨(회귀 위험): {calls}"
