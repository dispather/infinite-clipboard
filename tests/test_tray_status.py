"""2026-09-28 UX 검토 B1/B2/B5 — 트레이 상태 표시 순수 함수 (헤드리스).

ui/tray_status.py 는 pystray/Tk 를 import 하지 않으므로 CI 헤드리스에서도 돈다.
"""

import re
import time

import pytest

from ui import tray_status as ts

HANGUL = re.compile(r"[가-힣]")


def _client(**kw):
    snap = {"mode": "client", "connected": False, "server_host": "100.64.0.9", "port": 9999,
            "peers": [], "startup_error": False, "client_error": "", "active": [],
            "last_received": None}
    snap.update(kw)
    return snap


def _server(**kw):
    snap = _client(mode="server", connected=True)
    snap.update(kw)
    return snap


# ── 사유 분류: 실제 NetworkClient 예외 문구 기준 ──────────────────────

@pytest.mark.parametrize("err, code", [
    ("", ""),
    ("server version '2.2' != client '3.0' - hard break, upgrade required", "version"),
    ("v2.2 expects MSG_HANDSHAKE_CHALLENGE first, got 'x'. Server is likely v2.1 or older - hard break", "version"),
    ("ACK 헤더 수신 실패", "auth"),
    ("server HMAC verification failed - wrong key on server", "auth"),
    ("[Errno 111] Connection refused", "refused"),
    ("[WinError 10061] 대상 컴퓨터에서 연결을 거부했으므로 연결하지 못했습니다", "refused"),
    ("timed out", "unreachable"),
    ("[Errno 113] No route to host", "unreachable"),
    ("[Errno -2] Name or service not known", "unreachable"),
    ("challenge 헤더 수신 실패", "other"),
])
def test_classify_client_error(err, code):
    assert ts.classify_client_error(err) == code


# ── B2: 색은 연결 상태만, 전송은 배지 ─────────────────────────────────

def test_server_waiting_is_amber_without_badge():
    assert ts.icon_state(_server(peers=[])) == ("amber", None)


def test_transfer_keeps_connection_color_and_adds_busy_badge():
    """B2 회귀: 예전엔 전송 중이면 아이콘 전체가 amber(=서버 대기색)였다."""
    snap = _server(peers=["mac"], active=[{"filename": "a", "direction": "receive"}])
    assert ts.icon_state(snap) == ("green", "busy")
    snap = _client(connected=True, active=[{"filename": "a", "direction": "send"}])
    assert ts.icon_state(snap) == ("green", "busy")


def test_server_startup_failure_is_red():
    assert ts.icon_state(_server(startup_error=True))[0] == "red"


# ── B5: 방금 받음 배지 ────────────────────────────────────────────────

def test_recent_receive_shows_new_badge_then_expires():
    now = time.time()
    snap = _client(connected=True, last_received={"name": "n.md", "size": 3, "at": now - 1})
    assert ts.icon_state(snap, now=now) == ("green", "new")
    later = now + ts.RECEIVED_BADGE_SECONDS + 1
    assert ts.icon_state(snap, now=later) == ("green", None)


def test_last_received_line_persists_after_badge_expires():
    snap = _client(connected=True, last_received={"name": "notes.md", "size": 3100, "at": 0})
    lines = ts.status_lines(snap, "ko")
    assert any(line.startswith("최근 받음: notes.md") for line in lines), lines


# ── B1: 상태 줄 ──────────────────────────────────────────────────────

def test_server_lists_connected_peer_names():
    lines = ts.status_lines(_server(peers=["mac-studio", "sh-knu"]), "ko")
    assert lines[0] == "● 서버 실행 중 — 기기 2대 연결"
    assert "    · mac-studio" in lines and "    · sh-knu" in lines


def test_many_peers_are_capped():
    peers = [f"pc{i}" for i in range(12)]
    lines = ts.status_lines(_server(peers=peers), "ko")
    assert lines[-1].strip() == "· 외 4대"


def test_client_disconnected_shows_reason():
    lines = ts.status_lines(_client(client_error="ACK 헤더 수신 실패"), "ko")
    assert lines[0] == "○ 서버에 연결 안 됨 — 100.64.0.9:9999"
    assert "인증 키가 서버와 다를 수 있어요" in lines[1]


def test_client_disconnected_without_error_says_connecting():
    lines = ts.status_lines(_client(), "ko")
    assert lines[1].strip() == "연결 시도 중…"


def test_active_transfer_line():
    snap = _client(connected=True, active=[
        {"filename": "a.zip", "direction": "receive"}, {"filename": "b", "direction": "send"}])
    assert "↓ 받는 중: a.zip 외 1건" in ts.status_lines(snap, "ko")


def test_long_names_are_shortened():
    snap = _client(connected=True, active=[{"filename": "x" * 100, "direction": "send"}])
    line = [l for l in ts.status_lines(snap, "ko") if l.startswith("↑")][0]
    assert line.endswith("…") and len(line) < 60


def test_tooltip_uses_first_line_without_glyph():
    assert ts.tooltip_text(_client(connected=True), "ko") == "Infinite Clipboard — 서버에 연결됨 — 100.64.0.9"


@pytest.mark.parametrize("snap", [
    _server(peers=["a"] * 10, active=[{"filename": "f", "direction": "send"}, {"filename": "g"}],
            last_received={"name": "r", "size": 1, "at": 0}),
    _server(peers=[]),
    _server(startup_error=True),
    _client(connected=True),
    _client(client_error="hard break"),
    _client(client_error="ACK 헤더 수신 실패"),
    _client(client_error="[Errno 111] Connection refused"),
    _client(client_error="timed out"),
    _client(client_error="weird"),
    _client(),
])
def test_english_has_no_korean(snap):
    """새 상태 문구가 번역에서 빠지면 영어 UI 에 한국어가 섞인다(A6 과 같은 결함 부류)."""
    lines = ts.status_lines(snap, "en") + [ts.tooltip_text(snap, "en")]
    leaked = [l for l in lines if HANGUL.search(l)]
    assert not leaked, leaked


# ── 2026-09-29 자동 업데이트: 메뉴 라벨 ──────────────────────────


def _upd(version="3.0.99", phase="idle"):
    return {"update": {"version": version, "phase": phase}}


def test_update_label_none_without_update():
    assert ts.update_menu_label({}, "ko") is None
    assert ts.update_menu_label(_upd(version=None), "ko") is None


def test_update_label_states_ko():
    assert ts.update_menu_label(_upd(), "ko") == "업데이트 설치 (v3.0.99)"
    assert ts.update_menu_label(_upd(phase="downloading"), "ko") == "업데이트 다운로드 중…"


@pytest.mark.parametrize("snap", [
    _upd(), _upd(phase="downloading"),
])
def test_update_label_english_has_no_korean(snap):
    label = ts.update_menu_label(snap, "en")
    assert label and not HANGUL.search(label), label
    assert not HANGUL.search(ts.tr("업데이트 확인", "en"))
