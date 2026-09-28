"""2026-09-28 UX 검토 A5 — 이력 미리보기 가짜 말줄임 회귀.

image/files 항목은 content 가 "[image]" 같은 표식이라 한국어 preview "[이미지]" 보다
글자 수가 많아, 잘린 것으로 오판돼 "[이미지] …" 로 표시됐다.
"""

from ui.history_window import _prepare_preview


def test_image_entry_has_no_ellipsis():
    entry = {"type": "image", "content": "[image]", "preview": "[이미지]"}
    assert _prepare_preview(entry) == "[이미지]"


def test_files_entry_has_no_ellipsis():
    entry = {"type": "files", "content": "[files]", "preview": "[파일 3개]"}
    assert _prepare_preview(entry) == "[파일 3개]"


def test_multiline_text_still_marked_truncated():
    entry = {"type": "text", "content": "첫 줄\n둘째 줄", "preview": "첫 줄\n둘째 줄"}
    assert _prepare_preview(entry) == "첫 줄 …"


def test_short_text_unchanged():
    entry = {"type": "text", "content": "hello", "preview": "hello"}
    assert _prepare_preview(entry) == "hello"
