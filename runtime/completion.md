# 완료 기록과 임시 프로젝트 정리

조율자가 Orca 완료 알림을 받은 뒤 `scripts/finish_worker.py`를 한 번 호출한다. 종료한 정확한 worker의 native snapshot을 조회하고 [메타데이터 검사기](native-record.md)를 실행한다. 같은 dispatch·입력의 재호출은 저장된 결과를 반환하고 사용량을 다시 합산하지 않는다. 상시 서버·폴링·추가 에이전트는 만들지 않는다.

이 연결은 **조율자의 완료 처리 명령**이다. Orca 전체에 자동 hook을 설치한 것이 아니며, 이 가이드를 따르지 않는 세션까지 자동 수집하지 않는다. 설치 버전의 `worker-show`에는 Codex 원본 세션 경로가 없어 `--session-file`·`--session-id`는 해당 업무의 확인된 근거로 명시해야 한다. 경로 미확인 시 작업을 `수집 보류`로 보고하며 전체 세션 탐색으로 조용히 대체하지 않는다. 지원 범위는 로컬 Orca + Codex, Python 3.10 이상·POSIX 환경이다.

자동 정리는 [하네스 설정](delegation.md)의 `auto_cleanup: "Y" | "N"`으로 선택하며 **기본값은 Y**다. `--auto-cleanup N`이면 완료 기록을 남기고 정리를 건너뛴다. Y여도 지정된 정리 계획·권한·보관본·종료 검사는 그대로 필요하다. 이 설정은 Orca 보안 승인 모드를 바꾸지 않는다.

정책 프로젝트는 존재하는 디렉터리여야 한다. 완료 처리에서는 기본적으로 `--project`의 정책을 읽는다. 해당 임시 프로젝트를 제거한 뒤에도 완료 기록을 다시 조회하려면 처음부터 보존할 조율 프로젝트를 `--policy-project /path/to/coordinator-project`로 명시한다. 이때는 명시한 프로젝트의 정리 정책을 사용한다. 경로가 없어졌다고 다른 프로젝트나 기본 Y로 자동 전환하지 않는다. `workspace_cleanup.py apply`에서도 같은 옵션으로 정책 프로젝트를 지정할 수 있으며 기본은 현재 디렉터리다.

## 완료 알림 처리

다음은 가상 경로·ID다. 실행 설정을 변경하지 않는다.

```sh
python3 scripts/finish_worker.py \
  --project /path/to/temporary-experiment \
  --session-file /path/to/private/session.jsonl \
  --session-id session_example \
  --task-id task_example --dispatch-id ctx_example \
  --request-id request_example --role backend \
  --expect-reviewer auto_review \
  --directory /path/to/private/completions
```

- 한 번의 `worker-show --full --json`으로 task/dispatch·로컬 host·종료 상태를 확인한다. `worker_done` 메시지나 업무 성공만으로 프로세스 종료를 추정하지 않는다. 미종료·불명 상태면 종료 코드 2로 멈추며 반복 조회하지 않는다.
- 원본 snapshot, 메타데이터, 완료 영수증을 비공개 디렉터리에 저장한다. 잠금으로 동시 처리를 막고 재시작 후 같은 영수증을 재사용한다. 원래 입력 연결이나 저장된 메타데이터가 바뀌면 충돌로 멈춘다.
- 새 수집은 `--request-id`와 공통 `--role`을 함께 명시한다. 메타데이터의 `dispatch_usage`와 영수증의 `dispatch_usage_status/task_attributed/measurement_key`를 확인한다. 같은 native 턴에 여러 논리 요청을 배정하지 않는다. 자세한 구간 조건은 [배정별 수집](native-record.md#배정-구간의-사용량)을 따른다.
- 승인 모드/모델의 불일치·미확인은 결과를 보존하고 종료 코드 1을 반환한다. 미측정 사용량은 별도 상태로 유지한다. 기대값이 없는 검사는 `not_requested`이며 업무 AC 통과가 아니다.
- 형식 오류·권한·조회 실패는 종료 코드 2다. 같은 명령의 정식 권한 절차를 사용하며 설정 변경이나 다른 CLI로 우회하지 않는다.
- 소스·로그 등의 최종 보관과 업무 AC 판정은 조율자의 책임이다. 완료 기록 자체는 결과의 기능 검증을 대신하지 않는다.

사용량 귀속이 불가능해도 누적 참고값과 실패 이유는 남긴다. 기능 검증·프로필 검사와 별개이며, 자동으로 0 또는 요청 합계로 바꾸지 않는다. [DB 연결](trace-usage.md)은 선택 `--trace-db`와 `--trace-task-title`로 요청한다. 요청과 actor가 등록된 기존 비공개 DB가 필요하며 실패하면 완료 JSON을 보존한 채 정리 전에 멈춘다. 같은 명령의 replay로 수입을 재시도한다.

기존 영수증은 새 버전에서 자동 변경하지 않는다. 예전 누적 기록만 있는 영수증은 `not_collected_legacy`로 안내하고 replay한다. 요청/역할을 바꿔 덮어쓰면 충돌한다. 보존된 종료 작업을 새 수집기로 재관측하려면 기존 기록을 유지한 별도 비공개 `--directory`를 명시하고 새 근거를 확인한다. 같은 구간을 여러 번 관측한 결과는 같은 측정 키이므로 합산하지 않는다.

## 정리 계획 만들기

임시 공간을 만들 때부터 소유권·보관 위치·정리 권한을 작업 입력에 정한다. 보관이 끝나고 AC를 수용한 뒤 아래 명령으로 **정확한 폴더 하나**의 정리 계획을 만든다. `prepare`는 삭제하거나 앱 등록을 변경하지 않는다. 기존 사용자 프로젝트를 나중에 임시 공간으로 추정하지 않는다. 기존 실험을 정리하려면 해당 대상에 대한 사용자 지시가 있어야 한다.

```sh
python3 scripts/workspace_cleanup.py prepare \
  --source /path/to/task-scratch/temporary-experiment \
  --allowed-root /path/to/task-scratch \
  --archive /path/to/private/delivery/experiment \
  --output /path/to/private/cleanup/experiment.json \
  --authorization 'User authorized retirement of this temporary experiment after delivery' \
  --acceptance /path/to/private/accepted-assessment.json \
  --worker task_example/ctx_example \
  --setup-id setup_example \
  --discard target --discard .cache
```

`--authorization`은 기존 사용자 지시의 기록이며 권한을 새로 부여하지 않는다. `--acceptance`는 조율자가 수용한 검증 근거 파일이며 도구가 그 문장의 의미를 자동 판정하지 않는다. 모든 관련 배정을 `--worker`로 반복 지정한다. `--setup-id`는 공식 조회로 확인한 로컬 **folder setup**에만 지정하며, 등록이 없으면 생략한다. Git 프로젝트·Git worktree 제거는 지원하지 않는다.

기본적으로 모든 파일과 빈 디렉터리가 보관본에 있어야 한다. 제외할 다운로드 캐시·빌드 출력만 정확한 상대 경로로 `--discard`에 지정한다. 제외 경로의 파일도 목록·해시를 기록하므로 이후 변경은 삭제를 중단시킨다. 소스·보고서·검증 로그를 캐시로 분류하지 않는다. 보관본을 자동 생성하거나 압축 해제하지 않으며, 기존 보관본의 파일별 크기·SHA-256을 검사한다.

## 적용과 자동 연결

계획을 확인한 뒤 적용하거나, 마지막 worker 완료 처리에 `--cleanup-plan /path/to/private/cleanup/experiment.json`을 추가한다. 이 옵션은 준비된 계획의 정리까지 실행하라는 지시다. 완료 기록이 성공적으로 저장된 뒤 정리기로 전달하며 기록 검사가 불일치·미확인이면 정리하지 않는다.

관리 배정의 자리를 함께 해제하려면 완료 처리에 `--dispatch-directory`와 `--operation-id`도 전달한다. [위임 한도](delegation.md)의 정확한 종료 조회를 수행하며, 단순 완료 알림만으로 자리를 비우지 않는다.

```sh
python3 scripts/workspace_cleanup.py apply \
  --plan /path/to/private/cleanup/experiment.json
```

적용 시 확인하는 조건:

1. 임시 공간 분류·명시 권한·수용 근거 파일과 계획 내용이 유지되어야 한다. 저장소 자체·현재 작업 디렉터리·허용 루트 전체·보관본과 겹치는 경로는 거부한다.
2. 원본 루트 식별자와 전체 파일 목록·해시, 보관본 해시가 준비 시점과 일치해야 한다. Git·심볼릭 링크·특수 파일은 거부한다.
3. 관련 worker가 모두 정확히 종료 상태여야 한다. `lsof`로 해당 폴더의 열린 파일·cwd를 확인한다. 명령 부재·경고·불명 상태·사용 중이면 멈춘다. 다른 프로세스를 자동 종료하지 않는다.
4. Orca의 정확한 folder setup·경로·로컬 host와 비활성 상태를 확인한다. 다른 경로의 workspace, 자식 workspace, 에이전트·터미널이나 불명 상태가 있으면 멈춘다. `setup-delete --force`는 확인된 비활성 folder setup의 저장된 UI 등록을 해제하는 데만 사용한다. Git 삭제·archive hook은 실행하지 않는다.
5. 등록 해제 후 파일과 프로세스를 다시 확인하고, 원본을 같은 부모의 전용 정리 경로로 이동한 뒤 삭제한다. 최종 산출물과 정리 영수증은 보관한다.

등록 해제만 끝난 부분 실패는 상태 파일에 남고 재호출 때 현재 상태를 확인한다. 디스크 삭제 중 중단되면 전용 정리 경로를 유지하고 명시 점검을 요구하며 무조건 이어서 삭제하지 않는다. 완료 후 원래 경로에 새 폴더가 생기면 재호출로 지우지 않는다. 계획·완료 기록을 실행 중 편집하지 않는다.

`lsof` 확인은 해당 호출자가 볼 수 있는 프로세스 범위이며 OS 전체의 무잔존 보장이 아니다. 같은 사용자나 외부 프로세스가 검사 직후 파일을 바꾸는 모든 경쟁 상태까지 차단하는 OS 잠금은 아니다. 원본에 쓰는 작업을 마친 뒤 사용한다. 정리 중 자료가 바뀌면 보존하고 원인을 확인한다.

## 검증과 남은 연결

```sh
python3 -m unittest discover -s tests -p test_lifecycle.py -v
```

가상 폴더·native 응답으로 중복 완료, 동시 잠금, 기록 변경, 보관본 누락·수정, 새 파일, 열린 프로세스, 미종료 worker, 등록 충돌, 부분 실패·재시작·경로 재생성을 검사한다. 실제 native 호출과 가상 응답 검사를 구분해서 보고한다.

남은 범위는 공식 세션 경로 제공, Orca 전역 이벤트 hook, 새 worker의 자동 수집 전체 흐름, 원격 host/Windows, Git worktree·복수 프로젝트 정리, 프로세스 자동 종료다. 무조건적인 TTL 삭제나 다른 프로젝트의 일괄 정리는 포함하지 않는다.
