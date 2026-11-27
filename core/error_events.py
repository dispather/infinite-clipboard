"""오류 사건 수집 (d1) — 각 PC 의 WARNING 이상 로그·crash.log 를 «오류 사건»으로 묶어
서버 모드 PC 한 곳에 모은다. 옵트인(config.collect_error_logs, 기본 꺼짐).

이 모듈은 UI 무관(core 규칙 5)이고, 모든 경로를 인자로 받는다(테스트 주입).

데이터 흐름
-----------
  logging 레코드(WARNING+) → ErrorEventCollector.emit (메모리에서 서명별 합산만, I/O 없음)
  → 타이머 스레드(main.py) 가 flush() → outbox 파일(<설정 폴더>/error-events-outbox.jsonl)
  → take_batch(256KB 상한) → MSG_ERROR_EVENTS 로 서버 전송 → 성공 시 commit / 실패 시 restore
  → [서버] validate_events → CollectedLogStore.append → collected-logs/<기기>/events.jsonl

  서버 PC 자신의 사건은 네트워크를 거치지 않고 같은 store 에 바로 쓴다.
  crash.log 는 scan_crash_log() 가 마지막으로 읽은 위치(state 파일) 뒤의 새 내용만 사건 1개로.

events.jsonl 스키마 (schema=1) — log-triage-loop 의 입력. 바꾸면 schema 를 올린다.
------------------------------------------------------------------------------
한 줄 = JSON 객체 1개. 필드:
  schema      int   1
  sig         str   16자 hex. sha1(level|logger|head) 앞 16자 — 같은 결함 = 같은 sig
  level       str   "WARNING" | "ERROR" | "CRITICAL" | "CRASH"(faulthandler 덤프)
  logger      str   로거 이름 (crash 는 "faulthandler")
  head        str   메시지 머리(≤160자). 숫자·hex id·경로·IP·URL·따옴표 안 문자열을 지운 형태
  count       int   «이 줄이 덮는 구간»의 발생 횟수(누적 아님). 같은 sig 가 여러 줄에
                    나올 수 있다 — 전송 회차마다 1줄. 총 횟수는 sig 별 count 합.
  first_ts    str   그 구간 첫 발생 (UTC ISO8601, 초 단위, 끝에 Z)
  last_ts     str   그 구간 마지막 발생
  version     str   보낸 PC 의 앱 버전
  os          str   platform.system() — "Linux" | "Darwin" | "Windows"
  os_release  str   platform.release()
  traceback   str|null  첫 발생의 트레이스백(≤8KB). 홈 경로는 "~", auth_key 는 "<redacted>"
  -- 서버가 저장 시 덧붙임 --
  device      str   핸드셰이크로 확인된 기기 이름(보낸 쪽 자기신고 아님)
  peer        str   그 기기 peer_id 앞 8자
  received_at str   서버 저장 시각 (UTC ISO8601)

비밀·본문 정책: auth_key 는 문자열 그대로 치환, 클립보드 본문은 로그에 애초에 쓰지 않는다
(log-crash-capture-levels (e)) + head 정규화가 따옴표 안 문자열을 지운다. 사건은 사용자
자기 기기 네트워크(앱 연결) 밖으로 나가지 않는다.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import logging
import os
import platform
import re
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

SCHEMA_VERSION = 1

# 수집기 자신의 로그는 사건이 되지 않는다(전송 실패 로그 → 사건 → 전송 … 되먹임 차단)
SELF_LOGGER = "infinite-clipboard.error-events"
logger = logging.getLogger(SELF_LOGGER)

HEAD_MAX = 160
TRACEBACK_MAX = 8 * 1024
BATCH_MAX_BYTES = 256 * 1024          # 회당 전송 상한
OUTBOX_MAX_EVENTS = 500               # outbox 에 쌓아 둘 sig 수 상한 (오래된 것부터 버림)
PENDING_MAX_EVENTS = 1000             # 메모리 합산 dict 상한 (flush 전 폭주 방지)
INCOMING_MAX_EVENTS = 1000            # 서버가 한 메시지에서 받아들이는 사건 수 상한
DEVICE_FILE_MAX_BYTES = 2 * 1024 * 1024   # 기기별 events.jsonl 회전 문턱 (백업 1개)
CRASH_CHUNK_MAX = 16 * 1024           # crash.log 새 내용 중 사건에 담을 꼬리

# 전체 로그 요청
LOG_BUNDLE_MAX_RAW = 4 * 1024 * 1024      # 보내는 쪽: 로그 꼬리 합계 상한(압축 전)
LOG_BUNDLE_MAX_B64 = 8 * 1024 * 1024      # 받는 쪽: base64 문자열 상한
LOG_BUNDLE_MAX_UNZIPPED = 8 * 1024 * 1024  # 받는 쪽: gzip 해제 상한(압축 폭탄 방지)

VALID_LEVELS = frozenset({"WARNING", "ERROR", "CRITICAL", "CRASH"})


def _utc_iso(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


def _chmod600(path: Path) -> None:
    if os.name == "posix":
        try:
            os.chmod(str(path), 0o600)
        except OSError:
            pass


# ── 정규화·가리기 ───────────────────────────────────────────────────────

_RE_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"|«[^»]*»|`[^`]*`")
_RE_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+")
_RE_WINPATH = re.compile(r"\b[A-Za-z]:\\[^\s:,;)]*")
_RE_POSIXPATH = re.compile(r"(?<![\w.])(?:~|\.{1,2})?/[^\s:,;)]+")
_RE_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
_RE_HEXID = re.compile(r"\b[0-9a-fA-F]{6,}\b|\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b")
_RE_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_RE_SPACE = re.compile(r"\s+")


def normalize_head(message: str) -> str:
    """서명용 메시지 머리 — 사건마다 달라지는 값(숫자·id·경로·IP·따옴표 문자열)을 지운다."""
    s = (message or "").split("\n", 1)[0]
    s = _RE_QUOTED.sub("<s>", s)
    s = _RE_URL.sub("<url>", s)
    s = _RE_WINPATH.sub("<path>", s)
    s = _RE_POSIXPATH.sub("<path>", s)
    s = _RE_IPV4.sub("<ip>", s)
    s = _RE_HEXID.sub("<id>", s)
    s = _RE_NUM.sub("<n>", s)
    s = _RE_SPACE.sub(" ", s).strip()
    return s[:HEAD_MAX]


def make_signature(level: str, logger_name: str, head: str) -> str:
    return hashlib.sha1(f"{level}|{logger_name}|{head}".encode("utf-8")).hexdigest()[:16]


class Redactor:
    """비밀·개인 경로 가리기. auth_key 는 hex 가 아니라(URL-safe) 패턴이 아닌 «값» 치환."""

    def __init__(self, secrets: Iterable[str] = (), home: Optional[str] = None):
        self._secrets = [s for s in secrets if s and len(s) >= 4]
        home = home if home is not None else os.path.expanduser("~")
        self._homes = []
        if home and home not in ("/", "~"):
            self._homes.append(home)
            alt = home.replace("\\", "/")
            if alt != home:
                self._homes.append(alt)

    def __call__(self, text: Optional[str]) -> Optional[str]:
        if not text:
            return text
        for s in self._secrets:
            text = text.replace(s, "<redacted>")
        for h in self._homes:
            text = text.replace(h, "~")
        return text


# ── 수집기 (logging.Handler) ────────────────────────────────────────────


class ErrorEventCollector(logging.Handler):
    """WARNING 이상 레코드를 서명별로 메모리에서 합산한다. emit 에서 디스크·네트워크 I/O 없음.

    🔴 메인 서비스 프로세스에만 붙인다 — `--window` UI 서브프로세스까지 붙으면 프로세스마다
    같은 outbox 를 쓰다 경합한다.
    """

    def __init__(self, outbox_path: Path, version: str, redactor: Optional[Redactor] = None,
                 level: int = logging.WARNING):
        super().__init__(level=level)
        self.outbox_path = Path(outbox_path)
        self.version = version
        self.redact = redactor or Redactor()
        self._pending: Dict[str, dict] = {}
        self._dropped = 0
        self._plock = threading.Lock()
        self._olock = threading.Lock()   # outbox 파일 read-modify-write
        self._inflight: Dict[str, dict] = {}

    # logging.Handler ---------------------------------------------------
    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name == SELF_LOGGER or record.name.startswith(SELF_LOGGER + "."):
                return
            try:
                msg = record.getMessage()
            except Exception:
                msg = str(record.msg)
            level = record.levelname if record.levelname in VALID_LEVELS else "WARNING"
            head = normalize_head(self.redact(msg) or "")
            sig = make_signature(level, record.name, head)
            now = record.created
            with self._plock:
                ev = self._pending.get(sig)
                if ev is not None:
                    ev["count"] += 1
                    ev["last_ts"] = _utc_iso(now)
                    return
                if len(self._pending) >= PENDING_MAX_EVENTS:
                    self._dropped += 1
                    return
                tb = None
                if record.exc_info:
                    try:
                        tb = logging.Formatter().formatException(record.exc_info)
                    except Exception:
                        tb = None
                elif record.exc_text:
                    tb = record.exc_text
                self._pending[sig] = self._new_event(sig, level, record.name, head, now, tb)
        except Exception:
            # 로깅 핸들러는 절대 예외를 내보내지 않는다(handleError 도 stderr 라 창 모드엔 무의미)
            pass

    def _new_event(self, sig, level, logger_name, head, ts, tb) -> dict:
        if tb:
            tb = self.redact(tb) or ""
            if len(tb) > TRACEBACK_MAX:
                tb = tb[:TRACEBACK_MAX // 2] + "\n…(생략)…\n" + tb[-TRACEBACK_MAX // 2:]
        return {
            "schema": SCHEMA_VERSION, "sig": sig, "level": level, "logger": logger_name,
            "head": head, "count": 1, "first_ts": _utc_iso(ts), "last_ts": _utc_iso(ts),
            "version": self.version, "os": platform.system(),
            "os_release": platform.release(), "traceback": tb or None,
        }

    # crash.log ---------------------------------------------------------
    def scan_crash_log(self, crash_path: Path, state_path: Path) -> bool:
        """crash.log 에서 지난번 이후 새로 붙은 내용이 있으면 CRASH 사건 1개로 추가.

        state 파일은 settings.json 과 분리한다(settings.json 의 mtime 변화 = 앱 재시작).
        파일이 줄었으면(지워짐·교체) 0 부터 다시 읽는다. Returns: 사건을 추가했는지.
        """
        crash_path, state_path = Path(crash_path), Path(state_path)
        try:
            size = crash_path.stat().st_size
        except OSError:
            return False
        offset = 0
        try:
            offset = int(json.loads(state_path.read_text(encoding="utf-8")).get("crash_offset", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            offset = 0
        if offset > size:
            offset = 0
        if size == offset:
            return False
        try:
            with open(crash_path, "rb") as f:
                f.seek(max(offset, size - CRASH_CHUNK_MAX))
                chunk = f.read().decode("utf-8", errors="replace")
        except OSError:
            return False
        try:
            state_path.write_text(json.dumps({"crash_offset": size}), encoding="utf-8")
            _chmod600(state_path)
        except OSError:
            pass
        text = chunk.strip()
        if not text:
            return False
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        head = normalize_head(self.redact(first) or "")
        sig = make_signature("CRASH", "faulthandler", head)
        with self._plock:
            ev = self._pending.get(sig)
            if ev is not None:
                ev["count"] += 1
                ev["last_ts"] = _utc_iso()
            else:
                ev = self._new_event(sig, "CRASH", "faulthandler", head, time.time(), text)
                self._pending[sig] = ev
        return True

    # outbox ------------------------------------------------------------
    def _read_outbox(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        try:
            with open(self.outbox_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(ev, dict) and isinstance(ev.get("sig"), str):
                        out[ev["sig"]] = ev
        except OSError:
            pass
        return out

    def _write_outbox(self, events: Dict[str, dict]) -> None:
        if len(events) > OUTBOX_MAX_EVENTS:
            keep = sorted(events.values(), key=lambda e: e.get("last_ts", ""))[-OUTBOX_MAX_EVENTS:]
            events = {e["sig"]: e for e in keep}
        tmp = self.outbox_path.with_suffix(".tmp")
        try:
            self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                for ev in events.values():
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            _chmod600(tmp)
            os.replace(tmp, self.outbox_path)
        except OSError as e:
            logger.debug(f"outbox 쓰기 실패: {e}")

    @staticmethod
    def _merge(dst: Dict[str, dict], ev: dict) -> None:
        cur = dst.get(ev["sig"])
        if cur is None:
            dst[ev["sig"]] = dict(ev)
            return
        cur["count"] = int(cur.get("count", 0)) + int(ev.get("count", 0))
        cur["first_ts"] = min(cur.get("first_ts", ev["first_ts"]), ev["first_ts"])
        cur["last_ts"] = max(cur.get("last_ts", ev["last_ts"]), ev["last_ts"])
        if not cur.get("traceback") and ev.get("traceback"):
            cur["traceback"] = ev["traceback"]

    def flush_pending(self) -> int:
        """메모리 합산분을 outbox 파일로 옮긴다(오프라인 적재). Returns: 옮긴 sig 수."""
        with self._plock:
            pending, self._pending = self._pending, {}
        if not pending:
            return 0
        with self._olock:
            box = self._read_outbox()
            for ev in pending.values():
                self._merge(box, ev)
            self._write_outbox(box)
        return len(pending)

    def take_batch(self, max_bytes: int = BATCH_MAX_BYTES) -> List[dict]:
        """outbox 에서 상한 안에 드는 사건을 꺼낸다(inflight 로 이동). 전송 뒤 commit/restore 필수."""
        self.flush_pending()
        with self._olock:
            if self._inflight:
                return []   # 직전 회차가 아직 안 끝났다
            box = self._read_outbox()
            batch: List[dict] = []
            size = 2
            for ev in sorted(box.values(), key=lambda e: e.get("first_ts", "")):
                n = len(json.dumps(ev, ensure_ascii=False).encode("utf-8")) + 1
                if size + n > max_bytes:
                    if not batch and n > max_bytes:
                        # 한 건이 상한보다 크면 트레이스백을 빼고서라도 보낸다
                        ev = dict(ev, traceback=None)
                        n = len(json.dumps(ev, ensure_ascii=False).encode("utf-8")) + 1
                        if n > max_bytes:
                            box.pop(str(ev.get("sig", "")), None)
                            continue
                    else:
                        break
                batch.append(ev)
                size += n
            for ev in batch:
                box.pop(ev["sig"], None)
                self._inflight[ev["sig"]] = ev
            if batch:
                self._write_outbox(box)
            return batch

    def commit(self) -> None:
        with self._olock:
            self._inflight = {}

    def restore(self) -> None:
        """전송 실패 — inflight 를 outbox 로 되돌린다(재접속 때 재전송)."""
        with self._olock:
            if not self._inflight:
                return
            box = self._read_outbox()
            for ev in self._inflight.values():
                self._merge(box, ev)
            self._inflight = {}
            self._write_outbox(box)

    @property
    def dropped(self) -> int:
        return self._dropped


# ── 서버: 받은 사건 검증·저장 ───────────────────────────────────────────

_FIELD_LIMITS = {"sig": 16, "level": 16, "logger": 200, "head": HEAD_MAX, "first_ts": 32,
                 "last_ts": 32, "version": 32, "os": 32, "os_release": 128}


def validate_events(data: object) -> List[dict]:
    """피어가 보낸 MSG_ERROR_EVENTS 페이로드를 검증한다 — 형식이 어긋난 사건은 버린다.

    허용 필드만 복사(모르는 키는 저장 안 함), 문자열 길이·건수 상한.
    """
    if not isinstance(data, dict):
        return []
    events = data.get("events")
    if not isinstance(events, list):
        return []
    out: List[dict] = []
    for ev in events[:INCOMING_MAX_EVENTS]:
        if not isinstance(ev, dict):
            continue
        if ev.get("schema") != SCHEMA_VERSION:
            continue
        clean: dict = {"schema": SCHEMA_VERSION}
        ok = True
        for k, lim in _FIELD_LIMITS.items():
            v = ev.get(k)
            if not isinstance(v, str):
                ok = False
                break
            clean[k] = v[:lim]
        if not ok or clean["level"] not in VALID_LEVELS or not re.fullmatch(r"[0-9a-f]{16}", clean["sig"]):
            continue
        cnt = ev.get("count")
        if not isinstance(cnt, int) or isinstance(cnt, bool) or cnt < 1:
            continue
        clean["count"] = min(cnt, 10 ** 9)
        tb = ev.get("traceback")
        clean["traceback"] = tb[:TRACEBACK_MAX * 2] if isinstance(tb, str) else None
        out.append(clean)
    return out


_RE_UNSAFE_NAME = re.compile(r"[^0-9A-Za-z가-힣._-]+")


def device_dir_name(device_name: str, peer_id: str) -> str:
    """기기 폴더 이름 — 핸드셰이크로 확인된 이름+peer_id 앞 8자. 경로 문자·제어문자 제거."""
    base = _RE_UNSAFE_NAME.sub("_", device_name or "").strip("._")[:40] or "device"
    pid = re.sub(r"[^0-9a-f]", "", (peer_id or "").lower())[:8] or "unknown"
    return f"{base}-{pid}"


class CollectedLogStore:
    """서버 PC: <root>/<기기>/events.jsonl (+ crash 는 사건 안 traceback) · 전체 로그 묶음."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()

    def _device_dir(self, device_name: str, peer_id: str) -> Path:
        d = self.root / device_dir_name(device_name, peer_id)
        d.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            for p in (self.root, d):
                try:
                    os.chmod(str(p), 0o700)
                except OSError:
                    pass
        return d

    def append(self, device_name: str, peer_id: str, events: List[dict]) -> int:
        if not events:
            return 0
        with self._lock:
            d = self._device_dir(device_name, peer_id)
            path = d / "events.jsonl"
            try:
                if path.exists() and path.stat().st_size > DEVICE_FILE_MAX_BYTES:
                    os.replace(path, d / "events.1.jsonl")
            except OSError:
                pass
            now = _utc_iso()
            with open(path, "a", encoding="utf-8") as f:
                for ev in events:
                    row = dict(ev, device=device_name[:80], peer=(peer_id or "")[:8],
                               received_at=now)
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            _chmod600(path)
        return len(events)

    def save_log_bundle(self, device_name: str, peer_id: str, data: dict) -> Optional[Path]:
        """MSG_LOG_BUNDLE 저장 — 파일명은 payload 값이 아닌 고정 이름. 실패·거절이면 None."""
        if not isinstance(data, dict) or data.get("declined"):
            return None
        b64 = data.get("gz_b64")
        if not isinstance(b64, str) or len(b64) > LOG_BUNDLE_MAX_B64:
            return None
        try:
            raw = base64.b64decode(b64, validate=True)
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                body = gz.read(LOG_BUNDLE_MAX_UNZIPPED + 1)
        except (ValueError, OSError, EOFError):
            return None
        if len(body) > LOG_BUNDLE_MAX_UNZIPPED:
            return None
        with self._lock:
            d = self._device_dir(device_name, peer_id)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = d / f"full-log-{stamp}.txt"
            path.write_bytes(body)
            _chmod600(path)
            # 기기별 최근 3개만 유지
            olds = sorted(d.glob("full-log-*.txt"))[:-3]
            for p in olds:
                try:
                    p.unlink()
                except OSError:
                    pass
        return path


# ── 로그 파일: 꼬리 묶기·진단 묶음 ──────────────────────────────────────


def log_file_set(log_file: Path) -> List[Path]:
    """현재 로그 + 회전 백업(.1~.3) 중 존재하는 것, 오래된 것 먼저."""
    log_file = Path(log_file)
    files = [Path(f"{log_file}.{i}") for i in (3, 2, 1)] + [log_file]
    return [p for p in files if p.exists()]


# ── 옛 버전(≤ v3.0.19)이 남긴 클립보드 앞부분 줄 지우기 ────────────────
#
# v3.0.19 이하는 `<asctime> - core.clipboard_manager - DEBUG -   preview: <본문 앞 50자>` 를
# 남겼다(237f310 에서 제거). 본문에 줄바꿈이 있으면 시각 없는 연속 줄로 이어진다.
# 시작 때 1회 자기 로그에서 지우고(purge_legacy_preview_logs), 진단 묶음·전체 로그 응답도
# 같은 판정으로 거른다(표식 전 상태·검산 불성립으로 남은 파일 방어).

_RE_LOG_RECORD_START = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_RE_LEGACY_PREVIEW = re.compile(
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - \S+ - DEBUG -   preview: ")
# 본문 앞 50자에는 줄바꿈이 최대 50개 → 연속 줄은 최대 50줄. 그 뒤의 시각 없는 줄은 남의 것.
LEGACY_PREVIEW_MAX_CONT = 50
LEGACY_PREVIEW_MARKER = "legacy-preview-purged"


def _split_keepends(text: str) -> List[str]:
    """'\\n' 만으로 자른다(str.splitlines 는 \\r·\\x0c 등에서도 잘라 연속 줄 수가 틀어진다)."""
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def strip_legacy_preview(text: str):
    """옛 preview 줄 + 그 «바로 뒤» 시각 없는 연속 줄을 지운다.

    Returns: (남긴 텍스트, 지운 preview 줄 수, 지운 연속 줄 수).
    다른 레코드 뒤의 트레이스백(시각 없는 줄)은 preview 바로 뒤가 아니므로 남는다.
    """
    kept: List[str] = []
    n_preview = n_cont = 0
    cont_left = 0
    for line in _split_keepends(text):
        if _RE_LEGACY_PREVIEW.match(line):
            n_preview += 1
            cont_left = LEGACY_PREVIEW_MAX_CONT
            continue
        if cont_left and not _RE_LOG_RECORD_START.match(line):
            n_cont += 1
            cont_left -= 1
            continue
        cont_left = 0
        kept.append(line)
    return "".join(kept), n_preview, n_cont


def _strip_log_bytes(data: bytes) -> bytes:
    # surrogateescape — 깨진 바이트도 그대로 왕복(가리기는 뒤 단계에서)
    text = data.decode("utf-8", errors="surrogateescape")
    if "preview: " not in text:
        return data
    return strip_legacy_preview(text)[0].encode("utf-8", errors="surrogateescape")


def purge_legacy_preview_logs(log_file: Path, marker: Optional[Path] = None) -> dict:
    """[시작 때 1회] 자기 로그 4개에서 옛 preview 줄을 지운다. 파일 핸들러를 붙이기 «전»에 부른다.

    - 활성 로그도 inode 유지(r+ → 앞에서 다시 쓰기 → truncate), 권한 600.
    - 검산(입력 줄 = 남김 + preview + 연속, 바이트도 같은 식) 불성립 파일은 건드리지 않는다.
    - 끝나면 marker 파일을 남겨 다음 시작부터 건너뛴다. OSError 가 난 파일이 있으면 표식을
      남기지 않아 다음 시작에 다시 시도한다(검산 불성립은 결정적이라 표식을 남긴다).
    - 로그에는 줄 수·파일 이름만(내용은 쓰지 않는다) — 이 함수는 로깅하지 않고 결과만 돌려준다.

    Returns: {"skipped": bool, "preview": int, "cont": int, "files": int,
              "check_failed": [이름], "io_failed": [이름]}
    """
    log_file = Path(log_file)
    marker = Path(marker) if marker is not None else log_file.parent / LEGACY_PREVIEW_MARKER
    result = {"skipped": False, "preview": 0, "cont": 0, "files": 0,
              "check_failed": [], "io_failed": []}
    if marker.exists():
        result["skipped"] = True
        return result
    for p in log_file_set(log_file):
        try:
            with open(p, "r+b") as f:
                raw = f.read()
                text = raw.decode("utf-8", errors="surrogateescape")
                if "preview: " not in text:
                    continue
                kept, n_preview, n_cont = strip_legacy_preview(text)
                if n_preview == 0:
                    continue
                new = kept.encode("utf-8", errors="surrogateescape")
                n_in = len(_split_keepends(text))
                n_kept = len(_split_keepends(kept))
                if n_in != n_kept + n_preview + n_cont or len(new) >= len(raw):
                    result["check_failed"].append(p.name)
                    continue
                f.seek(0)
                f.write(new)
                f.truncate()
            _chmod600(p)
            result["files"] += 1
            result["preview"] += n_preview
            result["cont"] += n_cont
        except OSError:
            result["io_failed"].append(p.name)
    if not result["io_failed"]:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(_utc_iso() + "\n", encoding="utf-8")
            _chmod600(marker)
        except OSError:
            result["io_failed"].append(marker.name)
    return result


def build_log_tail(log_file: Path, crash_file: Optional[Path], redactor: Redactor,
                   max_raw: int = LOG_BUNDLE_MAX_RAW) -> bytes:
    """로그 4개 + crash.log 의 «최근» max_raw 바이트를 하나로 이어 붙인다(가린 뒤).

    로그 파일은 옛 preview 줄을 거른 뒤 붙인다(파일마다 — 머리줄이 연속 줄로 먹히지 않게).
    """
    parts: List[bytes] = []
    log_paths = log_file_set(log_file)
    paths = list(log_paths)
    if crash_file is not None and Path(crash_file).exists():
        paths = paths + [Path(crash_file)]
    for p in paths:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if p in log_paths:
            data = _strip_log_bytes(data)
        parts.append(f"\n===== {p.name} =====\n".encode("utf-8") + data)
    blob = b"".join(parts)
    if len(blob) > max_raw:
        blob = "...(앞부분 생략)...\n".encode("utf-8") + blob[-max_raw:]
    text = redactor(blob.decode("utf-8", errors="replace")) or ""
    return text.encode("utf-8")


def encode_log_bundle(body: bytes) -> str:
    return base64.b64encode(gzip.compress(body)).decode("ascii")


# 진단 묶음의 설정 요약 — «허용 목록». 새 필드는 여기 넣어야만 나간다(비밀 필드 자동 유출 차단).
DIAG_CONFIG_ALLOWLIST = (
    "mode", "port", "language", "lazy_paste", "lazy_size_threshold_mb", "fetch_grace_seconds",
    "file_conflict_policy", "staging_ttl_hours", "offer_ttl_hours", "max_file_size_gb",
    "clipboard_check_interval", "reconnect_interval", "clipboard_history_size",
    "auto_update_check", "collect_error_logs",
)


def config_summary(config: object) -> dict:
    return {k: getattr(config, k) for k in DIAG_CONFIG_ALLOWLIST if hasattr(config, k)}


def write_diagnostic_bundle(dest_dir: Path, log_file: Path, crash_file: Optional[Path],
                            config: object, version: str, redactor: Redactor) -> Path:
    """진단 묶음 zip — 로그 4개 + crash.log + info.json(버전·OS·설정 요약, auth_key 없음)."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = dest_dir / f"infinite-clipboard-diagnostics-{stamp}.zip"
    info = {
        "version": version, "os": platform.system(), "os_release": platform.release(),
        "machine": platform.machine(), "python": platform.python_version(),
        "created_at": _utc_iso(), "config": config_summary(config),
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("info.json", json.dumps(info, ensure_ascii=False, indent=2))
        log_paths = log_file_set(log_file)
        srcs = list(log_paths)
        if crash_file is not None and Path(crash_file).exists():
            srcs.append(Path(crash_file))
        for p in srcs:
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if p in log_paths:
                data = _strip_log_bytes(data)   # 옛 preview 줄 방어(표식 전·검산 불성립 파일)
            text = data.decode("utf-8", errors="replace")
            zf.writestr(p.name, redactor(text) or "")
    _chmod600(path)
    return path


# ── 로그 파일 권한 600 (회전 때 새 파일 포함) ───────────────────────────


def make_secure_rotating_handler(filename: str, **kwargs) -> logging.Handler:
    """RotatingFileHandler 인데 새로 여는 파일(처음·회전 후)을 POSIX 0o600 으로.

    회전 때 RotatingFileHandler 는 umask 기본(보통 644) 으로 새 파일을 만든다 —
    `_open` 을 감싸 매번 chmod. 이미 644 로 남은 백업(.1~.3)은 생성 시 한 번 고친다.
    """
    from logging.handlers import RotatingFileHandler

    class _SecureRotatingFileHandler(RotatingFileHandler):
        def _open(self):
            stream = super()._open()
            _chmod600(Path(self.baseFilename))
            return stream

        def doRollover(self):
            super().doRollover()
            for i in range(1, (self.backupCount or 0) + 1):
                p = Path(f"{self.baseFilename}.{i}")
                if p.exists():
                    _chmod600(p)

    handler = _SecureRotatingFileHandler(filename, **kwargs)
    for i in range(1, (kwargs.get("backupCount") or 0) + 1):
        p = Path(f"{filename}.{i}")
        if p.exists():
            _chmod600(p)
    return handler


# ── 주기 전송 서비스 · 전체 로그 요청 추적 ──────────────────────────────

SEND_INTERVAL_SECONDS = 600         # 10분마다 (+ 재접속 직후 kick)
LOG_REQUEST_TIMEOUT = 30.0          # 전체 로그 요청 — 이 시간 안에 답 없는 PC 는 «응답 없음»
LOG_RESPONSE_MIN_INTERVAL = 60.0    # 보내는 쪽: 요청 폭주 방지(이 간격 안의 재요청 무시)


class ErrorCollectionService:
    """outbox → 서버 전송(클라이언트) 또는 → 로컬 store(서버) 를 주기적으로 돌린다.

    send_fn(events) -> bool : 클라이언트 모드 전송(성공 여부가 정확해야 한다 — 실패면 restore).
    store/self_device/self_peer : 서버 모드 — 자기 사건은 네트워크 없이 store 로.
    """

    def __init__(self, collector: ErrorEventCollector, *, crash_path: Optional[Path],
                 state_path: Path, send_fn=None, store: Optional[CollectedLogStore] = None,
                 self_device: str = "", self_peer: str = "",
                 interval: float = SEND_INTERVAL_SECONDS):
        self.collector = collector
        self.crash_path = crash_path
        self.state_path = Path(state_path)
        self.send_fn = send_fn
        self.store = store
        self.self_device = self_device
        self.self_peer = self_peer
        self.interval = interval
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tick_lock = threading.Lock()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="error-events", daemon=True)
        self._thread.start()

    def kick(self) -> None:
        """재접속 직후 등 — 다음 주기를 기다리지 않고 한 번 돈다."""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        try:
            self.collector.flush_pending()   # 남은 합산분은 outbox 로(다음 실행 때 전송)
        except Exception:
            pass

    def _loop(self) -> None:
        # 시작 직후 1회: 지난 실행의 crash.log 덤프를 사건으로
        self.tick()
        while not self._stop.is_set():
            self._wake.wait(self.interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            self.tick()

    def tick(self) -> int:
        """한 회차. Returns: 전송(또는 저장)한 사건 수."""
        with self._tick_lock:
            try:
                if self.crash_path is not None:
                    self.collector.scan_crash_log(self.crash_path, self.state_path)
                if self.store is not None:
                    total = 0
                    while True:
                        batch = self.collector.take_batch()
                        if not batch:
                            break
                        try:
                            total += self.store.append(self.self_device, self.self_peer, batch)
                            self.collector.commit()
                        except OSError:
                            self.collector.restore()
                            break
                    return total
                if self.send_fn is None:
                    self.collector.flush_pending()
                    return 0
                batch = self.collector.take_batch()
                if not batch:
                    return 0
                ok = False
                try:
                    ok = bool(self.send_fn(batch))
                except Exception as e:
                    logger.debug(f"오류 사건 전송 예외: {e}")
                if ok:
                    self.collector.commit()
                    return len(batch)
                self.collector.restore()
                return 0
            except Exception as e:
                logger.debug(f"오류 사건 회차 실패: {e}")
                return 0


class LogRequestTracker:
    """서버 PC: «연결된 PC 전체 로그 요청» 한 번의 응답을 모아 타임아웃 뒤 결과를 낸다.

    결과: {"saved": [기기], "declined": [기기], "no_answer": [기기]} — no_answer 는
    옛 버전(메시지를 모름)이거나 그 사이 끊긴 PC.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._req: Optional[dict] = None

    def begin(self, request_id: str, peers: Dict[str, str]) -> bool:
        with self._lock:
            if self._req is not None:
                return False   # 진행 중 요청이 있다
            self._req = {"id": request_id, "waiting": dict(peers), "saved": [], "declined": [],
                         "done": threading.Event()}
            if not peers:
                self._req["done"].set()
            return True

    def on_response(self, request_id: object, peer_id: str, saved: bool, declined: bool) -> bool:
        with self._lock:
            req = self._req
            if req is None or request_id != req["id"] or peer_id not in req["waiting"]:
                return False
            name = req["waiting"].pop(peer_id)
            if saved:
                req["saved"].append(name)
            elif declined:
                req["declined"].append(name)
            else:
                req["waiting"][peer_id] = name   # 깨진 응답 — 응답 없음으로 남긴다
                return False
            if not req["waiting"]:
                req["done"].set()
            return True

    def wait(self, timeout: float = LOG_REQUEST_TIMEOUT) -> dict:
        with self._lock:
            req = self._req
        if req is None:
            return {"saved": [], "declined": [], "no_answer": []}
        req["done"].wait(timeout)
        with self._lock:
            result = {"saved": list(req["saved"]), "declined": list(req["declined"]),
                      "no_answer": sorted(req["waiting"].values())}
            self._req = None
        return result
