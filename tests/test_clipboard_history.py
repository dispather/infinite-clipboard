"""H2/M13 회귀 — clipboard_history 동시접근 락 + 손상 파일 무음 처리.

2026-07-03 감사:
  H2: 로컬 클립보드 모니터 스레드와 네트워크 수신 스레드가 락 없이
      self.clipboard_history 를 동시에 insert/pop/저장해 손상 가능.
  M13: `--window history` 서브프로세스가 clipboard_history.json 파싱 실패를
      `except Exception: pass` 로 완전히 무음 처리 — 사용자가 손상/빈 상태를
      구분할 수 없었다.
"""

import json
import threading

from config import AppConfig
from core.protocol import generate_peer_id
from main import InfiniteClipboard, _load_clipboard_history_file


def _make_app(history_size=1000):
    cfg = AppConfig(
        mode="client", auth_key="x" * 32, peer_id=generate_peer_id(),
        clipboard_history_size=history_size,
    )
    return InfiniteClipboard(cfg)


# ── H2: 동시 접근 ──────────────────────────────────────────────────────

def test_concurrent_add_to_history_no_corruption(tmp_path, monkeypatch):
    """여러 스레드가 동시에 _add_to_history 를 호출해도 예외 없이, trim 이
    일관되게 적용되고 저장된 JSON 파일이 항상 유효해야 한다."""
    app = _make_app(history_size=50)
    history_file = tmp_path / "clipboard_history.json"
    monkeypatch.setattr(app, "_get_history_file", lambda: str(history_file))

    errors = []

    def worker(n):
        try:
            for i in range(30):
                app._add_to_history("text", f"thread{n}-item{i}")
        except Exception as e:  # pragma: no cover - 실패 시에만 기록
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"동시 접근 중 예외 발생: {errors}"
    assert len(app.clipboard_history) <= 50, (
        f"H2 회귀 — trim 이 깨져 history_size 초과: {len(app.clipboard_history)}"
    )
    # 파일이 항상 유효한 JSON 이어야 함 (쓰기 도중 리스트가 바뀌어 깨지지 않았는지)
    with open(history_file, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert isinstance(on_disk, list)
    assert len(on_disk) == len(app.clipboard_history)


def test_add_to_history_uses_lock_for_mutation_and_save(tmp_path, monkeypatch):
    """_save_history_file 호출이 _history_lock 보유 중에 일어나는지 직접 확인."""
    app = _make_app()
    history_file = tmp_path / "clipboard_history.json"
    monkeypatch.setattr(app, "_get_history_file", lambda: str(history_file))

    observed_locked = []
    original_save = app._save_history_file

    def spy_save():
        # Lock.locked() 는 이 스레드가 보유 중인지까지는 구분 못 하지만, 최소한
        # "락이 걸려 있는 상태"에서 저장이 일어나는지는 확인 가능.
        observed_locked.append(app._history_lock.locked())
        return original_save()

    app._save_history_file = spy_save
    app._add_to_history("text", "hello")

    assert observed_locked == [True], (
        "H2 회귀 — _save_history_file 이 _history_lock 없이 호출됨"
    )


# ── M13: 손상 파일 처리 ────────────────────────────────────────────────

def test_load_history_missing_file_returns_empty_not_corrupted(tmp_path):
    history_file = tmp_path / "clipboard_history.json"
    history, corrupted = _load_clipboard_history_file(history_file)
    assert history == []
    assert corrupted is False


def test_load_history_valid_file_returns_list(tmp_path):
    history_file = tmp_path / "clipboard_history.json"
    history_file.write_text(json.dumps([{"type": "text", "content": "hi"}]), encoding="utf-8")
    history, corrupted = _load_clipboard_history_file(history_file)
    assert history == [{"type": "text", "content": "hi"}]
    assert corrupted is False


def test_load_history_corrupted_json_backs_up_and_flags(tmp_path):
    history_file = tmp_path / "clipboard_history.json"
    history_file.write_text("{ not valid json at all", encoding="utf-8")

    history, corrupted = _load_clipboard_history_file(history_file)

    assert history == []
    assert corrupted is True, "M13 회귀 — 손상을 UI 에 알리지 못함"
    backups = list(tmp_path.glob("clipboard_history.json.corrupt-*"))
    assert len(backups) == 1, "M13 회귀 — 손상된 원본이 백업되지 않음"
    assert backups[0].read_text(encoding="utf-8") == "{ not valid json at all"


def test_load_history_wrong_shape_treated_as_corrupted(tmp_path):
    """리스트가 아닌 형태(예: dict)도 손상으로 취급해야 나중에 UI 렌더링에서
    터지지 않는다."""
    history_file = tmp_path / "clipboard_history.json"
    history_file.write_text(json.dumps({"unexpected": "shape"}), encoding="utf-8")

    history, corrupted = _load_clipboard_history_file(history_file)

    assert history == []
    assert corrupted is True
