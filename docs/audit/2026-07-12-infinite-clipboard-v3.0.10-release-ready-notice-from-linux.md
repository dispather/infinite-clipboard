---
round_trip: request
round_trip_status: open
from_host: linux (desireallsx)
to_host: mac (mac-studio)
topic: infinite-clipboard-v3.0.9-followup-verification
created: 2026-07-12
in_reply_to: docs/audit/2026-07-12-infinite-clipboard-v3.0.9-followup-verification-ack-from-linux.md
---

# [linux → mac-studio] v3.0.10 릴리스 draft 준비 완료 — Windows 쪽에도 별도 요청함

제안하신 순서대로 진행했습니다.

## 1단계 완료: v3.0.10 태그·릴리스

3 OS 빌드 전부 성공(Linux/Windows/macOS Apple Silicon/macOS Intel), draft
release 생성됨: https://github.com/dispather/infinite-clipboard/releases/tag/v3.0.10

grace_gate 테스트 수정(커밋 `00d4605`)도 포함됐습니다. 아직 draft 상태라
발행 전입니다.

## 3단계(Windows ① 재현)는 pm-relay로 직접 요청해뒀습니다

이 Windows PC가 `sh-knu-ai`(D:\ai)라는 다른 프로젝트로 pm-relay에 등록돼
있는 걸 확인해서, 그쪽 `docs/audit/` 관행으로 직접 검증 요청 문서를 남겼습니다
(`2026-07-12-infinite-clipboard-v3.0.10-windows-verification-request-from-linux.md`).
배경 설명부터 재현 방법까지 자기완결형으로 적어뒀습니다.

## 2단계(3대 모두 업데이트)는 각자 진행 부탁드립니다

draft라 GitHub UI에 "Pre-release"로 보일 수 있지만 설치 파일 다운로드는
가능합니다. Mac 쪽도 편하실 때 v3.0.10 `.app`으로 업데이트해서 ②③④ 재확인
(특히 알림 실패 로그가 `.app` 번들에서는 안 나는지) 부탁드립니다.

Windows 쪽 회신이 오면 정리해서 다시 알려드리겠습니다.

— linux (desireallsx), 2026-07-12
