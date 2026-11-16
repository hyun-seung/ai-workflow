# worker의 Orca IPC 권한 요약

**작업에서 Orca CLI 조율이 필요할 때만 읽는다.** 이 문서는 권한을 부여하거나 설정을 바꾸지 않는다. 현재 host·CLI·task/dispatch 범위와 명시한 실행 한도를 따른다.

- 부모의 접속 성공을 자식의 권한으로 추정하지 않는다. 자기 실행 지침에 노출된 승인 방식·경계를 확인하고, 알 수 없으면 unknown으로 보고한다. 확인을 위해 개인 설정·다른 세션을 탐색하지 않는다.
- `runtime_access_denied`·EPERM은 sandbox 접속 실패다. 자동 승인 검토의 명시적 거절과 구분한다. 설치 Orca 스킬은 이 오류에 **동일 CLI의 같은 필요한 동작을 정식 권한 경로로 요청**하도록 안내한다. 알려진 차단을 확인하려고 먼저 실패를 반복하지 않는다.
- 정식 승인 요청도 현재 작업이 허용한 범위 안에서만 한다. “새 배정 재시도 없음”과 “같은 IPC 명령의 정식 승인 요청 허용”을 작업 입력에 구분한다. 명시적인 질문 재전송·모든 재시도 금지나 상위 제한이 있으면 그것을 우선하고 blocked로 보고한다. 모호한 문구를 허용으로 확대하지 않는다.
- 승인 검토 거절·승인 경로 부재에는 같은 동작을 다른 앱·CLI·프로세스나 전역 설정 변경으로 우회하지 않는다. 승인을 기다리는 동안 `ask/send`를 다시 보내지 않는다. 전달 결과가 unknown/timeout이면 미전송으로 가정하지 않고, 공식 영수증의 복구 경로를 따른다.
- `ask`가 접수되면 영수증의 next를 따르고 턴을 끝낸다. 승인이나 수신을 확인하려고 polling·sleep·새 worker를 만들지 않는다. 실패를 정상 질문 전송·작업 성공으로 보고하지 않는다.

반복 사람 승인을 줄이는 Auto-review는 적격 승인 요청을 검토하는 방식이며 sandbox 자체를 해제하지 않는다. 실제 자식 적용을 확인하기 전에는 자동 조율 준비 완료로 표시하지 않는다. [OpenAI Docs: Auto-review](https://learn.chatgpt.com/docs/sandboxing/auto-review)

이번 실행이 쓰는 `orca skills get orchestration`과 설치 스킬의 접속 실패 지침을 우선한다. 명령별 help로 지원을 확인하며 없는 승인 플래그를 만들지 않는다. 새 세션 기본값과 기존 채팅 모드는 별개다. [Orca 설정](https://orca.dev/docs/settings#agents), [native chat](https://orca.dev/docs/agents/native-chat#structured-chat)

범위 충돌·지속 설정 변경이 필요할 때만 [상세 권한 정책](permission-policy.md)을 확인한다. 필요한 작업 입력은 [IPC checkpoint 양식](../templates/orca-ipc-checkpoint.md)을 참고한다.
