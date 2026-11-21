"""Windows 원격 텍스트 쓰기 — 숫자로만 된 텍스트 회귀 (win-text-receive-heap-crash).

pywin32 ≤312 의 SetClipboardData(CF_UNICODETEXT, str) 는 인자를 먼저 핸들로 해석해
'201920' 같은 숫자 문자열을 가짜 HGLOBAL 로 넘긴다 → 오류 또는 힙 손상으로 조용히 사망
(pywin32 #2132, 수정 #2819 미배포). a5000 에서 원격 텍스트 수신 약 400번에 1번 사망.

  - 어느 OS 에서나: 가짜 win32clipboard 로 텍스트 경로가 SetClipboardData 에 str 을
    넘기지 않고 SetClipboardText 를 쓰는지 본다.
  - Windows 에서만: 실제 클립보드에 숫자 텍스트를 쓰고 그대로 읽히는지 본다.
"""

import sys
import types

import pytest

from core import clipboard_manager as cm


class _FakeWin32Clipboard(types.SimpleNamespace):
    def __init__(self):
        super().__init__(calls=[])

    def OpenClipboard(self, *a):
        self.calls.append(("open",))

    def CloseClipboard(self):
        self.calls.append(("close",))

    def EmptyClipboard(self):
        self.calls.append(("empty",))

    def SetClipboardData(self, fmt, data):
        self.calls.append(("data", fmt, data))

    def SetClipboardText(self, text, fmt):
        self.calls.append(("text", fmt, text))


def _fake_handler():
    h = cm.WindowsClipboard.__new__(cm.WindowsClipboard)
    h.win32clipboard = _FakeWin32Clipboard()
    h.win32con = types.SimpleNamespace(CF_UNICODETEXT=13, CF_DIB=8)
    h.time = __import__("time")
    h._clipboard_open = False
    h._max_retries = 1
    h._retry_delay = 0
    return h


@pytest.mark.parametrize("text", ["201920", "18621312165", "hello", "010-1234-5678"])
def test_text_never_goes_through_set_clipboard_data(text):
    h = _fake_handler()
    assert h.set_content("text", text) is True
    calls = h.win32clipboard.calls
    assert ("text", 13, text) in calls
    assert not any(c[0] == "data" and isinstance(c[2], str) for c in calls), \
        "str 을 SetClipboardData 에 넘기면 pywin32 가 숫자 텍스트를 핸들로 오해한다"


@pytest.mark.skipif(sys.platform != "win32", reason="실제 Windows 클립보드 전용")
@pytest.mark.parametrize("text", ["201920", "18621312165", "0", "한글 123"])
def test_numeric_text_roundtrip_on_real_clipboard(text):
    import win32clipboard
    import win32con

    h = cm.WindowsClipboard()
    assert h.set_content("text", text) is True
    win32clipboard.OpenClipboard()
    try:
        got = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()
    assert got == text
