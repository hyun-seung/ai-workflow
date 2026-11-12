# 실행자와 대상을 구분하는 관측 입력

명령을 실행한 사람, 제품 담당자, 검증 대상, 판정자와 기록 작성자를 별도로 적는다. 조율자가 백엔드 산출물을 시험했다면 명령의 실행자는 조율자다. 백엔드는 제품 담당자와 검증 대상이 될 수 있지만 그 이유로 실행자가 되지는 않는다.

`scripts/trace_observation.py`의 `build_event`와 조회 도구의 `import-observation`은 이 명시 입력으로 event의 actor를 정하고 저장 전에 검사한다. SQLite 테이블이나 기존 exchange `schema_version: 1`은 바꾸지 않고 새 event에 `attribution`을 추가한다. 기존 기록의 실행자를 재해석하거나 이전 오류를 다시 수정하지 않는다.

## 연결 값

| 값 | 의미 |
| --- | --- |
| `recorded_by_actor_id` | 이 관측을 작성·수집한 actor. 실제 실행자와 다를 수 있음 |
| `executor_actor_id` | 해당 명령을 실제로 실행한 actor |
| `product_owner_actor_id` | 산출물을 담당한 actor. 모르면 null |
| `verification_subject_actor_id` | 검증 대상 산출물의 담당 actor. 대상이 없거나 모르면 null |
| `verifier_actor_id` | 검증 판정을 내린 actor. 명령만 관측해 판정자가 없으면 null |

이름이나 역할로 값을 추측하지 않는다. nullable 값도 입력에 명시한다. 각 ID는 같은 요청에 미리 등록된 actor여야 한다. 한 actor가 구현·실행·자체검증을 맡았다면 같은 ID를 여러 곳에 사용할 수 있다. 서로 다른 ID라는 사실만으로 독립 검증을 증명하지 않는다.

## 입력과 실행

다음은 가상 요청의 입력이다. 복사할 수 있는 전체 예시는 [가상 관측](../tests/fixtures/traces/observation.json)에 있다.

```json
{
  "schema_version": 1,
  "id": "example-command",
  "request_id": "example-request",
  "kind": "tool_call",
  "time": null,
  "recorded_at": null,
  "attribution": {
    "version": 1,
    "recorded_by_actor_id": "coordinator_example",
    "executor_actor_id": "coordinator_example",
    "product_owner_actor_id": "backend_example",
    "verification_subject_actor_id": "backend_example",
    "verifier_actor_id": "coordinator_example"
  },
  "detail": {
    "name": "shell",
    "input": {"command": ["python3", "example.py"]},
    "state": "completed",
    "exitCode": 0
  }
}
```

```sh
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 \
  import-observation /private/path/observations/command.json
```

요청과 actor를 먼저 [bundle 수입](trace-viewer.md)으로 등록한다. 입력에 `actor_id`를 따로 추가하지 않는다. 생성기가 실제 실행자 또는 판정자로부터 event actor를 정하므로 제품 담당자를 복사해 연결하는 오류를 줄인다. 이 명령은 입력에 적힌 명령을 실행하지 않는다.

- `tool_call`: 실제 실행자가 필요하다. `detail.name`, 객체 `input`, `state=completed`, 정수 `exitCode`로 종료된 명령만 기록한다. 종료 2인 음성 시험도 명령 완료로 관측할 수 있으며 업무 판정은 별도다.
- `tool_result`: 실제 실행자가 필요하다. `stdout`, `stderr` 문자열과 정수 `exitCode`를 기록한다. 호출·결과를 자동 연결하거나 둘의 일치를 검증하는 기능은 이번 범위가 아니다.
- `verification`: 판정자와 검증 대상이 필요하다. `executor_actor_id`는 null로 두고 `detail.verdict=pass|fail|unverified`와 `summary`를 적는다. 명령 실행과 판정은 다른 관측이다.

`time`은 원래 사건 시각, `recorded_at`은 관측을 기록한 시각이다. 확인하지 못한 값은 null이며 수입 시각으로 채우지 않는다. 수입 파일 경로·SHA256·작성 actor·recorded_at은 별도 collection에 남긴다. 입력은 최대 1 MiB의 안정된 일반 JSON 파일이며 중복 키·비정상 숫자·읽는 중 변경을 거부한다. `NaN/Infinity`뿐 아니라 `1e999`처럼 float 범위를 넘는 수도 거부하며, 개인정보 필터로 지워질 필드의 수치도 먼저 검사한다. 명령·출력·경로에 실제 자료가 있으므로 [비공개 보관 기준](../principles/privacy.md)을 적용한다.

## 저장 전 검사와 한계

새 source `trace.attributed-observation.v1`은 attribution이 필수다. 일반 JSON/JSONL bundle 수입에도 같은 검사를 적용하며, attribution만 다른 source에 붙이거나 새 source의 attribution을 없애도 거부한다. 지원하지 않는 버전은 자동으로 해석하지 않는다.

관측과 입력 근거 collection의 보호된 source namespace는 개인정보 필터 전후에 검사한다. source 문자열의 일부가 필터에서 지워져 일반 legacy 근거처럼 수입되는 것을 허용하지 않는다.

명령 event의 actor는 executor, 판정 event의 actor는 verifier와 일치해야 한다. 작성자·실행자·제품 담당자·검증 대상·판정자 중 하나라도 없거나 다른 요청의 actor라면 transaction 전체를 롤백한다. 확인하지 못한 선택값은 null로 유지한다.

같은 파일의 재수입은 event와 근거를 중복 생성하지 않는다. 같은 내용의 다른 파일은 event를 유지하고 새 파일 근거만 추가한다. 최종 관측은 결과·귀속을 바꿔 덮어쓰지 않는다. 기존 live tool-call의 상태 갱신 동작은 유지하지만 이 새 계약은 종료된 관측만 대상으로 한다. 정정 revision은 후속 범위이며, 새 ID로 잘못된 관측을 숨기거나 두 번의 실제 실행처럼 기록하지 않는다.

기존 attribution 없는 입력은 호환을 위해 수입할 수 있다. 이 검사는 새 보호 source 또는 attribution이 있는 관측에 적용되며, 과거 legacy source의 실제 실행자까지 확인됐다는 뜻은 아니다. 기록된 주장이 사실인지, 누가 정말 명령을 실행했는지는 실행 도구의 근거와 대조해야 한다. 파일 해시는 입력 일치를 확인하며 작성자의 진위를 증명하지 않는다.

기존 [토큰 수입](trace-usage.md), 원래 actor, 원본 로그와 사용량은 변경하지 않는다. 이 입력은 토큰 귀속·명령별 토큰 측정·전체 자동 수집·실제 프로필 갱신 기능이 아니다. 관계는 저장되고 원문 상세에서 확인할 수 있으며 별도 화면 구성은 후속이다.

## 검증

```sh
python3 -m unittest discover -s tests -p 'test_trace_observation.py'
```

가상 자료로 독립 실행·자체검증·미확인 대상, 잘못된 actor 연결과 교차 요청, generic 수입 우회, transaction rollback, 불변 관측, replay·JSONL 복원과 기존 입력 호환을 확인한다.
