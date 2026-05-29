"""원격 PC 를 흉내내는 loopback TCP origin.

스파이크의 receiver 콜백은 paste 시점에 origin.fetch() 를 호출해 페이로드를 가져온다.
origin 은 연결마다 monotonic 시각을 기록하므로, "fetch 가 copy 가 아닌 paste 시점에
발생했는가"(laziness) 를 반증가능하게 증명할 수 있다 (verdict.compute_lazy_proven 참조).
"""
import socket
import threading
import time


class TestOrigin:
    """127.0.0.1 임의 포트에서 payload 를 serve. 연결 accept 마다 monotonic 시각 기록."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self.connection_times: list[float] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "TestOrigin":
        self._thread.start()
        return self

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return  # 의식적 종료 (__exit__ 에서 close)
            self.connection_times.append(time.monotonic())
            with conn:
                try:
                    conn.sendall(self.payload)
                except OSError:
                    pass

    def fetch(self, timeout: float = 5.0) -> bytes:
        """receiver 콜백이 호출하는 동기 fetch. paste 시점에 실행되어야 laziness 성립."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as c:
            c.settimeout(timeout)
            buf = bytearray()
            while True:
                chunk = c.recv(65536)
                if not chunk:
                    break
                buf += chunk
            return bytes(buf)

    def __exit__(self, *_) -> None:  # noqa: ANN001 — 컨텍스트 매니저 종료, 예외 미사용
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass
