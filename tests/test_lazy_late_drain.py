"""X11·Wayland·Windows lazy 백엔드 — 시간 초과로 포기한 등록이 나중에 실행되지 않는지.

infinite-clipboard::lazy-register-late-drain: register_offer 는 이벤트(메시지) 스레드가
등록을 집을 때까지 제한 시간만 기다린다. 과거엔 _pending 튜플이라 대기자가 포기한 뒤에도
이벤트 스레드가 나중에 그 등록을 실행해 «받기 목록(실패 보고) + 뒤늦은 클립보드 소유»
이중 상태가 됐다. 이제 mac 과 같이 OwnerThreadCall 이라 취소된 호출은 run 이 건너뛴다.

OS 디스플레이 없이 돌도록 인스턴스를 __new__ 로 만들고 _do_register 를 기록기로 바꾼다
(실제 소유 동작은 OS 별 Xvfb·sway·Windows 테스트가 따로 본다). 백엔드 모듈 import 가
불가능한 OS(예: 리눅스의 lazy_win)에선 skip.
"""

import threading
import time

import pytest

from core.lazy_clipboard import KIND_FILE

BACKENDS = [
    ("core.lazy_x11", "X11LazyProvider"),
    ("core.lazy_wayland", "WaylandLazyProvider"),
    ("core.lazy_win", "WindowsLazyProvider"),
]


def _bare_provider(modname, clsname, ran):
    mod = pytest.importorskip(modname)
    cls = getattr(mod, clsname)
    p = cls.__new__(cls)
    p._lock = threading.Lock()
    p._pending = None
    p._offer = None
    p._fetch_cb = None
    p._cache = None
    if clsname == "WindowsLazyProvider":
        p._hwnd_ready = threading.Event()
        p._hwnd_ready.set()
        p._hwnd = None  # PostMessage 생략(테스트에선 아래 drain 을 직접 부른다)

    def fake_register(offer, cb):
        ran.append(offer["offer_id"])
        with p._lock:
            p._offer = offer
        return True

    p._do_register = fake_register
    return p


OFFER = {"kind": KIND_FILE, "offer_id": "late-offer"}


@pytest.mark.parametrize("modname,clsname", BACKENDS)
def test_timed_out_register_never_runs_later(modname, clsname):
    """이벤트 스레드가 바빠 제한 시간 안에 못 집은 등록 → False, 그 뒤 집어도 실행 안 됨."""
    ran = []
    p = _bare_provider(modname, clsname, ran)
    p._ensure_thread = lambda: None  # 이벤트 스레드가 다른 일(fetch)로 바쁨 — 아무도 안 집음

    t0 = time.monotonic()
    assert p.register_offer(OFFER, lambda oid: None) is False
    assert time.monotonic() - t0 < 6.0

    # 바쁨이 풀린 뒤 이벤트 스레드가 남은 커맨드를 집는다
    p._drain_pending()
    assert ran == [], "포기한 등록이 나중에 실행됨(받기 목록 + 클립보드 소유 이중 상태)"
    assert p.owns_clipboard() is False


@pytest.mark.parametrize("modname,clsname", BACKENDS)
def test_register_picked_up_in_time_succeeds(modname, clsname):
    """제한 시간 안에 이벤트 스레드가 집으면 실제 결과(True)를 돌려준다."""
    ran = []
    p = _bare_provider(modname, clsname, ran)

    def soon():
        def run():
            time.sleep(0.1)
            p._drain_pending()
        threading.Thread(target=run, daemon=True).start()

    p._ensure_thread = soon
    assert p.register_offer(OFFER, lambda oid: None) is True
    assert ran == ["late-offer"]
    assert p.owns_clipboard() is True
