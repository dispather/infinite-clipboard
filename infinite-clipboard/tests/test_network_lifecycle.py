"""네트워크 라이프사이클 회귀 — v2.1 종료 시 race 정리.

v2.0.0 에선 stop 시 socket close → 다른 thread 의 recv OSError 가
'수신 오류' / '클라이언트 처리 오류' ERROR 로그를 1 줄 출력했다.
Windows 에선 'WinError 10038', POSIX 에선 'OSError EBADF' 형태로 노출.

v2.1 변경: stop() 이 shutdown(SHUT_RDWR) 후 close + except 블록의
running 가드로 의식적 종료를 silent 처리. 본 테스트는 그 invariant 보장.
"""

import logging
import socket as sock_module
import threading
import time

import pytest

from core.network import NetworkServer, NetworkClient
from core.protocol import MSG_PING


def _free_port() -> int:
    """OS 가 비어있는 TCP 포트 하나를 할당받아 반환."""
    s = sock_module.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _filter_network_errors(records):
    """본 모듈이 관심 갖는 ERROR 레코드만 추출 (다른 모듈 잡음 제외)."""
    return [
        r for r in records
        if r.levelno >= logging.ERROR and r.name.startswith("core.network")
    ]


def test_server_only_stop_silent(caplog):
    """서버만 띄우고 stop → ERROR 로그 부재."""
    caplog.set_level(logging.DEBUG, logger="core.network")
    server = NetworkServer(
        port=_free_port(), auth_key="k", tailscale_trust=False,
        bind_address="127.0.0.1",  # v2.2 R2: 테스트는 loopback 명시
    )
    server.start()
    # accept thread 가 진입할 시간 확보
    time.sleep(0.1)
    server.stop()
    time.sleep(0.2)

    errors = _filter_network_errors(caplog.records)
    assert not errors, f"unexpected error logs: {[r.getMessage() for r in errors]}"


def test_server_client_lifecycle_silent(caplog):
    """서버 시작 → 클라이언트 연결 → 정상 종료 → ERROR 로그 부재.

    이 시나리오가 v2.0.0 에선 fail (수신 오류 1 줄), v2.1 에선 pass.
    """
    caplog.set_level(logging.DEBUG, logger="core.network")
    port = _free_port()

    server = NetworkServer(
        port=port, auth_key="shared", tailscale_trust=False,
        bind_address="127.0.0.1",  # v2.2 R2
    )
    server.start()

    client = NetworkClient(
        host="127.0.0.1", port=port, auth_key="shared", device_name="testdev",
    )
    client.start()

    # 핸드셰이크 완료까지 대기
    connected = _wait_until(lambda: client.connected, timeout=3.0)
    assert connected, "client did not connect within timeout"

    # recv 루프가 자리잡을 시간 (수신 thread 가 _recv_all 에 진입)
    time.sleep(0.1)

    # 의식적 종료 — 순서는 client → server (또는 server → client) 둘 다 silent 여야
    client.stop()
    server.stop()

    # 모든 thread 가 정리될 시간
    time.sleep(0.3)

    errors = _filter_network_errors(caplog.records)
    assert not errors, (
        f"종료 시 ERROR 로그 발생 (v2.0 회귀): "
        f"{[r.getMessage() for r in errors]}"
    )


def test_server_stop_with_pending_client_silent(caplog):
    """서버가 먼저 종료될 때 클라이언트 측 recv 가 silent EOF 처리 되는지.

    클라이언트 종료를 명시적으로 호출하지 않아도 server.stop() 후
    클라이언트 _receive 가 ConnectionError 를 잡아 logger.error 안 찍어야 한다.
    (단, client.running 은 True 라 reconnect 로 전환되며 이 시점부터는
     '서버 연결 실패' 가 expected — 따라서 검증은 server.stop 직후 잠깐만.)
    """
    caplog.set_level(logging.DEBUG, logger="core.network")
    port = _free_port()

    server = NetworkServer(
        port=port, auth_key="shared", tailscale_trust=False,
        bind_address="127.0.0.1",  # v2.2 R2
    )
    server.start()

    client = NetworkClient(
        host="127.0.0.1", port=port, auth_key="shared", device_name="testdev",
    )
    # 재연결 간격 짧게 (기본 5 초로는 테스트 시간 길어짐)
    client.reconnect_interval = 30
    client.start()

    assert _wait_until(lambda: client.connected, timeout=3.0)
    time.sleep(0.1)

    # 로그 마커 — 이후 발생한 로그만 검증
    marker_idx = len(caplog.records)

    # 서버만 먼저 종료
    server.stop()
    # 클라이언트 _receive 가 EOF 받고 connected=False 로 전환할 시간
    _wait_until(lambda: not client.connected, timeout=2.0)

    # 클라이언트 정리
    client.stop()
    time.sleep(0.2)

    # marker 이후의 ERROR 만 검사 — server stop 이후
    new_records = caplog.records[marker_idx:]
    errors = _filter_network_errors(new_records)
    # ConnectionError 는 _receive 의 except 에 잡혀야 silent (running=True 라도
    # 이건 v2.0 에서도 '수신 오류: 연결 끊김' 으로 ERROR 출력했음).
    # v2.1 에선 socket close 후 EOF 가 ConnectionError 로 잡히고, 이는 정상
    # disconnect 시그널이라 client.running 이 True 일 때만 '수신 오류' 로 찍음.
    # 즉 v2.1 도 이 케이스는 ERROR 가 *남는* 게 정상 (사용자가 stop 안 했으니).
    # 따라서 여기선 "WinError 10038" / "EBADF" 같은 OS 레벨 에러가 없는지만 확인.
    os_level_errors = [
        r for r in errors
        if "10038" in r.getMessage() or "EBADF" in r.getMessage()
        or "Bad file descriptor" in r.getMessage()
    ]
    assert not os_level_errors, (
        f"OS 레벨 race 에러 발생: "
        f"{[r.getMessage() for r in os_level_errors]}"
    )


def test_send_to_peer_failure_after_stop_is_debug_not_error(caplog):
    """L3: 종료 중(running=False) 인 send_to_peer 실패는 ERROR 가 아닌 DEBUG
    로 남아야 한다 — 정상 종료의 자연스러운 결과인데 과거엔 running 여부와
    무관하게 항상 ERROR 로 찍혀 로그 노이즈였다."""
    caplog.set_level(logging.DEBUG, logger="core.network")
    port = _free_port()

    server = NetworkServer(
        port=port, auth_key="k", tailscale_trust=False, bind_address="127.0.0.1",
    )
    server.start()
    client = NetworkClient(host="127.0.0.1", port=port, auth_key="k", device_name="dev")
    client.start()

    assert _wait_until(lambda: client.connected, timeout=3.0)
    assert _wait_until(lambda: len(server.clients) == 1, timeout=3.0)

    with server.clients_lock:
        sock, info = next(iter(server.clients.items()))
        peer_id = info["peer_id"]

    # stop() 이 하는 것과 동일한 첫 단계(running=False)를 흉내낸 뒤, 소켓을
    # 직접 닫아 sendall 이 실패하도록 유도.
    server.running = False
    try:
        sock.close()
    except OSError:
        pass

    marker = len(caplog.records)
    ok = server.send_to_peer(peer_id, MSG_PING)
    assert ok is False

    new_records = caplog.records[marker:]
    errors = [
        r for r in new_records
        if r.levelno == logging.ERROR and "send_to_peer" in r.getMessage()
    ]
    assert not errors, (
        f"L3 회귀 — 종료 중(running=False)인데 ERROR 로 찍힘: "
        f"{[r.getMessage() for r in errors]}"
    )

    server.running = True  # 정상 stop() 흐름으로 정리
    client.stop()
    server.stop()
