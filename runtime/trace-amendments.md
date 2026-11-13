# 여러 턴의 작업 묶음과 원본 보존 정정

물리 측정 단위와 논리 작업을 구분한다. [조율자 수집기](coordinator-usage.md)는 닫힌 native 턴 하나를 측정하고, [DB 수입](trace-usage.md)은 해당 구간을 한 번만 저장한다. 아래 기능은 명시 호출형이며 자동 세션 탐색·완료 hook·운영 DB 소급 변환을 하지 않는다.

## 여러 턴을 같은 작업으로 표시

`group-usage`는 이미 수입한 `usage` 이벤트 ID를 참조한다. 새 토큰 측정을 만들지 않는다. 같은 요청·담당 actor·측정 역할의 구간들만 묶고, 한 측정은 현재 유효한 그룹 하나에만 속한다. 서로 다른 worker dispatch도 실제 소유자를 확인해 같은 actor에 수입했다면 묶을 수 있다. 기존 worker 수집기의 단일 배정·단일 턴 제약을 완화하지 않는다.

가상 manifest:

```json
{
  "schema_version": 1,
  "id": "group_example",
  "request_id": "request_example",
  "actor_id": "coordinator_example",
  "time": null,
  "recorded_at": null,
  "detail": {
    "version": 1,
    "recorded_by_actor_id": "coordinator_example",
    "task_id": "results_example",
    "title": "결과 취합",
    "role": "coordinator",
    "measurement_event_ids": ["usage:example_one", "usage:example_two"]
  }
}
```

```sh
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 group-usage /private/path/group.json
```

위 ID는 가상 값이다. 실제 측정 ID는 export의 `kind=usage`에서 가져온다. 요청 조회의 `usage_groups`에 묶음별 입력·출력·캐시·추론·합계를 제공한다. 캐시는 입력의 부분값이고 추론은 출력의 부분값이다. 부분값이 하나라도 미측정이면 그 부분 합계는 null이다. 안전한 정수 범위를 넘는 합산은 거부한다. 요청·역할 총합은 원래 물리 측정만 합산하며 그룹 합계를 다시 더하지 않는다. 화면은 현재 `usage_groups`의 제목·구성·합계로 명시 묶음을 표시하며, 미그룹 물리 측정과 미측정 담당자를 별도로 보존한다. 묶음 상세에서 멤버 측정과 최초값·정정 이유·이력을 펼쳐 확인한다. 시간 흐름은 각 멤버의 유효 실제 구간만 표시하며 구간 사이 공백을 메우지 않는다. 요청·역할 합계는 묶음을 다시 더하지 않는다.

## 잘못된 관측과 묶음의 정정

원래 이벤트는 UPDATE/DELETE하지 않는다. `revision` 이벤트를 추가하고 원본과 이력을 함께 내보낸다. 이번 대상은 [표준 관측](trace-observations.md)의 실행·검증 기록과 위 작업 묶음이다. 토큰 원천 숫자·물리 구간·요청 이동·legacy/native 이벤트의 변경은 지원하지 않는다. 잘못된 원천 측정의 대체·제외는 별도 설계가 필요하다.

먼저 현재 값과 직전 정정 ID를 읽는다.

```sh
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 event-state \
  --request-id request_example --event-id observation_example
```

출력의 `current`를 복사해 수정하고 `detail_sha256`를 정규 JSON 해시로 다시 계산한다. 정규화는 `trace_amendments.canonical_target`으로 할 수 있다. 시간은 UTC ISO 형식 또는 null이다. 다음 manifest를 명시 작성한다.

```text
schema_version: 1
id: 새 정정 ID
request_id: 원래 요청 ID
actor_id: 기록하는 최상위 조율자 actor ID
time: 정정 발생 시각 또는 null
recorded_at: 입력 작성 시각 또는 null
detail:
  version: 1
  recorded_by_actor_id: 위 조율자 ID
  target_event_id: 최초 이벤트 ID
  previous_revision_id: event-state의 값 (첫 정정은 null)
  expected_event_sha256: event-state의 현재 값 해시
  reason: 확인한 근거와 정정 이유
  replacement: 수정한 current 전체 객체
```

```sh
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 revise-event /private/path/revision.json
```

최초 ID·요청·종류·source·source_id·completeness는 유지한다. 실행자·검증 주체 등을 바꾸면 표준 관측의 주체 규칙과 같은 요청 내 참조를 다시 검사한다. 작성자는 같은 요청의 최상위 actor여야 한다. 이는 참조 일관성 검사이며 호출자의 신원·원문 주장의 진위 인증은 아니다.

직전 정정과 현재 해시가 일치해야 한다. 분기·순환·없는 선행 기록·다른 요청 참조·원본 충돌은 트랜잭션 전체를 롤백한다. 같은 입력 재실행은 멱등적이다. JSON/JSONL 복원은 이벤트 순서에 의존하지 않는다. 원본을 제거한 일부 이력만의 복원은 거부한다.

조회의 `events`는 원본과 정정을 모두 보존한다. 정정이 있으면 `effective_events`와 `event_revisions`를 추가하며, 화면은 현재 값을 표시하고 `정정됨` 및 최초 기록·정정 이력을 펼쳐 볼 수 있게 한다. 입력 파일의 경로·SHA256·작성자는 별도 collection에 남긴다. manifest는 최대 1 MiB이며 중복 키·비유한 숫자·읽는 중 변경을 거부한다. 기록된 명령을 실행하지 않는다.

그룹과 정정도 수집 자료에 추가되므로 기존 요청 마감은 stale이 될 수 있다. 근거를 재확인한 뒤 [새 마감](trace-closeout.md)을 작성한다. 원본 파일·과거 보고서·마감을 삭제해서 현재 판정에 맞추지 않는다.
