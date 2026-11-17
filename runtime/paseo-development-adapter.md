# Paseo 개발 흐름 연결의 사전 조사

2026-11-18 설치 CLI 0.16.0의 도움말과 배포된 CLI 구현을 읽은 결과다. 실제 agent를 생성하거나 앱 설정을 바꾸지 않았다. 현재 컨트롤러의 Paseo adapter는 계속 `unavailable`이다.

## 확인한 계약

| 항목 | 확인한 지원 | 연결 시 주의점 |
| --- | --- | --- |
| 생성 | `paseo run --workspace <id> --no-wait --label <key=value> <prompt>` | 실행 전 예약과 정확한 host/workspace 고정 필요 |
| 응답 유실 후 조회 | `paseo ls --all --label <key=value> --json` | label 필터는 지원하지만 유일성·중복 생성 방지는 보장하지 않음 |
| 정확한 agent 조회 | `paseo inspect <id> --json` | 전체 ID로 조회. 현재 CLI 출력에는 canonical provider session ID가 없음 |
| 완료 대기 | `paseo wait <id>` | idle은 프로세스 종료나 원래 AC 통과의 증거가 아님 |
| 중단 | `paseo stop <id>` | 실행 중이면 interrupt, idle이면 no-op. 정확한 종료 증거와 구분 |
| 사용량 | inspect의 `LastUsage` | 누락된 세부값을 0으로 표시하는 변환이 있어 원본 측정 여부·요청 귀속을 별도로 확인해야 함 |

확인 근거는 `run/ls/inspect/wait/stop --help`와 배포 패키지 `@getpaseo/cli/dist/commands/agent/`의 같은 이름 `.js` 파일이다. run 구현은 labels를 createAgent에 전달한다. inspect의 출력 변환은 provider·agent ID·cwd·상태를 노출하지만 provider 세션 ID를 노출하지 않는다. 이 사실을 서버 전체 API의 기능 부재로 확대하지 않는다. CLI에 사용자가 지정하는 생성 idempotency key는 확인되지 않았다.

## 다음 구현의 작은 경계

먼저 원본 provider 세션 ID와 turn 종료를 반환하는 공식 API 계약을 확보한다. 현재 환경에는 Paseo MCP 도구가 연결돼 있지 않으므로 설치돼 있다는 이유만으로 MCP 기능을 사용했다고 보고하지 않는다. CLI/SDK의 실제 지원과 대상 host·workspace를 대조한 다음 구현 범위를 확정한다. 다른 앱의 세션 파일을 탐색해 빈 계약을 메우지 않는다.

labels는 복구 후보를 찾는 수단으로만 설계한다. 제안 경계는 다음과 같으며 아직 구현·실증하지 않았다.

1. 비공개 예약에 host/workspace/request/stage/operation과 고유 label을 저장하고 최초 한 번만 제출한다. label에 개인 정보·프롬프트를 넣지 않는다.
2. 응답 유실 시 같은 label로 같은 host에서 조회한다. 0건이면 pending, 2건 이상이면 충돌로 남기고 재생성하지 않는다. 1건도 cwd/workspace/provider·생성 범위와 정확한 ID를 대조하기 전에는 연결하지 않는다.
3. 실제 canonical provider 세션 ID로 개발자·검증자·조율자가 서로 다른 세션인지 검사한다. agent ID만 다르다고 독립 검토로 인정하지 않는다.
4. 작업 turn의 실제 종료·결과와 프로세스 종료를 각각 기록한다. Paseo의 유지형 세션을 Orca의 종료 모델에 억지로 맞추지 않으며, idle/interrupt를 exited로 변환하지 않는다.
5. 사용량은 검증된 요청 구간만 수입한다. 계정 quota·누적/최근 사용량·누락값의 표시상 0을 배정별 측정값으로 복제하지 않는다.

## 수용 기준

구현 전에는 공식 응답 schema와 오류·응답 유실·조회 지연의 계약을 보관한다. 구현 검증에는 재시작 후 중복 제출 없음, 조회 0/1/복수건, host/workspace 불일치, 동일 provider 세션 거부, permission 대기, idle과 종료 구분, 원래 AC/snapshot 연결, 사용량 누락·replay를 포함한다. 실제 생성 실증은 등록된 별도 요청과 명시한 배정 한도를 사용한다. 이전 Orca 실증의 2배정 한도를 재사용하지 않는다.

[개발 컨트롤러](development-workflow.md) · [Paseo 환경 가이드](environments/paseo.md)
