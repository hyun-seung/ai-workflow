# Orca 환경 연결

공통 업무 규칙·프로젝트 지침은 그대로 적용한다. 여기서는 Orca가 제공하는 작업 공간·worker·메시지·결과 lifecycle을 사용한다. 공급자와 모델은 환경 선택과 구분한다.

## 공식 가이드와 연결 확인

2026-11-10 설치 버전 1.4.255의 배포 가이드와 help를 확인했다. 세션 시작 때 버전·연결을 확인하고 필요한 가이드를 읽는다. 같은 세션·버전에서 이미 확인한 내용은 재사용하며, 버전 변경·미지원 응답·새 기능을 사용할 때 해당 부분을 다시 확인한다.

1. 세션의 ORCA_CLI_COMMAND가 있으면 그 실행 파일을 고정한다. 없으면 설치된 orca-cli 스킬의 플랫폼별 선택 규칙을 따른다. 선택한 파일이 실패해도 다른 실행 파일로 바꾸지 않는다.
2. 선택한 CLI로 skills get orca-cli --json 및 skills get orchestration --json을 읽는다. 아래 ORCA는 그 실행 파일을 뜻하는 표기이며 그대로 실행하는 이름이 아니다.
3. ORCA status --json으로 ready/connected와 caller를 확인한다. ORCA worktree current --json으로 현재 폴더·작업 공간을 확인하고 명시 프로젝트와 다르면 대상 selector를 확인한다.
4. runtime_access_denied/EPERM은 정식 권한 절차로 해결한다. 재시작으로 우회하지 않는다.

배포 앱 자체의 skills get 가이드는 해당 버전의 공식 동작 근거다. 시작 단계에서 출력된 환경 가이드를 읽는 것만으로 이 배포 가이드를 대체하지 않는다.

## 공통 책임을 Orca 기능에 연결

| 공통 책임 | 확인한 Orca 인터페이스 |
| --- | --- |
| 결과를 받을 작업 생성·위임 | orchestration worker-start --spec-file <path> --worktree current --agent <agent> --json |
| 작업 전달/후속 지시 | orchestration send --to dispatch:<id> --subject <text> --body <text> |
| worker 상태 확인 | 설치 도움말의 orchestration worker-show / worker-list. 필요할 때 정확한 dispatch를 조회 |
| 결과·질문 수신 | 네이티브 worker_done/question 알림과 wake가 지정한 check. 질문은 orchestration reply |
| 결과 읽기·검증 | 필요 시 worker-read --source auto 또는 transcript, 결과 AC·파일 버전·테스트 증거 대조 |

처음 사용하는 하위 명령은 --help와 필요한 배포 reference를 읽는다. receipt가 접수됐다는 사실과 worker 실행·완료를 구분한다. 결과가 필요한 위임은 orchestration을 사용하고 worker 터미널에 직접 steer하지 않는다.

worker-start 뒤에는 안내된 next를 따르고 턴을 끝내 네이티브 알림을 기다린다. poll/watch 루프로 check 메시지를 소비하지 않는다. 실패하면 재호출부터 하지 말고 failedStage와 recovery-and-cleanup reference를 확인한다.

provider는 --agent로 선택하며 모델·effort 지원은 공급자와 버전에 따라 다르다. 사용자가 명시한 모델이 있으면 그대로 전달할 수 있는지 확인한다. native skill이 요구하는 모델 선택 절차와 충돌하면 이를 드러낸다. 지원하지 않는 옵션을 조용히 생략하거나 다른 모델로 바꾸지 않는다.

## 승인 대기 알림 처리

알림 본문만으로 지금도 멈춰 있다고 단정하지 않는다. 먼저 정확한 dispatch의 `worker-show --full --json`에서 현재 상태와 `observation.agentWait`을 확인한다. 설치 도움말상 `agentWait: null`은 조회 시 대기 없음이며, 필드 부재는 확인 불가다. 부재를 대기 없음으로 바꾸지 않는다. 이미 종료한 worker에는 재개 지시를 보내지 않는다.

실제 대기 원인을 알아야 할 때만 `worker-read --limit`와 필요한 cursor로 관련 부분을 읽는다. cursor가 무효화되면 새 읽기를 하며, 전체 transcript를 매번 다시 수집하지 않는다. 질문·승인 근거의 식별자와 마지막 처리 상태는 비공개 기록에 남겨 중복 알림을 구분한다. 새 프롬프트·내용 변경·미해결 상태 확인이 필요한 경우에는 다시 확인한다. resolved 표시는 허용/거부의 증거가 아니며, 사람의 보안 승인은 orchestration reply/send로 대신할 수 없다.

외부 알림 전송은 별도 기능이다. 중복 제거·전송 실패·민감 정보 제외와 알림 대상의 사용자 승인을 검증한 뒤 연결한다. 휴대폰에서 실제 승인하는 기능과 알림을 혼동하지 않는다. 현재 이 저장소에는 자동 알림 전송기나 원격 승인기가 없다.

## 반복 승인 줄이기

작업 시작 전에 다운로드·캐시·파일 쓰기 범위와 localhost 소켓이 필요한지 확인하고 정식 권한 절차를 준비한다. 거부된 명령을 다른 앱·프로세스로 실행하거나 광범위한 명령 허용으로 우회하지 않는다. 반복 명령은 검토 가능한 프로젝트 스크립트로 만들고 필요한 범위만 승인한다.

Orca 공식 문서는 Settings → Agents의 Manual/Auto/Yolo와 기존 채팅의 별도 권한 모드를 설명한다. 기본 설정 변경이 기존 채팅에 소급된다고 가정하지 않는다. 부모의 승인 정책도 자식에게 전달됐다고 가정하지 않는다. 설치된 `worker-start` help에서 승인 모드 옵션은 확인되지 않았으므로 임의 옵션이나 설정을 만들어 넣지 않는다. Auto 적용은 실제 새 worker의 유효 모드와 제한된 동작으로 별도 검증해야 한다. 이 가이드는 권한 설정을 변경하지 않는다.

공식 근거: [Orca native chat 권한 모드](https://www.onorca.dev/docs/agents/native-chat), [Codex Auto-review와 좁은 권한 범위](https://learn.chatgpt.com/docs/sandboxing/auto-review). 제품별 설정과 정책은 구분한다.

## 프로젝트·기록의 연결

상위 폴더와 Git 저장소가 다른 경우 정확한 folder/worktree/host 주소를 확인한다. 원격 current를 로컬 current와 혼동하지 않는다. 미커밋 변경이 새 worktree에 포함된다고 가정하지 않는다. 사용자 요구 없이 커밋 기반 worktree를 새로 만드는 것은 기본 동작으로 삼지 않는다.

공통 task ID와 Orca가 반환한 run/task/dispatch·작업 공간 주소를 비공개 연결 자료에 함께 둔다. 완료 통지만으로 AC 통과를 확정하지 않는다. 측정 출처·범위가 없는 사용량은 unavailable로 표시한다. 기존 workflow.py의 Codex 로그가 Orca가 관리한 실행을 자동 수집한다고 가정하지 않는다.

이 가이드는 연결 절차다. 모든 위임을 대신하는 자체 엔진이나 현재 프로젝트에서의 네이티브 실행 검증 완료를 뜻하지 않는다.
