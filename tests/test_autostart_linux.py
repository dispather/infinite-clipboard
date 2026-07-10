"""M10 회귀 — Linux .desktop Exec= 필드가 공백 포함 경로를 따옴표 없이 써서
autostart 가 깨지던 문제.

2026-07-03 감사 M10: `_linux_enable`이 동적으로 생성하는 .desktop 파일의
`Exec={exe_path}` 가 따옴표 없이 그대로 기록됐다. freedesktop Desktop Entry
스펙에서 Exec= 값은 공백으로 인자를 분리하므로, 설치 경로에 공백이 있으면
(`/home/user/My Apps/...` 등) 실행 파일 경로가 여러 인자로 쪼개져 autostart
가 조용히 실패한다. Windows(`_windows_enable`)는 이미 같은 패턴으로 처리하고
있었다 — Linux 만 빠져 있었다.
"""

import pathlib

from core import autostart

_SYSTEM_DESKTOP = "/usr/share/applications/infinite-clipboard.desktop"
_original_exists = pathlib.Path.exists


def _fake_exists_hiding_system_desktop(self):
    """이 dev 머신엔 실제 설치된 infinite-clipboard.desktop 이 있을 수 있어,
    시스템 설치 심볼릭 링크 분기 대신 동적 생성 분기를 강제로 타게 한다.
    그 경로만 '없음'으로 위장하고 나머지는 실제 exists() 로 위임."""
    if str(self) == _SYSTEM_DESKTOP:
        return False
    return _original_exists(self)


def test_linux_desktop_exec_quotes_path_with_spaces(tmp_path, monkeypatch):
    target = tmp_path / "infinite-clipboard.desktop"
    monkeypatch.setattr(autostart, "_linux_desktop_path", lambda: target)
    monkeypatch.setattr(pathlib.Path, "exists", _fake_exists_hiding_system_desktop)

    exe_with_spaces = "/home/user/My Apps/Infinite Clipboard/InfiniteClipboard"
    ok = autostart._linux_enable(exe_with_spaces)

    assert ok is True
    content = target.read_text(encoding="utf-8")
    exec_line = next(line for line in content.splitlines() if line.startswith("Exec="))
    assert exec_line == f'Exec="{exe_with_spaces}"', (
        f"M10 회귀 — Exec= 값이 따옴표로 감싸지지 않음: {exec_line!r}"
    )


def test_linux_desktop_exec_does_not_double_quote_already_quoted_path(tmp_path, monkeypatch):
    """개발 모드(_executable_path)는 이미 '"python3" "main.py"' 형태로 quoted
    문자열을 준다 — 이중으로 감싸면 안 된다."""
    target = tmp_path / "infinite-clipboard.desktop"
    monkeypatch.setattr(autostart, "_linux_desktop_path", lambda: target)
    monkeypatch.setattr(pathlib.Path, "exists", _fake_exists_hiding_system_desktop)

    already_quoted = '"/usr/bin/python3" "/home/user/My Project/main.py"'
    ok = autostart._linux_enable(already_quoted)

    assert ok is True
    content = target.read_text(encoding="utf-8")
    exec_line = next(line for line in content.splitlines() if line.startswith("Exec="))
    assert exec_line == f"Exec={already_quoted}", (
        f"이미 quoted 된 값을 이중으로 감쌈: {exec_line!r}"
    )
