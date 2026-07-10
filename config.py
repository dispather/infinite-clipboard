"""
Infinite Clipboard 설정 관리
"""

import json
import logging
import platform
import os
import secrets
import shutil
import time
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List

from core.protocol import generate_peer_id, is_valid_peer_id

_logger = logging.getLogger(__name__)

# C4: load_config() 가 손상된 settings.json 을 감지해 재생성했을 때 남기는 경고.
# tray 가 아직 없는 시점(main() 이 load_config() 를 tray 생성보다 먼저 호출)에
# 발생하므로, main() 이 get_last_config_warning() 으로 읽어 tray 준비 후 notify.
_last_config_warning: Optional[str] = None


def get_last_config_warning() -> Optional[str]:
    """가장 최근 load_config() 호출에서 발생한 설정 파일 손상 경고 (없으면 None)."""
    return _last_config_warning


# v2.3: 파일 충돌 정책 화이트리스트 — file_transfer.py 가 import 해서 사용.
# 사용자 사고 차단을 위해 SHA-256 dedup short-circuit 이 모든 정책에 우선 적용된다.
VALID_FILE_CONFLICT_POLICIES = (
    "overwrite",                # 기존 덮어쓰기
    "skip",                     # 수신 안 함
    "rename_with_timestamp",    # name.YYYYMMDD-HHMMSS.ext
    "rename_with_counter",      # name (1).ext (v2.2 까지의 동작)
)


# OS 감지
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


def _get_config_dir() -> Path:
    """설정 파일 저장 디렉토리 반환"""
    if IS_WINDOWS:
        app_data = os.environ.get("APPDATA", os.path.expanduser("~"))
        config_dir = Path(app_data) / "InfiniteClipboard"
    elif IS_MACOS:
        config_dir = Path.home() / "Library" / "Application Support" / "InfiniteClipboard"
    else:
        config_dir = Path.home() / ".config" / "InfiniteClipboard"

    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def _get_download_dir() -> str:
    """기본 다운로드 경로 반환"""
    downloads = Path.home() / "Downloads"
    if downloads.exists():
        return str(downloads)
    return str(Path.home())


def _generate_auth_key() -> str:
    """랜덤 인증 키 생성 (URL-safe 22자, 약 128비트 엔트로피).

    이전 버전의 6자리 숫자 PIN(~20비트)은 오프라인 크랙이 가능해 폐기.
    모든 PC가 같은 키를 공유해야 하므로 설정 파일의 auth_key를 복사해
    다른 PC의 settings.json에 동일하게 넣거나 설정창에서 붙여넣는다.
    """
    return secrets.token_urlsafe(16)


@dataclass
class AppConfig:
    """앱 설정"""
    mode: str = "client"                    # "server" 또는 "client"
    server_host: str = ""                   # 빈 문자열이면 Tailscale IP 자동 감지
    port: int = 9999
    auth_key: str = ""                      # 빈 문자열이면 PIN 자동 생성
    # M4: HMAC handshake 는 이 값과 무관하게 항상 필수 — 인증을 우회/완화하지
    # 않는다. 실제 효과는 접속 peer 가 Tailscale IP 대역이면 로그 한 줄만 남김.
    tailscale_trust: bool = True
    device_name: str = ""                   # 빈 문자열이면 platform.node() 사용
    download_path: str = ""                 # 빈 문자열이면 ~/Downloads 사용
    clipboard_history_size: int = 20
    clipboard_check_interval: float = 0.5   # 클립보드 폴링 간격 (초)
    reconnect_interval: int = 5             # 재연결 시도 간격 (초)
    max_file_size_gb: int = 10              # 최대 파일 크기 (GB)
    # v2.2 R1: history privacy. 민감 패턴 (JWT, AWS key, BEGIN PRIVATE KEY 등)
    # 감지 시 history 저장 skip. 텍스트 클립보드 동기화 자체는 영향 없음.
    history_privacy_mode: bool = True
    # v2.2 R2: 서버 bind 주소. 빈 문자열 = 자동 (Tailscale IP, 미감지 시 0.0.0.0).
    # 명시적 "0.0.0.0" = 모든 인터페이스 (물리 LAN 노출 — opt-in).
    # 명시적 IP (예 "100.99.126.25") = 그 인터페이스만.
    bind_address: str = ""
    # v2.3: 파일 충돌 정책 — 동일 파일명 재수신 시 동작.
    # SHA-256 dedup short-circuit (송신측 hash ↔ 수신측 hash 일치 시 자동 skip)
    # 이 모든 정책에 우선 적용되어, 같은 파일 재수신 시 numbering 누적을 차단한다.
    # 옵션은 VALID_FILE_CONFLICT_POLICIES 참조. 기본값은 v2.2 까지 동작 호환.
    file_conflict_policy: str = "rename_with_counter"
    # v2.3: staging 디렉토리 (/tmp/ic_clipboard 등) TTL — mtime 기반 만료 후 삭제.
    # 1 ~ 720 시간 (1시간 ~ 30일). 0 또는 음수는 비허용 (의도치 않은 즉시 삭제 방지).
    staging_ttl_hours: int = 24
    # v3.0: lazy offer TTL — copy 후 이 시간이 지나면 paste(fetch) 시 expired 거부.
    # 1 ~ 720 시간. staging_ttl 과 동일 검증 패턴. 오래된 offer 의 stale fetch 차단.
    offer_ttl_hours: int = 24
    # v3.0.3: lazy fetch grace — 받는 PC 가 offer 를 등록한 직후 이 시간(초) 안에 들어온
    # 클립보드 read 는 OS 셸(Windows explorer.exe)/DE 의 즉시 peek 으로 간주해 전송하지
    # 않는다(진짜 paste 는 항상 수 초 뒤). 0 이면 가드 끔(즉시 전송=eager). 크로스머신은
    # 1초 내 paste 가 사실상 불가능해 안전. 같은 PC 에서 빠른 paste 가 잦으면 낮춰서 튜닝.
    fetch_grace_seconds: float = 2.0
    # v3.0.5: 파일/이미지 수신 모드. False(기본)=명시적 '받기' 모드 — 받는 PC 가 클립보드를
    # 소유하지 않아 클립보드 매니저/파일인식 앱의 자동 read 가 paste 없이 전송을 트리거하는
    # 것을 원천 차단(함정 #28). 받을 항목은 전송창 '받기' 버튼으로 수신. True=기존 paste-
    # 트리거 lazy 등록(자동 peek 없는 환경 한정 opt-in; KDE 등에선 자동 전송 회귀 위험).
    lazy_paste: bool = False
    # v3.0: 안정적 peer 식별자 (targeted relay 라우팅 전제). 빈 문자열이면
    # 자동 생성 후 영속. 같은 PC 는 재연결해도 같은 id. 32-char lowercase hex.
    # 형식 정의/검증은 core.protocol.is_valid_peer_id 가 SSOT.
    peer_id: str = ""
    # UI 표시 언어. 빈 문자열이면 OS 로케일 자동감지("en"/"ko" 명시 가능).
    # server_host/bind_address 등과 동일한 "빈 문자열=자동" 컨벤션.
    language: str = ""

    def __post_init__(self):
        if not self.device_name:
            self.device_name = platform.node()
        if not self.peer_id:
            self.peer_id = generate_peer_id()
        if not self.download_path:
            self.download_path = _get_download_dir()
        # 약한 기본값(빈 문자열, "change-me", 6자리 숫자 PIN)이면 강한 키로 교체
        if not self.auth_key or self.auth_key == "change-me" or (
            len(self.auth_key) <= 8 and self.auth_key.isdigit()
        ):
            self.auth_key = _generate_auth_key()
        if not self.server_host:
            self.server_host = "100.64.0.1"

        # v2.2 R1: 설정값 boundary 검증. 위반 시 default 로 보정 + warning.
        # 잘못된 settings.json 으로 앱 자체가 망가지는 것을 방지.
        self._validate_and_clamp()

    def _validate_and_clamp(self) -> None:
        """잘못된 값을 기본값으로 보정한다 (warning 로그 + 정정)."""
        # mode
        if self.mode not in ("server", "client"):
            _logger.warning(f"mode={self.mode!r} invalid, reset to 'client'")
            self.mode = "client"

        # language: 빈 문자열(자동감지)이거나 지원 언어("en"/"ko")만 허용
        if self.language and self.language not in ("en", "ko"):
            _logger.warning(f"language={self.language!r} invalid, reset to 'en'")
            self.language = "en"

        # port: 1~65535
        if not isinstance(self.port, int) or not (1 <= self.port <= 65535):
            _logger.warning(f"port={self.port!r} out of range [1,65535], reset to 9999")
            self.port = 9999

        # max_file_size_gb: 1~1000
        if not isinstance(self.max_file_size_gb, int) or not (1 <= self.max_file_size_gb <= 1000):
            _logger.warning(
                f"max_file_size_gb={self.max_file_size_gb!r} out of range [1,1000], "
                f"reset to 10"
            )
            self.max_file_size_gb = 10

        # clipboard_check_interval: > 0 (너무 작으면 CPU 과부하)
        if not isinstance(self.clipboard_check_interval, (int, float)) \
                or self.clipboard_check_interval <= 0 or self.clipboard_check_interval > 60:
            _logger.warning(
                f"clipboard_check_interval={self.clipboard_check_interval!r} "
                f"out of range (0, 60], reset to 0.5"
            )
            self.clipboard_check_interval = 0.5

        # reconnect_interval: 0~3600
        if not isinstance(self.reconnect_interval, int) \
                or not (0 <= self.reconnect_interval <= 3600):
            _logger.warning(
                f"reconnect_interval={self.reconnect_interval!r} out of range [0,3600], "
                f"reset to 5"
            )
            self.reconnect_interval = 5

        # clipboard_history_size: 0~1000
        if not isinstance(self.clipboard_history_size, int) \
                or not (0 <= self.clipboard_history_size <= 1000):
            _logger.warning(
                f"clipboard_history_size={self.clipboard_history_size!r} out of range "
                f"[0,1000], reset to 20"
            )
            self.clipboard_history_size = 20

        # download_path: 쓰기 가능해야 함
        try:
            Path(self.download_path).mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as e:
            fallback = _get_download_dir()
            _logger.warning(
                f"download_path={self.download_path!r} unwritable ({e}), "
                f"fallback to {fallback}"
            )
            self.download_path = fallback

        # auth_key 강도 경고 (강제 아님 — 사용자 의도된 짧은 키 가능)
        if 0 < len(self.auth_key) < 16:
            _logger.warning(
                f"auth_key length {len(self.auth_key)} < 16, "
                f"consider regenerating for stronger entropy"
            )

        # v2.2 R2: bind_address 형식 검증 — 빈 문자열 / 유효 IPv4 만 허용.
        # 잘못된 값은 "" 로 reset (자동 Tailscale 모드).
        if not isinstance(self.bind_address, str):
            _logger.warning(
                f"bind_address={self.bind_address!r} not a string, reset to ''"
            )
            self.bind_address = ""
        elif self.bind_address:
            try:
                import ipaddress
                ipaddress.ip_address(self.bind_address)
            except ValueError:
                _logger.warning(
                    f"bind_address={self.bind_address!r} not a valid IP, reset to ''"
                )
                self.bind_address = ""

        # v2.3: file_conflict_policy 화이트리스트 검증.
        # 모르는 값이면 default 로 reset 해 사용자 사고를 차단.
        if self.file_conflict_policy not in VALID_FILE_CONFLICT_POLICIES:
            _logger.warning(
                f"file_conflict_policy={self.file_conflict_policy!r} invalid, "
                f"reset to 'rename_with_counter'"
            )
            self.file_conflict_policy = "rename_with_counter"

        # v2.3: staging_ttl_hours 범위 검증 (1~720 시간).
        # 0/음수는 의도치 않은 즉시 삭제 위험으로 거부.
        if not isinstance(self.staging_ttl_hours, int) \
                or not (1 <= self.staging_ttl_hours <= 720):
            _logger.warning(
                f"staging_ttl_hours={self.staging_ttl_hours!r} out of range [1,720], "
                f"reset to 24"
            )
            self.staging_ttl_hours = 24

        # v3.0: offer_ttl_hours 범위 검증 (staging_ttl 과 동일 패턴, 1~720 시간).
        if not isinstance(self.offer_ttl_hours, int) \
                or not (1 <= self.offer_ttl_hours <= 720):
            _logger.warning(
                f"offer_ttl_hours={self.offer_ttl_hours!r} out of range [1,720], "
                f"reset to 24"
            )
            self.offer_ttl_hours = 24

        # v3.0.3: fetch_grace_seconds 범위 검증 (0 ~ 30초). 0 = 가드 끔(eager).
        # 음수/비수치/과대값은 default 2.0 으로 보정.
        if not isinstance(self.fetch_grace_seconds, (int, float)) \
                or isinstance(self.fetch_grace_seconds, bool) \
                or not (0 <= self.fetch_grace_seconds <= 30):
            _logger.warning(
                f"fetch_grace_seconds={self.fetch_grace_seconds!r} out of range "
                f"[0,30], reset to 2.0"
            )
            self.fetch_grace_seconds = 2.0
        else:
            self.fetch_grace_seconds = float(self.fetch_grace_seconds)

        # v3.0: peer_id 형식 검증 (32-char lowercase hex). settings.json 이
        # 손상된 값을 담고 있으면 재생성 — 라우팅이 깨진 id 로 동작하는 것 방지.
        if not is_valid_peer_id(self.peer_id):
            _logger.warning(
                f"peer_id={self.peer_id!r} invalid format, regenerating"
            )
            self.peer_id = generate_peer_id()


CONFIG_FILE = _get_config_dir() / "settings.json"
LOG_FILE = _get_config_dir() / "infinite-clipboard.log"


def _backup_corrupt_config() -> None:
    """C4: 손상된 settings.json 을 타임스탬프 붙여 백업 (원본 보존, 재생성 전 안전망).

    백업 자체가 실패해도(디스크 가득 참 등) 흡수하고 재생성 흐름은 계속 진행한다 —
    백업은 안전망이지 필수 전제조건이 아니다.
    """
    try:
        if CONFIG_FILE.exists():
            backup_path = CONFIG_FILE.with_name(
                f"{CONFIG_FILE.name}.corrupt-{int(time.time())}"
            )
            shutil.copy2(CONFIG_FILE, backup_path)
            _logger.warning(f"손상된 설정 파일 백업: {backup_path}")
    except OSError as e:
        _logger.error(f"손상된 설정 파일 백업 실패: {e}")


def load_config(persist_corrections: bool = True) -> AppConfig:
    """설정 파일에서 로드. 없으면 기본값 생성 후 저장.

    Args:
        persist_corrections: H4 자동교정 저장을 이 호출에서 시도할지. 오래 사는
            메인 프로세스(서버/클라이언트)만 True 로 둬야 한다 — 리뷰에서 발견된
            레이스: `--window settings/history/transfers` 처럼 짧게 뜨는 별도
            프로세스가 메인과 거의 동시에 로드하며 각자 다른 랜덤 auth_key/peer_id
            로 교정해 저장하면, 늦게 쓴 쪽이 이기고 먼저 쓴 프로세스의 메모리 상태가
            디스크와 어긋난다. 짧게 사는 하위 창은 False 로 호출해 저장을 메인
            프로세스에게만 맡긴다.
    """
    global _last_config_warning
    _last_config_warning = None
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            filtered = {k: v for k, v in data.items() if k in AppConfig.__dataclass_fields__}
            config = AppConfig(**filtered)
        except (json.JSONDecodeError, TypeError, OSError) as e:
            # C4: 과거엔 (json.JSONDecodeError, TypeError) 만 잡아 PermissionError 등
            # OSError 계열은 아예 안 잡혀 앱이 tray 생기기도 전에 크래시했고, print()
            # 는 windowed 빌드(콘솔 없음)에서 아무도 못 봤으며, 백업 없이 새 auth_key/
            # peer_id 로 원본을 덮어써 그룹 동기화가 원인 불명으로 깨졌다.
            _last_config_warning = (
                f"설정 파일이 손상되어 기본값으로 재설정되었습니다 (원본은 "
                f"settings.json.corrupt-* 로 백업됨). 원인: {e}"
            )
            _logger.warning(_last_config_warning)
            _backup_corrupt_config()

            # 설정 파일이 손상되었으면 기본값 생성 후 즉시 저장
            config = AppConfig()
            save_config(config)
            return config

        # H4: __post_init__/_validate_and_clamp 가 로드된 값을 교정했거나(약한
        # auth_key, 잘못된 peer_id, 범위 밖 값) 신규 필드가 누락돼 기본값을
        # 채웠다면 즉시 저장한다. 저장하지 않으면 디스크엔 원래 값이 남아 재시작
        # 마다 재교정이 반복되는데, auth_key/peer_id 는 교정마다 "다른" 랜덤값이
        # 나오므로(secrets.token_urlsafe/generate_peer_id) 매 재시작 값이 바뀐다
        # — auth_key 는 그룹 간 공유 키가 어긋나고, peer_id 는 "재연결해도 같은
        # id" invariant 가 깨져 identity squatting 가드(H1)/dedup/targeted relay
        # 가 오작동한다.
        #
        # 리뷰 발견: 이 저장 시도를 위 읽기/파싱 try 안에 두면, 저장 자체가
        # (디스크 가득/AV 락 등으로) OSError 를 던졌을 때 위 except 가 "파일이
        # 손상됐다"고 오판해 방금 읽은 *유효한* 설정을 손상 취급으로 백업하고
        # 새 auth_key/peer_id 로 덮어썼다 — H4 가 막으려던 바로 그 정체성 교체
        # 버그를 다른 경로로 재현하는 셈이었다. 그래서 별도 try 로 분리해
        # 저장 실패는 "교정을 다음 기회에 재시도" 정도로만 다룬다.
        corrected = asdict(config)
        needs_save = any(
            field_name not in filtered or filtered[field_name] != corrected[field_name]
            for field_name in AppConfig.__dataclass_fields__
        )
        if needs_save and persist_corrections:
            try:
                _logger.info("설정 파일 자동 교정/신규 필드 반영 — settings.json 갱신")
                save_config(config)
            except OSError as e:
                _logger.warning(
                    f"설정 자동 교정 저장 실패 (다음 로드에 재시도, 원본은 그대로): {e}"
                )

        return config

    # 설정 파일이 없으면 기본값 생성 후 즉시 저장 — 최초 부트스트랩은
    # persist_corrections 와 무관하게 항상 저장한다(그렇지 않으면 하위 창
    # 프로세스가 우연히 먼저 뜬 경우 설정 파일 자체가 영영 안 생길 수 있음).
    config = AppConfig()
    save_config(config)
    return config


def save_config(config: AppConfig) -> None:
    """설정을 JSON 파일로 저장 (auth_key가 담기므로 0o600 권한)."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 원자적 쓰기: 임시 파일에 기록 후 교체
    tmp_path = CONFIG_FILE.with_suffix(".json.tmp")

    # L6 (TOCTOU): 기존엔 open() 으로 만든 뒤 chmod() 로 권한을 조였는데, 그
    # 사이 파일이 잠깐 기본 umask 권한(예: 0o644)으로 존재해 auth_key 가 담긴
    # 내용이 노출될 여지가 있었다. POSIX 는 os.open 의 mode 인자로 생성
    # 시점부터 0o600 을 강제해 그 창을 아예 없앤다.
    if os.name == "posix":
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        f = os.fdopen(fd, "w", encoding="utf-8")
    else:
        # Windows는 ACL 기반이라 POSIX mode 무시됨 — 기존 open() 그대로.
        f = open(tmp_path, "w", encoding="utf-8")
    with f:
        json.dump(asdict(config), f, indent=2, ensure_ascii=False)

    os.replace(tmp_path, CONFIG_FILE)
