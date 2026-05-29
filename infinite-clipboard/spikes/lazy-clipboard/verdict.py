"""판정 계약 — 모든 OS 스파이크 스크립트 공통.

핵심: lazy_proven 을 스크립트가 임의로 True 로 못 박지 못하게, origin 의 raw 연결 시각과
copy/paste 시각으로부터 **계산**한다 (반증가능). emit() 은 raw 타임스탬프도 함께 출력해
사후 검증을 가능케 한다.

verdict 규칙:
- 인프라가 정상 실행되면 exit 0 (FAIL/NEEDS-MANUAL 도 0). FAIL 은 유용한 데이터지 CI 에러가 아님.
- 진짜 크래시(deps 부재, import 실패, 컴포지터 미기동)만 exit != 0 (빨강).
- verdict=PASS 인데 lazy_proven=False 또는 bytes_match=False 면 모순 → 스크립트가
  PASS 를 주장하기 전 두 조건을 먼저 확인하고, 어기면 NEEDS-MANUAL 로 강등할 것.
"""
import json
import os

PASS = "PASS"
NEEDS_MANUAL = "NEEDS-MANUAL"
FAIL = "FAIL"


def compute_lazy_proven(conn_times, t_copy_done: float, t_paste_issued: float) -> bool:
    """copy~paste 사이 origin 연결 0건 + paste 이후에만 fetch 발생해야 True.

    - conn_times 비어 있으면 fetch 자체가 없었던 것 → False
    - t_paste_issued 이전 연결이 1건이라도 있으면 eager(=copy 시점 prefetch) → False
    - 첫 연결이 paste 이후여야 lazy
    """
    if not conn_times:
        return False
    # 계약: copy~paste 구간에 origin 연결이 1건도 없어야 함 (eager prefetch 배제)
    suspicious = [t for t in conn_times if t_copy_done <= t < t_paste_issued]
    first = min(conn_times)
    return len(suspicious) == 0 and first > t_paste_issued


def emit(
    os_name: str,
    verdict: str,
    *,
    conn_times,
    t_copy_done: float,
    t_paste_issued: float,
    bytes_match: bool,
    notes: str = "",
) -> int:
    """판정 1줄 JSON 을 stdout 마지막 줄 + GITHUB_STEP_SUMMARY 에 출력. exit code 반환."""
    lazy = compute_lazy_proven(conn_times, t_copy_done, t_paste_issued)
    rec = {
        "os": os_name,
        "verdict": verdict,
        "bytes_match": bool(bytes_match),
        "lazy_proven": lazy,
        "notes": notes,
        "raw": {
            "t_copy_done": t_copy_done,
            "t_paste_issued": t_paste_issued,
            "conn_times": list(conn_times),
        },
    }
    print(json.dumps(rec, ensure_ascii=False))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(
                    f"- **{os_name}**: `{verdict}` "
                    f"lazy={lazy} bytes={bool(bytes_match)} — {notes}\n"
                )
        except OSError:
            pass
    return 0  # 인프라 정상이면 FAIL/NEEDS-MANUAL 도 0
