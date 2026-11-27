---
round_trip_status: closed
closed_by: "2026-11-28-delegation-449b43b0-response-to-project-manager.md"
closed_at: 2026-11-28
round_trip: request
ball: infinite-clipboard
expects_reply: true
title: "위임 — 분배 — infinite-clipboard::linux-plain-notify-dbus-signature"
date: 2026-11-28
from: project-manager
from_host: linux-desktop
to: [infinite-clipboard]
to_host: linux-desktop
msg_id: dlg-20261128-449b43b0
delegation_task_id: dlg-20261128-449b43b0
delegation_origin: dispatch
delegation_reply_to: 26c76499-2ec2-44f1-8abf-0f419d29b1c9
canonical: "project-manager:docs/audit/2026-11-28-delegation-449b43b0-request-to-infinite-clipboard.md"
mirror: "infinite-clipboard:docs/audit/2026-11-28-delegation-449b43b0-request-from-project-manager.md"
---

# 위임 — 분배 — infinite-clipboard::linux-plain-notify-dbus-signature

> PM 이 자동으로 맡긴 일이다(origin dispatch — 사람이 직접 지시한 위임이 아님)(작업 ID `dlg-20261128-449b43b0`). 처리 뒤 회신 문서를 `docs/audit/` 에 쓰고 이 문서를 `round_trip_status: closed` 로 닫는다.
> 상태는 PM 채널 도구 `reply` 에 `taskId: dlg-20261128-449b43b0` 와 `state` 를 함께 보내 올린다 — 착수 `working` · 사람 답 필요 `input_required` · 끝 `completed`(못 하면 `failed`, 안 맡으면 `rejected`). 그 도구가 taskId 인자를 모르면(구판) 보고 글에 작업 ID 를 적는다.

## 요청

PM 중앙 분배로 맡기는 하위 작업입니다(origin dispatch — 묶음 «Linux 일반 알림을 서명 명시 D-Bus 로», 코드 작업).
작업 ID: infinite-clipboard::linux-plain-notify-dbus-signature
제목: Linux 일반 알림(받기 완료·전체 로그 요청 결과 등)이 간헐 실패 — plyer 의 dbus-python 경로가 빈 actions/hints 서명을 못 추측
받는 곳: PM 이 이 문서로 띄운 새 작업 세션(모델 claude-opus-5-5[1m] · effort medium 로 띄움).
🔴 이 레포 상주 세션은 이 작업을 직접 하지 마세요 — 상주는 조율자입니다: 작업 세션이 종결을 올리면 PM 이 상주에 알리고, 상주는 회신 문서(in_reply_to)의 종료 보고 4칸을 검사해 빠진 것을 작업 세션·요청자에게 되묻습니다.

이 세션은 이 작업 전용 git worktree(브랜치 `dispatch/<위임 작업 ID>`)에서 떠 있습니다. 본 체크아웃은 다른 세션들이 쓰는 곳이라 손대지 않습니다.
할 일: 원장(.next-tasks.json)에서 infinite-clipboard::linux-plain-notify-dbus-signature 항목의 condition·note 를 «읽기만» 하고 그대로 구현하세요.
- 검증: 이 레포 루트엔 package.json 이 없습니다 — 루트에 npm 검증 스크립트(lint·typecheck)는 없습니다. 원장 항목에 검증 명령(`verify_cmd`·condition)이 적혀 있으면 그것이 우선, 없으면 그 레포 CLAUDE.md 의 검증·테스트 명령(예: pytest)과 관련 테스트까지.
  머지 뒤 ci 단계도 없습니다(루트 package.json 없음) — 위 검증이 마지막이니 여기서 끝내고, 머지 뒤 검증을 leftover 에 적지 마세요 — 적으면 task_ref 없는 leftover 로 묶음 머지가 막힙니다(11-22 라이브).
- 커밋은 이 브랜치에 `git commit -- <자기 파일>` 로. push 하지 않습니다.
- 진행 보고: 계획을 세우면 단계 3–10개를 reply 의 `steps`(`[{title, done}]`, 전체 목록)로 state=working 과 함께 올리고, 단계가 끝날 때마다 done 을 바꾼 전체 목록을 다시 보내세요(PM 분배 판 «N개 중 M개»). reply 도구에 steps 인자가 없으면(구판 브리지) 생략합니다.
🔴 하지 말 것(머지가 깨집니다):
- `.next-tasks.json` 수정·`/next` 전이·ledger 스크립트 — 원장 전이와 leftover 등록은 머지 뒤 조율자가 본 체크아웃에서 합니다.
- 상대 레포 미러(`write_project_file`)·초인종(`notify_session`) — 보낸 쪽과 받는 쪽이 같은 레포라 본 체크아웃에 같은 경로 파일이 생겨 머지가 막힙니다.
- 이 worktree 밖 경로에 쓰기.
- 사람 몫 단계(승인·화면 확인·비가역)에 닿으면 멈추고 reply 로 state=input_required.
- 끝나면(못 끝내도) 회신 문서를 «이 worktree 의» docs/audit/ 에 쓰고 이 브랜치에 커밋하세요 — frontmatter 에 `in_reply_to: <이 문서의 delegation_task_id>`, 본문에 아래 «종료 보고» 4칸을 «전부»(없으면 `- 없음`). produced 에 커밋 해시를 적습니다.
- leftover 는 원장에 등록하지 말고 항목마다 제안 ID 를 `task_ref: infinite-clipboard::<kebab-slug>` 로 적고 «⛳착수:» 한 줄을 붙이세요 — 조율자가 등록합니다.
- 그 뒤 reply 로 state=completed(못 하면 failed).

## 종료 보고

done:
- <합격 기준 중 충족한 것 — 근거(커밋·파일·명령 출력)>
leftover:
- <못 한 것 — 왜 못 했나 · 다음 사람이 알 것 · task_ref: infinite-clipboard::<당신 원장에 등록한 ID>>
blocked:
- <막힌 것 — 무엇이 없어서(입력·결정·권한)>
produced:
- <남긴 산출물 경로·커밋>

## 합격 기준

이 브랜치에 코드 커밋이 1개 이상 있고, 같은 브랜치의 회신 문서(in_reply_to = 이 작업 ID)에 종료 보고 4칸이 다 있으며, .next-tasks.json 은 브랜치에서 안 바뀌었다. 그리고 reply 로 종결 상태를 올렸다.
