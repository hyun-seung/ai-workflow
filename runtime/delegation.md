# 하네스 설정과 제한된 Orca 위임

`harness.json`은 공통 운영 설정이다. 역할별 모델·effort를 정하는 `workflow.json`이나 Orca 권한 설정과 구분한다. 다음 순서로 적용한다.

1. 저장소의 `harness.json`.
2. 대상 프로젝트의 `.ai-workflow-harness.json`(있을 때).
3. `--config`로 지정한 파일.
4. 해당 명령의 명시 옵션.

알 수 없는 키·잘못된 값·지정 파일 누락을 오류로 처리한다. 정책을 읽을 프로젝트는 실제로 존재하는 디렉터리여야 한다. 경로 오타나 일반 파일을 설정 부재로 간주해 기본 Y를 적용하지 않는다. [스키마](../schema/harness.schema.json)를 제공하며 실행기에서도 검증한다.

## 자동 정리 Y/N

공통 기본값은 `"auto_cleanup": "Y"`다. 프로젝트별로 끄려면 다음 파일을 둔다.

```json
{
  "version": 1,
  "auto_cleanup": "N"
}
```

`finish_worker.py --auto-cleanup N`도 사용할 수 있다. N이면 완료 기록은 저장하고 정리는 `skipped_disabled`로 건너뛴다. Y이면 **정리 계획이 지정된 임시 공간**에만 기존 보관본·종료·경로 검사를 적용한다. Y는 새 삭제 권한이나 모든 프로젝트 자동 탐색을 뜻하지 않는다. `workspace_cleanup.py apply`도 `--policy-project`, `--config`, `--auto-cleanup`을 받아 같은 선택을 따른다. 상세 조건은 [완료 처리](completion.md)에 있다.

## 작업마다 실행 한도 지정

`max_active_workers`와 `max_dispatches_per_request`는 공통 숫자 기본값이 없다(null). 첫 배정 전에 프로젝트/명시 설정이나 CLI로 양의 정수를 지정해야 한다. 지정하지 않으면 native 실행 전에 멈춘다. 같은 사용자 요청의 후속 배정은 처음 저장한 한도를 재사용할 수 있다. 이미 시작한 요청의 한도를 조용히 늘리지 않는다.

다음은 시험용 숫자를 명시한 가상 예시이며 기본값이 아니다.

```sh
python3 scripts/dispatch_guard.py \
  --project /path/to/project --directory /path/to/private/coordinator-ledger \
  start --request-id request_example --operation-id implement_server \
  --max-active-workers 3 --max-dispatches-per-request 8 \
  --spec-file /path/to/private/server-task.md --worktree current \
  --agent codex --writes server
```

`--directory`는 같은 조율자가 관리하는 모든 요청·Run이 공유하는 비공개 상태 디렉터리다. 시작 전 설정·파일·호출 내용을 확정하고 잠금 안에서 한도 검사와 예약을 함께 저장한다. 실제 시작은 Orca `worker-start` 한 번에 맡긴다. 같은 `--operation-id`와 입력을 다시 제출하면 접수 결과를 반환하고 worker를 또 만들지 않는다. 같은 ID의 다른 입력은 거부한다.

- 동시 한도는 조율자를 제외한 관리 중 worker의 합계다. 시작 예약·실행·승인 대기·종료 대기·실행 불명은 모두 자리를 차지한다.
- 여러 요청이 동시에 진행되면 현재 활성 요청과 새 요청 중 가장 낮은 동시 한도를 따른다. 새 request ID로 다른 활성 요청의 한도를 높이지 않는다.
- 누적 한도는 예약이 수락된 새 배정·후속 업무·실패 재시도를 센다. 보수적으로 시작 여부가 불명확한 예약도 포함하며, 종료 후 줄이지 않는다. 한도 초과로 거부된 배정과 동일 operation 재조회는 추가로 세지 않는다.
- 실행 중인 요청의 한도 변경은 현재 지원하지 않는다. 필요한 재계획과 사용자 결정 없이 상태 파일을 지우거나 새 request ID로 누적 한도를 초기화하지 않는다.
- `--writes`에는 담당 수정 경로를 반복 지정한다. 다른 활성 배정의 경로와 같거나 포함 관계면 보류한다. 실제 파일 쓰기를 OS에서 차단하는 기능은 아니므로 정확한 선언과 역할 지침이 필요하다.
- `--depends-on <operation-id>`는 성공·종료가 확인된 선행 배정만 허용한다. 공통 계약 이후 독립 작업을 시작하는 데 사용한다. 자동 요청 분해·우선순위 큐는 포함하지 않는다.

## 완료·후속 작업·재시도

완료 알림을 받으면 정확한 operation을 한 번 확인한다.

```sh
python3 scripts/dispatch_guard.py \
  --project /path/to/project --directory /path/to/private/coordinator-ledger \
  refresh --operation-id implement_server
```

`worker-show`의 task/dispatch·로컬 host·정확한 종료를 확인해야 자리를 해제한다. 업무 완료 메시지나 timeout만으로 해제하지 않는다. native 자동 회수가 남으면 알림/공식 회수 경로를 따른다. 이 명령은 프로세스를 중단하거나 폴링하지 않는다.

`finish_worker.py`에 `--dispatch-directory`와 `--operation-id`를 함께 전달하면 메타데이터 저장 후 같은 해제 확인을 연결한다. 모델/승인 검사 불일치와 실제 프로세스 종료는 별개이며, 이미 끝난 프로세스의 자리는 반환할 수 있다.

종료가 확인된 관리 대상의 다음 업무는 새 operation으로 `followup --previous <operation-id> --spec-file <path>`를 사용한다. 같은 요청의 누적 한도를 소비하고 공식 `send --to dispatch:<id>`로 전달한다. 현재는 이전 worker의 실제 종료 후 후속 업무만 지원하며, 같은 선행 배정에서 이어진 후속 업무·재시도 중 하나라도 실행 또는 불명 상태면 추가 배정을 거부한다. 오래된 선행 operation을 다시 지정해도 이 점유 검사를 적용한다. 새 모델을 지정하는 후속 전송은 지원하지 않는다.

실패하거나 명시적으로 중단한 배정의 정확한 종료가 확인되면 `retry --previous <operation-id>`로 같은 native Task와 `--retry-of`를 사용한다. 성공한 배정에는 retry를 사용하지 않는다. retry 응답의 native Task/Run이 원래 연결과 다르면 unknown으로 보존하고 재호출하지 않는다. 새 operation이므로 누적 한도에 포함한다. Orca의 실패 연쇄·회로 차단을 새 Task로 우회하지 않으며 자동 재시도하지 않는다. 사용자의 재시도 지시 범위와 공식 recovery 가이드를 먼저 확인한다.

## 오류·재시작·확인 범위

공식 중단 응답의 `stopped`만으로 종료를 인정하지 않는다. 현재 지원하는 native 조합은 worker의 `stopped/process_stopped`, dispatch의 `failed/lastFailure=stopped`, 정확한 로컬 worker의 `exited` 관측이 함께 있는 경우다. 이때 원래 중단 상태를 기록하고 같은 Task의 재시도를 허용한다. 미확인 종료는 계속 자리를 차지한다.

```sh
python3 scripts/dispatch_guard.py \
  --project /path/to/project --directory /path/to/private/coordinator-ledger status
```

종료 코드 0은 관리 접수/조회 성공, 3은 한도·선행 조건·수정 범위에 따른 배정 보류, 2는 입력·연결·불명 상태다. native receipt의 `next`와 실패/회수 안내를 함께 반환한다. 실패·응답 유실 뒤 새로운 worker를 자동 생성하지 않는다. 실패 종료 코드와 `ok:false`, 결과 없는 `runtime_access_denied`가 함께 확인되어 시작되지 않은 경우에만 같은 operation의 정식 승인 재호출을 허용한다. 성공·배정 결과와 오류가 섞인 모순 응답은 `unknown`으로 남긴다.

원본 receipt를 받지 못한 `launching/unknown`은 재시작 후에도 자리를 유지한다. native 원본 상태/접수 결과를 확인해야 하며 이 버전에는 근거 없는 수동 슬롯 삭제 명령이 없다. 기록·설정 파일의 형식 변경이나 다른 조율자 세션을 같은 ledger에 연결하면 멈춘다. 실제 사용량은 [별도 측정 기록](native-record.md)과 연결하며 배정 횟수를 토큰으로 환산하지 않는다.

현재 범위는 **이 관리 경로로 시작한 로컬 Orca 배정**이다. 기존 프로젝트의 native 기능을 사용하며 신규 worktree 생성·원격 host·중첩 위임은 제외한다. 하위 worker는 새 에이전트를 직접 생성하지 말고 조율자에게 요청한다. 직접 CLI·다른 ledger·다른 조율자의 실행을 막는 보안 경계나 OS 전체 상한은 아니다. 도입 전에 남아 있는 이전 worker를 확인하고 같은 조율자의 관리 경로를 하나로 유지한다.

가상 응답 검증과 실제 native 실행 검증을 구분한다. 미지원 receipt 형식은 `unknown`으로 보존하고 성공으로 추정하지 않는다. [병렬 작업 판단](parallelism.md)은 이 수량 제한과 함께 적용한다.

등록된 Orca·Codex 배정의 토큰 자동 수집을 준비할 때 `--usage-role`, `--usage-actor-id`, `--usage-task-title`을 함께 명시한다. native 호출 전에 계획에 고정하며 측정값은 입력받지 않는다. 프로젝트 hook의 설치·신뢰와 [실제 완료 이벤트 검증](completion-hook.md)이 별도로 필요하다. 옵션이 없는 기존 배정은 자동 수집 대상으로 승격하지 않는다.
