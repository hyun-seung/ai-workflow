# 배정별 토큰의 DB 연결과 집계

[native 수집기](native-record.md)가 만든 `dispatch_usage`를 기존 SQLite 기록에 연결한다. 원래 actor의 `usage`·지시·로그는 그대로 두고 `kind=usage` 이벤트와 입력 근거 collection을 추가한다. 모델 호출이나 원본 세션 경로 자동 탐색은 하지 않는다.

## 수입

요청과 담당 actor를 먼저 [조회 도구](trace-viewer.md)의 bundle/Orca snapshot 수입으로 등록한다. 다음은 가상 경로·ID다.

```sh
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 \
  import-usage /private/path/completions/native-record.json \
  --task-title '서버 구현'
```

기본 actor는 native dispatch ID다. 기존 기록이 같은 worker의 마지막 dispatch만 대표하는 경우, 실제 세션·배정 연결을 확인하고 `--actor-id existing_actor`를 명시할 수 있다. 역할명이나 이름이 같다는 이유로 자동 연결하지 않는다. 원본 native actor/dispatch ID와 명시 연결 근거를 보존한다. 다른 요청의 actor나 없는 actor는 거부한다.

새 `usage` 이벤트는 요청·담당 actor·배정별 역할·작업명·native 구간·측정값을 갖는다. 입력 파일 경로·SHA256·원본 source 참조·관측 시각은 별도 collection에 남긴다. 원본에 관측 시각이 없으면 null이며 수입 시각을 원래 관측 시각으로 바꾸지 않는다. JSON에 적힌 원본 경로를 자동으로 열지 않는다. 입력은 최대 8 MiB의 일반 JSON 파일이다.

## 마감 명령에 연결

```sh
python3 scripts/finish_worker.py \
  --project /path/to/project --session-file /private/path/session.jsonl \
  --session-id session_example --task-id task_example --dispatch-id ctx_example \
  --request-id request_example --role backend \
  --directory /private/path/completions \
  --trace-db /private/path/traces/workflow.sqlite3 --trace-task-title '서버 구현'
```

필요하면 `--trace-actor-id`로 명시 연결한다. DB는 이미 존재해야 하고 완료 기록과 마찬가지로 대상 프로젝트·Git 작업 트리 밖의 비공개 경로여야 한다. 요청·역할 없이 DB 수입만 요청하거나 DB 없이 trace 옵션만 지정하면 중단한다.

완료 JSON 저장 → 관리 위임 자리 해제(지정 시) → DB 수입 → 프로필 판정·준비된 정리 순서다. 요청한 DB 수입에 실패하면 종료 2로 멈추고 정리를 실행하지 않는다. 완료 JSON은 남으므로 같은 명령을 다시 실행하면 worker를 새로 실행하거나 원본을 다시 수집하지 않고 DB 연결을 재시도한다. 정확한 종료로 해제한 관리 자리는 DB 오류 때문에 다시 점유하지 않는다. 수집기가 미측정으로 남긴 값은 수입하지 않으며 원본 JSON에서 이유를 확인한다.

## 보존과 중복 방지

- 성공한 `dispatch`, 명시 요청/역할, 지원된 source/version, native 구간 키와 토큰 증거를 검사한다. 입력+출력=합계, 캐시/추론 포함 관계, 기준·종료값 차이와 증가분 합을 대조한다. 미측정·상충·잘못된 수치는 거부한다.
- 이벤트 ID는 물리 세션·native 턴의 측정 키에 기반하며 요청·파일 이름에 따라 달라지지 않는다. 같은 파일의 재수입은 중복되지 않는다. 다른 snapshot 파일에서 같은 수치가 나오면 측정 이벤트는 그대로 유지하고 새로운 파일 근거만 추가한다.
- 같은 물리 구간을 다른 요청/actor에 붙이거나 다른 수치·작업명으로 덮어쓰면 충돌한다. 한 dispatch에 다른 물리 구간을 추가하거나 같은 세션의 구간을 겹치게 넣어도 거부한다. 다중 시도·수정된 측정값을 승인하는 별도 revision 계약은 아직 없다.
- 기존 actor에 이미 귀속 측정값이 있으면 동일 키·동일 수치인 경우만 연결한다. 다른 legacy 값과 새 관측을 자동으로 섞지 않는다. generic bundle/JSONL 수입에도 동일한 검증을 적용한다.
- 다른 actor의 legacy 측정도 물리 세션·구간을 대조한다. 키나 수입 순서가 달라도 겹친 구간을 거부하며, 알려진 같은 세션인데 구간을 확인할 수 없으면 명시 재조정을 요구한다. 물리 정보가 있는 legacy 측정끼리도 같은 검사를 적용한다. 분리된 구간은 허용한다.
- 수입은 하나의 transaction이다. 잘못된 연결·키·숫자·구간·근거가 있으면 함께 받은 기록도 롤백한다. JSON/JSONL 내보내기에는 관측과 근거가 모두 들어가며, 복원할 때 원래 입력 파일이 없어도 과거 기록을 보존한다.

이 검사는 기록의 수학적·참조 무결성을 확인한다. 입력 작성자나 원본의 암호학적 진위를 보증하지 않는다. 수집 JSON은 직접 수정하지 않고 원천·구간·프로필을 확인한 뒤 수입한다.

## 조회 화면

한 actor에 여러 측정 배정이 연결되면 역할별 토큰 영역에서 각각의 작업을 펼친다. actor 카드는 그 담당자의 측정된 합계를 표시한다. 각 작업의 역할은 해당 측정에 명시된 역할을 따른다. 기존 actor의 원래 역할을 수정하지 않는다.

요청·역할 합계는 동일한 배정 목록에서 계산한다. 역할 합계를 다시 요청 합계에 더하지 않고 캐시 입력·추론 출력도 다시 더하지 않는다. 아직 관측이 없는 actor는 미측정 행으로 남으며, 표시된 행의 측정 비율이 실제 전체 작업 수나 전체 사용량을 증명하지 않는다. 조율자·검증·추가 작업이 누락될 수 있음을 표시한다. 읽기·검색·수정 등의 도구별 토큰은 여전히 미수집이다.

새 관측·입력 근거는 수집 자료의 일부이므로 기존 [마감 기록](trace-closeout.md)이 stale로 바뀔 수 있다. 자료와 결과 파일을 다시 확인한 뒤 새 마감 revision을 등록한다. 토큰 수입 자체를 기능 AC 통과나 프로세스 종료 증거로 사용하지 않는다.

## 검증

```sh
python3 -m unittest discover -s tests -p 'test_trace_usage.py'
node --test tests/test_trace_frontend.cjs
```

가상 자료로 재처리·다른 snapshot·교차 요청/actor·겹친 구간·generic 복원 우회·숫자·출처·실패 후 재시도를 검증한다. 실제 적용 전에는 비공개 백업과 사본 검사를 수행하고 기존 원문·actor·다른 요청의 보존, 수입 결과와 요청/역할/작업 합계를 대조한다.
