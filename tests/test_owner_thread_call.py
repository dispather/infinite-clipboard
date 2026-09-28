"""core.lazy_clipboard.OwnerThreadCall — 제한 대기 + 취소 (함정 #46).

lazy 백엔드가 등록을 OS 이벤트 루프 소유 스레드(macOS=메인 run loop)에 맡길 때 쓰는
헬퍼. 소유 스레드가 바쁘면(paste 콜백 안에서 fetch 대기) 호출 스레드(네트워크 수신
스레드)는 timeout 까지만 기다리고, 아직 시작 안 된 호출은 취소돼 나중에도 실행되지
않아야 한다. pyobjc 없이 순수 파이썬이라 이 환경(Linux)에서 검증한다.
"""

import threading
import time

import pytest

from core.lazy_clipboard import OwnerThreadBusy, OwnerThreadCall


def test_completes_when_owner_runs_promptly():
    call = OwnerThreadCall(lambda: 42)
    threading.Thread(target=call.run, daemon=True).start()
    assert call.wait(2.0) == 42
    assert call.state == OwnerThreadCall.DONE


def test_timeout_cancels_and_owner_never_runs_fn():
    """소유 스레드가 timeout 안에 집지 못하면 취소 — 나중에 run() 해도 fn 은 안 돈다.

    (취소 안 하면 «받기 모드로 보고 + 뒤늦은 클립보드 가로채기» 이중 상태)
    """
    ran = []
    call = OwnerThreadCall(lambda: ran.append(1) or True)
    t0 = time.monotonic()
    with pytest.raises(OwnerThreadBusy):
        call.wait(0.2)
    elapsed = time.monotonic() - t0
    assert 0.15 <= elapsed < 1.0, f"대기 상한이 안 지켜짐: {elapsed:.2f}s"
    assert call.state == OwnerThreadCall.CANCELLED
    call.run()  # 소유 스레드가 뒤늦게 큐에서 집은 상황
    assert ran == [], "취소된 호출이 실행됨"
    assert call.state == OwnerThreadCall.CANCELLED


def test_timeout_during_running_returns_real_result():
    """timeout 시점에 이미 실행 중이면 끝까지 기다려 실제 결과를 돌려준다.

    진행 중인 등록을 실패로 보고하면 «등록됐는데 받기 모드» 이중 상태가 된다.
    """
    started = threading.Event()

    def _slow():
        started.set()
        time.sleep(0.4)
        return "registered"

    call = OwnerThreadCall(_slow)
    threading.Thread(target=call.run, daemon=True).start()
    assert started.wait(1.0)
    assert call.wait(0.1) == "registered"
    assert call.state == OwnerThreadCall.DONE


def test_exception_propagates_to_waiter():
    def _boom():
        raise ValueError("setDataProvider 실패")

    call = OwnerThreadCall(_boom)
    threading.Thread(target=call.run, daemon=True).start()
    with pytest.raises(ValueError, match="setDataProvider"):
        call.wait(2.0)
    assert call.state == OwnerThreadCall.DONE


def test_running_grace_bounds_a_stuck_owner(monkeypatch):
    """fn 자체가 멈춰도 호출 스레드는 timeout + RUNNING_GRACE 뒤에 풀려난다."""
    monkeypatch.setattr(OwnerThreadCall, "RUNNING_GRACE", 0.2)
    release = threading.Event()
    started = threading.Event()

    def _stuck():
        started.set()
        release.wait(5.0)
        return True

    call = OwnerThreadCall(_stuck)
    threading.Thread(target=call.run, daemon=True).start()
    assert started.wait(1.0)
    t0 = time.monotonic()
    try:
        with pytest.raises(OwnerThreadBusy):
            call.wait(0.1)
        assert time.monotonic() - t0 < 1.0
    finally:
        release.set()
