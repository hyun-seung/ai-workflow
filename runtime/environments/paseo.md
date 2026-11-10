# Paseo 환경 연결

공통 업무 규칙·프로젝트 지침을 유지하고, 에이전트 생성·작업 전달·상태·결과는 Paseo의 네이티브 기능으로 처리한다. Paseo라는 환경과 Codex 등의 provider/model을 구분한다.

## 버전과 공식 근거

2026-11-10 CLI/daemon 0.15.4에서 --help, status/run/send/inspect/logs 도움말과 연결 응답을 확인했다. 연결 확인과 실제 에이전트 생성·결과 수신 검증은 별개다.

- [공식 CLI 문서](https://paseo.sh/docs/cli)
- [공식 provider 설명](https://paseo.sh/docs/providers)
- 설치된 paseo 스킬과 MCP 도구 메타데이터, paseo --help 및 하위 명령 --help

온라인 문서가 설치 버전과 다르면 실제 도움말을 기준으로 지원 차이를 기록하고 중단하거나 명시적으로 조정한다.

## 현재 세션·host·프로젝트 확인

1. MCP가 연결됐으면 list_hosts로 self와 대상 serverId를 확인한다. 도구가 없으면 조용히 다른 앱을 쓰지 말고, 명시적인 Paseo CLI 연결을 확인한다.
2. CLI에서는 paseo status --json으로 daemon 연결을 확인한다. 원격 대상은 --host 또는 확인한 PASEO_HOST의 대상과 serverId를 대조한다. endpoint·페어링 URL·자격 증명을 공개하지 않는다.
3. PASEO_AGENT_ID가 있으면 해당 세션 근거를 사용하고, paseo inspect <id> --json으로 현재 agent와 workspace·provider를 확인한다. 다른 agent를 임의로 현재 agent라고 선택하지 않는다.
4. 현재 세션 근거가 없으면 --environment paseo와 명시한 host/workspace를 사용한다. 앱 설치나 daemon 연결만으로 현재 세션의 부모 관계를 확정하지 않는다.

MCP의 {serverId, agentId}를 항상 함께 보존한다. CLI 작업에도 선택한 host를 일관되게 적용한다. 원격 path·workspace ID는 그 host의 것이며 로컬 프로젝트 경로를 그대로 가정하지 않는다.

## 공통 책임을 Paseo 기능에 연결

| 공통 책임 | MCP가 연결된 경우 | 확인한 CLI |
| --- | --- | --- |
| 작업 공간·provider 확인 | list_workspaces, list_profiles와 notes, list_providers/inspect_provider | workspace/provider 하위 help와 ls, provider models |
| 에이전트 생성·첫 작업 | create_agent의 initialPrompt | paseo run --workspace <id> --provider <provider> <prompt> |
| 후속 전달 | send_agent_prompt | paseo send <id> --prompt-file <path> |
| 상태 | get_agent_status/get_agent_activity | paseo inspect <id> --json |
| 결과 | notifyOnFinish 완료 알림 | paseo logs <id> --json과 실제 상태/결과 대조 |

실제 도구가 노출됐는지 확인하고 스킬의 매개변수를 적용한다. profile을 사용할 때는 provider/model·mode·thinking·feature를 실제 생성 인자로 풀어 전달한다. MCP에 profile이라는 생성 인자가 있다고 가정하지 않는다.

CLI의 run은 기본적으로 완료를 기다리고 --no-wait는 접수 후 반환한다. --output-schema 지원은 현재 help에 있지만 --no-wait와 조합할 수 없다. 편한 쪽으로 의미를 바꾸지 않는다. 세션 안의 기본 workspace·parent와 바깥 CLI의 생성 기본값도 다르므로 target을 명확히 한다.

MCP의 notifyOnFinish를 사용했다면 스킬대로 턴을 종료하고 알림을 기다린다. CLI를 쓸 때도 접수·시작·idle·AC 통과를 구분하고, 기존 세션에 중복 작업을 보내지 않는다.

## 결과·사용량 한계

원래 AC와 변경 파일·버전·테스트 근거를 결과와 연결한다. 도구가 제공하는 구조화된 출력도 의미 검증을 대신하지 않는다. 계정 quota를 보여주는 paseo usage는 개별 agent의 토큰·비용 실측이 아니다. 실제 측정 출처가 없으면 unavailable을 기록한다.

설치 버전이 필요한 생성·전달·결과 기능을 제공하지 않거나 권한·연결을 확인할 수 없으면 중단한다. Orca 또는 직접 Codex 실행으로 대체하지 않는다.
