"""에러 로그 (d1) — 오류 사건 자동 수집·진단 묶음·전체 로그 요청 회귀 테스트.

통과 기준(원장 infinite-clipboard::log-access-collect):
  옵트인 꺼짐이면 전송 0 · 사건에 비밀·클립보드 본문 없음 · 같은 서명 1000회 → 사건 1개+횟수
  · 상한 지킴 · 오프라인 적재 후 재전송 · 옛 피어 타임아웃
+ 서버는 새 메시지를 중계하지 않음 · 저장 기기 폴더는 핸드셰이크 identity · 회전 로그 600.
기기명·사용자명·IP 는 전부 가짜 값(공개 저장소).
"""

import base64
import gzip
import json
import logging
import os
import stat
import threading
import time
import zipfile

import pytest

from config import AppConfig
from core import error_events as ee
from core.protocol import (
    MSG_ERROR_EVENTS, MSG_LOG_BUNDLE, MSG_LOG_REQUEST, Protocol, generate_peer_id,
)

AUTH_KEY = "Zx9-fakeAuthKey_abcdefgh"   # URL-safe 형태(hex 아님) — 값 치환으로 가려져야 한다


def _collector(tmp_path, home="/home/fakeuser"):
    return ee.ErrorEventCollector(
        tmp_path / "outbox.jsonl", "9.9.9", ee.Redactor(secrets=[AUTH_KEY], home=home),
    )


def _record(msg, level=logging.WARNING, name="infinite-clipboard", exc_info=None):
    return logging.LogRecord(name, level, __file__, 1, msg, None, exc_info)


def _read_jsonl(path):
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ── 서명·합산 ──────────────────────────────────────────────────────────

def test_same_signature_1000_times_is_one_event_with_count(tmp_path):
    c = _collector(tmp_path)
    for i in range(1000):
        c.emit(_record(
            f"클라이언트 처리 오류 (dev{i}) peer={i:08x}deadbeef 경로 /tmp/x/{i}.bin "
            f"10.0.{i % 250}.{i % 200}:9999 크기 {i * 1024:,} bytes"
        ))
    assert c.flush_pending() == 1
    rows = _read_jsonl(tmp_path / "outbox.jsonl")
    assert len(rows) == 1
    ev = rows[0]
    assert ev["count"] == 1000
    assert ev["schema"] == ee.SCHEMA_VERSION
    assert ev["level"] == "WARNING" and ev["logger"] == "infinite-clipboard"
    assert len(ev["sig"]) == 16
    # 다른 메시지는 다른 사건
    c.emit(_record("전혀 다른 경고"))
    c.flush_pending()
    assert len(_read_jsonl(tmp_path / "outbox.jsonl")) == 2


def test_info_and_self_logger_not_collected(tmp_path):
    c = _collector(tmp_path)
    log = logging.getLogger("test-ee-info")
    log.addHandler(c)
    try:
        log.info("정보 로그")
        logging.getLogger(ee.SELF_LOGGER).addHandler(c)
        logging.getLogger(ee.SELF_LOGGER).warning("수집기 자신의 경고")
    finally:
        log.removeHandler(c)
        logging.getLogger(ee.SELF_LOGGER).removeHandler(c)
    assert c.flush_pending() == 0


def test_event_has_no_secret_clipboard_text_or_home_path(tmp_path):
    c = _collector(tmp_path, home="/home/fakeuser")
    clip = "my clipboard secret text"
    try:
        raise RuntimeError(f"open failed /home/fakeuser/private/{AUTH_KEY}")
    except RuntimeError:
        import sys
        exc = sys.exc_info()
    c.emit(_record(f"전송 실패 key={AUTH_KEY} preview='{clip}' «{clip}»",
                   level=logging.ERROR, exc_info=exc))
    c.flush_pending()
    blob = (tmp_path / "outbox.jsonl").read_text(encoding="utf-8")
    assert AUTH_KEY not in blob
    assert clip not in blob
    assert "/home/fakeuser" not in blob
    ev = _read_jsonl(tmp_path / "outbox.jsonl")[0]
    assert ev["traceback"] and "RuntimeError" in ev["traceback"]
    assert "<redacted>" in ev["traceback"]


# ── 상한 ───────────────────────────────────────────────────────────────

def test_batch_respects_byte_cap_and_outbox_cap(tmp_path):
    c = _collector(tmp_path)
    for i in range(ee.OUTBOX_MAX_EVENTS + 200):
        rec = _record(f"고유 경고 종류 {chr(0x4E00 + i)}")
        rec.exc_text = "x" * 4000
        c.emit(rec)
    c.flush_pending()
    assert len(_read_jsonl(tmp_path / "outbox.jsonl")) == ee.OUTBOX_MAX_EVENTS
    batch = c.take_batch()
    size = len(json.dumps({"events": batch}, ensure_ascii=False).encode("utf-8"))
    assert 0 < len(batch) < ee.OUTBOX_MAX_EVENTS
    assert size <= ee.BATCH_MAX_BYTES + 64
    # 실제 와이어 메시지도 상한 근처
    wire = Protocol("k").create_message(MSG_ERROR_EVENTS, {"events": batch})
    assert len(wire) <= ee.BATCH_MAX_BYTES + 128


def test_pending_cap_drops_new_signatures(tmp_path):
    c = _collector(tmp_path)
    for i in range(ee.PENDING_MAX_EVENTS + 50):
        c.emit(_record(f"종류 {chr(0x4E00 + i)}"))
    assert c.dropped == 50


# ── 오프라인 적재 후 재전송 ─────────────────────────────────────────────

def test_offline_accumulates_then_resends(tmp_path):
    c = _collector(tmp_path)
    sent = []
    online = {"v": False}

    def send(events):
        if not online["v"]:
            return False
        sent.append(events)
        return True

    svc = ee.ErrorCollectionService(c, crash_path=None, state_path=tmp_path / "st.json",
                                    send_fn=send)
    for _ in range(3):
        c.emit(_record("연결 실패 반복"))
    assert svc.tick() == 0                       # 오프라인 — 보내지 못함
    for _ in range(2):
        c.emit(_record("연결 실패 반복"))        # 그 사이 같은 사건 더
    assert svc.tick() == 0
    rows = _read_jsonl(tmp_path / "outbox.jsonl")
    assert len(rows) == 1 and rows[0]["count"] == 5   # 로컬에 쌓였다(합산)
    online["v"] = True
    assert svc.tick() == 1
    assert sent and sent[-1][0]["count"] == 5
    assert _read_jsonl(tmp_path / "outbox.jsonl") == []
    assert svc.tick() == 0                       # 보낸 건 다시 안 보낸다


def test_send_exception_restores(tmp_path):
    c = _collector(tmp_path)

    def boom(events):
        raise OSError("소켓 끊김")

    svc = ee.ErrorCollectionService(c, crash_path=None, state_path=tmp_path / "st.json",
                                    send_fn=boom)
    c.emit(_record("경고"))
    assert svc.tick() == 0
    assert len(_read_jsonl(tmp_path / "outbox.jsonl")) == 1


# ── crash.log ──────────────────────────────────────────────────────────

def test_crash_log_new_content_becomes_one_event(tmp_path):
    c = _collector(tmp_path)
    crash = tmp_path / "crash.log"
    state = tmp_path / "state.json"
    crash.write_text("Fatal Python error: Segmentation fault\n\nThread 0x0001:\n  File x\n",
                     encoding="utf-8")
    assert c.scan_crash_log(crash, state) is True
    assert c.scan_crash_log(crash, state) is False          # 같은 내용은 다시 안 올림
    c.flush_pending()
    ev = _read_jsonl(tmp_path / "outbox.jsonl")[0]
    assert ev["level"] == "CRASH" and "Segmentation fault" in ev["traceback"]
    crash.write_text("Fatal Python error: Aborted\n", encoding="utf-8")   # 줄어듦 → 0 부터
    assert c.scan_crash_log(crash, state) is True


# ── 앱 연결 (main.py) ──────────────────────────────────────────────────

class _FakeClient:
    def __init__(self):
        self.raw = []

    def send_raw(self, raw):
        self.raw.append(raw)
        return True

    def send(self, *a, **k):
        return None

    def stop(self):
        pass


def _types(raws):
    p = Protocol("k")
    return [p.parse_message(r[4:]) for r in raws]


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(main, "LOG_FILE", tmp_path / "infinite-clipboard.log")
    monkeypatch.setattr(main, "CRASH_LOG_FILE", tmp_path / "crash.log")
    monkeypatch.setattr(ee.ErrorCollectionService, "start", lambda self: None)
    made = []

    def make(mode="client", collect=False):
        app = main.InfiniteClipboard(AppConfig(
            mode=mode, auth_key=AUTH_KEY, peer_id=generate_peer_id(),
            collect_error_logs=collect, download_path=str(tmp_path / "dl"),
            device_name="fake-device",
        ))
        app._notify = lambda title, msg: None
        made.append(app)
        return app

    yield make
    for app in made:
        if app._error_collector is not None:
            logging.getLogger().removeHandler(app._error_collector)


def test_optin_off_sends_nothing(app_factory):
    app = app_factory("client", collect=False)
    app.client = _FakeClient()
    app.connected = True
    app._start_error_collection()
    assert app._error_service is None and app._error_collector is None
    logging.getLogger("infinite-clipboard").error("옵트인 꺼짐 상태의 오류")
    # 전체 로그 요청이 와도 로그 본문은 안 나간다 — declined 만
    app._handle_log_request({"request_id": "r1"})
    msgs = _types(app.client.raw)
    assert [m["type"] for m in msgs] == [MSG_LOG_BUNDLE]
    assert msgs[0]["data"].get("declined") is True and "gz_b64" not in msgs[0]["data"]
    assert not any(m["type"] == MSG_ERROR_EVENTS for m in msgs)


def test_first_log_request_answered_right_after_boot(app_factory, monkeypatch):
    """monotonic 이 «부팅 뒤 경과»라 켜진 지 60초 안 된 PC 에서도 첫 요청은 답해야 한다."""
    import main
    monkeypatch.setattr(main.time, "monotonic", lambda: 5.0)
    app = app_factory("client", collect=False)
    app.client = _FakeClient()
    app._handle_log_request({"request_id": "boot"})
    assert [m["type"] for m in _types(app.client.raw)] == [MSG_LOG_BUNDLE]


def test_optin_on_client_sends_events(app_factory):
    app = app_factory("client", collect=True)
    app.client = _FakeClient()
    app.connected = True
    app._start_error_collection()
    logging.getLogger("infinite-clipboard").error("옵트인 켜짐 상태의 오류")
    assert app._error_service.tick() == 1
    msgs = _types(app.client.raw)
    assert msgs[0]["type"] == MSG_ERROR_EVENTS
    assert msgs[0]["data"]["events"][0]["level"] == "ERROR"


def test_optin_on_client_answers_log_request_redacted(app_factory, tmp_path):
    import main
    app = app_factory("client", collect=True)
    app.client = _FakeClient()
    (tmp_path / "infinite-clipboard.log").write_text(f"line key={AUTH_KEY}\n", encoding="utf-8")
    app._handle_log_request({"request_id": "r2"})
    data = _types(app.client.raw)[0]["data"]
    body = gzip.decompress(base64.b64decode(data["gz_b64"])).decode("utf-8")
    assert "line key=" in body and AUTH_KEY not in body
    # 폭주 방지 — 바로 다시 와도 무시
    app._handle_log_request({"request_id": "r3"})
    assert len(app.client.raw) == 1
    assert main.LOG_FILE == tmp_path / "infinite-clipboard.log"


class _FakeServer:
    def __init__(self, sock, name, peer_id):
        self.clients = {sock: {"name": name, "peer_id": peer_id}}
        self.clients_lock = threading.Lock()
        self.broadcasts = []
        self.targeted = []

    def broadcast(self, msg_type, data=None, exclude_sock=None):
        self.broadcasts.append((msg_type, data))

    def broadcast_raw(self, raw, exclude_sock=None):
        self.broadcasts.append(("raw", raw))

    def send_to_peer(self, peer_id, msg_type, data=None):
        self.targeted.append((peer_id, msg_type))
        return True

    def send_raw_to_peer(self, peer_id, raw):
        self.targeted.append((peer_id, "raw"))
        return True

    def send_to(self, sock, msg_type, data=None):
        self.targeted.append(("sock", msg_type))

    def stop(self):
        pass


def _valid_event(**over):
    ev = {"schema": 1, "sig": "0123456789abcdef", "level": "ERROR", "logger": "x",
          "head": "h", "count": 3, "first_ts": "2026-11-27T00:00:00Z",
          "last_ts": "2026-11-27T00:01:00Z", "version": "3.0.19", "os": "Linux",
          "os_release": "6", "traceback": None}
    ev.update(over)
    return ev


def test_server_stores_by_handshake_identity_and_never_relays(app_factory, tmp_path):
    app = app_factory("server", collect=True)
    sock = object()
    pid = generate_peer_id()
    app.server = _FakeServer(sock, "fake-laptop", pid)
    app._start_error_collection()
    app._on_server_message(sock, {"type": MSG_ERROR_EVENTS, "data": {
        "device": "../../evil", "events": [_valid_event(), {"junk": 1}, _valid_event(level="BAD")],
    }})
    d = tmp_path / "collected-logs" / f"fake-laptop-{pid[:8]}"
    rows = _read_jsonl(d / "events.jsonl")
    assert len(rows) == 1
    assert rows[0]["device"] == "fake-laptop" and rows[0]["peer"] == pid[:8]
    assert "received_at" in rows[0]
    assert not (tmp_path / "evil").exists()
    # 세 메시지 모두 중계·broadcast 없음
    app._on_server_message(sock, {"type": MSG_LOG_REQUEST, "data": {"request_id": "x"}})
    app._on_server_message(sock, {"type": MSG_LOG_BUNDLE, "data": {"request_id": "x",
                                                                   "declined": True}})
    assert app.server.broadcasts == [] and app.server.targeted == []


def test_server_with_optin_off_drops_events(app_factory, tmp_path):
    app = app_factory("server", collect=False)
    sock = object()
    app.server = _FakeServer(sock, "fake-laptop", generate_peer_id())
    app._on_server_message(sock, {"type": MSG_ERROR_EVENTS, "data": {"events": [_valid_event()]}})
    assert not (tmp_path / "collected-logs").exists()


def test_server_own_events_go_to_store_without_network(app_factory, tmp_path):
    app = app_factory("server", collect=True)
    app.server = _FakeServer(object(), "x", generate_peer_id())
    app._start_error_collection()
    logging.getLogger("infinite-clipboard").error("서버 자신의 오류")
    assert app._error_service.tick() == 1
    d = tmp_path / "collected-logs" / f"fake-device-{app.config.peer_id[:8]}"
    assert _read_jsonl(d / "events.jsonl")[0]["head"] == "서버 자신의 오류"
    assert app.server.broadcasts == []


def test_full_log_request_old_peer_times_out(app_factory, tmp_path):
    app = app_factory("server", collect=True)
    sock_a = object()
    pid_a, pid_b = generate_peer_id(), generate_peer_id()
    app.server = _FakeServer(sock_a, "pc-a", pid_a)
    app.peers = {pid_a: "pc-a", pid_b: "pc-old"}
    app._start_error_collection()

    def answer():
        # pc-a(새 버전)만 응답 — pc-old 는 메시지를 몰라 침묵
        for _ in range(100):
            if app.server.broadcasts:
                break
            time.sleep(0.01)
        rid = app.server.broadcasts[0][1]["request_id"]
        app._on_server_message(sock_a, {"type": MSG_LOG_BUNDLE, "data": {
            "request_id": rid, "gz_b64": ee.encode_log_bundle(b"log body\n")}})

    threading.Thread(target=answer, daemon=True).start()
    t0 = time.monotonic()
    result = app.request_peer_logs(timeout=0.5)
    assert result["saved"] == ["pc-a"] and result["no_answer"] == ["pc-old"]
    assert time.monotonic() - t0 < 3
    assert app.server.broadcasts[0][0] == MSG_LOG_REQUEST
    saved = list((tmp_path / "collected-logs" / f"pc-a-{pid_a[:8]}").glob("full-log-*.txt"))
    assert len(saved) == 1 and saved[0].read_bytes() == b"log body\n"


def test_log_bundle_decompression_bomb_rejected(tmp_path):
    store = ee.CollectedLogStore(tmp_path)
    bomb = base64.b64encode(gzip.compress(b"\0" * (ee.LOG_BUNDLE_MAX_UNZIPPED + 10))).decode()
    assert store.save_log_bundle("pc", generate_peer_id(), {"gz_b64": bomb}) is None
    assert store.save_log_bundle("pc", generate_peer_id(), {"gz_b64": "!!notb64"}) is None


def test_device_dir_name_sanitized():
    name = ee.device_dir_name("../..\\evil\x00name", "ABCDEF0123456789" * 2)
    assert "/" not in name and "\\" not in name and ".." not in name and "\x00" not in name
    assert name.endswith("-abcdef01")


# ── 진단 묶음 ──────────────────────────────────────────────────────────

def test_diagnostic_bundle_has_logs_and_no_auth_key(app_factory, tmp_path):
    app = app_factory("client", collect=False)
    (tmp_path / "infinite-clipboard.log").write_text(f"a key={AUTH_KEY}\n", encoding="utf-8")
    (tmp_path / "infinite-clipboard.log.1").write_text("older\n", encoding="utf-8")
    (tmp_path / "crash.log").write_text("Fatal Python error\n", encoding="utf-8")
    path = app.export_diagnostic_bundle()
    assert path is not None and path.parent == tmp_path / "dl"
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert {"info.json", "infinite-clipboard.log", "infinite-clipboard.log.1",
                "crash.log"} <= names
        everything = b"".join(zf.read(n) for n in names).decode("utf-8")
        info = json.loads(zf.read("info.json"))
    assert AUTH_KEY not in everything
    assert "auth_key" not in info["config"] and "peer_id" not in info["config"]
    assert set(info["config"]) <= set(ee.DIAG_CONFIG_ALLOWLIST)


# ── 회전 로그 권한 ─────────────────────────────────────────────────────

@pytest.mark.skipif(os.name != "posix", reason="POSIX 권한 전용")
def test_rotating_log_files_are_0600(tmp_path):
    old = os.umask(0o022)
    try:
        base = tmp_path / "app.log"
        (tmp_path / "app.log.2").write_text("old", encoding="utf-8")
        os.chmod(tmp_path / "app.log.2", 0o644)
        h = ee.make_secure_rotating_handler(str(base), maxBytes=200, backupCount=3,
                                            encoding="utf-8")
        log = logging.getLogger("test-ee-rotate")
        log.propagate = False
        log.addHandler(h)
        try:
            for i in range(50):
                log.warning("x" * 40)
        finally:
            log.removeHandler(h)
            h.close()
    finally:
        os.umask(old)
    for p in [base, tmp_path / "app.log.1", tmp_path / "app.log.2"]:
        assert p.exists(), p
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p


def test_config_collect_error_logs_default_off_and_validated():
    assert AppConfig(auth_key=AUTH_KEY).collect_error_logs is False
    assert AppConfig(auth_key=AUTH_KEY, collect_error_logs="yes").collect_error_logs is False
