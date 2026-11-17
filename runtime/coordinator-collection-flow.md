# 명시 조율자 수집 흐름

`scripts/coordinator_collection_flow.py`는 하나의 닫힌 Orca/Codex 조율자 native 턴을 선택하고, 바인딩·manifest·측정 범위를 검토한 뒤 기존 [명시 원본 수집기](request-usage-collection.md)에 연결한다. 자동 업무 추론이나 현재 열린 턴 수집은 지원하지 않는다. [바인딩 초안](coordinator-usage.md#명시적으로-선택한-조율자-턴의-바인딩-초안)의 기존 파서와 DB 검사를 재사용하며 기존 공개 API는 변경하지 않는다.

## 입력과 준비

별도 flow recipe version 1은 아래 필드를 **모두 정확히** 받는다. draft recipe version 1과 다른 형식이며 `hook_config`, `task_title`, `collection_id`, `collection_directory`가 추가된다. ID와 경로는 가상 예시다.

```json
{
  "schema_version": 1,
  "project": "/path/to/project",
  "trace_db": "/private/example/inputs/trace.sqlite3",
  "hook_config": "/private/example/inputs/hook.json",
  "request_id": "request_example",
  "actor_id": "coordinator_example",
  "task_id": "logical_task_example",
  "task_title": "선택한 결과 취합",
  "session_file": "/private/example/sources/session.jsonl",
  "session_id": "session_example",
  "native_turn_id": "turn_example",
  "scope": "single_work",
  "collection_id": "collection_example",
  "collection_directory": "/private/example/collected"
}
```

호출자는 턴 안의 **모든** 사용자 메시지가 같은 논리 작업이라고 `single_work`를 선언한다. 혼합 업무는 입력하지 않는다. 원문 의미를 분석해 거짓 선언을 판별하는 기능은 없다. task_id는 명시한 논리 작업 ID이며 native Task ID라는 주장이 아니다. project/DB/hook/source는 기존 canonical 절대 경로여야 하며 DB에는 해당 요청의 최상위 coordinator actor가 이미 있어야 한다. hook config의 project와 trace_db는 recipe와 같아야 한다. 현재 열린 턴, 잘못된 routing·ID, 누락·중첩·모호한 턴, 미측정·한도 초과 원본은 거부한다. 기존 파서의 크기 한도를 올리지 않는다.

recipe·DB·hook config와 준비 산출물은 프로젝트/Git 밖의 소유자 전용 비공개 경로를 사용한다. 준비 디렉터리와 수집 디렉터리는 서로 겹치지 않고 모든 입력 파일의 부모 밖에 있어야 한다. 기존 파일은 덮어쓰지 않는다. 준비 파일의 동일 바이트 재실행은 inode/mtime을 보존하며 다른 입력·출력 충돌은 거부한다. 실패 시 일부 준비 파일이 남을 수 있으므로 수정한 입력은 새로운 비공개 준비 경로에 검토한다.

```sh
python3 scripts/coordinator_collection_flow.py prepare \
  --input /private/example/inputs/flow.json \
  --directory /private/example/prepared
```

준비는 `draft_coordinator_binding.build`를 호출하여 `draft-recipe.json`, `binding.json`, `manifest.json`, `review.json`을 생성한다. `prepare.lock`은 동시 준비를 막는 비공개 잠금 파일이다. 원문·도구 결과·추론은 저장하지 않는다. 준비는 DB 쓰기, native 호출, hook 실행을 하지 않는다. 반환값은 `requires_review:true`, `execution_authorized:false`이며 자동 승인하지 않는다.

## 검토와 명시 수입

review.json에서 요청·actor·프로젝트·DB·native coordinator config·세션/파일/닫힌 턴·논리 작업·제목·바인딩의 모든 메시지 행/해시·manifest·선택 측정값·제외 범위를 검토한다. JSON과 경로·해시·검토 내용 자체도 비공개다. CLI의 `approve_sha256`는 **review.json 전체 JSON 값**을 `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')`로 직렬화한 SHA256이다. digest 자체는 review 내부에 넣지 않는다. 키 순서/공백은 정규 해시에 포함되지 않지만 기존 준비 파일의 바이트 변경은 replay 검사에서 거부한다. recipe·config·binding·manifest는 별도로 바이트 해시를 고정하고 선택 source의 파서 해시와 측정값도 고정한다.

검토자가 확인한 정확한 해시를 명시한다. 해시를 자동 조회·복사해 승인하는 스크립트로 검토를 대신하지 않는다.

```sh
python3 scripts/coordinator_collection_flow.py import \
  --input /private/example/inputs/flow.json \
  --directory /private/example/prepared \
  --approve-sha256 REVIEWED_64_HEX_SHA256 \
  --orca-cli /path/to/orca
```

수입은 현재 recipe/config/DB root/source로 준비를 다시 검증한 뒤 기존 `collect_request_usage.collect`를 호출한다. 다른 요청·actor·source·config·binding·측정 또는 제목으로 재연결하지 않는다. 준비 이후 source append도 해시가 달라지므로 새 준비·검토가 필요하다. 동일 입력의 수입 replay는 기존 journal과 물리 session/turn 중복 검사로 중복 이벤트를 만들지 않는다. 현재 `ORCA_AGENT_SESSION_ID`와 hook config의 native coordinator가 같아야 한다. worker는 이 진입점에서 선택하지 않으므로 native worker 호출도 없다. 수집기의 fresh source 검증, 읽기 전용 DB staging, 물리 구간 충돌 및 원자적 DB 수입을 유지한다.

승인 해시는 검토한 내용의 일치 확인이며 검토자의 신원 증명이나 OS/Orca 실행 권한 부여가 아니다. 같은 사용자에 의한 파일시스템 변경을 잠그는 기능은 없으며 외부 동시 변경을 완전히 방지한다고 주장하지 않는다. 실제 이전 기록·사용자 DB로 이 명령을 시험하지 않는다.

성공은 exit 0, 준비 부적격/미측정은 exit 1, 잘못된 입력·승인·IO·충돌·수입 실패는 exit 2의 제한된 JSON 오류다. 원본 오류 원문은 출력하지 않는다.

## 수집 범위와 검증

coverage는 `selected_coordinator_turn_only`, `stop_delivery_inferred:false`, `full_usage_complete:false`다. 현재 열린 턴, 선택하지 않은 턴, worker 사용량과 도구별 토큰은 미측정으로 표시한다. Stop 전달, native finish, 요청 마감과 업무 의미 분류는 제외한다. 성공은 전체 요청의 완전 수집이나 Stop 수신을 뜻하지 않으며 부모가 별도로 Stop/exit/사용량 수입을 마감해야 한다.

```sh
python3 -m unittest discover -s tests -p test_coordinator_collection_flow.py -v
python3 -m unittest discover -s tests -v
```

기본 TemporaryDirectory 합성 원본과 SQLite로 CLI 준비·수입·replay, 변경 승인 거부, 열린/모호한 턴, 잘못된 ID·중복 키·한도·경로·출력 충돌, DB 불변·원자성·물리 중복과 원문 비저장을 검사한다. 실제 환경의 전체 사용량·권한·사람 검토를 이 합성 검사로 증명하지 않는다.
