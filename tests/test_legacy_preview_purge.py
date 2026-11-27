"""옛 버전(≤ v3.0.19) 로그의 클립보드 preview 줄 정리 회귀 테스트.

통과 기준(원장 infinite-clipboard::legacy-preview-log-purge):
  preview + 줄바꿈 연속 줄 제거 · 다른 레코드 트레이스백 보존 · 표식 뒤 재실행 0
  · 검산 불성립 시 파일 불변 · 진단 묶음·전체 로그에 preview 0 · 권한 600.
내용은 전부 가짜 문자열(공개 저장소).
"""

import gzip
import os
import stat
import zipfile

from config import AppConfig
from core import error_events as ee

SECRET = "FAKE-SECRET-CLIP"

LOG = (
    "2026-11-01 10:00:00,001 - infinite-clipboard - INFO - 시작\n"
    "2026-11-01 10:00:01,002 - core.clipboard_manager - INFO - 텍스트 클립보드 설정: 30자\n"
    f"2026-11-01 10:00:01,003 - core.clipboard_manager - DEBUG -   preview: {SECRET} line1\n"
    f"{SECRET} line2\n"
    f"{SECRET} line3...\n"
    "2026-11-01 10:00:02,004 - infinite-clipboard - ERROR - 다른 오류\n"
    "Traceback (most recent call last):\n"
    '  File "x.py", line 1, in <module>\n'
    "ValueError: boom\n"
    f"2026-11-01 10:00:03,005 - core.clipboard_manager - DEBUG -   preview: {SECRET} one-line\n"
    "2026-11-01 10:00:04,006 - infinite-clipboard - INFO - 끝\n"
)


def _write(p, text, mode=0o600):
    p.write_bytes(text.encode("utf-8"))
    if os.name == "posix":
        os.chmod(p, mode)
    return p


def test_strip_removes_preview_and_continuation_keeps_other_traceback():
    kept, n_prev, n_cont = ee.strip_legacy_preview(LOG)
    assert SECRET not in kept
    assert (n_prev, n_cont) == (2, 2)
    assert "Traceback (most recent call last):" in kept
    assert "ValueError: boom\n" in kept
    assert "다른 오류" in kept and "시작" in kept and "끝" in kept
    assert len(ee._split_keepends(LOG)) == len(ee._split_keepends(kept)) + n_prev + n_cont


def test_strip_continuation_capped():
    # 50줄 넘는 시각 없는 줄은 preview 것이 아니다 — 51번째부터는 남긴다
    text = ("2026-11-01 10:00:01,003 - core.clipboard_manager - DEBUG -   preview: x\n"
            + "".join(f"tail{i}\n" for i in range(55)))
    kept, n_prev, n_cont = ee.strip_legacy_preview(text)
    assert (n_prev, n_cont) == (1, 50)
    assert kept == "".join(f"tail{i}\n" for i in range(50, 55))


def test_purge_rewrites_in_place_and_marks(tmp_path):
    log = _write(tmp_path / "infinite-clipboard.log", LOG)
    backup = _write(tmp_path / "infinite-clipboard.log.1", LOG, mode=0o644)
    inode = log.stat().st_ino
    res = ee.purge_legacy_preview_logs(log)
    assert res["skipped"] is False
    assert (res["files"], res["preview"], res["cont"]) == (2, 4, 4)
    assert res["check_failed"] == [] and res["io_failed"] == []
    for p in (log, backup):
        assert SECRET not in p.read_text(encoding="utf-8")
        assert "ValueError: boom" in p.read_text(encoding="utf-8")
        if os.name == "posix":
            assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert log.stat().st_ino == inode            # 활성 로그 inode 유지
    assert (tmp_path / ee.LEGACY_PREVIEW_MARKER).exists()


def test_purge_skipped_after_marker(tmp_path):
    log = _write(tmp_path / "infinite-clipboard.log", LOG)
    ee.purge_legacy_preview_logs(log)
    _write(log, LOG)                              # 표식 뒤에는 다시 건드리지 않는다
    res = ee.purge_legacy_preview_logs(log)
    assert res["skipped"] is True
    assert log.read_text(encoding="utf-8") == LOG


def test_purge_check_failure_leaves_file_unchanged(tmp_path, monkeypatch):
    log = _write(tmp_path / "infinite-clipboard.log", LOG)
    before = log.read_bytes()
    real = ee.strip_legacy_preview

    def broken(text):
        kept, n_prev, n_cont = real(text)
        return kept, n_prev, n_cont + 1           # 검산 불성립을 흉내

    monkeypatch.setattr(ee, "strip_legacy_preview", broken)
    res = ee.purge_legacy_preview_logs(log)
    assert res["check_failed"] == [log.name]
    assert res["files"] == 0
    assert log.read_bytes() == before


def test_purge_no_preview_untouched(tmp_path):
    clean = "2026-11-01 10:00:00,001 - infinite-clipboard - INFO - 시작\n"
    log = _write(tmp_path / "infinite-clipboard.log", clean)
    mtime = log.stat().st_mtime_ns
    res = ee.purge_legacy_preview_logs(log)
    assert res["files"] == 0 and res["preview"] == 0
    assert log.stat().st_mtime_ns == mtime


def test_purge_preserves_invalid_utf8_bytes(tmp_path):
    raw = LOG.encode("utf-8") + b"2026-11-01 10:00:05,007 - x - INFO - bad \xff\xfe\n"
    log = tmp_path / "infinite-clipboard.log"
    log.write_bytes(raw)
    ee.purge_legacy_preview_logs(log)
    assert log.read_bytes().endswith(b"bad \xff\xfe\n")


def test_bundles_filter_preview_before_marker(tmp_path):
    # 정리 전(표식 없음) 상태라도 진단 묶음·전체 로그 응답엔 preview 가 0
    log = _write(tmp_path / "infinite-clipboard.log", LOG)
    _write(tmp_path / "infinite-clipboard.log.2", LOG)
    crash = _write(tmp_path / "crash.log", "Fatal Python error: fake\n")
    red = ee.Redactor(secrets=[], home="/home/fakeuser")

    body = ee.build_log_tail(log, crash, red).decode("utf-8")
    assert SECRET not in body
    assert "ValueError: boom" in body and "Fatal Python error" in body
    assert SECRET not in gzip.decompress(
        __import__("base64").b64decode(ee.encode_log_bundle(body.encode("utf-8")))).decode()

    zpath = ee.write_diagnostic_bundle(tmp_path / "out", log, crash, AppConfig(), "9.9.9", red)
    with zipfile.ZipFile(zpath) as zf:
        for name in zf.namelist():
            assert SECRET not in zf.read(name).decode("utf-8")
        assert "ValueError: boom" in zf.read("infinite-clipboard.log").decode("utf-8")
    assert log.read_text(encoding="utf-8") == LOG   # 묶음은 원본 파일을 바꾸지 않는다
