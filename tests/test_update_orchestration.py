"""main.InfiniteClipboard 자동 업데이트 오케스트레이션 (2026-09-29 Task 5 — 50% 체크포인트).

로컬 http.server 로 릴리스 목록 + 가짜 자산을 서빙하고 check_for_update → request_update_install
→ 다운로드·검증 → _post_exit 세팅 → 앱 정지까지를 본다. 상태 파일은 conftest 의
_isolate_config_dir 가 tmp_path 로 격리한다(실사용 앱의 update_state.json 을 건드리지 않음).
"""

import hashlib
import json
import platform as platform_module
import time

import pytest

from config import AppConfig
from core import updater
from core.protocol import generate_peer_id
from main import InfiniteClipboard, _consume_update_result
from test_updater_net import serve_fixture

ASSET_BODY = b"fake-package" * 5000
LINUX_ASSET = "infinite-clipboard-3.0.99-1-x86_64.pkg.tar.zst"


def _releases(base, body=ASSET_BODY, digest=None):
    digest = digest or ("sha256:" + hashlib.sha256(body).hexdigest())
    return [{
        "tag_name": "v3.0.99", "draft": False, "prerelease": False,
        "html_url": base + "/release-page",
        "assets": [{"name": LINUX_ASSET, "size": len(body), "digest": digest,
                    "browser_download_url": base + "/" + LINUX_ASSET}],
    }]


def _routes(releases_json, body=ASSET_BODY):
    return {
        "/releases": (200, json.dumps(releases_json).encode(), None, None),
        "/" + LINUX_ASSET: (200, body, None, None),
    }


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(platform_module, "system", lambda: "Linux")   # 함정 #41 — 분기 명시
    monkeypatch.setattr(platform_module, "machine", lambda: "x86_64")
    a = InfiniteClipboard(AppConfig(mode="client", auth_key="x" * 32, peer_id=generate_peer_id()))
    a.running = True
    notes = []
    monkeypatch.setattr(a, "_notify", lambda title, msg: notes.append(msg))
    a._notes = notes
    opened = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url, *args, **kw: opened.append(url))
    a._opened = opened
    yield a
    a.running = False


def _wait(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


class _Server:
    """릴리스 목록의 자산 URL 이 자기 주소를 가리키게 서빙하는 헬퍼."""

    def __init__(self, app, body=ASSET_BODY, digest=None, releases_fn=None):
        self.routes = {}
        self.app = app
        self.body = body
        self.digest = digest
        self.releases_fn = releases_fn

    def __enter__(self):
        self._cm = serve_fixture(self.routes)
        base = self._cm.__enter__()
        rel = self.releases_fn(base) if self.releases_fn else _releases(base, self.body, self.digest)
        self.routes.update(_routes(rel, self.body))
        self.app._releases_url = base + "/releases"
        return base

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)


# ── 확인 ────────────────────────────────────────────────────────


def test_check_finds_update_and_notifies_once(app):
    with _Server(app):
        info = app.check_for_update(manual=False)
        assert info is not None and info.version == "3.0.99"
        assert app.update_available == info
        assert len(app._notes) == 1 and "3.0.99" in app._notes[0]
        app.check_for_update(manual=False)
        assert len(app._notes) == 1   # 같은 버전은 자동 확인에서 다시 알리지 않음
        app.check_for_update(manual=True)
        assert len(app._notes) == 2   # 수동 확인은 항상 응답
    assert updater.load_state()["notified_version"] == "3.0.99"
    assert "last_check_at" in updater.load_state()


def test_check_up_to_date_manual_says_so(app):
    with _Server(app, releases_fn=lambda base: []):
        assert app.check_for_update(manual=True) is None
    assert len(app._notes) == 1 and "최신 버전" in app._notes[0]


def test_check_failure_silent_when_automatic(app):
    app._releases_url = "http://127.0.0.1:9/releases"   # discard 포트 — 연결 거부
    assert app.check_for_update(manual=False) is None
    assert app._notes == []
    assert app.check_for_update(manual=True) is None
    assert len(app._notes) == 1 and "실패" in app._notes[0]


def test_status_snapshot_carries_update(app):
    snap = app.status_snapshot()["update"]
    assert snap == {"version": None, "phase": "idle", "confirm_pending": False,
                    "pending_receivables": 0}


def test_state_write_does_not_touch_settings_json(app, tmp_path):
    with _Server(app):
        app.check_for_update(manual=False)
    assert (tmp_path / "update_state.json").exists()
    assert not (tmp_path / "settings.json").exists()


# ── 설치 ────────────────────────────────────────────────────────


def _silent(monkeypatch):
    monkeypatch.setattr(updater, "install_mode", lambda *a, **kw: "silent")


def test_install_downloads_verifies_and_schedules_post_exit(app, monkeypatch):
    _silent(monkeypatch)
    with _Server(app):
        app.check_for_update(manual=False)
        app.request_update_install()
        assert _wait(lambda: app._post_exit is not None)
    assert app._post_exit[0] == "/bin/sh"
    assert app.running is False                         # 트레이 없으면 앱을 직접 정지
    assert updater.load_state()["installing_version"] == "3.0.99"


def test_install_uses_tray_ui_thread_when_tray_exists(app, monkeypatch):
    _silent(monkeypatch)
    calls = []

    class FakeTray:
        @staticmethod
        def _on_ui_thread(fn):
            calls.append(fn.__name__)

        def stop(self):
            pass

        def notify(self, *a):
            pass

    app.tray = FakeTray()
    with _Server(app):
        app.check_for_update(manual=False)
        app.request_update_install()
        assert _wait(lambda: app._post_exit is not None)
    assert calls == ["stop"]


def test_install_refused_during_transfer(app, monkeypatch):
    _silent(monkeypatch)
    with app._progress_lock:
        app._transfer_progress["t1"] = {"filename": "a.bin", "direction": "receive"}
    with _Server(app):
        app.check_for_update(manual=False)
        app.request_update_install()
        time.sleep(0.3)
    assert app._post_exit is None and app.running is True
    assert "전송 중" in app._notes[-1]
    assert app._update_phase == "idle"


def test_install_with_receivables_needs_second_click_within_window(app, monkeypatch):
    _silent(monkeypatch)
    app.receivable_offers["o1"] = {"filename": "x"}
    clock = [1000.0]
    import main as main_module
    monkeypatch.setattr(main_module.time, "time", lambda: clock[0])
    with _Server(app):
        app.update_available = updater.pick_update(
            updater.fetch_releases(app._releases_url), "3.0.13", "Linux", "x86_64")
        app.request_update_install()                    # 1차 — 경고만
        assert app._post_exit is None and app._update_phase == "idle"
        assert app.status_snapshot()["update"]["confirm_pending"] is True
        assert "1개" in app._notes[-1]
        clock[0] += 121                                  # 2분 경과 → 만료
        app.request_update_install()                    # 다시 1차 취급
        assert app._update_phase == "idle"
        clock[0] += 5
        app.request_update_install()                    # 창 안 2차 → 진행
        assert _wait(lambda: app._post_exit is not None)


def test_install_hash_mismatch_keeps_running(app, monkeypatch):
    _silent(monkeypatch)
    with _Server(app, digest="sha256:" + "00" * 32):
        app.check_for_update(manual=False)
        app.request_update_install()
        assert _wait(lambda: app._update_phase == "idle" and len(app._notes) >= 3)
    assert app._post_exit is None and app.running is True
    assert "확인할 수 없어" in app._notes[-1]
    assert "installing_version" not in updater.load_state()


def test_install_double_click_downloads_once(app, monkeypatch):
    _silent(monkeypatch)
    started = []
    orig = app._prepare_update_and_exit

    def slow(info, mode):
        started.append(1)
        time.sleep(0.3)
        orig(info, mode)

    monkeypatch.setattr(app, "_prepare_update_and_exit", slow)
    with _Server(app):
        app.check_for_update(manual=False)
        app.request_update_install()
        app.request_update_install()
        assert _wait(lambda: app._post_exit is not None)
    assert started == [1]


def test_install_page_mode_opens_release_page(app, monkeypatch):
    monkeypatch.setattr(updater, "install_mode", lambda *a, **kw: "page")
    with _Server(app) as base:
        app.check_for_update(manual=False)
        app.request_update_install()
    assert app._opened == [base + "/release-page"]
    assert app._post_exit is None and app.running is True


def test_install_windows_branch_builds_powershell(app, monkeypatch):
    # 함정 #41: Windows 분기도 명시 — 자산은 exe 로
    monkeypatch.setattr(platform_module, "system", lambda: "Windows")
    _silent(monkeypatch)
    exe_name = "infinite-clipboard-setup-3.0.99.exe"

    def rel(base):
        return [{"tag_name": "v3.0.99", "html_url": base + "/p", "assets": [{
            "name": exe_name, "size": len(ASSET_BODY),
            "digest": "sha256:" + hashlib.sha256(ASSET_BODY).hexdigest(),
            "browser_download_url": base + "/" + exe_name}]}]

    srv = _Server(app, releases_fn=rel)
    with srv as base:
        srv.routes["/" + exe_name] = (200, ASSET_BODY, None, None)
        app.check_for_update(manual=False)
        app.request_update_install()
        assert _wait(lambda: app._post_exit is not None)
    assert app._post_exit[0] == "powershell.exe"


# ── 재시작 후 검증 ──────────────────────────────────────────────


def test_consume_update_result_success(monkeypatch):
    import version
    monkeypatch.setattr(version, "__version__", "3.0.99")
    updater.update_state(installing_version="3.0.99")
    msg = _consume_update_result("ko")
    assert "v3.0.99" in msg and "업데이트됐습니다" in msg
    assert "installing_version" not in updater.load_state()
    assert _consume_update_result("ko") is None   # 1회만


def test_consume_update_result_failure(monkeypatch):
    import version
    monkeypatch.setattr(version, "__version__", "3.0.13")
    updater.update_state(installing_version="3.0.99")
    msg = _consume_update_result("en")
    assert msg.startswith("The update was not installed") and "update-helper.log" in msg


def test_check_loop_runs_first_check_after_delay(app, monkeypatch):
    monkeypatch.setattr(updater, "FIRST_CHECK_DELAY_SECONDS", 0)
    calls = []

    def fake_check(manual=False):
        calls.append(manual)
        app.running = False   # 한 번 돌고 루프 종료

    monkeypatch.setattr(app, "check_for_update", fake_check)
    app._update_check_loop()
    assert calls == [False]


def test_start_skips_check_thread_when_disabled(monkeypatch):
    started = []
    a = InfiniteClipboard(AppConfig(mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
                                    auto_update_check=False))
    monkeypatch.setattr(a, "_update_check_loop", lambda: started.append(1))
    monkeypatch.setattr(a, "_start_client", lambda: None)
    monkeypatch.setattr(a, "_monitor_clipboard", lambda: None)
    a.start()
    time.sleep(0.2)
    a.running = False
    assert started == []
