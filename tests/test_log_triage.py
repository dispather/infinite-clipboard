"""scripts/log_triage.py (log-triage-loop d2) — fake events.jsonl, 3 devices · 2 versions.

Device names and peer ids here are fake on purpose (public repo).
"""

import copy
import importlib.util
import json
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("log_triage", ROOT / "scripts" / "log_triage.py")
lt = importlib.util.module_from_spec(_spec)
sys.modules["log_triage"] = lt
_spec.loader.exec_module(lt)

OLD, NEW = "3.0.9", "3.0.10"   # 3.0.10 > 3.0.9 — a string compare gets this wrong

DEVICES = {  # folder -> (device, peer8)
    "alpha-aaaa1111": ("alpha", "aaaa1111"),
    "bravo-bbbb2222": ("bravo", "bbbb2222"),
    "charlie-cccc3333": ("charlie", "cccc3333"),
}

SIG_NEW = "1111111111111111"       # no ledger item
SIG_ONGOING = "2222222222222222"   # live item
SIG_GONE = "3333333333333333"      # fixed in NEW, only seen at OLD, exposure enough
SIG_REGRESS = "4444444444444444"   # fixed in NEW, seen again at NEW
SIG_HOLD = "5555555555555555"      # fixed in 3.0.11 — nobody runs it yet


def ev(sig, version, day, count=1, level="ERROR", head="something failed <n>", tb=None):
    return {"schema": 1, "sig": sig, "level": level, "logger": "core.network", "head": head,
            "count": count, "first_ts": f"2026-11-{day:02d}T01:00:00Z",
            "last_ts": f"2026-11-{day:02d}T02:00:00Z", "version": version, "os": "Linux",
            "os_release": "6.0", "traceback": tb}


def write_collected(root: Path, extra_lines=None):
    rows = {
        "alpha-aaaa1111": [
            ev(SIG_NEW, NEW, 20, count=3,
               tb='Traceback (most recent call last):\n  File "C:\\Users\\x\\AppData\\Local\\Temp\\_MEI123\\core\\network.py", line 42, in run\nOSError: boom'),
            ev(SIG_ONGOING, OLD, 10), ev(SIG_GONE, OLD, 10, count=5), ev(SIG_HOLD, NEW, 21),
        ],
        "bravo-bbbb2222": [
            ev(SIG_NEW, NEW, 22, count=2), ev(SIG_ONGOING, NEW, 23),
            ev(SIG_REGRESS, OLD, 11), ev(SIG_REGRESS, NEW, 24, count=4),
        ],
        "charlie-cccc3333": [ev(SIG_GONE, OLD, 12), ev(SIG_ONGOING, NEW, 25)],
    }
    for folder, events in rows.items():
        d = root / folder
        d.mkdir(parents=True)
        dev, peer = DEVICES[folder]
        lines = [json.dumps(dict(e, device=dev, peer=peer, received_at="2026-11-26T00:00:00Z"))
                 for e in events]
        if folder == "charlie-cccc3333" and extra_lines:
            lines += extra_lines
        (d / "events.jsonl").write_text("\n".join(lines) + "\n\n", encoding="utf-8")
        # same folder holds full-log dumps — must not be parsed
        (d / "full-log-20261126T000000Z.txt").write_text("not json\n", encoding="utf-8")


def ledger():
    return {"tasks": [
        {"title": "ongoing", "task_ref": "infinite-clipboard::fix-ongoing", "status": "in_progress",
         "signature": SIG_ONGOING, "condition": "x"},
        {"title": "gone", "task_ref": "infinite-clipboard::fix-gone", "status": "completed",
         "signature": [SIG_GONE], "fixed_in": NEW, "condition": "x"},
        {"title": "regress", "task_ref": "infinite-clipboard::fix-regress", "status": "completed",
         "signature": SIG_REGRESS, "fixed_in": NEW, "closed_at": "2026-11-15", "condition": "old line"},
        {"title": "hold", "task_ref": "infinite-clipboard::fix-hold", "status": "completed",
         "signature": SIG_HOLD, "fixed_in": "3.0.11", "condition": "x"},
        {"title": "unrelated", "task_ref": "infinite-clipboard::other", "status": "pending",
         "condition": "x"},
    ]}


def analyse(root, led=None, min_devices=2, min_days=3.0):
    read = lt.read_collected(root)
    groups = lt.group_events(read.events)
    rows = lt.classify(groups, read.events, led if led is not None else ledger(),
                       min_devices, min_days)
    return read, groups, {r["sig"]: r for r in rows}


def test_version_compare_numeric():
    assert lt.version_ge("3.0.10", "3.0.9")
    assert not lt.version_ge("3.0.9", "3.0.10")
    assert lt.version_ge("v3.1", "3.0.99")


def test_four_verdicts_and_hold(tmp_path):
    write_collected(tmp_path)
    _, groups, rows = analyse(tmp_path)
    assert rows[SIG_NEW]["verdict"] == "new"
    assert rows[SIG_ONGOING]["verdict"] == "ongoing"
    assert rows[SIG_REGRESS]["verdict"] == "regression"
    assert rows[SIG_REGRESS]["seen_after_fix"] == 4
    # gone: devices on >= NEW are alpha, bravo, charlie (3) over 20..25 → 5 days
    assert rows[SIG_GONE]["verdict"] == "gone"
    assert rows[SIG_GONE]["exposure"]["peers"] == 3
    # hold: nobody runs 3.0.11
    assert rows[SIG_HOLD]["verdict"] == "on_hold"
    assert rows[SIG_HOLD]["exposure"]["peers"] == 0
    # total = sum of per-interval counts, identity by peer
    assert groups[SIG_NEW]["count"] == 5 and len(groups[SIG_NEW]["peers"]) == 2
    assert groups[SIG_ONGOING]["count"] == 3 and len(groups[SIG_ONGOING]["peers"]) == 3


def test_gone_needs_exposure(tmp_path):
    write_collected(tmp_path)
    _, _, rows = analyse(tmp_path, min_devices=4)
    assert rows[SIG_GONE]["verdict"] == "on_hold"
    _, _, rows = analyse(tmp_path, min_days=10)
    assert rows[SIG_GONE]["verdict"] == "on_hold"


def test_completed_without_fixed_in(tmp_path):
    write_collected(tmp_path)
    led = ledger()
    del led["tasks"][2]["fixed_in"]
    _, _, rows = analyse(tmp_path, led)
    assert rows[SIG_REGRESS]["verdict"] == "no_fixed_in"


def test_checksum_with_malformed_lines(tmp_path):
    bad = ["{not json", json.dumps({"schema": 2, "sig": "x"}), json.dumps({"schema": 1, "level": "ERROR"})]
    write_collected(tmp_path, extra_lines=bad)
    read, groups, _ = analyse(tmp_path)
    cs = lt.checksum(read, groups)
    assert cs["input_lines"] == 10 + 3
    assert cs["grouped_lines"] == 10 and cs["unreadable_lines"] == 3
    assert cs["lines_ok"] and cs["count_ok"]
    assert dict(read.unreadable) == {"bad_json": 1, "schema_mismatch": 1, "missing_sig": 1}
    assert all("full-log" not in f["path"] for f in read.files)


def test_not_measured_states(tmp_path):
    assert lt.read_collected(tmp_path / "missing").state == "no_dir"
    (tmp_path / "c").mkdir()
    assert lt.read_collected(tmp_path / "c").state == "no_files"
    (tmp_path / "c" / "dev-00000000").mkdir()
    (tmp_path / "c" / "dev-00000000" / "events.jsonl").write_text("\n", encoding="utf-8")
    assert lt.read_collected(tmp_path / "c").state == "no_lines"
    rc = lt.run(["--collected-dir", str(tmp_path / "missing"), "--report-dir", str(tmp_path / "r")])
    assert rc == lt.EXIT_NOT_MEASURED


def test_delta_new_and_regression_idempotent(tmp_path):
    write_collected(tmp_path)
    _, groups, rows = analyse(tmp_path)
    delta = lt.build_delta(list(rows.values()), "2026-11-27")
    assert [i["signature"] for i in delta["new_items"]] == [SIG_NEW]
    item = delta["new_items"][0]
    assert item["task_ref"] == "infinite-clipboard::err-" + SIG_NEW
    assert "core/network.py:42" in item["condition"]
    assert item["relation_check"]["verdict"] == "new"
    # privacy: ledger text carries counts only
    blob = json.dumps(delta, ensure_ascii=False)
    for folder, (dev, peer) in DEVICES.items():
        assert dev not in blob and peer not in blob
    assert [o["task_ref"] for o in delta["reopen"]] == ["infinite-clipboard::fix-regress"]

    d = ledger()
    lt.apply_delta(d, delta)
    regress = next(t for t in d["tasks"] if t["task_ref"] == "infinite-clipboard::fix-regress")
    assert regress["status"] == "pending" and "closed_at" not in regress
    assert regress["condition"].startswith("회귀 [log-triage 2026-11-27]")
    assert regress["condition"].endswith("old line")     # existing lines kept
    n = len(d["tasks"])
    snapshot = copy.deepcopy(d)
    lt.apply_delta(d, delta)                               # second run: nothing changes
    assert len(d["tasks"]) == n and d == snapshot
    assert sum(1 for t in d["tasks"] if t.get("signature") == SIG_NEW) == 1


def test_dedup_against_existing_signature_any_status(tmp_path):
    write_collected(tmp_path)
    led = ledger()
    led["tasks"].append({"task_ref": "infinite-clipboard::x", "status": "cancelled",
                         "signature": SIG_NEW, "condition": "x"})
    _, _, rows = analyse(tmp_path, led)
    assert rows[SIG_NEW]["verdict"] == "ignored"
    delta = lt.build_delta(list(rows.values()), "2026-11-27")
    assert delta["new_items"] == []
    # a stale delta applied to a ledger that already got the sig adds nothing
    stale = {"generated_at": "2026-11-27", "new_items": [
        {"signature": SIG_NEW, "task_ref": "infinite-clipboard::err-" + SIG_NEW}], "reopen": []}
    n = len(led["tasks"])
    lt.apply_delta(led, stale)
    assert len(led["tasks"]) == n


def test_cli_report_and_self_contained_transform(tmp_path):
    coll = tmp_path / "collected"
    write_collected(coll)
    led_path = tmp_path / "ledger.json"
    led_path.write_text(json.dumps(ledger()), encoding="utf-8")
    xf = tmp_path / "out" / "transform.py"
    rc = lt.run(["--collected-dir", str(coll), "--ledger", str(led_path),
                 "--report-dir", str(tmp_path / "reports"), "--emit-transform", str(xf),
                 "--today", "2026-11-27"])
    assert rc == lt.EXIT_OK
    md = (tmp_path / "reports" / "triage-2026-11-27.md").read_text(encoding="utf-8")
    assert "성립" in md and "불성립" not in md and "회귀" in md
    report = json.loads((tmp_path / "reports" / "triage-2026-11-27.json").read_text(encoding="utf-8"))
    assert report["checksum"]["lines_ok"] and len(report["rows"]) == 5
    dj = json.loads((tmp_path / "reports" / "triage-2026-11-27-ledger-delta.json").read_text(encoding="utf-8"))
    assert len(dj["new_items"]) == 1 and len(dj["reopen"]) == 1
    # the transform runs without importing this repo
    ns = runpy.run_path(str(xf), run_name="transform")
    d = json.loads(led_path.read_text(encoding="utf-8"))
    before = len(d["tasks"])
    ns["apply"](d)
    assert len(d["tasks"]) == before + 1
    ns["apply"](d)
    assert len(d["tasks"]) == before + 1


def test_guess_cause_file_posix_and_windows():
    tb = ('  File "/opt/app/core/clipboard_manager.py", line 10, in a\n'
          '  File "/usr/lib/python3/threading.py", line 5, in b\n'
          '  File "C:\\x\\_MEI9\\ui\\tray.py", line 7, in c\n'
          '  File "/usr/lib/python3/logging/__init__.py", line 1, in d\n')
    assert lt.guess_cause_file(tb) == "ui/tray.py:7"
    assert lt.guess_cause_file(None) is None
