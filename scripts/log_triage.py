#!/usr/bin/env python3
"""log-triage-loop (d2) — collected error events -> per-signature verdicts -> ledger delta.

Input: the server PC's ``<config dir>/collected-logs/<device>-<peer8>/events.jsonl``
(+ ``events.1.jsonl`` rotation backup). Schema = ``core/error_events.py`` module docstring
(schema=1). Only those two file names are read — ``full-log-*.txt`` in the same folder is not
an event file.

Verdict per signature (``sig``):

  new         no ledger item carries this signature
  ongoing     a live ledger item (pending / in_progress / blocked / deferred) carries it
  ignored     the item was cancelled (decided not to fix) — reported, no action
  regression  the item is ``completed`` with ``fixed_in`` and the sig is seen at a version >= fixed_in
  gone        completed + fixed_in, not seen at >= fixed_in, AND exposure is sufficient
  on_hold     completed + fixed_in, not seen at >= fixed_in, exposure NOT sufficient
  no_fixed_in completed but the item has no ``fixed_in`` — regression/gone cannot be judged

Ledger contract (fields this script reads on ``.next-tasks.json`` items):
  signature  str | list[str]   sig(s) the item covers (dedup key — one item per sig)
  fixed_in   str               release version that ships the fix (e.g. "3.0.20")

Exposure proxy (for ``gone``): healthy devices emit no events, so «version X ran cleanly» is not
observable. The proxy is the set of distinct peers that sent ANY event at a version >= fixed_in and
the time span of those events. Thresholds: ``--min-devices`` / ``--min-days``. Below them the
verdict is ``on_hold`` — never ``gone``.

Checksums (exit 3 if broken):
  per file and total: non-blank lines = grouped lines + unreadable lines
  sum(count) over groups = sum(count) over valid lines

Outputs:
  * report (markdown + json) in a LOCAL directory outside the repo (default
    ``<config dir>/triage-reports/``) — it contains device names, never commit it.
  * with ``--ledger``: ``triage-<date>-ledger-delta.json`` next to the report (new_items[] are
    complete ledger items; reopen[] are regression re-openings). PM-launched sessions apply it
    with the Edit tool — ledger-apply.sh is blocked there by the PM guard.
  * optional ``--emit-transform PATH``: a self-contained transform for
    ``ledger-apply.sh <PATH> <repo> --allow-new-keys`` (adds drafts for new sigs, reopens
    regressions). Dedup is re-checked inside ``apply(d)`` against the freshly locked ledger.
    Ledger text carries counts only — no device names, peer ids or hosts.

This script never writes the ledger itself (single write path = ledger-apply.sh), never sends
anything off this PC, and never publishes a release (human gate).

Exit codes: 0 ok · 2 not measured (no collected-logs dir / no event files / no lines) ·
3 checksum broken · 64 usage.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1
EVENT_FILE_NAMES = ("events.jsonl", "events.1.jsonl")
TASK_REF_PREFIX = "infinite-clipboard::err-"
LIVE_STATUSES = frozenset({"pending", "in_progress", "blocked", "deferred"})
REQUIRED_KEYS = ("sig", "level", "logger", "head", "count", "first_ts", "last_ts", "version")
LEVEL_PRIORITY = {"CRASH": "high", "CRITICAL": "high", "ERROR": "medium", "WARNING": "low"}
TRACEBACK_IN_LEDGER = 1500

VERDICTS = ("new", "regression", "ongoing", "on_hold", "no_fixed_in", "gone", "ignored")

EXIT_OK, EXIT_NOT_MEASURED, EXIT_CHECKSUM, EXIT_USAGE = 0, 2, 3, 64


# ── versions / time ────────────────────────────────────────────────────


def version_key(v: Optional[str]) -> Tuple:
    """Numeric tuple compare — "3.0.10" > "3.0.9". Non-numeric parts sort below numbers."""
    parts = re.split(r"[.\-+]", str(v or "").lstrip("vV"))
    key = []
    for p in parts:
        if p.isdigit():
            key.append((1, int(p), ""))
        elif p:
            key.append((0, 0, p))
    return tuple(key)


def version_ge(a: Optional[str], b: Optional[str]) -> bool:
    return version_key(a) >= version_key(b)


def parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not isinstance(s, str):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ── reading ────────────────────────────────────────────────────────────


class ReadResult:
    def __init__(self):
        self.state = "ok"                 # ok | no_dir | no_files | no_lines
        self.files: List[dict] = []       # {path, device_dir, lines, valid, unreadable}
        self.events: List[dict] = []      # valid rows (with _device_dir)
        self.unreadable: Dict[str, int] = defaultdict(int)  # reason -> n
        self.total_lines = 0


def _validate(row) -> Optional[str]:
    """None if the row is a usable schema-1 event, else the unreadable reason."""
    if not isinstance(row, dict):
        return "not_object"
    if row.get("schema") != SCHEMA_VERSION:
        return "schema_mismatch"
    for k in REQUIRED_KEYS:
        if k not in row:
            return "missing_" + k
    if not isinstance(row["sig"], str) or not row["sig"]:
        return "bad_sig"
    if not isinstance(row["count"], int) or isinstance(row["count"], bool) or row["count"] < 1:
        return "bad_count"
    return None


def read_collected(root: Path) -> ReadResult:
    res = ReadResult()
    root = Path(root)
    if not root.is_dir():
        res.state = "no_dir"
        return res
    paths = sorted(p for d in root.iterdir() if d.is_dir()
                   for name in EVENT_FILE_NAMES for p in [d / name] if p.is_file())
    if not paths:
        res.state = "no_files"
        return res
    for path in paths:
        info = {"path": str(path), "device_dir": path.parent.name,
                "lines": 0, "valid": 0, "unreadable": 0}
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                info["lines"] += 1
                try:
                    row = json.loads(line)
                except ValueError:
                    reason = "bad_json"
                else:
                    reason = _validate(row)
                if reason:
                    info["unreadable"] += 1
                    res.unreadable[reason] += 1
                    continue
                info["valid"] += 1
                row["_device_dir"] = path.parent.name
                res.events.append(row)
        res.files.append(info)
        res.total_lines += info["lines"]
    if res.total_lines == 0:
        res.state = "no_lines"
    return res


# ── grouping ───────────────────────────────────────────────────────────


def _peer_of(ev: dict) -> str:
    # identity = peer id prefix (names can collide or change); fall back to the folder name
    return ev.get("peer") or ev.get("_device_dir") or "?"


def group_events(events: Iterable[dict]) -> Dict[str, dict]:
    groups: Dict[str, dict] = {}
    for ev in events:
        g = groups.get(ev["sig"])
        if g is None:
            g = groups[ev["sig"]] = {
                "sig": ev["sig"], "level": ev["level"], "logger": ev["logger"],
                "head": ev["head"], "count": 0, "lines": 0,
                "first_ts": ev["first_ts"], "last_ts": ev["last_ts"],
                "peers": set(), "devices": set(), "versions": defaultdict(int),
                "os": set(), "traceback": None,
            }
        g["count"] += ev["count"]          # count is per interval: total = sum
        g["lines"] += 1
        if ev["first_ts"] < g["first_ts"]:
            g["first_ts"] = ev["first_ts"]
        if ev["last_ts"] > g["last_ts"]:
            g["last_ts"] = ev["last_ts"]
        g["peers"].add(_peer_of(ev))
        g["devices"].add(ev.get("device") or ev.get("_device_dir") or "?")
        g["versions"][str(ev["version"])] += ev["count"]
        if ev.get("os"):
            g["os"].add(ev["os"])
        if not g["traceback"] and ev.get("traceback"):
            g["traceback"] = ev["traceback"]
    return groups


def checksum(read: ReadResult, groups: Dict[str, dict]) -> dict:
    grouped_lines = sum(g["lines"] for g in groups.values())
    unreadable = sum(read.unreadable.values())
    per_file_ok = all(f["lines"] == f["valid"] + f["unreadable"] for f in read.files)
    count_groups = sum(g["count"] for g in groups.values())
    count_lines = sum(ev["count"] for ev in read.events)
    return {
        "input_lines": read.total_lines, "grouped_lines": grouped_lines,
        "unreadable_lines": unreadable,
        "lines_ok": read.total_lines == grouped_lines + unreadable and per_file_ok,
        "count_groups": count_groups, "count_lines": count_lines,
        "count_ok": count_groups == count_lines,
    }


def exposure(events: Iterable[dict], min_version: str) -> dict:
    """Distinct peers with ANY event at version >= min_version, and the span of those events."""
    peers, first, last = set(), None, None
    for ev in events:
        if not version_ge(ev["version"], min_version):
            continue
        peers.add(_peer_of(ev))
        a, b = parse_ts(ev["first_ts"]), parse_ts(ev["last_ts"])
        if a and (first is None or a < first):
            first = a
        if b and (last is None or b > last):
            last = b
    days = (last - first).total_seconds() / 86400 if first and last else 0.0
    return {"peers": len(peers), "days": round(days, 2),
            "first_ts": first.strftime("%Y-%m-%dT%H:%M:%SZ") if first else None,
            "last_ts": last.strftime("%Y-%m-%dT%H:%M:%SZ") if last else None}


# ── ledger join ────────────────────────────────────────────────────────


def _sigs_of(task: dict) -> List[str]:
    s = task.get("signature")
    if isinstance(s, str):
        return [s] if s else []
    if isinstance(s, list):
        return [x for x in s if isinstance(x, str) and x]
    return []


def ledger_index(ledger: Optional[dict]) -> Dict[str, List[dict]]:
    idx: Dict[str, List[dict]] = defaultdict(list)
    for t in (ledger or {}).get("tasks", []) or []:
        if isinstance(t, dict):
            for s in _sigs_of(t):
                idx[s].append(t)
    return idx


def _pick_item(items: List[dict]) -> dict:
    # a live item wins over closed ones (the fix is still being worked on)
    for t in items:
        if t.get("status") in LIVE_STATUSES:
            return t
    for t in items:
        if t.get("status") == "completed":
            return t
    return items[0]


def classify(groups: Dict[str, dict], events: List[dict], ledger: Optional[dict],
             min_devices: int, min_days: float) -> List[dict]:
    idx = ledger_index(ledger)
    out = []
    for sig, g in groups.items():
        row = {"sig": sig, "group": g, "task_ref": None, "fixed_in": None,
               "seen_after_fix": 0, "exposure": None}
        items = idx.get(sig)
        if not items:
            row["verdict"] = "new"
        else:
            t = _pick_item(items)
            row["task_ref"] = t.get("task_ref")
            st = t.get("status")
            if st in LIVE_STATUSES:
                row["verdict"] = "ongoing"
            elif st == "cancelled":
                row["verdict"] = "ignored"
            elif st == "completed":
                fixed = t.get("fixed_in")
                if not fixed:
                    row["verdict"] = "no_fixed_in"
                else:
                    row["fixed_in"] = fixed
                    after = sum(n for v, n in g["versions"].items() if version_ge(v, fixed))
                    row["seen_after_fix"] = after
                    if after > 0:
                        row["verdict"] = "regression"
                    else:
                        ex = exposure(events, fixed)
                        row["exposure"] = ex
                        ok = ex["peers"] >= min_devices and ex["days"] >= min_days
                        row["verdict"] = "gone" if ok else "on_hold"
            else:
                row["verdict"] = "ongoing"
        out.append(row)
    order = {v: i for i, v in enumerate(VERDICTS)}
    out.sort(key=lambda r: (order[r["verdict"]], -r["group"]["count"], r["sig"]))
    return out


# ── ledger delta (counts only — no device names) ───────────────────────

_RE_FRAME = re.compile(r'File "([^"]+)", line (\d+)')
_RE_REPO_FILE = re.compile(r"(?:^|[\\/])((?:core|ui)[\\/][\w\\/.-]+\.py|main\.py|config\.py|start\.py)$")


def guess_cause_file(tb: Optional[str]) -> Optional[str]:
    """Last traceback frame inside this repo (core/, ui/, main.py …), either path separator."""
    if not tb:
        return None
    hit = None
    for path, line in _RE_FRAME.findall(tb):
        m = _RE_REPO_FILE.search(path)
        if m:
            hit = f"{m.group(1).replace(chr(92), '/')}:{line}"
    return hit


def _obs_summary(g: dict) -> dict:
    return {"count": g["count"], "devices": len(g["peers"]),
            "versions": sorted(g["versions"], key=version_key),
            "os": sorted(g["os"]), "first_ts": g["first_ts"], "last_ts": g["last_ts"]}


def build_delta(rows: List[dict], today: str) -> dict:
    new_items, reopen = [], []
    for r in rows:
        g = r["group"]
        obs = _obs_summary(g)
        if r["verdict"] == "new":
            cause = guess_cause_file(g["traceback"])
            tb = (g["traceback"] or "")[-TRACEBACK_IN_LEDGER:]
            cond = [
                f"⛳착수: 오류 사건 서명 {r['sig']} 의 원인 조사 — 추정 원인 파일 {cause or '미상(트레이스백 없음)'}.",
                f"관측 [log-triage {today}]: {g['level']} · 로거 {g['logger']} · «{g['head']}» · "
                f"{obs['count']}회 · 기기 {obs['devices']}대 · 버전 {', '.join(obs['versions'])} · "
                f"OS {', '.join(obs['os']) or '?'} · {obs['first_ts']} ~ {obs['last_ts']}.",
                "고치고 닫을 때 fixed_in 에 그 수정이 실린 릴리스 버전을 적을 것 — 없으면 회귀·사라짐 판정 불가.",
            ]
            if tb:
                cond.append("대표 트레이스백(끝부분):\n" + tb)
            new_items.append({
                "title": f"오류 사건 {g['level']} «{g['head'][:80]}» — {obs['count']}회·기기 {obs['devices']}대",
                "task_ref": TASK_REF_PREFIX + r["sig"],
                "status": "pending",
                "priority": LEVEL_PRIORITY.get(g["level"], "low"),
                "phase": "decide",
                "added_at": today,
                "source": f"log-triage-loop {today} (scripts/log_triage.py)",
                "signature": r["sig"],
                "observations": obs,
                "relation_check": {"verdict": "new", "seen": [],
                                   "reason": "signature 미등록 — scripts/log_triage.py 판정"},
                "condition": "\n".join(cond),
            })
        elif r["verdict"] == "regression" and r["task_ref"]:
            after = [v for v in obs["versions"] if version_ge(v, r["fixed_in"])]
            reopen.append({
                "task_ref": r["task_ref"], "sig": r["sig"], "fixed_in": r["fixed_in"],
                "line": (f"회귀 [log-triage {today}]: fixed_in {r['fixed_in']} 이후 버전 "
                         f"{', '.join(after)} 에서 서명 {r['sig']} 재관측 {r['seen_after_fix']}회 · "
                         f"기기 {obs['devices']}대 · 마지막 {obs['last_ts']} — 재개."),
                "observations": obs,
            })
    return {"generated_at": today, "new_items": new_items, "reopen": reopen}


def apply_delta(d: dict, delta: dict) -> dict:
    """Apply a delta to a ledger dict in place. Idempotent: re-running adds/reopens nothing."""
    tasks = d.setdefault("tasks", [])
    idx = ledger_index(d)
    refs = {t.get("task_ref") for t in tasks if isinstance(t, dict)}
    for item in delta.get("new_items", []):
        if item["signature"] in idx or item["task_ref"] in refs:
            continue
        tasks.append(dict(item))
        idx[item["signature"]].append(item)
        refs.add(item["task_ref"])
    for op in delta.get("reopen", []):
        for t in tasks:
            if not isinstance(t, dict) or t.get("task_ref") != op["task_ref"]:
                continue
            if t.get("status") != "completed":
                break  # already reopened (or changed by someone else) — leave it
            t["status"] = "pending"
            t["reopened_at"] = delta.get("generated_at")
            t.pop("closed_at", None)
            t["condition"] = op["line"] + ("\n" + t["condition"] if t.get("condition") else "")
            t["observations"] = op["observations"]
            break
    return d


_TRANSFORM_TEMPLATE = '''# Generated by scripts/log_triage.py on {today} — ledger delta for ledger-apply.sh.
# Run: ledger-apply.sh <this file> <repo root> --allow-new-keys   (add --dry-run to preview)
# Self-contained on purpose: the repo checkout that produced it may be gone.
import json

DELTA = json.loads({delta_json!r})

{apply_src}

def apply(d):
    return apply_delta(d, DELTA)
'''


def render_transform(delta: dict) -> str:
    import inspect
    src = "\n\n".join(inspect.getsource(f) for f in (_sigs_of, ledger_index, apply_delta))
    src = ("from collections import defaultdict\n"
           "from typing import Dict, List, Optional\n\n" + src)
    return _TRANSFORM_TEMPLATE.format(today=delta["generated_at"],
                                      delta_json=json.dumps(delta, ensure_ascii=False),
                                      apply_src=src)


# ── report ─────────────────────────────────────────────────────────────

_VERDICT_KO = {"new": "새 오류", "regression": "회귀", "ongoing": "계속", "on_hold": "사라짐 판정 보류",
               "no_fixed_in": "fixed_in 미기재", "gone": "사라짐 의심", "ignored": "안 고치기로 함"}


def render_report(read: ReadResult, rows: List[dict], cs: dict, args_info: dict) -> str:
    L = [f"# 오류 사건 분석 — {args_info['today']}", "",
         f"- 입력: `{args_info['collected']}` · 파일 {len(read.files)}개",
         f"- 검산: 입력 {cs['input_lines']}줄 = 묶임 {cs['grouped_lines']} + 못 읽음 {cs['unreadable_lines']} "
         f"→ {'성립' if cs['lines_ok'] else '불성립'} · 횟수 합 {cs['count_groups']} = {cs['count_lines']} "
         f"→ {'성립' if cs['count_ok'] else '불성립'}"]
    if read.unreadable:
        L.append("- 못 읽은 줄 사유: " + ", ".join(f"{k} {v}" for k, v in sorted(read.unreadable.items())))
    L.append(f"- 사라짐 문턱: 고친 버전 이상에서 사건을 보낸 기기 ≥ {args_info['min_devices']}대 "
             f"그리고 관측 기간 ≥ {args_info['min_days']}일 (대리 지표 — 건강한 기기는 사건을 안 보내므로 "
             "«그 버전이 깨끗이 돌았다»는 직접 관측 불가)")
    tally = defaultdict(int)
    for r in rows:
        tally[r["verdict"]] += 1
    L.append("- 판정: " + " · ".join(f"{_VERDICT_KO[v]} {tally[v]}" for v in VERDICTS if tally[v]))
    L += ["", "| 판정 | 서명 | 레벨 | 횟수 | 기기 | 버전 | 처음 ~ 마지막 | 원장 | 메시지 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        g = r["group"]
        extra = ""
        if r["exposure"]:
            e = r["exposure"]
            extra = f" (노출: 기기 {e['peers']}대·{e['days']}일)"
        if r["fixed_in"]:
            extra += f" fixed_in {r['fixed_in']}"
        L.append(f"| {_VERDICT_KO[r['verdict']]}{extra} | `{r['sig']}` | {g['level']} | {g['count']} | "
                 f"{', '.join(sorted(g['devices']))} | {', '.join(sorted(g['versions'], key=version_key))} | "
                 f"{g['first_ts']} ~ {g['last_ts']} | {r['task_ref'] or '-'} | {g['head'].replace('|', '/')} |")
    L += ["", "이 보고서에는 기기 이름이 들어 있다 — 저장소에 커밋하지 말 것.", ""]
    return "\n".join(L)


def _rows_json(rows: List[dict]) -> List[dict]:
    out = []
    for r in rows:
        g = r["group"]
        out.append({"sig": r["sig"], "verdict": r["verdict"], "task_ref": r["task_ref"],
                    "fixed_in": r["fixed_in"], "seen_after_fix": r["seen_after_fix"],
                    "exposure": r["exposure"], "level": g["level"], "logger": g["logger"],
                    "head": g["head"], "count": g["count"], "devices": sorted(g["devices"]),
                    "versions": dict(g["versions"]), "first_ts": g["first_ts"],
                    "last_ts": g["last_ts"]})
    return out


# ── CLI ────────────────────────────────────────────────────────────────


def default_config_dir() -> Path:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import config  # noqa: E402 — lazy: only when no explicit path was given
    return config._get_config_dir()


def run(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Triage collected error events (log-triage-loop d2).")
    ap.add_argument("--collected-dir", type=Path, help="default: <config dir>/collected-logs")
    ap.add_argument("--ledger", type=Path, help=".next-tasks.json to join (read-only)")
    ap.add_argument("--report-dir", type=Path, help="default: <config dir>/triage-reports (local only)")
    ap.add_argument("--emit-transform", type=Path,
                    help="write a ledger-apply.sh transform here (ledger itself is never written)")
    ap.add_argument("--min-devices", type=int, default=2)
    ap.add_argument("--min-days", type=float, default=3.0)
    ap.add_argument("--today", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    today = a.today or date.today().isoformat()
    cfg = None
    if a.collected_dir is None or a.report_dir is None:
        cfg = default_config_dir()
    collected = a.collected_dir or cfg / "collected-logs"
    report_dir = a.report_dir or cfg / "triage-reports"

    ledger = None
    if a.ledger is not None:
        try:
            ledger = json.loads(a.ledger.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"log-triage: 원장을 못 읽음 ({a.ledger}: {e}) — 판정 불가", file=sys.stderr)
            return EXIT_USAGE

    read = read_collected(collected)
    if read.state != "ok":
        why = {"no_dir": "collected-logs 폴더 없음", "no_files": "기기 events 파일 0개",
               "no_lines": "events 파일에 줄 0개"}[read.state]
        print(f"log-triage: 미측정 — {why} ({collected}). «오류 0건»이 아니다 "
              "(서버 PC 에서 수집이 켜졌는지 확인).")
        return EXIT_NOT_MEASURED

    groups = group_events(read.events)
    cs = checksum(read, groups)
    rows = classify(groups, read.events, ledger, a.min_devices, a.min_days)
    info = {"today": today, "collected": str(collected),
            "min_devices": a.min_devices, "min_days": a.min_days}

    report_dir.mkdir(parents=True, exist_ok=True)
    md = report_dir / f"triage-{today}.md"
    md.write_text(render_report(read, rows, cs, info), encoding="utf-8")
    (report_dir / f"triage-{today}.json").write_text(json.dumps(
        {"info": info, "checksum": cs, "unreadable": dict(read.unreadable),
         "files": read.files, "rows": _rows_json(rows)}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    tally = defaultdict(int)
    for r in rows:
        tally[r["verdict"]] += 1
    print(f"log-triage: 서명 {len(rows)}개 — " +
          " · ".join(f"{v} {tally[v]}" for v in VERDICTS if tally[v]) +
          f" · 검산 lines={cs['lines_ok']} count={cs['count_ok']} · 보고서 {md}")

    if not (cs["lines_ok"] and cs["count_ok"]):
        print("log-triage: 검산 불성립 — 원장 델타를 만들지 않는다", file=sys.stderr)
        return EXIT_CHECKSUM

    if a.emit_transform is not None and ledger is None:
        print("log-triage: --emit-transform 은 --ledger 가 있어야 한다(중복 판정 불가)", file=sys.stderr)
        return EXIT_USAGE
    if ledger is not None:
        # Always leave the delta as plain JSON next to the report: PM-launched (resident / auto)
        # sessions are not allowed to run ledger-apply.sh and add these items with the Edit tool
        # (append each new_items[] object to tasks[]; for reopen[] set status pending, drop
        # closed_at, set reopened_at, prepend «line» to condition).
        delta = build_delta(rows, today)
        dj = report_dir / f"triage-{today}-ledger-delta.json"
        dj.write_text(json.dumps(delta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"log-triage: 원장 델타 JSON — 새 항목 {len(delta['new_items'])} · 재개 {len(delta['reopen'])} → {dj}")
    if a.emit_transform is not None:
        a.emit_transform.parent.mkdir(parents=True, exist_ok=True)
        a.emit_transform.write_text(render_transform(delta), encoding="utf-8")
        print(f"log-triage: 원장 델타 — 새 항목 {len(delta['new_items'])} · 재개 {len(delta['reopen'])} "
              f"→ {a.emit_transform} (적용: ledger-apply.sh {a.emit_transform} <레포> --allow-new-keys)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(run())
