# 명시 원본을 통한 요청 사용량 수집

`scripts/collect_request_usage.py`는 호출자가 명시한 닫힌 조율자 턴과 등록 worker 원본을 유한 목록으로 수집한다. 명령 실행 자체가 이 목록의 수집 승인이다. 세션 탐색·배정 생성·슬롯 해제·ledger 변경·보관·정리는 수행하지 않는다.

```sh
python3 scripts/collect_request_usage.py \
  --manifest /private/path/inputs/manifest.json \
  --directory /private/path/collection-output \
  --orca-cli /path/to/orca
```

실행 환경의 `ORCA_AGENT_SESSION_ID`는 hook config의 native coordinator와 같아야 한다. manifest의 `coordinator_id`는 해당 요청에 이미 존재하는 최상위 coordinator actor다. 출력은 프로젝트와 입력 파일의 부모 디렉터리 밖에 있는 canonical 비공개 경로여야 한다. 명령은 성공 JSON과 종료 0, 입력·원본·수입 오류에는 짧은 오류 JSON과 종료 2를 반환한다. 원문·오류 원문은 출력하지 않는다.

## Manifest version 1

아래 ID와 경로는 가상 예시다. coordinator_turns와 worker_sources의 합은 1~100개다. 항목 생략은 빈 배열로 표현한다. 중복 키·항목, 알 수 없는 필드, 비유한 숫자, 과도한 크기·깊이는 거부한다.

```json
{
  "schema_version": 1,
  "id": "collection_example",
  "hook_config": "/private/path/inputs/completion-hook.json",
  "request_id": "request_example",
  "coordinator_id": "coordinator_example",
  "coordinator_turns": [
    {
      "session_file": "/private/path/sources/coordinator.jsonl",
      "session_id": "session_example",
      "binding_file": "/private/path/inputs/turn-binding.json",
      "task_title": "결과 취합"
    }
  ],
  "worker_sources": [
    {
      "operation_id": "operation_example",
      "session_file": "/private/path/sources/worker.jsonl",
      "session_id": "worker_session_example",
      "expected": {
        "model": "example-model",
        "effort": "high",
        "reviewer": "auto_review"
      }
    }
  ]
}
```

hook config는 [완료 hook](completion-hook.md)의 기존 형식이다. binding은 [조율자 턴](coordinator-usage.md)의 version 1 또는 2를 사용한다. 모든 binding은 같은 요청과 root actor에 속해야 하며 같은 session/turn을 두 번 선택할 수 없다. worker는 같은 요청의 확정된 bound/released operation이어야 한다. 등록된 plan의 role/actor/title을 그대로 사용하며 Task/Dispatch/Run과 plan을 journal에 고정한다. 매번 현재 native worker-show로 dispatch의 Task/Dispatch/Run과 local host, worker Dispatch, exact exit를 먼저 확인한다. dispatch.runId는 필수이며 worker.runId는 없어도 되지만 존재하면 등록 Run과 같아야 한다. 최초 finish가 별도로 받은 snapshot도 같은 계약으로 검사한다. 기존 finish 경로에서 profile을 확인한다. 사용량이 unavailable이거나 profile이 확인되지 않으면 전체 DB 수입을 중단한다.

## 재실행과 원자성

native 수집 전에 manifest/config/binding 파일의 바이트 해시와 worker 등록 mapping을 고정한다. worker 재실행과 DB 복원도 현재 native 관측이 필수다. running·확인 불가·잘못된 mapping은 과거 종료 snapshot으로 우회할 수 없다. 각 native 관측은 별도의 비공개 native-check 파일로 남기며 원래 metadata/evidence 바이트를 변경하지 않는다. 각 원본은 재실행마다 다시 읽고 저장된 측정·귀속·세션·경로·구간 context와 대조한다. 정상적으로 뒤에 추가된 턴 때문에 전체 transcript 해시가 바뀐 것은 거부 사유가 아니다. 누락·중단·변경·모호한 원본은 저장 snapshot으로 우회하지 않는다. 동일 journal을 다른 manifest나 binding으로 다시 연결할 수 없다.

메타데이터 경로·바이트와 journal을 DB보다 먼저 보존한다. 읽기 전용 live SQLite를 비공개 clone으로 backup하고 기존 `import_usage`로 모든 항목과 물리 구간 충돌을 검사한다. clone에서 새로 생긴 event/collection만 실제 `Store.import_bundle` 한 transaction에 전달하며 request/actor 상태를 복사하지 않는다. 동시 충돌은 transaction을 rollback한다. 동일 재실행은 측정과 evidence를 중복 생성하지 않으며 DB 복원 뒤 사라진 이벤트는 같은 ID로 재수입할 수 있다.

root actor의 `trace.manual-source-recovery` explanation에는 manifest ID/digest와 선택 measurement key만 기록하며 `stop_delivery_inferred:false`, `full_usage_complete:false`를 명시한다. **Stop receipt를 만들지 않는다.** [복구 점검](completion-recovery.md)의 received-Stop coverage나 missing 판정은 바뀌지 않는다. 선택한 원본 수집 성공은 요청 전체 사용량·제품 수용·보관 완료를 의미하지 않는다.

저장 원본을 마감 근거로 사용할 때는 [v2 일괄 마감](request-completion.md#명시한-저장-측정-소스-v2)에서 `explicit_source`를 직접 선택한다. 검증 통과 메타데이터와 비공개 원본, 현재 native 종료·프로필을 별도로 확인하며 이 수집기의 성공을 Stop 전달로 바꾸지 않는다.

합성 세션 파서·SQLite와 native worker mock 검증:

```sh
python3 -m unittest discover -s tests -p test_collect_request_usage.py -v
```

실제 현재 원본의 수용 및 보관 정책은 별도 담당자가 확인한다. 이 경로는 명시한 로컬 Orca/Codex 원본에 한정한다.

테스트는 기본 `tempfile.TemporaryDirectory()`를 사용한다. `AI_WORKFLOW_TEST_TMP` 환경변수로 이미 존재하는 소유자 전용 fixture 부모 디렉터리를 명시할 수 있다. 과거 작업의 비공개 경로를 생성하거나 재사용하지 않는다.

실제 로컬 조율자의 닫힌 두 턴을 수입하고 같은 명령의 재호출에서 DB 불변을 확인했다. 별도 SQLite 사본과 빈 영수증 디렉터리에서는 실제 종료 worker 원본을 조율자 턴과 함께 회수하고, 재호출·이벤트 복원을 확인했다. 이 시험에서 Stop 미수신 상태는 그대로 유지했다. 독립 검토는 합성 원본·native mock 경계이며 실제 환경 검증과 구분한다. 작성 중인 원본의 변경 감지는 수입을 중단하며 안정된 시점에 같은 입력으로 재시도한다.

동일 호출에서 소스별로 묶은 coordinator turn이 같은 canonical 경로·프로젝트·세션을 사용하면 검증된 파싱 결과 하나를 공유한다. 복구 context도 같은 결과에서 산출한다. 메모리에는 한 소스의 파싱 결과만 유지하며, 선택된 소스들의 dev/inode/size/mtime_ns/ctime_ns를 재사용 전과 live import 직전에 확인한다. 변경되면 오래된 값을 가져오지 않고 중단한다. 새 호출, replay와 restore는 원본을 새로 파싱한다. SQLite commit 승인 직전에도 같은 속성을 확인하여 변경 시 트랜잭션을 거부한다. 전역 캐시나 원시 transcript 복사본은 만들지 않는다. 파일시스템 확인 이후의 외부 변경을 잠그는 기능은 아니다.

명시 저장 원본의 파일 준비 조건은 [읽기 전용 사전검사](source-preflight.md)로 먼저 진단할 수 있다. 실제 닫힌 worker 원본으로 재사용 객체의 동일 호출 내 한 번 파싱과 새 호출의 fresh 파싱을 확인했다. 복수 조율자 턴의 값·context·순서 보존, 원본 교체·append·변경 시 DB rollback은 합성 원본과 SQLite로 독립 검증했다. 이 확인을 실제 요청 전체의 속도 개선율로 해석하지 않는다.

## 조율자 바인딩 준비

선택한 닫힌 턴의 줄 번호·메시지 해시는 [조율자 바인딩 초안](coordinator-usage.md#명시적으로-선택한-조율자-턴의-바인딩-초안)으로 준비할 수 있다. 검토한 결과를 이 수집기의 `coordinator_turns`에 명시한다. 초안 생성과 실제 사용량 수입은 별도 단계이며, 열린 턴이나 혼합 업무를 자동 귀속하지 않는다.

단일 조율자 턴의 준비물·검토 해시 승인·수입을 연결하는 CLI는 [명시 조율자 수집 흐름](coordinator-collection-flow.md)에 있다.
