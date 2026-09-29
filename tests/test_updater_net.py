"""core/updater.py 네트워크 — 릴리스 조회·검증 다운로드 (로컬 http.server 픽스처).

2026-09-29 자동 업데이트 Task 2. 실제 GitHub 에 요청하지 않는다. tests/test_update_orchestration.py
도 `serve_fixture` 를 재사용한다.
"""

import hashlib
import http.server
import json
import threading
from contextlib import contextmanager

import pytest

from core import updater
from core.updater import UpdateError, UpdateInfo


@contextmanager
def serve_fixture(routes: dict):
    """routes: path → (status, body bytes, headers dict | None, truncate_to | None).

    truncate_to 가 있으면 Content-Length 는 전체 길이로 보내고 본문은 그만큼만 쓴 뒤 끊는다
    (다운로드 중단 재현).
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server 규약
            status, body, headers, truncate = routes.get(
                self.path, (404, b"not found", None, None))
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body if truncate is None else body[:truncate])
            self.wfile.flush()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _info(url, body, sha=None):
    return UpdateInfo(version="3.0.99", asset_name="infinite-clipboard-setup-3.0.99.exe",
                      url=url, sha256=hashlib.sha256(body).hexdigest() if sha is None else sha,
                      size=len(body), html_url="https://example.invalid/rel")


# ── fetch_releases ───────────────────────────────────────────────


def test_fetch_releases_ok_sends_user_agent():
    seen = {}

    payload = json.dumps([{"tag_name": "v3.0.99"}]).encode()
    with serve_fixture({"/releases": (200, payload, {"Content-Type": "application/json"}, None)}) as base:
        # User-Agent 는 서버 쪽에서 확인하기 번거로워 Request 생성 함수를 직접 검사
        req = updater._api_request(base + "/releases")
        seen["ua"] = req.get_header("User-agent")
        rels = updater.fetch_releases(base + "/releases")
    assert rels == [{"tag_name": "v3.0.99"}]
    assert seen["ua"].startswith("InfiniteClipboard/")


def test_fetch_releases_rate_limited():
    with serve_fixture({"/releases": (403, b'{"message":"rate limit"}', None, None)}) as base:
        with pytest.raises(UpdateError) as e:
            updater.fetch_releases(base + "/releases")
    assert e.value.reason == "rate_limited"


def test_fetch_releases_server_error_is_bad_response():
    with serve_fixture({"/releases": (500, b"oops", None, None)}) as base:
        with pytest.raises(UpdateError) as e:
            updater.fetch_releases(base + "/releases")
    assert e.value.reason == "bad_response"


def test_fetch_releases_not_a_list_is_bad_response():
    with serve_fixture({"/releases": (200, b'{"a":1}', None, None)}) as base:
        with pytest.raises(UpdateError) as e:
            updater.fetch_releases(base + "/releases")
    assert e.value.reason == "bad_response"


def test_fetch_releases_connection_refused_is_network():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()   # 아무도 안 듣는 포트
    with pytest.raises(UpdateError) as e:
        updater.fetch_releases(f"http://127.0.0.1:{port}/releases", timeout=2)
    assert e.value.reason == "network"


# ── download_verified ────────────────────────────────────────────


BODY = b"installer-bytes" * 10000   # 150KB — 64KB 청크 여러 번


def test_download_verified_ok(tmp_path):
    with serve_fixture({"/a.exe": (200, BODY, None, None)}) as base:
        path = updater.download_verified(_info(base + "/a.exe", BODY), tmp_path)
    assert path == tmp_path / "infinite-clipboard-setup-3.0.99.exe"
    assert path.read_bytes() == BODY
    assert list(tmp_path.iterdir()) == [path]   # .part 남지 않음


def test_download_verified_progress_callback(tmp_path):
    seen = []
    with serve_fixture({"/a.exe": (200, BODY, None, None)}) as base:
        updater.download_verified(_info(base + "/a.exe", BODY), tmp_path,
                                  progress=lambda done, total: seen.append((done, total)))
    assert seen and seen[-1] == (len(BODY), len(BODY))


def test_download_verified_hash_mismatch(tmp_path):
    with serve_fixture({"/a.exe": (200, BODY, None, None)}) as base:
        with pytest.raises(UpdateError) as e:
            updater.download_verified(_info(base + "/a.exe", BODY, sha="00" * 32), tmp_path)
    assert e.value.reason == "corrupt"
    assert list(tmp_path.iterdir()) == []


def test_download_verified_truncated(tmp_path):
    with serve_fixture({"/a.exe": (200, BODY, None, 1000)}) as base:
        with pytest.raises(UpdateError) as e:
            updater.download_verified(_info(base + "/a.exe", BODY), tmp_path, timeout=5)
    assert e.value.reason == "corrupt"
    assert list(tmp_path.iterdir()) == []


def test_download_verified_size_mismatch(tmp_path):
    info = _info("http://unused.invalid/", BODY)
    info = UpdateInfo(**{**info.__dict__, "size": len(BODY) + 1})
    with serve_fixture({"/a.exe": (200, BODY, None, None)}) as base:
        info = UpdateInfo(**{**info.__dict__, "url": base + "/a.exe"})
        with pytest.raises(UpdateError) as e:
            updater.download_verified(info, tmp_path)
    assert e.value.reason == "corrupt"


def test_download_verified_no_digest_refuses_before_download(tmp_path):
    info = UpdateInfo(version="3.0.99", asset_name="x.exe", url="http://unused.invalid/x",
                      sha256=None, size=1, html_url="h")
    with pytest.raises(UpdateError) as e:
        updater.download_verified(info, tmp_path)
    assert e.value.reason == "unverifiable"
    assert list(tmp_path.iterdir()) == []


def test_download_verified_http_error(tmp_path):
    with serve_fixture({}) as base:
        with pytest.raises(UpdateError) as e:
            updater.download_verified(_info(base + "/missing.exe", BODY), tmp_path)
    assert e.value.reason == "network"


def test_ssl_context_is_created():
    ctx = updater._ssl_context()
    import ssl
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
