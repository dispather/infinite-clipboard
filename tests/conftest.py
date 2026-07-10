"""pytest 공통 설정 — 프로젝트 루트를 sys.path에 추가."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate_config_dir(monkeypatch, tmp_path):
    """테스트가 실제 사용자의 `~/.config/InfiniteClipboard/`(또는 OS별 등가 경로)를
    건드리지 않도록 격리.

    `InfiniteClipboard(AppConfig(...))`를 직접 생성하는 테스트(예:
    test_receive_offer_retry.py)는 `CheckpointManager()`(core/file_transfer.py:1171)와
    `_save_transfer_state`(main.py) 등이 `config._get_config_dir()`를 거쳐 실제 앱
    설정 디렉토리에 즉시 파일을 쓴다. 이 리포는 실제로 실행 중인 데몬이 있고 그
    디렉토리의 `transfer_state.json`에 사용자가 아직 받지 않은 실제 receivable
    offer 가 들어있을 수 있다 — 격리 없이 테스트를 돌리면 그 상태를 덮어써 사라지게
    할 위험이 있다. `main.py`/`core/file_transfer.py` 모두 `from config import
    _get_config_dir`를 호출 시점에 지연 임포트하므로, 모듈 속성 하나만 patch 하면
    양쪽 경로가 전부 격리된다.
    """
    import config
    monkeypatch.setattr(config, "_get_config_dir", lambda: tmp_path)


@pytest.fixture(autouse=True)
def _no_real_desktop_notifications(monkeypatch):
    """테스트가 실제 OS 알림(notify-send/gdbus)을 띄우지 않도록 plyer 를 무음화.

    2026-07-04 발견: `main.InfiniteClipboard._notify`는 tray 없으면(테스트에서
    `self.tray`는 항상 기본값 None) `plyer.notification.notify(...)`로 폴백하는데,
    이건 실제로 subprocess(notify-send/gdbus)를 실행해 개발자 데스크톱에 진짜 팝업을
    띄운다. `_receive_offer`/`_provider_fetch` 를 반복 호출하는 테스트(특히
    test_receive_offer_retry.py 21건)가 세션당 수십 개의 실제 알림을 띄우는 부작용을
    일으켰다. tests/test_notify_fallback.py 처럼 개별 테스트가 plyer 자체를 명시적으로
    monkeypatch 하는 경우엔 그 테스트 함수 안의 patch 가 이 기본값을 그대로 덮어써
    정상 동작한다(monkeypatch 는 LIFO 로 되돌아가므로 서로 간섭 없음).
    """
    try:
        import plyer.notification
    except ImportError:
        return
    monkeypatch.setattr(plyer.notification, "notify", lambda **_kwargs: None, raising=False)


@pytest.fixture(autouse=True)
def _no_real_actionable_notifications(monkeypatch):
    """테스트가 실제 액션 버튼 알림(D-Bus Notify 등)을 띄우지 않도록 기본 비활성화.

    `_add_receivable`(main.py)이 `_ensure_actionable_notifier()`를 통해
    `get_actionable_notifier()`를 호출하는데, 이 Linux 개발 환경엔 실제 세션
    D-Bus 가 연결돼(`core/notify_linux.py`) 진짜 "받기"/"무시" 버튼 알림을
    개발자 데스크톱에 띄운다 — plyer 를 무음화한 `_no_real_desktop_notifications`
    와 같은 이유로 기본은 미지원(None)으로 되돌려 기존 plyer 폴백 경로만 타게
    한다. main.py 가 `from core.actionable_notify import get_actionable_notifier`
    로 이름을 자기 네임스페이스에 바인딩해두므로, 원본 모듈이 아니라 `main`
    모듈의 바인딩을 patch 해야 실제로 적용된다. actionable 백엔드 자체를
    테스트하려는 케이스는 `app.actionable_notifier`/`app._actionable_notifier_inited`
    를 직접 주입해(tests/test_lazy_orchestration.py 의 `_StubProvider` 패턴과
    동일) 이 기본값을 우회한다.

    `main` 임포트는 `core.file_transfer`(xxhash) / `core.clipboard_manager`
    (Pillow) 를 끌어오는 무거운 체인이다 — CI 의 windows/macos 잡처럼
    `core.lazy_win`/`core.lazy_mac` 만 테스트하려고 최소 의존성만 설치한
    환경(2026-07-10 CI 회귀로 실측)에서는 `import main` 자체가 실패한다.
    그 경우 애초에 그 테스트들이 `main`을 쓰지 않는다는 뜻이므로(수집은
    이미 성공했고, 이 fixture 는 setup 시점에만 실행됨) 조용히 아무 것도
    안 하고 넘어간다 — `_no_real_desktop_notifications`의 plyer ImportError
    처리와 동일한 방어 패턴.
    """
    try:
        import main
    except ImportError:
        return
    monkeypatch.setattr(main, "get_actionable_notifier", lambda: None)
