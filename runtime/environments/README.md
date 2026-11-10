# 실행 환경 선택과 시작

공통 규칙은 [AGENTS.md](../../AGENTS.md), [역할](../../roles/README.md), [작업 양식](../../templates/README.md)이다. 이 디렉터리는 도구별 연결만 다룬다. [설계 결정](../architecture.md)과 [프로젝트 지침](../../projects/README.md)을 함께 적용한다.

## 최소 시작 명령

저장소 루트에서 실행한다. 에이전트 생성·프롬프트 전송·모델 호출·앱 시작·설정 변경은 하지 않는다.

    python3 scripts/startup.py --project /path/to/project

선택 우선순위는 --environment 인자 → 프로젝트 시작 설정 → 세션 근거다. 명시값이 auto이면 자동 추론을 요구한다. 자동 추론은 ORCA_AGENT_SESSION_ID 또는 PASEO_AGENT_ID 중 정확히 하나가 있을 때만 후보를 선택한다. ORCA_CLI_COMMAND, PASEO_HOST, CLI 설치, 앱 실행, Codex 설치만으로는 현재 환경을 정하지 않는다.

    python3 scripts/startup.py --project /path/to/project --environment orca
    python3 scripts/startup.py --project /path/to/project --environment paseo

프로젝트 루트의 .ai-workflow-startup.json 또는 --config로 지정한 JSON을 사용한다. [설정 스키마](../../schema/startup.schema.json), [가상 복수 저장소 양식](../../templates/project.startup.json)을 참고한다. 기존 .ai-workflow.json과 별개이며 새 환경 필드를 기존 Codex 실행 설정에 끼워 넣지 않는다.

## 확인 수준

- Orca: 선택한 CLI의 status --json에서 ready/connected/reachable을 확인한다. 자동 모드에서는 응답 caller의 live·세션 ID까지 환경 정보와 대조한다.
- Paseo: status --json에서 connectedDaemon=reachable과 serverId를 확인한다. PASEO_AGENT_ID는 공식 문서의 세션 근거이지만 status만으로 해당 agent의 생존·대상 host를 확인하지 못하므로 session_identity_confirmed=false로 반환한다. 네이티브 가이드에서 정확한 host·agent·workspace를 추가 대조한다.
- 명시적 선택은 호출자가 원한 환경을 뜻하며 세션 확인을 대신하지 않는다. 공급자/모델 요청값도 지원 확인 결과가 아니다.
- project_scope_confirmed=false: 지침 경로 확인은 환경의 작업 공간이 그 프로젝트에 바인딩됐다는 증명이 아니다.

출력은 선택한 공통·환경 가이드, 프로젝트/자식 저장소 지침 경로와 연결 요약이다. 자동으로 모든 도구의 프롬프트에 삽입되지는 않는다. 사용하는 도구의 지침 기능이나 작업 전달에 명시적으로 포함해야 한다. 역할별 문서는 실제 맡길 역할만 추가한다.

## 실패 처리

세션 정보가 없거나 양쪽 환경을 동시에 가리키면 명시적 선택을 요구한다. 선택한 CLI 부재, 연결·권한·timeout 오류, 알 수 없는 응답, 필요한 가이드나 지정한 프로젝트 지침 부재는 종료 2다. 다른 환경·다른 Orca 실행 파일·단독 Codex로 fallback하지 않는다.

샌드박스가 접속을 거부하면 해당 실행 환경의 정식 승인 절차로 같은 조회를 다시 한다. 앱 재시작이나 다른 프로세스로 우회하지 않는다. 원시 상태 응답에는 host·개인 경로가 있을 수 있으므로 비공개로 취급한다.

지원 확인은 가이드 선택/연결에 한정한다. 생성·작업 전달·상태·결과·중단 기능과 공급자 접근 가능 여부는 각 가이드의 설치 버전 도움말·공식 문서에서 확인한다.

- [Orca 연결](orca.md)
- [Paseo 연결](paseo.md)
