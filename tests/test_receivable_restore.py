"""재시작 뒤 «받을 파일» 목록 복원 회귀 (함정 #51, 2026-10-02).

receivable_offers 는 메모리뿐이라 재시작(설정 저장·업데이트)마다 사라졌고, 전송창은 시작 때
다시 쓰이지 않은 transfer_state.json 의 옛 목록을 보여 줬다 — 받기를 누르면 unknown_offer
(a5000 v3.0.16 N5). 이제 start() 맨 앞의 _restore_receivables 가 그 파일에서 복원하고, 복원
«뒤»에 파일을 한 번 다시 쓴다(창의 받기 개수 = 본체가 받을 수 있는 개수).

loopback 테스트는 첫 인스턴스의 실제 stop() 을 거친다 — 종료 경로가 파일을 다시 써서
목록을 지우는지까지 본다. 서버는 상태 파일을 따로 쓰게 해(같은 tmp 설정 폴더 공유) 클라이언트
파일을 덮지 않게 한다.
"""

import json
import os
import time
import uuid

import main
from config import AppConfig
from core.protocol import generate_peer_id
from main import InfiniteClipboard
from tests.test_lazy_orchestration import _KEY, _free_port, _wait_until


def _app(mode, port, download_path, peer_id=None, offer_ttl_hours=24):
    download_path.mkdir(parents=True, exist_ok=True)
    return InfiniteClipboard(AppConfig(
        mode=mode, server_host="127.0.0.1", port=port, auth_key=_KEY,
        peer_id=peer_id or generate_peer_id(), download_path=str(download_path),
        tailscale_trust=False, bind_address="127.0.0.1", fetch_grace_seconds=0,
        lazy_paste=False, offer_ttl_hours=offer_ttl_hours,
    ))


def _server(tmp_path, port, peer_id=None):
    srv = _app("server", port, tmp_path / "srv_dl", peer_id=peer_id)
    state = str(tmp_path / "server_transfer_state.json")
    srv._get_transfer_state_file = lambda: state
    srv._start_server()
    return srv


def _client(tmp_path, port, peer_id):
    cli = _app("client", port, tmp_path / "cli_dl", peer_id=peer_id)
    cli._lazy_provider_inited = True  # provider 없음 → 받기 모드
    return cli


def _connect(cli, srv):
    cli._start_client()
    assert _wait_until(lambda: cli.client and cli.client.connected), "client 연결 실패"
    assert _wait_until(lambda: cli.config.peer_id in srv.peers), "server 가 client 를 학습 못 함"


def _state_receivable(app):
    with open(app._get_transfer_state_file(), encoding="utf-8") as f:
        return json.load(f)["receivable"]


def _offer_receivable_then_stop(tmp_path, srv, port, content=b"restore-me " * 500):
    """client 가 받기 모드로 offer 를 받은 뒤 실제 stop() — (client peer_id, offer_id, 원본 바이트)."""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    f = src / "report.txt"
    f.write_bytes(content)
    cli = _client(tmp_path, port, generate_peer_id())
    _connect(cli, srv)
    srv._announce_offer([str(f)])
    assert _wait_until(lambda: bool(cli.receivable_offers)), "receivable 등록 안 됨"
    offer_id = next(iter(cli.receivable_offers))
    cli.stop()
    # 같은 peer_id 재접속이 H1(살아 있는 중복 거부)에 걸리지 않게 옛 연결 정리를 기다린다
    assert _wait_until(lambda: cli.config.peer_id not in srv.peers), "server 가 옛 연결을 못 지움"
    return cli.config.peer_id, offer_id, content


def test_receivable_survives_client_restart_and_receive_works(tmp_path):
    port = _free_port()
    srv = _server(tmp_path, port)
    cli2 = None
    try:
        peer_id, offer_id, content = _offer_receivable_then_stop(tmp_path, srv, port)
        # 종료 뒤에도 파일에 남아 있고, 이어받기용 items 가 같이 저장됐다
        saved = _state_receivable(InfiniteClipboard)
        assert [e["offer_id"] for e in saved] == [offer_id]
        assert saved[0]["items"] and saved[0]["items"][0]["name"] == "report.txt"

        cli2 = _client(tmp_path, port, peer_id)  # 같은 peer_id 로 재시작
        cli2._restore_receivables()
        assert list(cli2.receivable_offers) == [offer_id]
        assert cli2.receivable_offers[offer_id]["name"] == "report.txt"
        _connect(cli2, srv)

        cli2._receive_offer(offer_id)
        dest = os.path.join(cli2.config.download_path, "report.txt")
        assert os.path.exists(dest), os.listdir(cli2.config.download_path)
        assert open(dest, "rb").read() == content
        assert not cli2.receivable_offers
        assert _state_receivable(cli2) == []
    finally:
        if cli2 is not None:
            cli2.stop()
        srv.stop()


def test_restored_offer_superseded_after_source_restart_is_cleared(tmp_path):
    """발신자도 재시작했으면(current_offer 없음) 받기 → superseded(terminal) → 목록·파일에서 제거 (M6)."""
    port = _free_port()
    srv = _server(tmp_path, port)
    srv_peer = srv.config.peer_id
    cli2 = srv2 = None
    try:
        peer_id, offer_id, _content = _offer_receivable_then_stop(tmp_path, srv, port)
        srv.stop()
        port2 = _free_port()
        srv2 = _server(tmp_path, port2, peer_id=srv_peer)  # 같은 발신자, 새 프로세스
        cli2 = _client(tmp_path, port2, peer_id)
        cli2._restore_receivables()
        assert offer_id in cli2.receivable_offers
        _connect(cli2, srv2)

        cli2._receive_offer(offer_id)
        assert offer_id not in cli2.receivable_offers
        assert _state_receivable(cli2) == []
        assert os.listdir(cli2.config.download_path) == []
    finally:
        if cli2 is not None:
            cli2.stop()
        if srv2 is not None:
            srv2.stop()
        srv.stop()


def _entry(source_peer, created_at, items=True, **over):
    e = {
        "offer_id": str(uuid.uuid4()), "source_peer": source_peer, "name": "a.txt 외 1개",
        "kind": "file", "total_size": 30, "created_at": created_at,
    }
    if items:
        e["items"] = [{"name": "a.txt", "size": 10, "hash": ""},
                      {"name": "b.txt", "size": 20, "hash": ""}]
    e.update(over)
    return e


def test_restore_filters_entries_and_rewrites_state_file(tmp_path):
    """만료·형식 오류·자기 offer 는 버리고, 3.0.16 이하 형식(items 없음)은 받는다.
    복원 «뒤» 파일을 다시 써서 창이 보는 개수 = 복원한 개수 (N5). 두 번째 재시작에도 유지."""
    app = _app("client", _free_port(), tmp_path / "dl", offer_ttl_hours=24)
    now = time.time()
    other_a, other_b = generate_peer_id(), generate_peer_id()
    new_fmt = _entry(other_a, now - 60)
    legacy = _entry(other_b, now - 60, items=False, name="photo.png", kind="image")
    rows = [
        new_fmt,
        legacy,
        _entry(generate_peer_id(), now - 25 * 3600),             # 만료(24h)
        _entry(generate_peer_id(), now, offer_id="not-a-uuid"),   # 형식 오류
        _entry(app.config.peer_id, now),                          # 자기 offer
        "garbage",
    ]
    with open(app._get_transfer_state_file(), "w", encoding="utf-8") as f:
        json.dump({"active": {}, "completed": [], "receivable": rows}, f)

    app._restore_receivables()

    assert set(app.receivable_offers) == {new_fmt["offer_id"], legacy["offer_id"]}
    assert set(app.received_offers) == {new_fmt["offer_id"], legacy["offer_id"]}
    assert app.received_offers[legacy["offer_id"]]["items"] == []
    assert app.receivable_offers[legacy["offer_id"]]["name"] == "photo.png"
    assert app.received_offers[new_fmt["offer_id"]]["source_peer"] == other_a
    assert {e["offer_id"] for e in _state_receivable(app)} == set(app.receivable_offers)

    again = _app("client", _free_port(), tmp_path / "dl2", peer_id=app.config.peer_id)
    again._restore_receivables()
    assert set(again.receivable_offers) == set(app.receivable_offers)


def test_restore_with_missing_or_broken_state_file_writes_empty_list(tmp_path):
    app = _app("client", _free_port(), tmp_path / "dl")
    app._restore_receivables()  # 파일 없음
    assert app.receivable_offers == {} and _state_receivable(app) == []

    with open(app._get_transfer_state_file(), "w", encoding="utf-8") as f:
        f.write("{broken")
    app._restore_receivables()
    assert app.receivable_offers == {} and _state_receivable(app) == []


def test_start_restores_before_network_and_ipc_pollers(tmp_path, monkeypatch):
    """받기 IPC 폴러가 복원보다 먼저 돌면 남은 받기 요청이 unknown_offer 로 항목을 지운다."""
    app = _app("client", _free_port(), tmp_path / "dl")
    calls = []
    monkeypatch.setattr(app, "_restore_receivables", lambda: calls.append("restore"))
    monkeypatch.setattr(app, "_cleanup_staging", lambda notify=False: None)
    monkeypatch.setattr(app, "_start_client", lambda: calls.append("network"))
    monkeypatch.setattr(app, "_start_server", lambda: calls.append("network"))

    class _Thread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, **_kw):
            self._target = target

        def start(self):
            calls.append(getattr(self._target, "__name__", "thread"))

    monkeypatch.setattr(main.threading, "Thread", _Thread)
    app.start()
    assert calls[0] == "restore", calls
    assert "network" in calls and "_watch_receive_requests" in calls
