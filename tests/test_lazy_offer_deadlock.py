"""함정 #46 회귀: 붙여넣기 수신(fetch) 중 두 번째 offer 가 와도 네트워크 수신 스레드가 막히지 않는다.

macOS lazy 백엔드는 등록을 메인 스레드에 위임하는데, 메인 스레드는 paste 콜백 안에서
fetch 청크를 기다린다. 그 청크를 읽는 네트워크 수신 스레드가 두 번째 offer 의
register_offer 에서 메인을 무기한 기다리면 순환 대기 → fetch 타임아웃까지 교착한다
(2026-09-28 mac 실기: 64 MiB 두 번째 offer → 256s 타임아웃 + 서버 송신 timeout 으로 끊김).

이 개발 환경은 pyobjc 가 없어 core/lazy_mac.py 자체는 못 돌린다(그건 tests/test_lazy_mac.py
가 macOS CI 에서). 여기서는 lazy_mac 의 스레딩 모델을 흉내 내는 provider(메인 스레드 =
게시된 콜러블을 도는 전용 스레드, 등록은 거기로 위임)로 **오케스트레이션 전체**(실제
loopback 소켓 · main.py · OwnerThreadCall)를 돌린다.

대조군 `test_unbounded_register_reproduces_deadlock` 은 옛 무기한 대기(waitUntilDone=True)
로 교착이 이 하니스에서 실제로 재현됨을 보인다 — 하니스가 결함을 가를 수 있다는 증거다.
"""

import queue
import socket
import threading
import time

import pytest

from config import AppConfig
from core.lazy_clipboard import OwnerThreadBusy, OwnerThreadCall
from core.protocol import generate_peer_id
from main import FetchFailure, InfiniteClipboard

_KEY = "loopback-shared-secret-key-0123456789ab"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_until(predicate, timeout: float = 4.0, interval: float = 0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class _MainLoop:
    """macOS 메인 run loop 흉내 — 게시된 콜러블을 순서대로 실행하는 전용 스레드."""

    def __init__(self):
        self._q = queue.Queue()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def post(self, fn):
        self._q.put(fn)

    def _run(self):
        while True:
            fn = self._q.get()
            if fn is None:
                return
            try:
                fn()
            except Exception:  # noqa: BLE001 — 루프는 죽지 않는다(run loop 와 같음)
                pass

    def stop(self):
        self._q.put(None)


class _MacLikeProvider:
    """core/lazy_mac.MacLazyProvider 의 스레딩 모델 흉내.

    - 등록(_do_register)은 메인 루프에서 실행 (performSelectorOnMainThread 위임)
    - paste(provideDataForType)는 메인 루프에서 fetch_callback 을 동기 호출
    wait_mode: "bounded" = 현행(OwnerThreadCall) / "unbounded" = 옛 waitUntilDone=True
    fast_path: 메인이 fetch 중이면 기다리지 않고 False (lazy_mac._providing)
    """

    def __init__(self, main, wait_mode="bounded", fast_path=False, timeout=0.5):
        self.main = main
        self.wait_mode = wait_mode
        self.fast_path = fast_path
        self.timeout = timeout
        self.registered = []  # 메인에서 실제로 등록된 offer_id (순서대로)
        self._lock = threading.Lock()
        self._offer = None
        self._cb = None
        self._providing = False

    def is_supported(self, kind):
        return kind in ("file", "image")

    def owns_clipboard(self):
        return False

    def clear(self):
        with self._lock:
            self._offer = None
            self._cb = None

    def stop(self):
        pass

    def register_offer(self, offer, fetch_callback):
        if self.fast_path:
            with self._lock:
                if self._providing:
                    return False

        def _do_register():
            with self._lock:
                self._offer = offer
                self._cb = fetch_callback
            self.registered.append(offer["offer_id"])
            return True

        if self.wait_mode == "unbounded":
            done, holder = threading.Event(), {}

            def _run():
                holder["ok"] = _do_register()
                done.set()

            self.main.post(_run)
            done.wait()
            return holder["ok"]

        call = OwnerThreadCall(_do_register)
        self.main.post(call.run)
        try:
            return bool(call.wait(self.timeout))
        except OwnerThreadBusy:
            return False

    def paste(self):
        """메인 루프에서 현재 offer 를 fetch (Finder 즉시 peek, grace=0). 결과 홀더 반환."""
        holder: dict = {"done": threading.Event()}

        def _provide():
            with self._lock:
                offer, cb = self._offer, self._cb
                self._providing = True
            try:
                holder["result"] = cb(offer["offer_id"])
            except Exception as e:  # noqa: BLE001
                holder["error"] = e
            finally:
                with self._lock:
                    self._providing = False
                holder["done"].set()

        self.main.post(_provide)
        return holder


def _make_app(mode, port, download_path) -> InfiniteClipboard:
    download_path.mkdir(parents=True, exist_ok=True)
    cfg = AppConfig(
        mode=mode, server_host="127.0.0.1", port=port, auth_key=_KEY,
        peer_id=generate_peer_id(), download_path=str(download_path),
        tailscale_trust=False, bind_address="127.0.0.1",
        fetch_grace_seconds=0,  # macOS 는 grace=0(함정 #38) — 등록 즉시 peek
        lazy_paste=True,
    )
    return InfiniteClipboard(cfg)


def _run_second_offer_during_fetch(tmp_path, provider_factory):
    """첫 offer 를 fetch 하는 동안(원본 송신을 게이트로 붙잡아 둔 채) 두 번째 offer 를 보낸다.

    반환 dict:
      b_receivable_before_gate — 게이트를 열기 «전»에 두 번째 offer 가 받기 목록에 들어갔나
                                 (= 네트워크 수신 스레드가 안 막혔나)
      first — 첫 fetch 결과 홀더 · b_id · provider · a_bytes
    """
    src = tmp_path / "src"
    src.mkdir()
    fa = src / "deadlock-a.bin"
    fa.write_bytes(bytes(range(256)) * 800)  # ~200KB
    fb = src / "deadlock-b.bin"
    fb.write_bytes(b"second-offer " * 100)

    port = _free_port()
    server_app = _make_app("server", port, tmp_path / "srv_dl")
    client_app = _make_app("client", port, tmp_path / "cli_dl")
    main = _MainLoop()
    prov = provider_factory(main)
    client_app.lazy_provider = prov
    client_app._lazy_provider_inited = True
    server_app.lazy_provider = None
    server_app._lazy_provider_inited = True
    # 대조군(교착)이 30s 하한까지 가지 않게 fetch 타임아웃을 짧게
    client_app._fetch_timeout = lambda total_size: 3.0

    gate = threading.Event()
    fetch_arrived = threading.Event()
    orig_serve = server_app._serve_fetch

    def _gated_serve(*args, **kwargs):
        fetch_arrived.set()
        gate.wait(10.0)
        return orig_serve(*args, **kwargs)

    server_app._serve_fetch = _gated_serve

    server_app._start_server()
    client_app._start_client()
    out = {"provider": prov, "a_bytes": fa.read_bytes()}
    try:
        assert _wait_until(lambda: client_app.client and client_app.client.connected)
        assert _wait_until(lambda: len(server_app.peers) == 1)

        # 1. 첫 offer — 메인이 한가하니 lazy 등록 성공
        server_app._announce_offer([str(fa)])
        assert _wait_until(lambda: len(prov.registered) == 1), "첫 offer 등록 실패"

        # 2. Finder 즉시 peek → 메인 스레드가 paste 콜백 안에서 fetch 대기
        out["first"] = prov.paste()
        assert fetch_arrived.wait(4.0), "fetch 요청이 원본에 도착하지 않음"

        # 3. 첫 fetch 진행 중 두 번째 offer
        server_app._announce_offer([str(fb)])
        with server_app._offer_lock:
            b_id = server_app.current_offer["offer_id"]
        out["b_id"] = b_id
        out["b_receivable_before_gate"] = _wait_until(
            lambda: b_id in client_app.receivable_offers, timeout=2.5,
        )

        # 4. 원본 송신 재개 → 첫 fetch 완료(또는 교착이면 타임아웃)
        gate.set()
        assert out["first"]["done"].wait(10.0), "첫 fetch 가 끝나지 않음"
        # 메인 큐에 남은 (취소된) 등록이 처리될 시간
        time.sleep(0.5)
        out["registered"] = list(prov.registered)
        out["b_receivable_final"] = b_id in client_app.receivable_offers
        return out
    finally:
        gate.set()
        client_app.stop()
        server_app.stop()
        main.stop()


@pytest.mark.parametrize("fast_path", [False, True], ids=["bounded-wait", "providing-fast-path"])
def test_second_offer_during_fetch_does_not_block_network_thread(tmp_path, fast_path):
    out = _run_second_offer_during_fetch(
        tmp_path, lambda main: _MacLikeProvider(main, "bounded", fast_path=fast_path),
    )
    # 네트워크 수신 스레드가 안 막혀 두 번째 offer 가 곧바로 받기 모드로 들어갔다
    assert out["b_receivable_before_gate"], "두 번째 offer 처리가 첫 fetch 에 묶임(교착)"
    # 첫 fetch 는 청크를 정상 수신해 완료
    first = out["first"]
    assert "error" not in first, f"첫 fetch 실패: {first.get('error')!r}"
    got = first["result"]
    assert len(got.paths) == 1
    with open(got.paths[0], "rb") as f:
        assert f.read() == out["a_bytes"]
    # 취소된 두 번째 등록은 나중에도 실행되지 않는다(뒤늦은 클립보드 가로채기 없음)
    assert out["b_id"] not in out["registered"], "취소된 등록이 뒤늦게 실행됨"
    assert out["b_receivable_final"]


def test_unbounded_register_reproduces_deadlock(tmp_path):
    """대조군 — 옛 무기한 대기면 이 하니스에서 교착이 실제로 재현된다.

    두 번째 offer 처리가 첫 fetch 타임아웃까지 묶이고(받기 목록에 안 들어감), 첫 fetch 는
    청크를 못 읽어 타임아웃, 두 번째 offer 는 그 뒤에야 등록된다 — 2026-09-28 mac 로그와
    같은 모양(두 번째 «수신·등록» 이 첫 fetch 타임아웃과 같은 초).
    """
    out = _run_second_offer_during_fetch(
        tmp_path, lambda main: _MacLikeProvider(main, "unbounded"),
    )
    assert not out["b_receivable_before_gate"]
    err = out["first"].get("error")
    assert isinstance(err, FetchFailure) and err.reason == "timeout", f"error={err!r}"
    assert _wait_until(lambda: out["b_id"] in out["provider"].registered, timeout=3.0), \
        "교착이 풀린 뒤 두 번째 offer 가 등록돼야 함(뒤늦은 가로채기)"
