"""
Linux 전용 PyInstaller runtime hook.

번들된 Python 이 시스템 `python-gobject` (gi / AppIndicator 바인딩) 를
찾을 수 있도록 `/usr/lib/python3.*/site-packages` 를 sys.path 에 추가한다.

왜 번들에 포함하지 않는가:
- gi 는 `libgirepository` + `.typelib` 파일 + 여러 시스템 라이브러리에 의존.
- Arch/CachyOS 에서는 `python-gobject` + `libayatana-appindicator` 가 이미
  pacman 의존성으로 설치되므로 시스템 경로 참조가 가장 안전.
- 번들 Python 버전이 시스템 Python 버전과 같을 때 ABI 호환되어 정상 동작.

이 hook 은 PyInstaller 가 main.py 를 실행하기 **직전** 에 호출되므로,
main.py 상단의 `import gi` / PYSTRAY_BACKEND 자동 감지 로직이 성공한다.
"""

import sys

if sys.platform.startswith("linux"):
    import glob
    import os

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    # 1순위: 번들 Python 버전과 같은 시스템 경로
    candidates = [
        f"/usr/lib/python{py_ver}/site-packages",
        f"/usr/lib64/python{py_ver}/site-packages",
    ]
    # 2순위: 버전 안 맞으면 최신 python3.* 경로(내림차순)
    candidates.extend(sorted(glob.glob("/usr/lib/python3.*/site-packages"), reverse=True))

    for path in candidates:
        if os.path.isdir(path) and path not in sys.path:
            sys.path.append(path)

    # Wayland+Plasma 에서 pystray 가 반드시 AppIndicator 백엔드를 쓰도록 기본값 설정
    # (main.py 상단에서 gi 감지 후 설정하는 로직의 안전망)
    os.environ.setdefault("PYSTRAY_BACKEND", "appindicator")
