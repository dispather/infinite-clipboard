"""인증 키 강화 회귀 — 약한 기본값(PIN)이 자동으로 강한 토큰으로 교체되는지."""

import json
import os
import pytest

from config import AppConfig, save_config, load_config, CONFIG_FILE


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """CONFIG_FILE을 tmp_path로 리디렉트."""
    fake = tmp_path / "settings.json"
    import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_FILE", fake)
    yield fake


def test_empty_auth_key_gets_strong_token(isolated_config):
    c = AppConfig(auth_key="")
    # token_urlsafe(16) → base64 22자
    assert len(c.auth_key) >= 20
    assert c.auth_key != "change-me"


def test_old_6digit_pin_is_upgraded(isolated_config):
    c = AppConfig(auth_key="123456")  # 숫자 6자리 → 약한 PIN
    # 자동 교체되어야 함
    assert c.auth_key != "123456"
    assert len(c.auth_key) >= 20


def test_change_me_placeholder_replaced(isolated_config):
    c = AppConfig(auth_key="change-me")
    assert c.auth_key != "change-me"
    assert len(c.auth_key) >= 20


def test_strong_existing_key_preserved(isolated_config):
    strong = "abcDEFghi-1234567890_zyxWVU"  # 충분히 긴 비숫자 키
    c = AppConfig(auth_key=strong)
    assert c.auth_key == strong


@pytest.mark.skipif(os.name != "posix", reason="POSIX chmod만 검증")
def test_save_config_sets_0600(isolated_config):
    c = AppConfig()
    save_config(c)
    mode = isolated_config.stat().st_mode & 0o777
    assert mode == 0o600, f"settings.json 권한이 0o{mode:o} (기대: 0o600)"


@pytest.mark.skipif(os.name != "posix", reason="POSIX 파일 권한만 검증")
def test_save_config_no_toctou_loose_permission_window(isolated_config, monkeypatch):
    """L6: open() 후 chmod() 로 조이던 옛 방식은 그 사이 잠깐 기본 umask 권한
    (예: 0o644)으로 auth_key 가 노출될 여지가 있었다. 쓰기가 진행되는 그
    순간(json.dump 호출 시점)에도 이미 0o600 이어야 TOCTOU 창이 없는 것."""
    import config as cfg

    observed_modes = []
    original_dump = json.dump

    def spying_dump(obj, fp, **kwargs):
        mode = os.stat(fp.fileno()).st_mode & 0o777
        observed_modes.append(mode)
        return original_dump(obj, fp, **kwargs)

    monkeypatch.setattr(cfg.json, "dump", spying_dump)
    save_config(AppConfig())

    assert observed_modes == [0o600], (
        f"L6 회귀 — 쓰기 도중 파일 권한이 0o600 이 아님: "
        f"{[format(m, 'o') for m in observed_modes]}"
    )


def test_save_load_roundtrip(isolated_config):
    c = AppConfig(mode="server", port=12345)
    save_config(c)
    loaded = load_config()
    assert loaded.mode == "server"
    assert loaded.port == 12345
    assert loaded.auth_key == c.auth_key


def test_corrupt_json_backed_up_and_defaults_used(isolated_config):
    """C4: settings.json 이 손상되면 (1) 백업 파일이 남고 (2) 기본값으로 재생성되고
    (3) get_last_config_warning() 이 경고를 노출해야 한다 — 과거엔 print() 만 하고
    (windowed 빌드에선 아무도 못 봄) 백업 없이 원본을 덮어썼다."""
    from config import get_last_config_warning

    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text("{ this is not valid json ", encoding="utf-8")

    loaded = load_config()

    # 기본값으로 재생성됨
    assert loaded.mode == "client"

    # 손상된 원본이 타임스탬프 백업으로 남음
    backups = list(isolated_config.parent.glob(f"{isolated_config.name}.corrupt-*"))
    assert len(backups) == 1, f"손상 백업 파일이 없음: {list(isolated_config.parent.iterdir())}"
    assert backups[0].read_text(encoding="utf-8") == "{ this is not valid json "

    # 재생성 후 settings.json 자체는 유효한 JSON (덮어써짐)
    assert isolated_config.exists()
    json.loads(isolated_config.read_text(encoding="utf-8"))

    # 경고가 노출됨 (main() 이 tray.notify 로 사용)
    assert get_last_config_warning() is not None


def test_unreadable_config_file_does_not_crash(isolated_config, monkeypatch):
    """C4: settings.json 읽기 자체가 OSError(예: PermissionError) 를 던져도
    load_config() 는 예외를 삼키고 기본값으로 계속 진행해야 한다 — 과거엔
    (json.JSONDecodeError, TypeError) 만 잡아 OSError 계열은 앱을 tray 가
    생기기도 전에 크래시시켰다."""
    isolated_config.write_text("{}", encoding="utf-8")

    import builtins
    real_open = builtins.open

    def failing_open(path, *args, **kwargs):
        if str(path) == str(isolated_config):
            raise PermissionError("simulated permission error")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failing_open)
    loaded = load_config()  # 여기서 예외가 새면 테스트가 실패한다
    assert loaded.mode == "client"


def test_weak_auth_key_correction_is_persisted(isolated_config):
    """H4: settings.json 에 약한 auth_key(숫자 PIN)가 있으면 load_config() 가
    강한 키로 교정할 뿐 아니라 그 교정값을 디스크에도 즉시 저장해야 한다.
    저장 안 하면 재시작마다 다시 감지→'다른' 랜덤 키로 재교정이 반복돼
    그룹 공유 auth_key 가 계속 어긋난다."""
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text(
        json.dumps({"mode": "client", "auth_key": "123456"}), encoding="utf-8"
    )

    first = load_config()
    assert first.auth_key != "123456"

    # 디스크에 교정값이 반영됐는지 — 원본 약한 값이 남아있으면 회귀
    on_disk = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert on_disk["auth_key"] == first.auth_key

    # 재시작(재로드) 해도 같은 키 유지 — 저장 안 되면 매번 다른 랜덤 키가 나옴
    second = load_config()
    assert second.auth_key == first.auth_key


def test_missing_peer_id_field_is_persisted(isolated_config):
    """H4: peer_id 필드가 아예 없는 구버전 settings.json(v3.0 이전 업그레이드
    시나리오)을 로드하면 자동 생성된 peer_id 가 즉시 저장되어야 한다 — 안 그러면
    재시작마다 다른 peer_id 가 생겨 '재연결해도 같은 id' invariant 가 깨진다."""
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text(
        json.dumps({"mode": "client", "port": 9999}), encoding="utf-8"
    )

    first = load_config()
    assert first.peer_id

    on_disk = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert on_disk.get("peer_id") == first.peer_id

    second = load_config()
    assert second.peer_id == first.peer_id


def test_valid_config_does_not_rewrite_file(isolated_config, monkeypatch):
    """H4 부작용 가드: 이미 유효한 설정은 매 load_config() 마다 불필요하게
    재저장되면 안 된다(잦은 디스크 쓰기/atomic replace 노이즈)."""
    c = AppConfig(mode="server", port=23456)
    save_config(c)

    import config as cfg
    calls = []
    monkeypatch.setattr(cfg, "save_config", lambda cfg_obj: calls.append(cfg_obj))

    load_config()
    assert calls == [], "변경 없는 유효한 설정인데 save_config 가 호출됨"


def test_transient_save_failure_during_autocorrect_does_not_corrupt_original(
    isolated_config, monkeypatch,
):
    """리뷰 발견: H4 자동교정 저장(save_config)이 원본 읽기/파싱과 같은 try 안에
    있으면, 저장 자체가 (디스크 가득/AV 락 등으로) OSError 를 던졌을 때 "파일이
    손상됐다"고 오판해 방금 읽은 *유효한* 원본을 손상 취급으로 백업하고 새
    auth_key/peer_id 로 덮어썼다 — H4 가 막으려던 정체성 교체 버그를 다른
    경로로 재현하는 것. 저장 실패는 원본을 건드리지 않고 조용히 재시도 대상으로만
    남아야 한다."""
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    original_text = json.dumps({"mode": "client", "auth_key": "123456"})  # 약함 → 교정 유발
    isolated_config.write_text(original_text, encoding="utf-8")

    from config import get_last_config_warning
    import config as cfg

    def failing_save(_config):
        raise OSError("simulated transient disk-full during auto-correct save")

    monkeypatch.setattr(cfg, "save_config", failing_save)

    loaded = load_config()  # 예외가 새면 테스트 실패

    # 원본 파일은 그대로(손상 백업이 생기면 안 됨) — 저장이 실패했을 뿐 원본은 유효했음
    assert isolated_config.read_text(encoding="utf-8") == original_text, (
        "저장 실패인데 원본 settings.json 이 변경됨(손상 처리로 오인됐을 위험)"
    )
    backups = list(isolated_config.parent.glob(f"{isolated_config.name}.corrupt-*"))
    assert not backups, f"저장 실패를 손상으로 오인해 백업이 생김: {backups}"

    # 메모리상으로는 여전히 교정된(강한) 값을 반환해야 함
    assert loaded.auth_key != "123456"

    # 이 상황을 "손상"으로 취급하지 않았으므로 손상 경고도 없어야 함
    assert get_last_config_warning() is None


def test_persist_corrections_false_skips_autosave(isolated_config, monkeypatch):
    """리뷰 발견(레이스 완화): persist_corrections=False 로 호출하면 교정이
    필요해도 save_config 를 호출하지 않아야 한다 — 짧게 사는 --window 서브
    프로세스가 메인 프로세스와 동시에 저장을 시도해 서로 다른 랜덤 identity
    를 쓰는 레이스를 피하기 위함."""
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text(
        json.dumps({"mode": "client", "auth_key": "123456"}), encoding="utf-8"
    )

    import config as cfg
    calls = []
    monkeypatch.setattr(cfg, "save_config", lambda cfg_obj: calls.append(cfg_obj))

    loaded = load_config(persist_corrections=False)

    assert loaded.auth_key != "123456"  # 메모리상 교정은 여전히 적용됨
    assert calls == [], "persist_corrections=False 인데 save_config 가 호출됨"


def test_load_config_clears_stale_warning_on_success(isolated_config):
    """C4: 손상 이후 정상 로드가 성공하면 이전 경고가 남아있지 않아야 한다
    (get_last_config_warning 이 stale 경고를 계속 보고하면 재시작마다 잘못된
    notify 가 뜬다)."""
    from config import get_last_config_warning

    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text("not json at all", encoding="utf-8")
    load_config()
    assert get_last_config_warning() is not None

    c = AppConfig(mode="server", port=23456)
    save_config(c)
    load_config()
    assert get_last_config_warning() is None
