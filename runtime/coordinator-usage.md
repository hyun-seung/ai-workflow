# 조율자 실행 턴의 토큰 수집

하위 worker는 생성된 Orca 배정 머리말로 연결한다. 조율자의 일반 대화에는 그런 dispatch가 없으므로 `scripts/native_turn_record.py`는 별도의 명시 연결을 사용한다. dispatch ID를 만들거나 세션 누적값을 요청별로 복제하지 않는다. 현재 Orca·Codex의 닫힌 단일 native 턴만 지원한다.

## 연결과 실행

비공개 binding JSON에 요청·기존 최상위 조율자 actor·논리 작업과 정확한 native 턴·사용자 메시지 행/해시를 기록한다. 아래는 가상 값이다.

```json
{
  "schema_version": 1,
  "request_id": "request_example",
  "actor_id": "coordinator_example",
  "task_id": "request_example_results",
  "role": "coordinator",
  "native_turn_id": "turn_example",
  "message_line": 4,
  "message_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
}
```

`message_sha256`는 해당 `response_item`의 `payload.content`를 `json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')`로 직렬화한 SHA256이다. 예시의 0 값은 실제 해시가 아니다. 필요한 파일·세션·턴은 해당 요청의 명시된 기록에서 확인하며 세션 폴더를 일괄 탐색하지 않는다.

```sh
python3 scripts/native_turn_record.py \
  --project /path/to/project \
  --session-file /private/path/session.jsonl --session-id session_example \
  --binding /private/path/coordinator-binding.json \
  --output /private/path/coordinator-turn.json
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 \
  import-usage /private/path/coordinator-turn.json --task-title '결과 취합'
```

논리 요청 연결은 호출자가 명시한 결정이다. 원본에서 요청 ID를 관측했다고 주장하거나 입력 작성자의 진위를 보증하지 않는다. 해시는 선택한 메시지·snapshot의 일치 확인에 사용한다. binding 파일은 최대 1 MiB의 안정된 일반 JSON이며 중복 키·읽는 중 변경을 거부한다.

## 측정 조건

- 동일한 native 턴의 유일한 시작·정상 종료, 중첩·중단·ID 재사용 부재를 확인한다. 현재 진행 중인 조율자 턴은 닫힌 뒤 수집해야 한다. 이 명령은 완료 hook이 아니다.
- 턴 안에 사용자 메시지가 정확히 하나 있어야 하고 지정한 행·내용 해시·확인 가능한 routing 턴과 일치해야 한다. 추가 사용자 메시지가 있으면 같은 논리 요청이라고 가정해 합산하지 않는다. 여러 메시지·턴의 귀속은 후속 범위다.
- routing이나 turn_id가 실제로 없는 경우와 명시됐으나 잘못된 경우를 구분한다. 명시한 null·잘못된 형식·유효하지 않은 ID를 부재로 바꿔 허용하지 않는다. 하위 worker 배정에도 같은 검사를 적용한다.
- 생성된 worker 배정 머리말이 있는 턴은 이 경로로 수집하지 않는다. 기존 [배정 수집기](native-record.md)를 사용한다.
- 기준 누적값, 각 증가분과 `last_token_usage`, 종료값을 하위 배정과 같은 계산 함수로 대조한다. 누락·상충·상속 기준 미확인·메시지 앞 측정·턴 밖 측정은 미측정으로 남긴다.
- 출력에는 요청 원문·도구 결과·내부 추론을 저장하지 않는다. 원본 경로와 메타데이터는 비공개다. 출력은 프로젝트·Git 밖의 비공개 폴더에 새 파일로만 저장한다.

연결·저장 성공은 종료 0, 입력/저장 오류는 종료 2다. 성공 저장 안에서도 `turn_usage.status=unavailable`일 수 있으므로 상태와 `issues`를 확인한다. 미측정 JSON을 측정 이벤트로 수입하지 않는다.

## DB와 합계

`turn_usage.version=2`, `scope=native_turn`, `role=coordinator`, `dispatch_id=null`을 사용한다. 기존 worker의 `dispatch_usage.version=1` 계약은 유지한다. DB의 명시된 최상위 actor와 일치해야 하며 다른 actor로 재연결하지 않는다. 근거 collection과 원래 actor를 보존하는 방식은 [DB 연결](trace-usage.md)을 따른다.

두 형식은 같은 물리 `session/turn` 키를 사용한다. worker/조율자·다른 역할·다른 파일·수입 순서가 달라도 물리 키·구간이 겹치면 거부한다. 원래 actor에 적힌 누적값이나 검증되지 않은 native 턴 값을 화면 합계로 승격하지 않는다.

화면은 `조율자 턴 측정`으로 구분한다. 일부 상태 확인·결과 보고 턴이 측정됐어도 요청의 모든 조율·승인 검토·추가 업무가 포함됐다는 뜻은 아니다. 측정된 작업 행의 비율과 요청 전체 수집 완전성을 구분하고, 알고 있는 보류 항목과 이유를 마감·수집 범위 설명에 남긴다. 도구별 읽기·검색·수정 토큰은 이 수치에서 나눌 수 없다.
