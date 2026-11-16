# 완료 기록과 임시 프로젝트 정리

한 요청의 여러 배정과 자원을 함께 마감하려면 [요청 단위 일괄 마감](request-completion.md)을 사용한다. 기능 수용 뒤 수집·전체 보관·별도 DB 사전 검증·Y/N 정리·실제 SQLite 마감을 연결하며, 이 문서의 단건 종료 검사를 재사용한다.

조율자가 Orca 완료 알림을 받은 뒤 `scripts/finish_worker.py`를 한 번 호출한다. 종료한 정확한 worker의 native snapshot을 조회하고 [메타데이터 검사기](native-record.md)를 실행한다. 같은 dispatch·입력의 재호출은 저장된 결과를 반환하고 사용량을 다시 합산하지 않는다. 상시 서버·폴링·추가 에이전트는 만들지 않는다.

이 연결은 **조율자의 완료 처리 명령**이다. 등록된 Orca·Codex 배정의 Stop 이벤트 수집 연결은 [완료 시 토큰 hook](completion-hook.md)을 참고한다. hook은 닫힌 턴의 토큰만 수집하고 이 명령의 정확한 worker 종료·수용·정리를 대신하지 않는다. Orca 전체에 자동 hook을 설치한 것이 아니며, 이 가이드를 따르지 않는 세션까지 자동 수집하지 않는다. 설치 버전의 `worker-show`에는 Codex 원본 세션 경로가 없어 `--session-file`·`--session-id`는 해당 업무의 확인된 근거로 명시해야 한다. 검증된 Stop hook 영수증이 있으면 그 명시 원본 경로를 기존 완료 명령의 입력으로 참조할 수 있다. 경로 미확인 시 작업을 `수집 보류`로 보고하며 전체 세션 탐색으로 조용히 대체하지 않는다. 지원 범위는 로컬 Orca + Codex, Python 3.10 이상·POSIX 환경이다.

자동 정리는 [하네스 설정](delegation.md)의 `auto_cleanup: "Y" | "N"`으로 선택하며 **기본값은 Y**다. `--auto-cleanup N`이면 완료 기록을 남기고 정리를 건너뛴다. Y여도 지정된 정리 계획·권한·보관본·종료 검사는 그대로 필요하다. 이 설정은 Orca 보안 승인 모드를 바꾸지 않는다.

정책 프로젝트는 존재하는 디렉터리여야 한다. 완료 처리에서는 기본적으로 `--project`의 정책을 읽는다. 해당 임시 프로젝트를 제거한 뒤에도 완료 기록을 다시 조회하려면 처음부터 보존할 조율 프로젝트를 `--policy-project /path/to/coordinator-project`로 명시한다. 이때는 명시한 프로젝트의 정리 정책을 사용한다. 경로가 없어졌다고 다른 프로젝트나 기본 Y로 자동 전환하지 않는다. `workspace_cleanup.py apply`에서도 같은 옵션으로 정책 프로젝트를 지정할 수 있으며 기본은 현재 디렉터리다.

## 완료 알림 처리

### 등록된 Codex 배정의 단발 처리

완료 알림과 연결된 **수집 완료 Stop 영수증 키**가 있으면 아래 명령으로 저장된 수집·DB 연결, 정확한 worker 상태, 선택적 조기 해제와 한도 슬롯 반환을 연결한다. 영수증 키는 기존 수집 기록에서 명시하며 세션 폴더나 받은 편지함을 탐색하지 않는다. `--release-settled`를 생략하면 조기 해제를 요청하지 않는다.

```sh
python3 scripts/settle_worker.py \
  --config /path/to/private/hook-config.json \
  --operation-id operation_example \
  --receipt-key '<collected-stop-key>' \
  --directory /path/to/private/settlements \
  --release-settled
```

저장된 배정·요청·Task/Dispatch/Run·조율자와 현재 세션을 대조한다. 이미 정확히 종료한 worker는 상태 조회 한 번으로 슬롯을 반환한다. 정착됐지만 살아 있는 worker는 닫힌 원본 구간을 다시 검증한 뒤 공식 해제를 최대 한 번 요청하고 새 상태 조회로 종료를 확인한다. 이 경로는 최대 상태 조회 두 번이며 대기·폴링·inbox 소비·에이전트 생성은 없다. 관리 장부 잠금을 보유하는 동안 실행하므로 같은 장부를 쓰는 생성·후속 명령과 차례로 호출한다. 별도 검증·프로필 검사·기능 수용·보관·정리는 기존 완료 단계에서 수행한다.

명시적인 승인·질문 대기가 관측되면 해제를 요청하지 않는다. `agentWait` 필드 부재는 대기 여부 미확인으로 기록하며 “대기 없음”으로 바꾸지 않는다. 다른 조건이 충족된 정착 worker의 공식 해제 요청은 가능하지만, 사용자 유지·소유권·현재 실행 상태에 대한 Orca 자체 거부를 따른다. 거부나 미종료 응답에서는 슬롯을 반환하지 않는다.

해제 전 intent를 먼저 저장한다. timeout·중단·pending·retained·불명 응답 뒤 재호출은 상태를 확인하지만 해제 명령을 자동 재전송하지 않는다. 공식 영수증이 요구하는 복구는 별도로 처리한다. 종료 확인 전에는 슬롯을 유지하고, 실패한 업무는 실패로 남긴다. 같은 계보의 후속 배정이 활성 상태면 과거 배정의 해제를 요청하지 않는다. 성공 replay는 저장된 **과거 확인 결과**이며 새로운 생존 확인이나 다음 업무 실행 승인이 아니다.

호출·단계별 UTC 시각과 monotonic 경과 시간, 원본 native 응답은 비공개 기록에 남긴다. 기록하는 구간은 이 명령의 처리 시간이며 알림 전달·사람 승인 대기·전체 요청 시간으로 해석하지 않는다. 성공은 `exited`/종료 0, 미종료·후속 확인 필요는 종료 1, 입력·권한·검사 오류는 종료 2다. 미수집 Stop·원본 부재·DB 불일치는 수집/복구 단계로 돌려보내며 무측정 해제나 다른 실행 방식으로 대체하지 않는다.

로컬 Orca·Codex의 실제 첫 배정에서 자동 종료 확인, 같은 worker의 후속 배정에서 공식 조기 해제와 새 정확한 종료 확인을 각각 연결했다. 두 성공 명령의 replay는 native 호출 없이 기록·장부·요청 DB를 유지했다. `agentWait` 부재에 대한 최초 안전 중단과 보완 근거도 보존했고, 가상 경계의 독립 재검토를 통과했다. 이 확인은 전체 요청 속도 개선이나 원격·타 공급자 지원을 입증하지 않는다.

설치된 Orca가 정착된 worker의 공식 조기 해제를 지원하면 `orchestration worker-release --dispatch <dispatch_id>`를 사용할 수 있다. 로컬 Orca 1.4.259에서 닫힌 Stop 수집 뒤 이 명령과 정확한 종료 조회를 연결해 확인했다. 대상 소유권과 Keep open 등 환경의 거부 조건을 따르며, 사용량을 남길 때는 연결된 닫힌 턴의 수집을 먼저 확인한다. 해제 요청만으로 종료를 확정하지 않고 새 `worker-show`의 정확한 종료·자원 해제 근거를 대조한 뒤 슬롯을 비운다. pending·unknown 응답은 공식 다음 조치를 따르고 무조건 재호출하지 않는다.

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

새 임시 폴더를 만들 때부터 연결하려면 [생성·보관 도구](workspace-lifecycle.md#새-임시-폴더의-등록보관-연결)의 `create/finalize`를 사용한다. 등록된 새 폴더의 보관본 생성과 아래 기존 정리기를 연결한다. 아래 `prepare/apply` 자체는 보관본을 만들지 않는다.

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
python3 -m unittest discover -s tests -p test_settle_worker.py -v
```

가상 폴더·native 응답으로 중복 완료, 동시 잠금, 기록 변경, 보관본 누락·수정, 새 파일, 열린 프로세스, 미종료 worker, 등록 충돌, 부분 실패·재시작·경로 재생성을 검사한다. 실제 native 호출과 가상 응답 검사를 구분해서 보고한다.

새 작은 stdlib 작업에서 요청/계획 actor 등록 → 실제 Orca 배정 → 기능 수용·보관 → 완료 명령의 종료/프로필 검사·토큰 수집·DB 연결·기본 Y 정리 → 동일 명령 replay → 요청 마감을 연결해 확인했다. 재호출에서 완료/정리 영수증과 DB가 유지됐고, 이전 요청은 보존했다. 새 프로젝트 등록이나 상시 서버는 필요하지 않았다.

이 검증에서 요청 등록, 명시 세션 경로 인계, 기능 수용·보관본 생성, 정리 계획 준비와 완료 명령 호출·요청 마감은 조율자가 수행했다. worker의 자기 세션 ID와 한 날짜 폴더의 정확한 ID 후보를 대조한 경로 인계는 해당 시험의 수동 절차이며 공식 자동 경로 API가 아니다. 이 한 건의 성공을 모든 세션·공급자·병렬/원격 실행이나 날짜 경계로 확대하지 않는다.

새 기록은 실행자, 제품 담당자, 검증 대상과 검증자를 구분하고 수입 전에 연결을 대조한다. 초기 actor의 불변 프로필과 나중에 확인한 실제 설정은 별도 근거로 보존한다. 표준 관측의 잘못된 귀속은 [원본 보존 정정](trace-amendments.md)으로 바로잡는다. 토큰 원천값이나 legacy/native 기록 수정은 지원하지 않는다.

격리된 로컬 structured worker를 공식 명령으로 중단하고, 정확한 종료 뒤 같은 native Task/Run의 새 Dispatch에서 체크포인트를 복구하는 시험을 확인했다. 순차 재조회에서 결과와 처리 횟수가 유지됐으며, 중단 배정의 Stop 영수증 부재는 미측정으로 보존했다. 검토 자료에는 복구 결과뿐 아니라 중단·복구 양쪽의 native Task/Dispatch/Run 영수증을 함께 포함해야 한다. 이 시험은 앱 전체 재시작이나 임의 작업의 동시·재시도 안전성을 보장하지 않는다.

등록된 로컬 Orca·Codex 배정은 Stop이 제공한 원본 경로로 자동 토큰 수입을 실제 한 건 확인했다. 남은 범위는 모든 공급자의 공식 세션 경로 제공, Orca 전역 완료 이벤트와 종료/수용/정리 명령 자동 호출, 원격 host/Windows, Git worktree·복수 프로젝트 정리, 프로세스 자동 종료다. 무조건적인 TTL 삭제나 다른 프로젝트의 일괄 정리는 포함하지 않는다.
