"""core/_wayland_proto — vendored pywayland 프로토콜 바인딩 (생성물, 손수 편집 금지).

`core/lazy_wayland.py` 가 쓰는 Wayland 인터페이스의 pywayland.scanner 생성 코드다.
프로덕션은 빌드/런타임에 wayland XML(특히 staging `ext-data-control-v1.xml`)이
없을 수 있어(구버전 wayland-protocols) **vendoring** 한다 — 사용자 결정 2026-05-29.
파일 자체는 순수 Python(런타임에 `pywayland` 패키지만 있으면 됨)이라 PyInstaller
번들에 일반 모듈로 들어간다 (build/infinite-clipboard.spec Linux datas/hiddenimports).

포함 프로토콜:
  - wayland/            : wayland.xml 코어 (WlSeat/WlDisplay/WlRegistry 등, 23개)
  - ext_data_control_v1/: ext-data-control-v1.xml (백그라운드 클립보드 매니저 프로토콜 —
                          입력 focus serial 불필요. lazy clipboard 의 전제)

── 재생성 (프로토콜 갱신/pywayland 버전 변경 시) ──────────────────────────
시스템에 wayland-protocols(staging 포함) + libwayland + pywayland 가 있어야 한다:

  WL=$(find /usr/share/wayland -name wayland.xml | head -1)
  XML=$(find /usr/share/wayland-protocols -iname ext-data-control-v1.xml | head -1)
  python -m pywayland.scanner -i "$WL" "$XML" -o core/_wayland_proto
  touch core/_wayland_proto/__init__.py   # scanner 가 덮어쓰므로 이 docstring 복원

생성 모듈은 전부 패키지 상대 import(`from ..wayland import WlSeat`) 라서 패키지명/
위치를 바꿔도 구조만 보존하면 동작한다. spike(`spikes/lazy-clipboard/_proto/`,
실제 kwin + CI 검증)의 검증된 산출물을 그대로 옮겼다.
"""
