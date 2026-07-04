"""M12 회귀 — 알림 이중 폴백(tray→plyer) 완전 실패가 무로그/DEBUG 에 묻히던 문제.

2026-07-03 감사 M12: tray.notify() 실패는 `except Exception: pass` 로 완전
무로그였고, plyer 최종 실패도 `logger.debug(...)` 라 기본 콘솔(INFO 이상만
출력)엔 안 보였다. "받기 실패"/"덮어쓰기 실패"/"버전 불일치" 같은 이번 세션에
추가한 알림들이 실제로는 한 번도 사용자에게 안 보였는데 아무도 몰랐을 위험.
수정: tray 실패는 최소 DEBUG 로그(폴백 경로 명시), 양쪽 다 실패하면 WARNING
(기본 콘솔에 보임)으로 승격.
"""

import logging
import time

from config import AppConfig
from core.protocol import generate_peer_id
from main import InfiniteClipboard


def _make_app():
    return InfiniteClipboard(AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
    ))


class _FailingTray:
    def notify(self, title, message):
        raise RuntimeError("tray notify boom")


def test_notify_total_failure_logs_at_warning_level(monkeypatch, caplog):
    """tray 도 plyer 도 둘 다 실패하면 WARNING 으로 승격돼야 한다 — 기본
    콘솔에서 보이는 최소 레벨."""
    app = _make_app()
    app.tray = _FailingTray()

    def _boom_notify(*a, **k):
        raise RuntimeError("plyer notify boom")

    import types
    fake_plyer_notification = types.SimpleNamespace(notify=_boom_notify)
    monkeypatch.setitem(
        __import__("sys").modules, "plyer.notification", fake_plyer_notification,
    )
    # plyer 패키지 자체의 notification 서브모듈 임포트를 가로채기 위해
    # 'from plyer import notification' 형태를 지원하도록 plyer 모듈도 패치
    fake_plyer = types.SimpleNamespace(notification=fake_plyer_notification)
    monkeypatch.setitem(__import__("sys").modules, "plyer", fake_plyer)

    caplog.set_level(logging.WARNING, logger="infinite-clipboard")

    app._notify("받기 실패", "photo.png — 원본에서 받을 수 없음")

    # _notify 는 plyer 호출을 별도 스레드에서 하므로 잠깐 대기
    deadline = time.time() + 2.0
    while time.time() < deadline:
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        if warnings:
            break
        time.sleep(0.02)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "M12 회귀 — tray+plyer 완전 실패가 WARNING 으로 안 올라옴"
    assert any("완전 실패" in r.getMessage() for r in warnings)
    assert any("받기 실패" in r.getMessage() for r in warnings), (
        "실패한 알림의 제목이 로그에 없어 어떤 알림이 안 갔는지 알 수 없음"
    )
