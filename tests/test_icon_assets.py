"""함정 #44 회귀 — load_icon() 은 없는 size/color 조합이면 예외 없이 None 을 돌려준다.

2026-09-28 UX 검토 A2: transfer_window 완료 목록이 `load_icon(dir_arrow, size=14)` 를
불렀는데 사전 렌더 PNG 는 16/20/24/32 뿐이라 방향 화살표가 도입 이래 한 번도 안
보였다(호출부가 None 을 조용히 생략). ui/*.py 의 load_icon 호출을 AST 로 전수 스캔해
리터럴 size 가 실제 존재하는 크기인지, 이름·색까지 리터럴이면 파일이 있는지 확인한다.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PNG = ROOT / "ui" / "assets" / "icons" / "png"


def _available_sizes():
    return {int(p.name) for p in PNG.iterdir() if p.is_dir() and p.name.isdigit()}


def _load_icon_calls():
    for path in sorted((ROOT / "ui").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name != "load_icon":
                continue
            kw = {k.arg: k.value for k in node.keywords}
            icon = node.args[0] if node.args else kw.get("name")
            yield path.name, node.lineno, icon, kw.get("size"), kw.get("color")


def test_population_is_not_empty():
    # 스캔 자체가 깨져 0건이면 아래 검사가 전부 공허하게 통과한다
    assert sum(1 for _ in _load_icon_calls()) >= 10


def test_literal_sizes_exist_on_disk():
    sizes = _available_sizes()
    assert sizes, f"아이콘 크기 디렉토리를 못 찾음: {PNG}"
    bad = [
        f"{f}:{ln} size={s.value}"
        for f, ln, _i, s, _c in _load_icon_calls()
        if isinstance(s, ast.Constant) and s.value not in sizes
    ]
    assert not bad, f"존재하지 않는 아이콘 크기 요청(조용히 None) — 사용 가능 {sorted(sizes)}: {bad}"


def test_fully_literal_requests_resolve_to_files():
    missing = []
    for f, ln, icon, size, color in _load_icon_calls():
        if not (isinstance(icon, ast.Constant) and isinstance(icon.value, str)):
            continue
        s = size.value if isinstance(size, ast.Constant) else 20 if size is None else None
        c = color.value if isinstance(color, ast.Constant) else "text" if color is None else None
        if s is None or c is None:
            continue
        if not (PNG / str(s) / c / f"{icon.value}.png").is_file():
            missing.append(f"{f}:{ln} {icon.value} {s}/{c}")
    assert not missing, f"아이콘 파일 없음: {missing}"
