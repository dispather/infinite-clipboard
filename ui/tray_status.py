"""트레이 상태 표시 — 순수 함수 (2026-09-28 UX 검토 B1/B2/B5).

`InfiniteClipboard.status_snapshot()` 이 만든 dict 를 받아 트레이 아이콘 상태
(색 + 배지), 메뉴 상단 상태 줄, 툴팁 문자열로 바꾼다. pystray/Tk 를 import 하지
않아 헤드리스에서 그대로 테스트된다(ui/tray.py 는 import 시점에 X11 접속을
시도해 헤드리스 테스트가 skip 된다 — tests/test_tray_launch_window.py 참조).

예전엔 트레이 색 4가지가 연결 상태의 유일한 신호라, 「안 된다」 때 서버 미접속 /
키 불일치 / 버전 차이를 구분할 방법이 로그뿐이었다(B1). 전송 중엔 아이콘 전체가
amber 로 바뀌어 "서버 대기" 와 같은 색이었고(B2), 작은 파일이 자동 수신돼도 아무
신호가 없었다(B5).
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

from core.file_transfer import format_size as _format_size
from ui.i18n import t as tr

# 받기 완료 후 트레이에 "방금 받음" 배지를 유지하는 시간(초)
RECEIVED_BADGE_SECONDS = 30

# 메뉴에 나열할 연결 기기 최대 수 — 넘치면 "외 N대"
_MAX_PEER_LINES = 8
_MAX_NAME_CHARS = 40


def classify_client_error(error: str) -> str:
    """NetworkClient.last_error 원문 → 사유 코드.

    "" (오류 없음) / "version" / "auth" / "refused" / "unreachable" / "other".
    "auth" 는 추정이다 — 키가 틀리면 서버가 응답 없이 소켓을 닫아 클라이언트엔
    "ACK 헤더 수신 실패" 로만 보이고, 드물게 다른 끊김도 같은 모양이라 문구를
    "다를 수 있음" 으로 둔다(서버 쪽 키가 틀린 경우만 확정 문구가 온다).
    """
    if not error:
        return ""
    e = error.lower()
    if "hard break" in e or "version mismatch" in e:
        return "version"
    if "wrong key" in e or "hmac" in e or "ack 헤더 수신 실패" in e:
        return "auth"
    if "refused" in e or "10061" in e:
        return "refused"
    if ("timed out" in e or "timeout" in e or "unreachable" in e or "no route" in e
            or "name or service not known" in e or "nodename" in e or "getaddrinfo" in e
            or "10060" in e or "10065" in e):
        return "unreachable"
    return "other"


_REASON_TEXT = {
    "version": "버전이 달라요 — 양쪽 모두 최신 버전으로 업데이트하세요",
    "auth": "인증 키가 서버와 다를 수 있어요",
    "refused": "서버가 연결을 거부했어요 — 서버 PC 에서 앱이 실행 중인지 확인하세요",
    "unreachable": "서버에 닿지 않아요 — 주소와 Tailscale 연결을 확인하세요",
    "other": "연결에 실패했어요 — 로그 보기에서 확인하세요",
}


def _short(name: str) -> str:
    name = name or ""
    return name if len(name) <= _MAX_NAME_CHARS else name[: _MAX_NAME_CHARS - 1] + "…"


def received_badge_active(snap: dict, now: Optional[float] = None) -> bool:
    last = snap.get("last_received")
    if not last:
        return False
    now = time.time() if now is None else now
    return 0 <= now - float(last.get("at", 0)) < RECEIVED_BADGE_SECONDS


def icon_state(snap: dict, now: Optional[float] = None) -> Tuple[str, Optional[str]]:
    """(색, 배지). 색은 연결 상태만, 배지는 활동("busy" 전송 중 / "new" 방금 받음)."""
    if snap.get("mode") == "server":
        if snap.get("startup_error"):
            color = "red"
        elif snap.get("peers"):
            color = "green"
        else:
            color = "amber"
    else:
        color = "green" if snap.get("connected") else "red"

    if snap.get("active"):
        badge: Optional[str] = "busy"
    elif received_badge_active(snap, now):
        badge = "new"
    else:
        badge = None
    return color, badge


def status_lines(snap: dict, lang: str) -> list:
    """트레이 메뉴 맨 위에 비활성 항목으로 보여줄 상태 줄."""
    lines = []
    if snap.get("mode") == "server":
        port = snap.get("port")
        if snap.get("startup_error"):
            lines.append(tr("✕ 서버 시작 실패 — 포트 {port} 사용 불가", lang).format(port=port))
        else:
            peers = list(snap.get("peers") or [])
            if peers:
                lines.append(tr("● 서버 실행 중 — 기기 {n}대 연결", lang).format(n=len(peers)))
                for name in peers[:_MAX_PEER_LINES]:
                    lines.append(f"    · {_short(name)}")
                if len(peers) > _MAX_PEER_LINES:
                    lines.append("    · " + tr("외 {n}대", lang).format(n=len(peers) - _MAX_PEER_LINES))
            else:
                lines.append(tr("● 서버 실행 중 — 연결된 기기 없음", lang))
    else:
        host = snap.get("server_host", "")
        port = snap.get("port")
        if snap.get("connected"):
            lines.append(tr("● 서버에 연결됨 — {host}", lang).format(host=host))
        else:
            lines.append(tr("○ 서버에 연결 안 됨 — {host}:{port}", lang).format(host=host, port=port))
            code = classify_client_error(snap.get("client_error", ""))
            if code:
                lines.append("    " + tr(_REASON_TEXT[code], lang))
            else:
                lines.append("    " + tr("연결 시도 중…", lang))

    active = list(snap.get("active") or [])
    if active:
        first = active[0]
        key = "↑ 보내는 중: {name}" if first.get("direction") == "send" else "↓ 받는 중: {name}"
        text = tr(key, lang).format(name=_short(first.get("filename", "")))
        if len(active) > 1:
            text += " " + tr("외 {n}건", lang).format(n=len(active) - 1)
        lines.append(text)

    last = snap.get("last_received")
    if last:
        lines.append(tr("최근 받음: {name} ({size}) · {time}", lang).format(
            name=_short(last.get("name", "")),
            size=_format_size(last.get("size", 0)),
            time=time.strftime("%H:%M", time.localtime(float(last.get("at", 0)))),
        ))
    return lines


def tooltip_text(snap: dict, lang: str) -> str:
    """트레이 툴팁 — 앱 이름 + 첫 상태 줄(기호 제외)."""
    lines = status_lines(snap, lang)
    head = lines[0].lstrip("●○✕ ").strip() if lines else ""
    return f"Infinite Clipboard — {head}" if head else "Infinite Clipboard"


def update_menu_label(snap: dict, lang: str) -> Optional[str]:
    """2026-09-29 자동 업데이트: 트레이 «업데이트 설치» 항목 라벨. 새 버전이 없으면 None.

    snap["update"] = {"version", "phase", "confirm_pending", "pending_receivables"}
    (main.InfiniteClipboard.status_snapshot). confirm_pending 은 받을 파일이 있는 상태에서
    1차 클릭 뒤 재클릭을 기다리는 2분 창 — 라벨이 그 결과(받을 파일 유실)를 말한다.
    """
    upd = snap.get("update") or {}
    version = upd.get("version")
    if not version:
        return None
    if upd.get("phase") == "downloading":
        return tr("업데이트 다운로드 중…", lang)
    if upd.get("confirm_pending"):
        return tr("그래도 업데이트 설치 — 받을 파일 {n}개 사라짐", lang).format(
            n=upd.get("pending_receivables", 0))
    return tr("업데이트 설치 (v{version})", lang).format(version=version)
