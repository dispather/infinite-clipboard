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
