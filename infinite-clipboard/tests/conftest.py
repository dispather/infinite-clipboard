"""pytest 공통 설정 — 프로젝트 루트를 sys.path에 추가."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _no_real_desktop_notifications(monkeypatch):
    """테스트가 실제 OS 알림(notify-send/gdbus)을 띄우지 않도록 plyer 를 무음화.

    2026-07-04 발견: `main.InfiniteClipboard._notify`는 tray 없으면(테스트에서
    `self.tray`는 항상 기본값 None) `plyer.notification.notify(...)`로 폴백하는데,
    이건 실제로 subprocess(notify-send/gdbus)를 실행해 개발자 데스크톱에 진짜 팝업을
    띄운다. `_receive_offer`/`_provider_fetch` 를 반복 호출하는 테스트(특히
    test_receive_offer_retry.py 21건)가 세션당 수십 개의 실제 알림을 띄우는 부작용을
    일으켰다. tests/test_notify_fallback.py 처럼 개별 테스트가 plyer 자체를 명시적으로
    monkeypatch 하는 경우엔 그 테스트 함수 안의 patch 가 이 기본값을 그대로 덮어써
    정상 동작한다(monkeypatch 는 LIFO 로 되돌아가므로 서로 간섭 없음).
    """
    try:
        import plyer.notification
    except ImportError:
        return
    monkeypatch.setattr(plyer.notification, "notify", lambda **_kwargs: None, raising=False)
