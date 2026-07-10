"""공용 staging 폴더 때문에 실제 목적지엔 없는 "(1)" 접미어가 붙던 버그 회귀
(2026-07-10 제보).

이전엔 모든 수신 transfer 가 `_staging_dir()`(단일 OS 임시 폴더) 에 바로 풀렸다.
예전에 받은 동명 파일이 그 안에 남아있으면(정리 주기는 `staging_ttl_hours`, 기본
24시간) staging 내부에서 먼저 "(1)" 접미어가 붙고, `_receive_offer`(받기 버튼
흐름)는 그 이름 그대로 `download_path`에 복사했다 — 정작 `download_path`엔 동명
파일이 전혀 없어도 접미어가 붙었다.

수정: (1) `_handle_transfer_complete`가 staging 을 transfer_id 전용 하위 폴더로
격리해 애초에 staging 내부 충돌이 발생하지 않게 함. (2) `_receive_offer`가
`download_path`(실제 목적지) 기준으로 충돌 여부를 확인하도록 함.
"""

from pathlib import Path

from config import AppConfig
from core.protocol import generate_peer_id
from main import InfiniteClipboard


def _make_app():
    return InfiniteClipboard(AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
    ))


def _register_receivable(app, offer_id, source_peer, name="report.txt"):
    with app._offer_lock:
        app.receivable_offers[offer_id] = {
            "offer_id": offer_id,
            "source_peer": source_peer,
            "name": name,
            "kind": "file",
            "total_size": 123,
            "created_at": 0.0,
        }


class _Fetched:
    def __init__(self, paths):
        self.paths = paths


def test_stale_staging_leftover_does_not_leak_suffix_into_clean_destination(tmp_path, monkeypatch):
    app = _make_app()
    app.config.download_path = str(tmp_path / "downloads")
    # 실제 사용자의 공용 /tmp/ic_clipboard 대신 이 테스트 전용 임시 폴더로 격리.
    monkeypatch.setattr(app, "_staging_dir", lambda: tmp_path / "staging")

    # 예전 transfer(다른 offer_id)가 같은 이름으로 이미 써둔 staging 잔여물 —
    # per-transfer-id 격리 전에는 이런 잔여물 때문에 새 수신에 "(1)" 이 붙었다.
    stale_dir = app._staging_dir() / "unrelated-old-transfer-id"
    stale_dir.mkdir(parents=True, exist_ok=True)
    (stale_dir / "report.txt").write_bytes(b"old leftover content")

    offer_id = "offer-clean-dest"
    _register_receivable(app, offer_id, generate_peer_id())

    # 실제 코드 경로와 동일하게, 이번 transfer 자신의 staging 하위 폴더에서 fetch.
    my_staging = app._staging_dir() / offer_id
    my_staging.mkdir(parents=True, exist_ok=True)
    src = my_staging / "report.txt"
    src.write_bytes(b"new content")

    app._fetch_offer = lambda oid: _Fetched([str(src)])
    app._receive_offer(offer_id)

    dest = Path(app.config.download_path) / "report.txt"
    assert dest.exists()
    assert dest.read_bytes() == b"new content"
    assert not (Path(app.config.download_path) / "report (1).txt").exists(), (
        "실제 목적지엔 동명 파일이 없는데도 staging 잔여물 때문에 접미어가 붙음"
    )


def test_real_destination_conflict_still_gets_renamed(tmp_path, monkeypatch):
    """대조군 — 실제 download_path 에 진짜 다른 내용의 동명 파일이 있으면
    file_conflict_policy(기본 rename_with_counter)가 여전히 정상 적용돼야 한다."""
    app = _make_app()
    app.config.download_path = str(tmp_path / "downloads")
    monkeypatch.setattr(app, "_staging_dir", lambda: tmp_path / "staging")
    downloads = Path(app.config.download_path)
    downloads.mkdir(parents=True, exist_ok=True)
    (downloads / "report.txt").write_bytes(b"existing different content")

    offer_id = "offer-real-conflict"
    _register_receivable(app, offer_id, generate_peer_id())

    my_staging = app._staging_dir() / offer_id
    my_staging.mkdir(parents=True, exist_ok=True)
    src = my_staging / "report.txt"
    src.write_bytes(b"incoming new content")

    app._fetch_offer = lambda oid: _Fetched([str(src)])
    app._receive_offer(offer_id)

    assert (downloads / "report.txt").read_bytes() == b"existing different content"
    renamed = downloads / "report (1).txt"
    assert renamed.exists(), "실제 충돌 시엔 rename_with_counter 가 적용돼야 함"
    assert renamed.read_bytes() == b"incoming new content"
