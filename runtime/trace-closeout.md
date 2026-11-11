# 요청 결과와 조회 기록 마감

이미 수입한 요청·조율자·담당자 기록에 최종 결과를 연결한다. `trace_viewer.py close`는 명시한 결과 manifest를 한 transaction으로 추가하며 원래 이벤트·요청 상태·사용량을 덮어쓰지 않는다. 화면은 수입된 요청 상태와 별도의 마감 판정을 보여 준다. native 완료 알림이나 검증 프로그램을 자동 호출하지 않는다.

## 입력과 실행

요청과 담당자는 [기존 수입 경로](trace-viewer.md)로 먼저 연결한다. 조율자가 결과와 검증 근거를 확인한 뒤 다음 형식의 비공개 JSON을 작성한다. 아래 자료는 가상 예시이며 실제 파일 경로로 바꾸어야 한다.

```json
{
  "schema_version": 1,
  "id": "closeout-v1",
  "request_id": "request-example",
  "coordinator_id": "coordinator-example",
  "time": "2026-01-02T00:00:00Z",
  "status": "completed",
  "summary": "검증한 결과의 간결한 설명",
  "verification": "pass",
  "assignments": [
    {"actor_id": "implementation-example"},
    {"actor_id": "verification-example"},
    {"actor_id": "followup-example", "previous_actor_id": "implementation-example"}
  ],
  "artifacts": [
    {"kind": "result", "label": "최종 결과", "path": "/private/path/result.md"},
    {"kind": "verification", "label": "검증 근거", "path": "/private/path/assessment.json"}
  ],
  "limitations": ["부분 수집이며 전체 실행 기록을 뜻하지 않음"]
}
```

```sh
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 \
  close /private/path/closeout.json
```

- `time`은 마감 내용을 기록한 실제 시각이다. 과거 실행 당시의 판단처럼 쓰지 않는다.
- `status`는 completed/needs_followup/cancelled, `verification`은 pass/fail/unverified다. completed는 명시 pass와 결과·검증 파일, 수입된 모든 담당자의 종료 보고를 요구한다. 파일 내용을 해석해 검증 판정을 자동 결정하지 않는다.
- `coordinator_id`는 같은 요청에 수입된 최상위 담당자다. 나머지 모든 actor를 `assignments`에 한 번씩 포함한다. 확인된 후속 배정만 `previous_actor_id`로 연결한다. 다른 요청·없는 담당자·중복·순환 관계는 거부한다. 미수집 배정을 자동 발견하지 않는다.
- 파일은 절대 경로의 일반 파일만 읽으며 심볼릭 링크와 256 MiB 초과 파일을 거부한다. 경로의 각 요소를 부모 디렉터리 descriptor 기준으로 링크를 따르지 않고 열며, 읽은 뒤 같은 경로의 파일 식별자·내용 변경을 다시 확인한다. 이 기능은 POSIX의 `dir_fd`·`O_NOFOLLOW`를 사용하며 지원되지 않는 환경에서 다른 읽기 방식으로 대체하지 않는다. `kind`는 result/verification/other다. 수입 당시 경로·크기·SHA-256을 보존하고 파일 내용은 DB에 복사하지 않는다. 선택 `sha256`은 예상 해시와의 일치 검사다.
- 기록 파일과 DB는 비공개 위치에 둔다. 웹 서버는 이 경로의 파일을 내려주는 API를 만들지 않으며 화면에서는 경로를 텍스트로 표시한다. 결과 파일은 기존 보관 정책으로 유지한다.

## 중복·정정과 수집 상태

같은 요청과 manifest ID는 같은 completion 이벤트 ID를 만든다. 같은 입력·파일·수집 기록의 재호출은 replay이며 이벤트가 늘지 않는다. 입력·파일 내용 또는 수집 기록이 달라졌다면 기존 마감 자료를 보존하고 충돌로 멈춘다. 조율자가 새 근거를 확인한 뒤 새 ID와 더 늦은 기록 시각으로 마감한다.

화면의 `closeout.current`는 마감 당시와 현재 수집 기록의 해시 일치다. 마감 뒤 이벤트·담당자 상태·수집 근거가 추가되거나 바뀌면 `판정 재확인 필요`로 표시한다. 현재 프로세스의 상태나 디스크에 있는 결과 파일의 계속된 무결성을 증명하지 않는다. 파일 변경은 명시 재마감 때 다시 검사하며, 조회가 로컬 파일을 자동으로 읽지 않는다.

기록 범위는 partial 또는 unknown으로 유지한다. 마지막 수집 시각이 없으면 미확인이다. 업무 판정 pass·담당자 종료 보고를 전체 transcript 수집이나 프로세스 종료로 바꾸지 않는다. 실제 프로세스 종료와 원본 메타데이터 수집은 [완료 처리](completion.md)의 별도 근거를 따른다. 사용량의 미측정·누적 범위는 그대로 유지하고 합산하지 않는다.

## 교환 형식과 검증

기존 v1 bundle에 `kind: completion`, `source: trace.closeout` 이벤트를 추가한다. 요청 조회의 `closeout`은 이 이벤트에서 계산한 표현이며 별도 저장 레코드가 아니다. JSON/JSONL 내보내기·재수입은 마감 이벤트와 출처를 보존한다. completion을 모르는 이전 버전은 수입을 거부할 수 있으므로 같은 기능 버전으로 복원한다. 기존 DB 테이블 변경은 없다.

재수입도 같은 요청의 최상위 조율자와 배정 참조를 검증하며, 조율자가 자신의 배정 목록에도 중복 포함된 기록은 거부한다. 과거 마감 복원에서는 현재 결과 파일이나 이후 추가된 담당자 상태를 다시 평가하지 않는다. 따라서 나중에 새 실행이 추가된 과거 마감도 원문 그대로 복원하고 `판정 재확인 필요`로 표시할 수 있다.

```sh
python3 -m unittest discover -s tests -p test_trace_closeout.py
python3 -m unittest discover -s tests -p test_trace_viewer.py
node --test tests/test_trace_frontend.cjs
```

독립 배정·후속 관계·중복 마감·충돌·새 기록·불완전 실행 보고·파일 변경·잘못된 참조·JSONL 복원과 표시 문구를 검사한다. UI의 정보 구성·사용 흐름·디자인·가독성 개편은 다음 별도 작업이며 이번에는 최종 결과 표시에 필요한 범위만 추가한다.
