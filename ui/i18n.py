"""UI 문자열 번역 — 한국어 원문이 SSOT, 영어 매핑만 딕셔너리로 보관.

새 UI 문자열 추가 시 STRINGS["en"]에 항목을 안 넣어도 t()가 원문(한국어)을
그대로 반환한다 — KeyError로 죽지 않음. 로그 메시지·코드 주석은 이 모듈의
대상이 아니다(CLAUDE.md 정책, 한국어 유지).
"""

import locale
import os
import platform

SUPPORTED_LANGUAGES = ("ko", "en")

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # ui/settings_window.py
        "설정": "Settings",
        "저장하고 재시작": "Save & Restart",
        "취소": "Cancel",
        "저장 경로 선택": "Select Save Folder",
        "Tailscale 확인 중…": "Checking Tailscale…",
        "연결": "Connection",
        "모드": "Mode",
        "서버": "Server",
        "클라이언트": "Client",
        "호스트": "Host",
        "자동": "Auto",
        "포트": "Port",
        "노출": "Exposure",
        "Tailscale 자동": "Tailscale Auto",
        "모든 인터페이스": "All Interfaces",
        "⚠ 프로토콜은 평문 — 같은 물리 LAN 의 제3자가 스니핑 가능": (
            "⚠ Protocol is plaintext — a third party on the same physical LAN can sniff it"
        ),
        "인증": "Authentication",
        "인증 키": "Auth Key",
        "인증 키 표시/숨기기": "Show/hide auth key",
        "Tailscale 피어 로그 표시": "Show Tailscale peer logs",
        "인증에는 영향 없음 — HMAC 은 항상 필수": "No effect on authentication — HMAC is always required",
        "기기": "Device",
        "이름": "Name",
        "저장 경로": "Save Path",
        "폴더 선택": "Select Folder",
        "충돌 처리": "Conflict Handling",
        "덮어쓰기": "Overwrite",
        "건너뛰기": "Skip",
        "시점 추가": "Add Timestamp",
        "번호 추가": "Add Number",
        "임시 정리": "Temp Cleanup",
        "시간 후": "hours",
        "지금 정리": "Clean Now",
        "언어": "Language",
        "재시작 후 적용됩니다": "Applies after restart",
        "자동 실행": "Autostart",
        "OS 시작 시 자동 실행": "Launch at OS startup",
        "임시 파일 정리": "Clean Up Temp Files",
        "정리할 항목이 없습니다.": "Nothing to clean up.",
        "{deleted}개 항목 삭제\n{size} 회수": "{deleted} items deleted\n{size} reclaimed",
        "정리 실패: {e}": "Cleanup failed: {e}",

        # ui/history_window.py
        "방금": "just now",
        "{n}초 전": "{n}s ago",
        "{n}분 전": "{n}m ago",
        "{n}시간 전": "{n}h ago",
        "{n}일 전": "{n}d ago",
        "클립보드 이력": "Clipboard History",
        "이력": "History",
        "텍스트 항목을 클릭하면 다시 복사됩니다": "Click a text item to copy it again",
        "이력 파일이 손상되어 초기화됐어요": "History file was corrupted and reset",
        "기존 파일은 clipboard_history.json.corrupt-* 로 백업됨": (
            "Old file backed up as clipboard_history.json.corrupt-*"
        ),
        "이력이 비어 있어요": "History is empty",
        "어느 PC에서든 복사하면 여기 쌓입니다": "Copy on any PC and it shows up here",

        # ui/transfer_window.py
        "파일 전송": "Transfers",
        "받기": "Receive",
        "진행 중": "In Progress",
        "완료": "Done",
        "진행 중인 전송이 없어요": "No transfers in progress",
        "파일을 다른 PC에서 복사하면 여기 표시됩니다": "Files copied on another PC will appear here",
        "완료된 전송이 아직 없어요": "No completed transfers yet",
        "성공한 전송 기록이 여기 쌓입니다": "Successful transfers will appear here",
        "전송 취소": "Cancel transfer",
        "준비 중...": "Preparing...",
        "취소 중...": "Cancelling...",
        "파일": "File",
        "받는 중": "Receiving",
        "재시도": "Retry",
        "실패 — 재시도 가능": "Failed — retry available",
        "완료 중...": "Finishing...",
        "폴더 열기": "Open Folder",
        "{n}초 남음": "{n}s left",
        "{m}분 {s}초 남음": "{m}m {s}s left",
        "{n}시간 남음": "{n}h left",

        # ui/about_window.py
        "정보": "About",
        "Tailscale LAN 클립보드/파일 공유": "Real-time clipboard & file sharing over Tailscale LAN",
        "닫기": "Close",

        # main.py (_notify)
        "버전 불일치로 연결 실패": "Connection Failed: Version Mismatch",
        "받기 실패": "Receive Failed",
        "파일 받기": "Receive File",
        "받기 완료": "Received",
        "덮어쓰기 실패": "Overwrite Failed",
        "상대 PC 의 Infinite Clipboard 버전이 다릅니다 — 양쪽 모두 최신 버전으로 업그레이드하세요": (
            "The other PC is running a different version of Infinite Clipboard — "
            "upgrade both to the latest version"
        ),
        "{name} — 원본에서 받을 수 없음 (클립보드 유지)": "{name} — couldn't fetch from source (clipboard kept)",
        "{name} — 전송 창에서 [받기]": "{name} — use [Receive] in the Transfers window",
        "{name} — {message} (재시도 가능)": "{name} — {message} (retryable)",
        "{name} — {message}": "{name} — {message}",
        "{name} — {saved}개 → {dest_dir}": "{name} — {saved} item(s) → {dest_dir}",
        "{names} — 기존 파일 유지됨 (교체 안 됨, 사용 중이거나 권한 문제)": (
            "{names} — existing files kept (not replaced; in use or permission issue)"
        ),
    },
}


def detect_os_locale() -> str:
    """OS UI 언어를 "ko"/"en" 으로 매핑. 실패 시 "en" 폴백.

    macOS 는 .app 번들로 실행되면 LaunchServices 가 LANG/LC_CTYPE 환경변수를
    주입하지 않는다(core/clipboard_manager.py 의 pbcopy 한글 소실 버그와 동일
    원인 — CLAUDE.md 함정 #14). 그래서 macOS 는 `defaults read -g AppleLocale`
    로 시스템 로케일을 직접 조회하고, 그 외 OS 는 locale 모듈을 쓴다.
    """
    try:
        if platform.system() == "Darwin":
            import subprocess

            result = subprocess.run(
                ["defaults", "read", "-g", "AppleLocale"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            value = result.stdout.strip()
        else:
            locale.setlocale(locale.LC_ALL, "")
            value = locale.getlocale()[0] or os.environ.get("LANG", "")
        return "ko" if (value or "").lower().startswith("ko") else "en"
    except Exception:
        return "en"


def get_language(config) -> str:
    """config.language 가 비어있으면 OS 자동감지, 아니면 그 값(검증은
    config._validate_and_clamp 가 이미 수행했다고 가정)."""
    lang = getattr(config, "language", "") or ""
    if lang in SUPPORTED_LANGUAGES:
        return lang
    return detect_os_locale()


def t(text: str, lang: str) -> str:
    """한국어 원문 text 를 lang 으로 번역. 매핑 없으면 원문 그대로 반환."""
    if lang == "ko":
        return text
    return STRINGS.get(lang, {}).get(text, text)
