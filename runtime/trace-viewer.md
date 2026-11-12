# 요청 실행 기록과 로컬 조회

명시적으로 작성·전달된 판단 근거 요약, 지시와 행동·결과를 요청 단위로 모은다. 내부 추론을 저장하는 기능이 아니다. 신규 도구는 Python 표준 라이브러리만 사용하며 기존 `workflow.py`·역할 실행·JSON usage 기록을 변경하지 않는다. Orca native snapshot을 수입하며 native 실행을 자동 추적한다고 주장하지 않는다.

SQLite에는 요청·actor·허용된 지시/명령/결과·검증·마감과 수입한 토큰 관측을 저장한다. 원본 실행 로그·소스·보고서 보관본은 별도 파일로 유지하고 입력 근거 및 마감에서 경로·해시를 연결한다. 수집한 범위만 저장하며 전체 대화·모든 작업의 토큰이 자동으로 들어간다고 가정하지 않는다.

최종 결과·검증 파일과 담당자/후속 작업을 연결할 때는 [요청 마감](trace-closeout.md)을 사용한다. 수입된 요청 상태와 마감 판정·수집 범위를 구분하고, 마감 이후 수집 기록이 바뀌면 재확인이 필요하다고 표시한다.

## 실행과 비공개 저장

사용자가 정한 **Git 작업 트리 밖의 비공개 디렉터리**를 명시한다. 아래 경로는 가상값이다. 운영 기록 생성·실제 자료 수입은 사용자의 승인된 위치와 범위에서 수행한다.

```sh
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 import tests/fixtures/traces/sample.json
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 import-orca /private/path/snapshot.json --request-id request-example --title '요청 제목' --role backend
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 import-usage /private/path/native-record.json --task-title '서버 구현'
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 import-observation /private/path/observation.json
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 list --q example
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 export --request-id request-example
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 serve --port 8765
```

브라우저 주소는 `http://127.0.0.1:8765`다. localhost 별칭·다른 Host/Origin은 허용하지 않는다. 서버 종료는 Ctrl-C다. 검증을 위해 시작한 서버·탭은 검증 뒤 종료한다. 외부 자산·텔레메트리·쓰기·실행·승인 UI는 없다. 가상 fixture는 실제 관측이 아니다.

## 요청별 대시보드

검색 없이 최신 요청을 먼저 선택한다. 접수 시각이 있는 요청은 그 시각의 내림차순이며, 시각 없는 요청은 그 아래에 최근 등록순으로 표시한다. 상태 갱신이나 같은 자료의 재수입은 등록 순서를 바꾸지 않는다. 화면은 최대 100개 요청을 조회한다. CLI의 `list --q`와 API 검색은 유지한다.

- 상단: 요청 요약, 원문·보존 기록 펼치기, 상태, 참여한 담당 실행 수, 측정된 요청 토큰, 전체 실행 시간, 수집된 기록 수.
- 본문: 조율·계획 → 담당 업무 → 결과·검증의 역할별 카드. [공통 역할](../roles/README.md)의 조율자·설계자·디자이너·백엔드 개발자·프론트엔드 개발자·검증자에 고정 색상을 사용한다. 작업명은 역할명 아래에 따로 표시한다. 기록된 상위 담당자와 후속 배정 관계를 표시하며 카드 배치와 길이는 시간이나 병렬 실행을 의미하지 않는다.
- 담당자 상세: 전달 지시, 확인 모델, 입력·출력·합계 토큰, 실제 명령·도구 결과, 작업 설명·반환 결과. 조율자가 내린 지시와 담당자가 받은 지시는 각각 보존하며 근거 없는 연결을 만들지 않는다.
- 하단: 요청 전체 → 역할별 합계 → 작업별 입력·출력·합계 → 세부 항목의 펼치기. 접힌 전체 기록과 역할/이벤트 AND 필터, 수집 범위·출처를 유지한다. 원문 JSON·해시·원본 시각을 펼쳐 확인할 수 있으며 시각 표시는 UTC다.

요청의 명시 `summary`가 없으면 보존된 요청 본문 일부를 **발췌**로 표시한다. 새 요약이나 판단을 자동 생성하지 않는다. 일부 과거 지시는 도구 안내에서 잘려 있어 카드에 그 사실을 표시하고 원문을 유지한다. 전체 실행 시간은 명시 `request.execution.started_at/finished_at`이 있을 때만 계산하며 이벤트 간격으로 추정하지 않는다. 수입 actor 수는 고유 사람·세션 수나 전체 배정 횟수가 아니다.

모바일에서는 요청 목록이 선택 후 접히고 단계별 카드가 세로로 이어진다. 카드와 원문은 hover 없이 클릭·키보드로 펼친다. 부분 수집·미측정·판정 재확인 표시를 유지하며, 수집 완료를 실제 작업 완료로 취급하지 않는다. 새로고침은 명시 동작이고 자동 polling은 없다.

### 역할과 작업명 보완

새 actor는 `role` 또는 `role_id`에 `workflow.json`과 같은 ID(`coordinator/architect/designer/backend/frontend/verifier`)를 사용하고 `task_title`에 업무를 적는다. 기존의 자유 형식 역할을 임의로 백엔드/프론트엔드로 추론하지 않는다. 분류할 수 없으면 중립 색상과 `역할 미분류`로 표시한다.

기존 actor를 보완할 때는 명시 `assignment` 이벤트의 `detail.actor_roles`에 `{actor_id, role_id, task_title}` 배열과 사후 기록·판단 근거를 넣어 수입한다. 원래 actor와 지시는 보존되며 표시 역할만 이 근거를 사용한다. 대상 actor는 같은 요청에 있어야 하고, 한 이벤트 안의 중복과 모르는 역할 ID는 거부한다. 서로 다른 역할 지정이 충돌하면 자동 선택하지 않는다. 자료를 추가하면 기존 마감은 재확인이 필요하며, 결과 파일과 근거를 확인한 뒤 새 마감 revision을 등록한다.

### 토큰 표시·합산 계약

화면의 표시·합산 규칙과 [수집 JSON의 DB 연결](trace-usage.md)을 구현했다. 명시 `import-usage` 또는 완료 명령의 `--trace-db`로 연결하며 원래 actor 값은 갱신하지 않는다. 새 관측이 있는 actor는 배정별 행과 담당 합계를 표시한다. 세션 누적값은 참고값으로 분리하고, 배정별 관측이 없는 과거 자료는 `—`·미연결/미측정으로 표시한다.

화면은 actor의 usage에 다음 조건이 **모두** 있을 때 배정별 집계에 포함한다.

- `status=measured`, `scope=dispatch`, `task_attributed=true`.
- usage의 `actor_id`와 `request_id`가 해당 actor와 일치.
- 비어 있지 않은 원천 `source`와 측정 구간 식별자 `measurement_key`.
- `measurements.input_tokens/output_tokens`가 0 이상인 안전한 정수. 선택 `total_tokens`는 둘의 합과 일치.

`source + measurement_key`가 같은 입력이 여러 actor에 있으면 모두 중복 참고값으로 분리한다. 캐시 입력·추론 출력은 각 입력·출력에 포함된 세부값이며 다시 더하지 않는다. 일부 실행만 측정됐다면 측정된 합계와 `측정 실행 수/수입된 실행 수`를 함께 표시한다. 모든 수입 실행이 측정되어도 전체 요청의 수집이 완전하다는 뜻은 아니다. 이 계약은 원천 측정의 진위를 증명하지 않으므로 입력 작성자가 호출·세션·배정 구간의 근거를 보존해야 한다. 기존 actor 사용량의 사후 갱신·정정 자동화는 제공하지 않는다.

역할별 합계는 이 중복 검사를 거친 동일 작업들을 역할로 묶어 계산하며 작업 값을 다시 요청 합계에 더하지 않는다. 세부 항목은 입력 전체·캐시 입력·캐시 외 입력, 출력 전체·추론 출력·추론 외 출력, 총 토큰을 표시한다. 세부값이 없는 경우 차감값도 만들지 않는다. 역할의 세부 합계는 측정된 작업 모두가 해당 세부값을 제공할 때만 계산한다.

파일 읽기·검색·수정·명령 실행별 토큰 귀속은 현재 수집하지 않는다. 해당 행은 `도구별 귀속 미수집`으로 표시한다. 도구 호출 횟수, 파일 크기나 응답 문자열의 길이를 토큰으로 바꾸지 않는다. 파일 내용이 모델 입력에 포함되는 것과 그 파일 읽기만의 실제 사용량이 측정되는 것은 구분해야 한다. 현재 작업별 표시는 수입 actor/배정 단위이며, 그 안의 개별 명령까지 사용량을 배분한 것이 아니다.

`run_records.validate_records_dir`의 Git/프로젝트 밖·소유권·private directory 검사를 재사용한다. 새 디렉터리는 0700, DB는 0600이며 SQLite journal/sidecar도 검사한다. 기존 경로의 권한을 바꾸지 않는다. symlink 경로는 거부한다(시스템 `/tmp`, `/var` 별칭도 포함하므로 macOS에서는 실제 `/private/...` 경로를 명시). 공유·의심 경로는 사용하지 않는다. 이 검사는 같은 OS 사용자나 악성 동시 파일 교체에 대한 보안 격리가 아니다. DB·백업·내보낸 원문은 비공개로 취급한다.

## 교환 계약 v1

저장 구현과 UI/정규화 경계는 JSON 객체 계약이다. `trace_store.Store`는 현재 SQLite 구현이며 `requests(q, limit)`, `request(id)`, `import_bundle(bundle)`을 제공한다. 현재 실제 구현·검증한 저장소는 SQLite뿐이다. 파일 저장 모드는 추후 구현 대상이며, MongoDB는 기존 운영 기반 등 조건이 맞을 때, PostgreSQL은 중앙 기록 운영의 후보로 검토한다. 이들의 어댑터·저장소 선택 설정은 아직 구현하지 않았다. 비교 근거와 권고는 [저장 방식 선택](storage-options.md)을 따른다. 대형 공통 저장 엔진은 도입하지 않는다.

JSON bundle: `schema_version: 1`, `requests: []`, `actors: []`, `events: []`, 선택 `collections: []`. 예시는 [가상 bundle](../tests/fixtures/traces/sample.json), [가상 native snapshot](../tests/fixtures/traces/orca-snapshot.json)을 따른다. `export`는 같은 bundle을 stdout으로 반환하며 `--format jsonl`로 같은 교환 계약의 줄별 출력도 제공한다. 출력 리디렉션 파일은 사용자가 private 권한을 보장한다. JSONL 수입은 `.jsonl` 확장자로 선택하며 각 줄은 `{"schema_version":1,"type":"request|actor|event|collection","record":{...}}`다. bundle export를 JSONL로 변환할 때 각 배열 요소를 해당 type의 record로 감싸면 된다.

공통: `id`, `source`, `source_id`, `time`(timezone 포함 ISO 8601 또는 null), `completeness`(complete/partial/unknown 같은 출처 설명). ID는 영문·숫자 및 `_.:-`로 1–200자다.

- request: `title`, `status`, 가상 표시용 `sample: true`.
- actor(실행): `request_id`, `parent_id`, `role`, `environment`, `provider`, `status`, `requested: {model, effort}`, `observed: {model, effort}`. 요청과 확인값은 독립적이며 미확인은 null이다. native 수입은 workflow.json을 읽지 않으며 확인된 모델로 채우지 않는다.
- event: `request_id`, 선택 `actor_id`, `kind`, `detail`. kind는 user_request/decision/assignment/instruction/tool_call/tool_result/question/approval_request/approval_resolution/handoff/verification/explanation/completion이다. completion은 별도 마감 계약을 검증한다. 상세의 정규 JSON UTF-8 SHA256을 `detail_sha256`으로 저장한다. 원본 파일 전체 바이트의 해시와 다르다. 원문 지시는 detail에 보존하되 내부 추론은 제외한다. 사후 요약은 detail에 `recorded_retrospectively: true`와 작성시각·근거를 명시하고 당시 판단처럼 표현하지 않는다.
- `usage` 이벤트는 별도의 [측정·연결 계약](trace-usage.md)을 검증하며 기존 actor를 보존한다. 관측·근거는 기존 event/collection 배열로 JSON·JSONL 수입·내보내기를 지원한다.
- 최종 명령·검증 관측은 선택 [실행자/대상 계약](trace-observations.md)의 `attribution`을 지원한다. 이 source의 명령 actor는 실제 실행자, 판정 actor는 판정자다. 입력을 작성한 actor와 제품/검증 대상은 별도로 보존한다.
- usage: actor에 연결한다. `source`, `scope`, 비어 있지 않은 `measurements`가 있어야 저장 시 measured이며 없거나 `measurements={}`이면 unavailable이다. measurements는 비음수 수치다. 저장 시 measured라는 사실만으로 배정 귀속을 인정하지 않으며, 화면 합산에는 위의 더 엄격한 계약을 적용한다. 공급자의 누적/턴 범위 해석과 측정 근거는 입력 작성자가 명시한다.
- collections: snapshot source/sourceIdentity, native source, cursor/nextCursor/limited, 반환 개수·경고·원본 상태와 생략 블록 수를 보존한다. snapshot 범위는 별도이며 이벤트 전부를 수집했다는 증거가 아니다.

수입 파일은 최대 8 MiB다. bundle 전체는 한 transaction이며 잘못된 ID/참조/내용 충돌은 전체 롤백한다. 같은 ID·같은 내용은 중복 수입해도 늘지 않는다. request/actor는 명시적 status만 갱신할 수 있다. completed/finished/failed/succeeded/cancelled 이후 running/pending/unknown snapshot은 이전 상태를 보존한다. tool_call은 같은 입력에 한해 state/exitCode/durationMs만 갱신한다. resolved는 승인·거절이 아니라 원본 상태 문자열이다. 완료 상태에서 진행 상태로 되돌리지 않는다. 나머지 동일 ID의 다른 내용(잘린 출력이 나중에 바뀐 경우 포함)은 충돌로 거부한다. 원본 정정은 별도 출처/ID를 사용해야 하며 지시·결과를 조용히 덮어쓰지 않는다. 서로 다른 terminal 상태 간 마지막 명시 수입값은 저장되며 collections에서 관측 근거를 확인한다.

## Orca snapshot 범위와 한계

`orca orchestration worker-read --json`을 호출한 **결과 파일을 명시 입력**한다. importer가 Orca를 호출하거나 페이지를 추가 수집하지 않는다. 설치 CLI에서 관측한 `result.transcript.messages[].blocks`의 text/tool-call/tool-result만 지원하며 role/source/시각·tool input/output·state·callId를 보존한다. user text는 전달 지시로, 일반 assistant text는 명시된 설명으로 표시하며 내용에서 결정·업무 배정·성공을 추론하지 않는다. system의 `Run a command?`는 승인요청으로만 표시한다. 해결 상태만으로 승인/거절 이벤트를 만들지 않는다. 부모·역할은 `--parent-id`, `--role`로 명시하고 provider 외 모델/effort는 미확인이다.

`import-orca`의 request 메타데이터는 CLI의 명시 `--request-id`와 `--title`에서 만든다. request의 `source=trace.explicit-request`, `source_id=request_id`, time=null/completeness=unknown은 사용자가 지정한 공통 연결 정보이며 worker에서 관측한 사용자 요청이라는 뜻이 아니다. dispatch가 달라도 같은 ID·제목은 같은 request를 만들고, actor/event/collection은 각각 실제 Orca source·dispatch 연결을 유지한다. 조율자 actor와 decision/assignment/instruction 이벤트를 별도 bundle로 먼저 또는 나중에 같은 요청에 연결할 수 있다. 기존 request를 bundle에 포함할 때는 이 공통 메타데이터를 그대로 사용하거나 기존 request를 생략하고 actor/event의 request_id로 참조한다. 제목이나 출처 등 기존 명시 메타데이터가 다르면 전체 수입을 실패시키며 자동 덮어쓰기·이전 형식의 출처 정정은 하지 않는다.

`limited=true`는 partial, false라도 전체 실행 수집을 증명하지 못하므로 unknown이다. cursor·원본 source·경고를 화면에서 펼칠 수 있고, 입력/출력 자체의 잘림 여부는 미확인으로 표시한다. timestamp는 설치 버전의 epoch milliseconds로 정규화한다. 원본 메시지 ID와 block index로 이벤트 ID를 안정적으로 만든다. 알 수 없는 블록은 생략 수만 보존한다. analysis/reasoning/thinking/chain_of_thought 필드·블록·채널과 명시된 XML 추론 블록은 제외한다. 원시 snapshot 전체를 보관하지 않는다. 도구 결과에 숨은 자유 형식 추론을 자동 판별할 수 없으므로 입력 작성자는 내부 추론이 섞인 자료를 수입하지 않아야 한다.

원본 자료에 없는 이벤트를 자동 생성하지 않는다. 조율자의 별도 결정·배정·명시 모델/검증 등은 공통 bundle에 출처와 실제 기록시각을 넣어 연결한다. 설치 버전에서 지원하는 이벤트 구독·관측시각·작업/dispatch 연결·부분 페이지 병합·상태 정정 이력을 확인한 뒤 실시간 연결을 다음 범위로 검토한다. 자체 orchestration 엔진, 무제한 poller, 자동 승인·알림은 만들지 않는다. Paseo 실제 실행은 보류한다.

## 로컬 HTTP 경계·검증

127.0.0.1만 bind하며 DB는 각 API 요청에서 mode=ro/query_only로 연다. SQL 매개변수 바인딩, 검색길이/limit/ID 검증, 고정 asset allowlist, textContent 표시, CSP, no-store, Host/Origin/Fetch-Site 검사를 사용한다. 같은 OS 사용자·브라우저 확장에 대한 인증 시스템은 아니며 인터넷 공개 서버로 사용하지 않는다. 단일 HTTPServer로 최소 기능만 제공한다.

```sh
python3 -m unittest discover -s tests -p 'test_trace_viewer.py' -v
node --test tests/test_trace_frontend.cjs
python3 -m unittest discover -s tests -v
```

보안 검증은 권한·Git 경로·symlink·XSS payload 보존과 CSP·SQL injection 검색·임의 파일 경로·cross-origin·읽기 전용 응답을 포함한다. 브라우저 시각 검증은 실행별 비공개 인계 보고에 별도 기재한다.

표준 실행·검증 관측의 정정은 [원본 보존 정정](trace-amendments.md)을 사용한다. 정정이 있는 요청은 현재 값과 `정정됨` 표시를 보여주고 최초 원본·정정 이유·이력을 펼쳐 확인한다. 작업 묶음 합계는 조회 API의 `usage_groups`에 제공하며 화면의 토큰 행은 물리 측정 단위다.
