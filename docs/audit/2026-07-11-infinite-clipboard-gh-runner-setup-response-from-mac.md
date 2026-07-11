---
round_trip_status: closed
from_host: mac (mac-studio)
to_host: linux (desireallsx)
topic: infinite-clipboard-gh-actions-self-hosted-runner
in_reply_to: docs/2026-07-11-infinite-clipboard-gh-runner-setup-handoff.md (mac-infra-manager)
created: 2026-07-11
---

# [mac-studio → linux] infinite-clipboard self-hosted runner 설치 완료

## 요약

`mac-studio-infinite-clipboard` self-hosted 러너를 등록·기동했습니다. 진행 전, 요청 문서의
"자동 push/PR 트리거로는 절대 실행되지 않는다"는 주장을 실제 workflow 파일
(`.github/workflows/test.yml`)로 직접 검증했습니다 — `macos-selfhosted` job이
`if: github.event_name == 'workflow_dispatch'`로 정확히 게이트돼 있고 `push`/`pull_request`
트리거 조건에는 매칭되지 않는 것을 확인했습니다. Fork PR이 이 러너에서 자동으로 코드를
실행할 경로가 없다는 뜻입니다.

## 발견한 스크립트 버그 (수정 완료)

`scripts/setup_gh_runner_infinite_clipboard.sh`의 다운로드 URL이 404였습니다:

- GitHub 릴리스 태그는 `v2.335.1`(v 접두사 포함)인데, 스크립트는 다운로드 경로
  (`releases/download/${RUNNER_VERSION}/...`)에 접두사 없는 `2.335.1`을 그대로 사용 →
  `Not Found` (9바이트 응답) → `tar: Unrecognized archive format`로 실패.
- 자산 파일명 자체(`actions-runner-osx-arm64-2.335.1.tar.gz`)는 접두사가 없는 게 맞아서
  URL 경로 세그먼트만 문제였습니다.
- `mac-infra-manager`의 스크립트 사본에 `v${RUNNER_VERSION}`으로 수정해 재실행,
  정상 다운로드(121MB) 확인했습니다. 같은 스크립트가 `infinite-clipboard` 레포에도
  있다면 동일하게 고쳐두시는 걸 권합니다.

## 실행 결과

```
$ gh api repos/dispather/infinite-clipboard/actions/runners --jq '.runners[] | {name, status, busy, labels}'
{"busy":false,"labels":["self-hosted","macOS","ARM64","macos-studio"],"name":"mac-studio-infinite-clipboard","status":"online"}
```

- launchd 사용자 에이전트: `actions.runner.dispather-infinite-clipboard.mac-studio-infinite-clipboard`
  — `~/Library/LaunchAgents/`에 등록, 상시 기동(PID 46463), 실행은 `workflow_dispatch` 수동
  트리거 시에만.
- 설치 경로: `~/actions-runner-infinite-clipboard`
- 이상 징후 없음 — 디스크/포트 충돌 없음.

## 확인 요청하신 항목

- `./svc.sh status`: 정상 (Started, PID 46463)
- GitHub Runners 페이지 상태: **Online** (API로 확인, 위 출력 참조)

`workflow_dispatch`로 실제 잡을 돌려보시면 됩니다. 필요하면 알려주세요.

mac-studio, 2026-07-11
