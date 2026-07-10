"""받기 대기 목록(receivable_offers)이 새 offer 도착 시 통째로 지워지던 버그 회귀
(2026-07-10 제보).

`_handle_clip_offer`(main.py)는 새 offer 가 도착하면 이전엔 `receivable_offers`/
`received_offers` 전체를 `{}`로 교체했다 — 무관한 다른 발신자(source_peer)의 대기
항목까지 알림 없이 사라졌다. 같은 발신자의 이전 offer 만 대체하도록 고쳤다.
"""

import time
import uuid

from config import AppConfig
from core.protocol import generate_peer_id
from main import InfiniteClipboard


def _make_app():
    return InfiniteClipboard(AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
    ))


def _offer_dict(source_peer, name="file.bin"):
    return {
        "offer_id": str(uuid.uuid4()),
        "source_peer": source_peer,
        "kind": "file",
        "items": [{"name": name, "size": 10, "hash": ""}],
        "total_size": 10,
        "created_at": time.time(),
    }


def test_offer_from_different_source_does_not_clear_other_sources_receivable():
    app = _make_app()
    peer_a, peer_b = generate_peer_id(), generate_peer_id()

    offer_a = _offer_dict(peer_a, "from-a.bin")
    app._handle_clip_offer(offer_a)
    offer_b = _offer_dict(peer_b, "from-b.bin")
    app._handle_clip_offer(offer_b)

    with app._offer_lock:
        assert offer_a["offer_id"] in app.receivable_offers
        assert offer_b["offer_id"] in app.receivable_offers
        assert offer_a["offer_id"] in app.received_offers
        assert offer_b["offer_id"] in app.received_offers


def test_second_offer_from_same_source_supersedes_only_that_sources_prior_offer():
    app = _make_app()
    peer_a, peer_b = generate_peer_id(), generate_peer_id()

    offer_b = _offer_dict(peer_b, "from-b.bin")
    app._handle_clip_offer(offer_b)
    offer_a1 = _offer_dict(peer_a, "from-a-1.bin")
    app._handle_clip_offer(offer_a1)
    offer_a2 = _offer_dict(peer_a, "from-a-2.bin")
    app._handle_clip_offer(offer_a2)

    with app._offer_lock:
        # peer_a 의 첫 offer 는 같은 발신자의 새 offer 로 대체돼 사라짐.
        assert offer_a1["offer_id"] not in app.receivable_offers
        assert offer_a1["offer_id"] not in app.received_offers
        # peer_a 의 최신 offer 는 남아있어야 함.
        assert offer_a2["offer_id"] in app.receivable_offers
        assert offer_a2["offer_id"] in app.received_offers
        # peer_b 의 offer 는 peer_a 활동과 무관하게 그대로 보존돼야 함 (이번에 고친 버그).
        assert offer_b["offer_id"] in app.receivable_offers
        assert offer_b["offer_id"] in app.received_offers
