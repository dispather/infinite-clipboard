"""
파일/폴더 전송 강화 모듈

단일 파일, 다중 파일, 폴더 전송을 지원하며
xxHash64 청크 해시, SHA-256 전체 해시 이중 검증,
체크포인트 기반 이어받기 기능을 제공한다.
"""

import os
import json
import hashlib
import shutil
import tempfile
import threading
import platform
import time
import uuid
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Callable, Tuple

import xxhash

# Task 2.5: transfer_id 형식 검증 — protocol 정의를 재사용 (단일 진실 원천)
from core.protocol import is_valid_transfer_id as _is_valid_transfer_id

logger = logging.getLogger(__name__)

# ── 프로토콜 설정 참조 ─────────────────────────────────────────────────
CHUNK_SIZE = 1024 * 1024          # 1MB 청크
MAX_FILE_SIZE = 10 * 1024**3      # 기본 최대 10GB

# ── metadata schema 화이트리스트 ──────────────────────────────────────
_VALID_TRANSFER_TYPES = frozenset({"file", "files", "folder"})


# ═══════════════════════════════════════════════════════════════════════
# 데이터 클래스
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class FileMetadata:
    """파일/폴더 전송 메타데이터"""
    transfer_id: str                # UUID
    transfer_type: str              # "file", "files", "folder"
    root_name: str                  # 최상위 파일/폴더 이름
    files: List[dict] = field(default_factory=list)
    # files 원소: {"path": 상대경로, "size": int, "hash": "sha256..."}
    total_size: int = 0
    file_count: int = 0
    # 송신 전용: 상대경로 → 절대경로 매핑 (직렬화 제외)
    path_map: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """직렬화용 딕셔너리 반환 (path_map 제외)"""
        d = asdict(self)
        d.pop("path_map", None)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "FileMetadata":
        """딕셔너리에서 복원하고 Task 2.5 schema 검증을 수행한다.

        검증 항목 (위반 시 ValueError):
          - data 가 dict
          - transfer_id 가 UUID v4 형식
          - transfer_type 이 화이트리스트 ({file, files, folder})
          - files 가 list
          - 각 file 항목의 size 가 음수 아님 + path 가 _is_safe_rel_path 통과
          - total_size 가 음수 아님, file_count 가 음수 아님
          - total_size == sum(files.size), file_count == len(files)
        """
        if not isinstance(data, dict):
            raise ValueError(f"FileMetadata: dict 가 아님 (got {type(data).__name__})")

        transfer_id = data.get("transfer_id")
        if not _is_valid_transfer_id(transfer_id):
            raise ValueError(f"FileMetadata: invalid transfer_id: {transfer_id!r}")

        transfer_type = data.get("transfer_type")
        if transfer_type not in _VALID_TRANSFER_TYPES:
            raise ValueError(
                f"FileMetadata: unknown transfer_type: {transfer_type!r}"
            )

        root_name = data.get("root_name")
        if not isinstance(root_name, str):
            raise ValueError(
                f"FileMetadata: root_name 은 str 이어야 함 "
                f"(got {type(root_name).__name__})"
            )

        files = data.get("files", [])
        if not isinstance(files, list):
            raise ValueError(f"FileMetadata: files 는 list 이어야 함")

        # 각 file 엔트리 검증
        size_sum = 0
        for idx, f in enumerate(files):
            if not isinstance(f, dict):
                raise ValueError(f"FileMetadata.files[{idx}] 가 dict 아님")
            path = f.get("path")
            if not _is_safe_rel_path(path):
                raise ValueError(
                    f"FileMetadata.files[{idx}].path 가 unsafe: {path!r}"
                )
            size = f.get("size")
            if not isinstance(size, int) or size < 0:
                raise ValueError(
                    f"FileMetadata.files[{idx}].size 가 음수 또는 비정수: {size!r}"
                )
            size_sum += size

        total_size = data.get("total_size", 0)
        if not isinstance(total_size, int) or total_size < 0:
            raise ValueError(
                f"FileMetadata: total_size 음수/비정수: {total_size!r}"
            )
        if total_size != size_sum:
            raise ValueError(
                f"FileMetadata: total_size({total_size}) != "
                f"sum(files.size)({size_sum})"
            )

        file_count = data.get("file_count", 0)
        if not isinstance(file_count, int) or file_count < 0:
            raise ValueError(
                f"FileMetadata: file_count 음수/비정수: {file_count!r}"
            )
        if file_count != len(files):
            raise ValueError(
                f"FileMetadata: file_count({file_count}) != len(files)({len(files)})"
            )

        return cls(
            transfer_id=transfer_id,
            transfer_type=transfer_type,
            root_name=root_name,
            files=files,
            total_size=total_size,
            file_count=file_count,
        )


@dataclass
class TransferProgress:
    """전송 진행 상태"""
    transfer_id: str
    current_file: str               # 현재 전송 중인 파일 상대경로
    current_file_index: int         # 현재 파일 인덱스 (0-based)
    total_files: int
    bytes_sent: int                 # 지금까지 전송된 바이트
    total_bytes: int                # 전체 바이트
    speed: float = 0.0              # bytes/sec

    @property
    def progress_percent(self) -> float:
        """진행률(%)"""
        if self.total_bytes == 0:
            return 100.0
        return (self.bytes_sent / self.total_bytes) * 100

    @property
    def eta(self) -> float:
        """예상 남은 시간(초)"""
        if self.speed <= 0:
            return 0.0
        remaining = self.total_bytes - self.bytes_sent
        return remaining / self.speed


@dataclass
class Checkpoint:
    """이어받기용 체크포인트"""
    transfer_id: str
    completed_files: List[str] = field(default_factory=list)
    # 완료된 파일의 상대경로 목록
    current_file: str = ""          # 현재 전송 중인 파일
    last_chunk_index: int = -1      # 마지막 수신 청크 번호 (-1: 아직 없음)
    completed_hashes: Dict[str, str] = field(default_factory=dict)
    # {파일 상대경로: sha256 해시}


# ═══════════════════════════════════════════════════════════════════════
# Active transfer 상태 (v2.1: 단일 active invariant)
# ═══════════════════════════════════════════════════════════════════════
#
# v2.0.0 은 outgoing/incoming 양쪽 모두 dict 로 다중 active 를 허용했고,
# 새 transfer 가 시작돼도 기존 transfer 가 계속 돌아 race 와 "이전 파일이
# 붙여지는" 함정 #1 의 원인이 되었다. v2.1 부터는 한 시점에 active outgoing
# 1 개, active incoming 1 개를 invariant 로 둔다.
#
# stop_event 는 송수신 워커가 polling 하며 cancel 신호로 사용한다.
# cancel_reason 은 디버그/로깅용 (CANCEL_REASON_* 중 하나).

@dataclass
class ActiveOutgoing:
    """송신 중인 transfer 1 개의 상태."""
    transfer_id: str
    metadata: FileMetadata
    file_paths: List[str]
    stop_event: threading.Event = field(default_factory=threading.Event)
    cancel_reason: Optional[str] = None


@dataclass
class ActiveIncoming:
    """수신 중인 transfer 1 개의 상태."""
    transfer_id: str
    metadata: Optional[FileMetadata] = None
    received_bytes: int = 0
    stop_event: threading.Event = field(default_factory=threading.Event)
    cancel_reason: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════
# 헬퍼 함수
# ═══════════════════════════════════════════════════════════════════════

def format_size(size_bytes: int) -> str:
    """바이트를 사람이 읽기 쉬운 크기 문자열로 변환"""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_time(seconds: float) -> str:
    """초를 사람이 읽기 쉬운 시간 문자열로 변환"""
    if seconds < 60:
        return f"{int(seconds)}초"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}분 {secs}초"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        if minutes == 0:
            return f"{hours}시간"
        return f"{hours}시간 {minutes}분"


def get_temp_dir(transfer_id: str) -> Path:
    """전송 ID에 대응하는 임시 디렉토리 경로 반환 (생성은 하지 않음).

    Task 2.5: transfer_id 가 path 컴포넌트로 직접 사용되므로 UUID v4 형식
    검증을 통과하지 못하면 ValueError 를 발생시킨다 (path traversal 차단).
    원격 peer 가 보낸 transfer_id 는 항상 본 함수를 거쳐야 한다.
    """
    if not _is_valid_transfer_id(transfer_id):
        raise ValueError(f"Invalid transfer_id (path-unsafe): {transfer_id!r}")
    return Path(tempfile.gettempdir()) / f"ic_transfer_{transfer_id}"


def _is_safe_rel_path(rel_path: str) -> bool:
    """원격 peer가 제공한 상대 경로가 안전한지 검증한다.

    거부 조건:
    - 절대 경로 (`/etc/...`, `C:\\...`, `\\\\server\\...`)
    - `..` 상위 디렉토리 참조가 포함된 경로
    - 빈 문자열, 제어 문자, 널 바이트 포함

    프로토콜 규약상 rel_path는 항상 `/` 구분자의 상대 경로여야 한다.
    """
    if not rel_path or not isinstance(rel_path, str):
        return False
    if "\x00" in rel_path:
        return False
    # Windows 드라이브 문자 (C:, \\server\) 또는 POSIX 절대 경로 거부
    if os.path.isabs(rel_path):
        return False
    if len(rel_path) >= 2 and rel_path[1] == ":":
        return False
    if rel_path.startswith("\\") or rel_path.startswith("/"):
        return False
    # `/` 구분자로 통일 후 각 세그먼트 검증
    normalized = rel_path.replace("\\", "/")
    for part in normalized.split("/"):
        if part in ("", "..", "."):
            # 빈 세그먼트(//), 상위 참조(..), 현재 참조(.) 금지
            # 단 마지막이 빈 문자열이면(끝에 /) 허용하지 않음
            return False
    return True


def _dedup_rel_path(path_map: Dict[str, str], rel_path: str, abs_path: str) -> str:
    """C6: rel_path 가 이미 다른 절대경로를 가리키면 번호를 붙여 유일화한다.

    다중 파일/폴더 선택 시 서로 다른 위치의 동일 파일명(또는 동일 폴더명)이
    섞이면 path_map 의 rel_path 키가 충돌해 last-write-wins 로 앞 항목의
    데이터가 조립 단계에서 조용히 사라지는 문제(단일 폴더 전송은 전체 상대경로를
    보존해 영향 없음)를 막는다. 기존 "rename_with_counter" 정책과 같은 명명
    관례("name (1).ext")를 사용해 사용자에게 익숙하다.
    """
    if rel_path not in path_map or path_map[rel_path] == abs_path:
        return rel_path
    stem, ext = os.path.splitext(rel_path)
    n = 1
    candidate = f"{stem} ({n}){ext}"
    while candidate in path_map:
        n += 1
        candidate = f"{stem} ({n}){ext}"
    return candidate


def _resolve_within(base: Path, rel_path: str) -> Optional[Path]:
    """base 하위에 위치하는 안전한 절대 경로를 반환. 이탈 시 None.

    _is_safe_rel_path로 사전 필터링하고, resolve() 결과가 base 하위인지
    재확인한다 (심볼릭 링크 우회 방어).
    """
    if not _is_safe_rel_path(rel_path):
        return None
    try:
        base_resolved = base.resolve()
        final = (base_resolved / rel_path).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    try:
        final.relative_to(base_resolved)
    except ValueError:
        return None
    return final


def cleanup_staging_dir(
    staging_dir: Path,
    ttl_hours: int,
    active_paths: Optional[set] = None,
) -> Tuple[int, int]:
    """v2.3 audit P2: staging 디렉토리의 mtime 만료 항목 자동 삭제.

    `/tmp/ic_clipboard` 등 OS 클립보드에 등록된 임시 파일 저장소가 무한 증식하지
    않도록 주기 정리. dedup short-circuit 과 결합해 사용자 사고 (numbering 누적 +
    일괄 정리) 의 1차 원인 자체를 제거.

    Args:
        staging_dir: 정리 대상 디렉토리 (없으면 no-op).
        ttl_hours: 이 시간보다 오래된 항목 (mtime 기준) 만 삭제. 1 이상.
        active_paths: 진행 중 transfer 가 점유 중인 path 집합 (보호 대상).
            현재는 항상 None — 진행 중 파일은 temp_dir 에 있고 staging 에는
            transfer 완료 직후 들어가므로 cutoff 가 자동 보호. 미래 확장 여지로 둠.

    Returns:
        (삭제된 항목 수, 해제된 바이트 수).
    """
    if not staging_dir.exists() or not staging_dir.is_dir():
        return (0, 0)

    cutoff = time.time() - max(1, ttl_hours) * 3600
    active = active_paths or set()
    deleted = 0
    freed = 0

    for entry in staging_dir.iterdir():
        try:
            if str(entry) in active:
                continue
            stat = entry.stat()
            if stat.st_mtime >= cutoff:
                continue
            if entry.is_dir():
                size = sum(
                    f.stat().st_size for f in entry.rglob("*") if f.is_file()
                )
                shutil.rmtree(entry, ignore_errors=True)
            else:
                size = stat.st_size
                entry.unlink(missing_ok=True)
            deleted += 1
            freed += size
        except OSError as e:
            logger.warning(f"staging cleanup 실패 ({entry}): {e}")

    if deleted:
        logger.info(
            f"staging cleanup: {deleted}개 항목 삭제, {format_size(freed)} 회수"
        )
    return (deleted, freed)


def _get_app_config_dir() -> Path:
    """OS별 앱 설정 디렉토리 반환 (config.py 위임)"""
    from config import _get_config_dir
    return _get_config_dir()


# ═══════════════════════════════════════════════════════════════════════
# FileTransferManager — 파일 전송 핵심 로직
# ═══════════════════════════════════════════════════════════════════════

class FileTransferManager:
    """
    파일/폴더 전송 관리자

    - 단일 파일, 다중 파일, 폴더 전송 메타데이터 수집
    - 1MB 청크 분할 전송 (xxHash64 청크 해시)
    - SHA-256 전체 파일 해시 검증
    - 임시 디렉토리 기반 청크 수신 → 최종 파일 조립
    """

    def __init__(self, download_path: str, max_file_size_gb: int = 10):
        """
        Args:
            download_path: 수신 파일 저장 기본 경로
            max_file_size_gb: 최대 허용 파일 크기 (GB)
        """
        self.download_path = Path(download_path)
        self.max_file_size = max_file_size_gb * (1024 ** 3)

        # 다운로드 경로가 없으면 생성
        self.download_path.mkdir(parents=True, exist_ok=True)

        # v2.1 active transfer 상태 (단일 invariant) — Task 2 Phase A
        self._active_outgoing: Optional[ActiveOutgoing] = None
        self._outgoing_lock = threading.Lock()
        self._active_incoming: Optional[ActiveIncoming] = None
        self._incoming_lock = threading.Lock()

    # ── Active outgoing 큐 ─────────────────────────────────────────
    #
    # 호출 패턴:
    #   new_active, superseded = manager.begin_outgoing(metadata, file_paths)
    #   if superseded:
    #       # 호출자가 cancel 송출 + 기존 워커 종료 처리
    #       send_cancel(superseded.transfer_id, reason="superseded")
    #   # 워커는 new_active.stop_event 를 polling 하며 cancel 시 종료
    #   ...
    #   manager.end_outgoing(new_active.transfer_id)  # finally 블록에서

    def begin_outgoing(
        self,
        metadata: FileMetadata,
        file_paths: List[str],
    ) -> Tuple[ActiveOutgoing, Optional[ActiveOutgoing]]:
        """
        새 outgoing transfer 를 등록하고 기존 active 가 있으면 superseded 로 반환.

        반환된 superseded 는 호출자가 처리한다:
          1. 기존 워커에 stop 신호 전달 (이미 stop_event 가 set 됨)
          2. CANCEL 메시지 송출 (선택 — 수신측 정리용)

        Returns:
            (new_active, superseded_or_None)
        """
        new_active = ActiveOutgoing(
            transfer_id=metadata.transfer_id,
            metadata=metadata,
            file_paths=list(file_paths),
        )
        with self._outgoing_lock:
            superseded = self._active_outgoing
            if superseded is not None:
                superseded.stop_event.set()
                superseded.cancel_reason = "superseded"
                logger.info(
                    f"[outgoing] superseded by new transfer: "
                    f"{superseded.transfer_id} → {new_active.transfer_id}"
                )
            self._active_outgoing = new_active
        return new_active, superseded

    def end_outgoing(self, transfer_id: str) -> None:
        """Outgoing transfer 종료. 현재 active 의 transfer_id 와 일치할 때만 정리.

        다른 transfer 가 이미 active 인 경우 (superseded 후 새 transfer 가 시작된
        경우) 잘못 정리하지 않도록 transfer_id 매칭을 확인한다.
        """
        with self._outgoing_lock:
            if (self._active_outgoing is not None
                    and self._active_outgoing.transfer_id == transfer_id):
                self._active_outgoing = None

    def cancel_outgoing(
        self,
        transfer_id: str,
        reason: str = "user",
    ) -> Optional[ActiveOutgoing]:
        """현재 outgoing transfer 가 transfer_id 와 일치하면 cancel 신호 set.

        Returns:
            일치 시 ActiveOutgoing (호출자가 cleanup 에 사용), 불일치 시 None.
        """
        with self._outgoing_lock:
            active = self._active_outgoing
            if active is None or active.transfer_id != transfer_id:
                return None
            active.stop_event.set()
            active.cancel_reason = reason
            return active

    def get_active_outgoing(self) -> Optional[ActiveOutgoing]:
        with self._outgoing_lock:
            return self._active_outgoing

    # ── Active incoming 큐 (대칭 구조) ─────────────────────────────

    def begin_incoming(
        self,
        metadata: FileMetadata,
    ) -> Tuple[ActiveIncoming, Optional[ActiveIncoming]]:
        """
        새 incoming transfer 를 등록하고 기존 active 가 있으면 superseded 로 반환.

        반환된 superseded 는 호출자가 cleanup_incoming_artifacts(transfer_id)
        로 임시 디렉토리·체크포인트를 정리한다.
        """
        new_active = ActiveIncoming(
            transfer_id=metadata.transfer_id, metadata=metadata,
        )
        with self._incoming_lock:
            superseded = self._active_incoming
            if superseded is not None:
                superseded.stop_event.set()
                superseded.cancel_reason = "superseded"
                logger.info(
                    f"[incoming] superseded by new transfer: "
                    f"{superseded.transfer_id} → {new_active.transfer_id}"
                )
            self._active_incoming = new_active
        return new_active, superseded

    def end_incoming(self, transfer_id: str) -> None:
        with self._incoming_lock:
            if (self._active_incoming is not None
                    and self._active_incoming.transfer_id == transfer_id):
                self._active_incoming = None

    def cancel_incoming(
        self,
        transfer_id: str,
        reason: str = "user",
    ) -> Optional[ActiveIncoming]:
        """현재 incoming transfer 가 transfer_id 와 일치하면 cancel 신호 set."""
        with self._incoming_lock:
            active = self._active_incoming
            if active is None or active.transfer_id != transfer_id:
                return None
            active.stop_event.set()
            active.cancel_reason = reason
            return active

    def get_active_incoming(self) -> Optional[ActiveIncoming]:
        with self._incoming_lock:
            return self._active_incoming

    def cleanup_incoming_artifacts(self, transfer_id: str) -> None:
        """transfer_id 의 임시 디렉토리를 안전하게 삭제.

        Task 2.5 에서 transfer_id 형식 검증이 추가되며, get_temp_dir 가 검증
        실패 시 None 을 반환하면 cleanup 도 no-op 가 된다. 현재(Phase A)는
        get_temp_dir 가 형식과 무관하게 경로를 만들지만, 호출자는 항상
        ActiveIncoming.transfer_id 처럼 신뢰 가능한 source 만 넘겨야 한다.
        """
        try:
            temp_dir = get_temp_dir(transfer_id)
        except ValueError as e:
            logger.error(f"[incoming] cleanup 거부 (잘못된 transfer_id): {e}")
            return
        if temp_dir.exists():
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info(f"[{transfer_id}] incoming 임시 디렉토리 정리: {temp_dir}")
            except OSError as e:
                logger.error(f"[{transfer_id}] 임시 디렉토리 정리 실패: {e}")

    # ── 메타데이터 수집 ─────────────────────────────────────────────

    def collect_metadata(self, paths: List[str]) -> FileMetadata:
        """
        전송할 파일/폴더 경로 목록에서 메타데이터를 수집한다.

        - 단일 파일 → transfer_type="file"
        - 다중 파일 → transfer_type="files"
        - 단일 폴더 → transfer_type="folder"

        Args:
            paths: 전송할 파일/폴더 경로 목록

        Returns:
            FileMetadata 인스턴스

        Raises:
            FileNotFoundError: 경로가 존재하지 않을 때
            ValueError: 전체 크기가 최대 허용 크기를 초과할 때
        """
        transfer_id = str(uuid.uuid4())
        file_entries: List[dict] = []
        path_map: Dict[str, str] = {}  # rel_path → abs_path (송신 시 경로 해석용)

        # 전송 타입 감지
        if len(paths) == 1 and os.path.isdir(paths[0]):
            transfer_type = "folder"
            root_name = os.path.basename(paths[0])
            base_dir = paths[0]
            # 폴더 재귀 탐색
            for dirpath, _dirnames, filenames in os.walk(base_dir):
                for fname in filenames:
                    abs_path = os.path.join(dirpath, fname)
                    rel_path = os.path.relpath(abs_path, os.path.dirname(base_dir))
                    # 크로스 플랫폼: 경로 구분자를 항상 /로 정규화
                    rel_path = rel_path.replace("\\", "/")
                    fsize = os.path.getsize(abs_path)
                    file_entries.append({
                        "path": rel_path,
                        "size": fsize,
                        "hash": "",   # 전송 시 계산
                    })
                    path_map[rel_path] = abs_path
        elif len(paths) == 1 and os.path.isfile(paths[0]):
            transfer_type = "file"
            root_name = os.path.basename(paths[0])
            fsize = os.path.getsize(paths[0])
            file_entries.append({
                "path": root_name,
                "size": fsize,
                "hash": "",
            })
            path_map[root_name] = paths[0]
        else:
            # 다중 파일
            transfer_type = "files"
            root_name = ""
            for p in paths:
                if not os.path.exists(p):
                    raise FileNotFoundError(f"경로를 찾을 수 없음: {p}")
                if os.path.isdir(p):
                    # 다중 경로 중 폴더가 섞여 있으면 재귀 포함
                    dir_base = os.path.dirname(p)
                    for dirpath, _dirnames, filenames in os.walk(p):
                        for fname in filenames:
                            abs_path = os.path.join(dirpath, fname)
                            rel_path = os.path.relpath(abs_path, dir_base)
                            rel_path = rel_path.replace("\\", "/")
                            # C6: 서로 다른 선택 폴더가 동일한 이름/구조를 가지면
                            # rel_path 가 충돌할 수 있음 — 유일화
                            rel_path = _dedup_rel_path(path_map, rel_path, abs_path)
                            fsize = os.path.getsize(abs_path)
                            file_entries.append({
                                "path": rel_path,
                                "size": fsize,
                                "hash": "",
                            })
                            path_map[rel_path] = abs_path
                else:
                    fsize = os.path.getsize(p)
                    rel_path = os.path.basename(p)
                    # C6: 다중 파일 선택에 동일 파일명이 서로 다른 디렉토리에서 섞이면
                    # basename 만으로는 충돌 — 충돌 검사 없이 last-write-wins 였던
                    # 것이 앞 파일 데이터를 조립 단계에서 조용히 유실시켰다.
                    rel_path = _dedup_rel_path(path_map, rel_path, p)
                    file_entries.append({
                        "path": rel_path,
                        "size": fsize,
                        "hash": "",
                    })
                    path_map[rel_path] = p

        total_size = sum(e["size"] for e in file_entries)
        file_count = len(file_entries)

        # 크기 제한 검사
        if total_size > self.max_file_size:
            raise ValueError(
                f"전체 크기({format_size(total_size)})가 "
                f"최대 허용 크기({format_size(self.max_file_size)})를 초과합니다."
            )

        return FileMetadata(
            transfer_id=transfer_id,
            transfer_type=transfer_type,
            root_name=root_name,
            files=file_entries,
            total_size=total_size,
            file_count=file_count,
            path_map=path_map,
        )

    # ── 파일 송신 ───────────────────────────────────────────────────

    def send_file(
        self,
        filepath: str,
        chunk_callback: Callable[[int, bytes, str], None],
    ) -> str:
        """
        파일을 1MB 청크로 분할하여 콜백을 통해 전송한다.

        각 청크마다 xxHash64 해시를 계산하여 콜백에 전달하고,
        파일 전체에 대해서는 SHA-256 해시를 반환한다.

        Args:
            filepath: 전송할 파일 경로
            chunk_callback: chunk_callback(chunk_index, chunk_data, chunk_hash) 형태

        Returns:
            str: 파일 전체 SHA-256 해시 (16진수)
        """
        sha256 = hashlib.sha256()
        chunk_index = 0

        with open(filepath, "rb") as f:
            while True:
                chunk_data = f.read(CHUNK_SIZE)
                if not chunk_data:
                    # C5: 0바이트 파일은 여기서 chunk_index==0 인 채로 끝난다 — 청크
                    # 콜백이 한 번도 안 불리면 수신측 assembled_* 파일이 아예 안
                    # 만들어져 assemble_file 이 "조립 파일 없음"으로 실패하고, 그
                    # 실패가 전송 전체(폴더/다중 파일)를 취소시킨다(.gitkeep 등 흔한
                    # 케이스). 빈 청크를 1회 호출해 수신측 "첫 청크면 조립 파일
                    # touch" 로직을 그대로 태워 0바이트 assembled 파일을 만든다.
                    if chunk_index == 0:
                        empty_hash = xxhash.xxh64(b"").hexdigest()
                        chunk_callback(0, b"", empty_hash)
                    break

                # 청크별 xxHash64 해시
                chunk_hash = xxhash.xxh64(chunk_data).hexdigest()

                # 전체 파일 SHA-256 누적
                sha256.update(chunk_data)

                # 콜백 호출
                chunk_callback(chunk_index, chunk_data, chunk_hash)
                chunk_index += 1

        return sha256.hexdigest()

    # ── 청크 수신 ───────────────────────────────────────────────────

    def receive_chunk(
        self,
        transfer_id: str,
        file_path: str,
        chunk_index: int,
        chunk_data: bytes,
        expected_hash: str,
    ) -> bool:
        """
        수신된 청크를 최종 조립 파일의 올바른 offset에 직접 쓴다.

        기존 구현은 청크를 개별 파일로 저장한 뒤 assemble_file에서 다시
        읽어 하나로 합쳤지만, 이는 디스크에 전체 바이트를 두 번 쓰는 낭비다.
        새 구현은 `assembled_{safe_name}` 파일에 `chunk_index * CHUNK_SIZE`
        위치로 seek하여 즉시 write한다. SHA-256 검증은 assemble_file에서 수행.

        xxHash64 해시를 검증하고, 불일치 시 False를 반환한다.

        Args:
            transfer_id: 전송 ID
            file_path: 파일 상대 경로 (메타데이터의 path 필드)
            chunk_index: 청크 번호
            chunk_data: 청크 바이너리 데이터
            expected_hash: 송신측이 보낸 xxHash64 해시

        Returns:
            bool: 해시 검증 성공 시 True
        """
        # Task 2.5: transfer_id 형식 검증 (path 컴포넌트로 사용되므로)
        if not _is_valid_transfer_id(transfer_id):
            logger.error(
                f"[?] receive_chunk 거부 (invalid transfer_id): {transfer_id!r}"
            )
            return False

        # 경로 이탈 방어 — 원격 peer가 제공한 rel_path 검증
        if not _is_safe_rel_path(file_path):
            logger.error(f"[{transfer_id}] 경로 이탈 차단 (chunk): {file_path!r}")
            return False

        # Task 2.5: chunk_index 경계 검증
        if not isinstance(chunk_index, int) or chunk_index < 0:
            logger.error(
                f"[{transfer_id}] receive_chunk 거부 (invalid chunk_index): "
                f"{chunk_index!r}"
            )
            return False
        # 단일 청크 offset 이 max_file_size 를 넘지 않도록 — sparse file abuse 방어
        if chunk_index * CHUNK_SIZE >= self.max_file_size:
            logger.error(
                f"[{transfer_id}] receive_chunk 거부 (chunk_index 범위 초과): "
                f"index={chunk_index}, offset={chunk_index * CHUNK_SIZE}, "
                f"max={self.max_file_size}"
            )
            return False

        # xxHash64 검증
        actual_hash = xxhash.xxh64(chunk_data).hexdigest()
        if actual_hash != expected_hash:
            logger.error(
                f"[{transfer_id}] 청크 해시 불일치: "
                f"파일={file_path}, 청크={chunk_index}, "
                f"예상={expected_hash}, 실제={actual_hash}"
            )
            return False

        # 임시 디렉토리(0o700) 준비
        temp_dir = get_temp_dir(transfer_id)
        temp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        # 최종 조립 파일 경로
        safe_name = file_path.replace("\\", "__").replace("/", "__")
        assembled_path = temp_dir / f"assembled_{safe_name}"

        # 첫 청크이거나 파일이 없으면 생성
        if not assembled_path.exists():
            assembled_path.touch()
            if os.name == "posix":
                try:
                    os.chmod(assembled_path, 0o600)
                except OSError:
                    pass

        # chunk_index * CHUNK_SIZE 오프셋에 직접 쓰기
        offset = chunk_index * CHUNK_SIZE
        try:
            with open(assembled_path, "r+b") as f:
                f.seek(offset)
                f.write(chunk_data)
        except IOError as e:
            logger.error(
                f"[{transfer_id}] 청크 쓰기 실패: {file_path}#{chunk_index}: {e}"
            )
            return False

        return True

    # ── 파일 조립 ───────────────────────────────────────────────────

    def assemble_file(
        self,
        transfer_id: str,
        file_path: str,
        expected_sha256: str,
    ) -> bool:
        """
        조립된 파일의 SHA-256 해시를 검증한다.

        receive_chunk가 이미 offset 기반으로 최종 파일을 기록했으므로,
        여기서는 해시만 재계산하여 무결성을 확인한다.

        Args:
            transfer_id: 전송 ID
            file_path: 파일 상대 경로
            expected_sha256: 기대하는 SHA-256 해시

        Returns:
            bool: 해시 검증 성공 시 True
        """
        # Task 2.5: transfer_id 형식 검증
        if not _is_valid_transfer_id(transfer_id):
            logger.error(f"[?] assemble_file 거부 (invalid transfer_id): {transfer_id!r}")
            return False

        # 경로 이탈 방어 — receive_chunk와 동일 조건으로 재검증
        if not _is_safe_rel_path(file_path):
            logger.error(f"[{transfer_id}] 경로 이탈 차단 (assemble): {file_path!r}")
            return False

        temp_dir = get_temp_dir(transfer_id)
        safe_name = file_path.replace("\\", "__").replace("/", "__")
        assembled_path = temp_dir / f"assembled_{safe_name}"

        if not assembled_path.exists():
            logger.error(f"[{transfer_id}] 조립 파일 없음: {assembled_path}")
            return False

        # SHA-256 재계산 (스트리밍, 1MB 단위)
        sha256 = hashlib.sha256()
        try:
            with open(assembled_path, "rb") as f:
                while True:
                    block = f.read(CHUNK_SIZE)
                    if not block:
                        break
                    sha256.update(block)
        except IOError as e:
            logger.error(f"[{transfer_id}] 조립 파일 읽기 실패: {e}")
            return False

        actual_hash = sha256.hexdigest()
        if actual_hash != expected_sha256:
            logger.error(
                f"[{transfer_id}] 파일 해시 불일치: "
                f"파일={file_path}, "
                f"예상={expected_sha256}, 실제={actual_hash}"
            )
            # 검증 실패 시 조립 파일 삭제
            assembled_path.unlink(missing_ok=True)
            return False

        logger.info(f"[{transfer_id}] 파일 조립 완료: {file_path}")
        return True

    # ── 폴더 구조 복원 ──────────────────────────────────────────────

    def restore_folder(
        self, transfer_id: str, metadata: FileMetadata,
        dest_dir: Optional[Path] = None,
        conflict_policy: str = "rename_with_counter",
    ) -> Tuple[list[str], list[str]]:
        """
        모든 파일 수신/조립 완료 후 목적지에 폴더 구조를 복원한다.

        Args:
            transfer_id: 전송 ID
            metadata: 전송 메타데이터
            dest_dir: 저장 경로 (None이면 self.download_path 사용)
            conflict_policy: 동일 파일명 충돌 시 동작 (audit #10).
                "rename_with_counter" (default, v2.2 호환), "overwrite",
                "skip", "rename_with_timestamp". SHA-256 dedup short-circuit 은
                정책 무관 자동 skip 으로 우선 적용.

        Returns:
            Tuple[list[str], list[str]]:
                - 복원된 파일 절대경로 목록. dedup/skip 으로 새로 쓰지 않은 경우에도
                  기존 final_path 가 그대로 들어감 (사용자가 paste 가능하도록).
                - H8: policy=="overwrite" 인데 기존 파일 unlink 가 실패해 skip 으로
                  강등된 파일명 목록 (호출자가 사용자에게 알리는 용도). 정상 경로면
                  항상 빈 리스트.
        """
        # Task 2.5: transfer_id 형식 검증
        if not _is_valid_transfer_id(transfer_id):
            logger.error(f"[?] restore_folder 거부 (invalid transfer_id): {transfer_id!r}")
            return [], []

        target = dest_dir or self.download_path
        temp_dir = get_temp_dir(transfer_id)

        restored_paths: list[str] = []
        failed_overwrites: list[str] = []

        for file_entry in metadata.files:
            rel_path = file_entry["path"]
            expected_size = file_entry.get("size")
            expected_hash = file_entry.get("hash")

            # 경로 이탈 방어 — target 바깥으로 나가면 스킵
            final_path = _resolve_within(target, rel_path)
            if final_path is None:
                logger.error(
                    f"[{transfer_id}] 경로 이탈 차단 (restore): {rel_path!r}"
                )
                continue

            safe_name = rel_path.replace("\\", "__").replace("/", "__")
            assembled_path = temp_dir / f"assembled_{safe_name}"

            # 상위 디렉토리 생성
            final_path.parent.mkdir(parents=True, exist_ok=True)

            # v2.3 audit #10: dedup short-circuit + 4 정책 분기.
            # None 반환은 skip (dedup 일치 또는 skip policy) — 기존 final_path 를 그대로 사용.
            target_path = self._resolve_conflict(
                final_path, expected_size, expected_hash, conflict_policy, transfer_id,
                failed_overwrites=failed_overwrites,
            )

            if target_path is None:
                # 쓰기 안 함 — 기존 파일이 있으면 클립보드 등록용으로 path 만 추가.
                if final_path.exists():
                    restored_paths.append(str(final_path))
                continue

            # 조립된 파일 이동
            if assembled_path.exists():
                shutil.move(str(assembled_path), str(target_path))
                logger.info(f"[{transfer_id}] 파일 복원: {target_path}")
                restored_paths.append(str(target_path))
            else:
                logger.warning(
                    f"[{transfer_id}] 조립 파일 없음 (건너뜀): {assembled_path}"
                )

        # 청크 임시 디렉토리 정리 (조립 파일은 이미 이동됨)
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"[{transfer_id}] 임시 디렉토리 삭제: {temp_dir}")

        return restored_paths, failed_overwrites

    # ── 디스크 공간 확인 ────────────────────────────────────────────

    def check_disk_space(self, required_bytes: int) -> bool:
        """
        다운로드 경로에 충분한 디스크 공간이 있는지 확인한다.

        Args:
            required_bytes: 필요한 바이트 수

        Returns:
            bool: 가용 공간이 충분하면 True
        """
        try:
            usage = shutil.disk_usage(str(self.download_path))
            available = usage.free
            if available < required_bytes:
                logger.warning(
                    f"디스크 공간 부족: 필요={format_size(required_bytes)}, "
                    f"가용={format_size(available)}"
                )
                return False
            return True
        except OSError as e:
            logger.error(f"디스크 공간 확인 실패: {e}")
            return False

    # ── 내부 유틸 ───────────────────────────────────────────────────

    @staticmethod
    def _unique_path(path: Path) -> Path:
        """중복되지 않는 파일 경로를 반환한다."""
        if not path.exists():
            return path

        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 1
        while True:
            new_path = parent / f"{stem} ({counter}){suffix}"
            if not new_path.exists():
                return new_path
            counter += 1

    @staticmethod
    def _compute_file_hash(path: Path, chunk_size: int = 65536) -> str:
        """파일의 SHA-256 hex digest 를 streaming 으로 계산.

        dedup short-circuit 용. 큰 파일도 메모리 폭발 없이 계산.
        OSError 는 호출자가 처리 (빈 문자열 fallback 등).
        """
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(chunk_size), b""):
                h.update(block)
        return h.hexdigest()

    def _resolve_conflict(
        self,
        final_path: Path,
        expected_size: Optional[int],
        expected_hash: Optional[str],
        policy: str,
        transfer_id: str,
        failed_overwrites: Optional[list] = None,
    ) -> Optional[Path]:
        """동일 파일명 충돌을 dedup short-circuit + 4 정책으로 해결.

        Returns:
            Path: assemble 결과를 이동할 대상 경로 (final_path 또는 변형 경로).
                  policy=="overwrite" 인 경우 기존 파일을 미리 unlink 후 final_path 반환.
            None: 쓰기를 건너뛰어야 함 (dedup 일치 또는 policy=="skip").
                  호출자는 기존 final_path 를 클립보드 등록 등에 사용.

        dedup 우선순위: 정책 분기 전에 size+SHA-256 일치 확인 → 같은 콘텐츠면
        정책 무관 자동 skip. audit #10 의 사용자 사고 (numbering 누적) 차단.
        """
        try:
            path_exists = final_path.exists()
        except OSError as e:
            # exists() 도 내부적으로 stat() 을 호출하므로 동일하게 실패할 수 있다.
            # M14 와 같은 이유로 조용히 삼키지 않고 warning 후, 충돌 있다고 가정하고
            # 아래 dedup/정책 분기로 진행 (판단 불가를 "파일 없음"으로 오판하지 않는다).
            logger.warning(
                f"[{transfer_id}] dedup exists 확인 실패 ({final_path.name}): {e}"
            )
            path_exists = True
        if not path_exists:
            return final_path

        # 1) dedup short-circuit — size 먼저 (큰 파일 hash 비용 회피).
        if expected_hash and expected_size is not None:
            try:
                actual_size = final_path.stat().st_size
            except OSError as e:
                # M14: 바로 아래 hash 계산 실패는 warning 로그를 남기는데
                # stat() 실패만 조용히 무시돼 비일관이었다 — 같은 레벨로 통일.
                logger.warning(
                    f"[{transfer_id}] dedup stat 실패 ({final_path.name}): {e}"
                )
                actual_size = -1
            if actual_size == expected_size:
                try:
                    actual_hash = self._compute_file_hash(final_path)
                except OSError as e:
                    logger.warning(
                        f"[{transfer_id}] dedup hash 계산 실패 ({final_path.name}): {e}"
                    )
                    actual_hash = ""
                if actual_hash == expected_hash:
                    logger.info(
                        f"[{transfer_id}] dedup skip (동일 콘텐츠): {final_path.name}"
                    )
                    return None

        # 2) 4 정책 분기.
        if policy == "overwrite":
            try:
                final_path.unlink()
                logger.info(f"[{transfer_id}] overwrite: {final_path.name}")
                return final_path
            except OSError as e:
                logger.warning(
                    f"[{transfer_id}] overwrite 실패 → skip ({final_path.name}): {e}"
                )
                # H8: 사용자가 명시적으로 "overwrite" 를 선택했으므로, unlink 실패로
                # 조용히 skip 강등되면 기존 파일이 남는 게 사용자 의도와 다르다.
                # 호출자(restore_folder)가 사용자에게 알릴 수 있도록 기록한다.
                if failed_overwrites is not None:
                    failed_overwrites.append(final_path.name)
                return None

        if policy == "skip":
            logger.info(f"[{transfer_id}] skip (policy): {final_path.name}")
            return None

        if policy == "rename_with_timestamp":
            ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
            renamed = final_path.parent / f"{final_path.stem}.{ts}{final_path.suffix}"
            # 1초 내 중복 시 counter 폴백 (timestamp 충돌 방지).
            if renamed.exists():
                renamed = self._unique_path(renamed)
            return renamed

        # default — rename_with_counter (v2.2 호환).
        return self._unique_path(final_path)


# ═══════════════════════════════════════════════════════════════════════
# CheckpointManager — 이어받기 체크포인트 관리
# ═══════════════════════════════════════════════════════════════════════

class CheckpointManager:
    """
    전송 체크포인트를 JSON 파일로 관리하여 이어받기를 지원한다.

    체크포인트 디렉토리: OS별 앱 설정 폴더 / checkpoints /
    """

    def __init__(self):
        self.checkpoint_dir = _get_app_config_dir() / "checkpoints"
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, transfer_id: str) -> Path:
        """체크포인트 파일 경로.

        Task 2.5: transfer_id 가 path 컴포넌트로 사용되므로 형식 검증.
        """
        if not _is_valid_transfer_id(transfer_id):
            raise ValueError(f"Invalid transfer_id (path-unsafe): {transfer_id!r}")
        return self.checkpoint_dir / f"{transfer_id}.json"

    def save(self, checkpoint: Checkpoint) -> None:
        """체크포인트를 JSON 파일로 저장"""
        data = asdict(checkpoint)
        try:
            path = self._path_for(checkpoint.transfer_id)
        except ValueError as e:
            logger.error(f"체크포인트 저장 거부 (transfer_id 검증 실패): {e}")
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # v2.2 R1: POSIX 0o600 권한 (체크포인트에 transfer_id, file paths 등 포함)
            if os.name == "posix":
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            logger.debug(f"체크포인트 저장: {checkpoint.transfer_id}")
        except IOError as e:
            logger.error(f"체크포인트 저장 실패: {e}")

    def load(self, transfer_id: str) -> Optional[Checkpoint]:
        """체크포인트를 로드한다. 없으면 None 반환."""
        try:
            path = self._path_for(transfer_id)
        except ValueError as e:
            logger.error(f"체크포인트 로드 거부 (transfer_id 검증 실패): {e}")
            return None
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Checkpoint(
                transfer_id=data["transfer_id"],
                completed_files=data.get("completed_files", []),
                current_file=data.get("current_file", ""),
                last_chunk_index=data.get("last_chunk_index", -1),
                completed_hashes=data.get("completed_hashes", {}),
            )
        except (json.JSONDecodeError, KeyError, IOError) as e:
            logger.error(f"체크포인트 로드 실패 ({transfer_id}): {e}")
            return None

    def delete(self, transfer_id: str) -> None:
        """완료된 전송의 체크포인트를 삭제한다."""
        try:
            path = self._path_for(transfer_id)
        except ValueError as e:
            logger.error(f"체크포인트 삭제 거부 (transfer_id 검증 실패): {e}")
            return
        try:
            if path.exists():
                path.unlink()
                logger.debug(f"체크포인트 삭제: {transfer_id}")
        except IOError as e:
            logger.error(f"체크포인트 삭제 실패 ({transfer_id}): {e}")

    def list_pending(self) -> List[str]:
        """미완료 전송의 transfer_id 목록을 반환한다."""
        pending: List[str] = []
        try:
            for f in self.checkpoint_dir.glob("*.json"):
                pending.append(f.stem)
        except IOError as e:
            logger.error(f"체크포인트 목록 조회 실패: {e}")
        return pending
