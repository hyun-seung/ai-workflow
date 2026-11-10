# Orca 환경 연결

공통 업무 규칙·프로젝트 지침은 그대로 적용한다. 여기서는 Orca가 제공하는 작업 공간·worker·메시지·결과 lifecycle을 사용한다. 공급자와 모델은 환경 선택과 구분한다.

## 공식 가이드와 연결 확인

2026-11-10 설치 버전 1.4.255의 배포 가이드와 help를 확인했다. 실행할 때 다시 확인한다.

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

명령 실행 전 해당 하위 명령 --help와 필요한 배포 reference를 읽는다. receipt가 접수됐다는 사실과 worker 실행·완료를 구분한다. 결과가 필요한 위임은 orchestration을 사용하고 worker 터미널에 직접 steer하지 않는다.

worker-start 뒤에는 안내된 next를 따르고 턴을 끝내 네이티브 알림을 기다린다. poll/watch 루프로 check 메시지를 소비하지 않는다. 실패하면 재호출부터 하지 말고 failedStage와 recovery-and-cleanup reference를 확인한다.

provider는 --agent로 선택하며 모델·effort 지원은 공급자와 버전에 따라 다르다. 사용자가 명시한 모델이 있으면 그대로 전달할 수 있는지 확인한다. native skill이 요구하는 모델 선택 절차와 충돌하면 이를 드러낸다. 지원하지 않는 옵션을 조용히 생략하거나 다른 모델로 바꾸지 않는다.

## 프로젝트·기록의 연결

상위 폴더와 Git 저장소가 다른 경우 정확한 folder/worktree/host 주소를 확인한다. 원격 current를 로컬 current와 혼동하지 않는다. 미커밋 변경이 새 worktree에 포함된다고 가정하지 않는다. 사용자 요구 없이 커밋 기반 worktree를 새로 만드는 것은 기본 동작으로 삼지 않는다.

공통 task ID와 Orca가 반환한 run/task/dispatch·작업 공간 주소를 비공개 연결 자료에 함께 둔다. 완료 통지만으로 AC 통과를 확정하지 않는다. 측정 출처·범위가 없는 사용량은 unavailable로 표시한다. 기존 workflow.py의 Codex 로그가 Orca가 관리한 실행을 자동 수집한다고 가정하지 않는다.

이 가이드는 연결 절차다. 모든 위임을 대신하는 자체 엔진이나 현재 프로젝트에서의 네이티브 실행 검증 완료를 뜻하지 않는다.
