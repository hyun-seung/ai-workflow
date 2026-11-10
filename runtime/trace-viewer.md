# 요청 실행 기록과 로컬 조회

명시적으로 작성·전달된 판단 근거 요약, 지시와 행동·결과를 요청 단위로 모은다. 내부 추론을 저장하는 기능이 아니다. 신규 도구는 Python 표준 라이브러리만 사용하며 기존 `workflow.py`·역할 실행·JSON usage 기록을 변경하지 않는다. Orca native snapshot을 수입하며 native 실행을 자동 추적한다고 주장하지 않는다.

## 실행과 비공개 저장

사용자가 정한 **Git 작업 트리 밖의 비공개 디렉터리**를 명시한다. 아래 경로는 가상값이다. 운영 기록 생성·실제 자료 수입은 사용자의 승인된 위치와 범위에서 수행한다.

```sh
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 import tests/fixtures/traces/sample.json
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 import-orca /private/path/snapshot.json --request-id request-example --title '요청 제목' --role developer
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 list --q example
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 export --request-id request-example
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 serve --port 8765
```

브라우저 주소는 `http://127.0.0.1:8765`다. localhost 별칭·다른 Host/Origin은 허용하지 않는다. 서버 종료는 Ctrl-C다. 외부 자산·텔레메트리·쓰기·실행·승인 UI는 없다. 화면은 요청 검색 → 요청 선택 → 역할/이벤트 AND 필터 → 본문 미리보기·상세 펼치기를 제공한다. 담당자·모델·사용량과 수집 근거는 접힌 보조 정보로 제공하며, 수집 한계와 원본 잘림 경고는 항상 표시한다. 모바일 요청 목록은 선택 후 접히고, 긴 타임라인에서는 선택 문맥과 필터를 유지한다. 원문 JSON·출처·해시·원본 시각은 각 상세에서 확인할 수 있으며, 표시는 UTC다. 검색 초기화와 필터 초기화는 별개다. 가상 fixture는 실제 관측이 아니다.

`run_records.validate_records_dir`의 Git/프로젝트 밖·소유권·private directory 검사를 재사용한다. 새 디렉터리는 0700, DB는 0600이며 SQLite journal/sidecar도 검사한다. 기존 경로의 권한을 바꾸지 않는다. symlink 경로는 거부한다(시스템 `/tmp`, `/var` 별칭도 포함하므로 macOS에서는 실제 `/private/...` 경로를 명시). 공유·의심 경로는 사용하지 않는다. 이 검사는 같은 OS 사용자나 악성 동시 파일 교체에 대한 보안 격리가 아니다. DB·백업·내보낸 원문은 비공개로 취급한다.

## 교환 계약 v1

저장 구현과 UI/정규화 경계는 JSON 객체 계약이다. `trace_store.Store`는 현재 SQLite 구현이며 `requests(q, limit)`, `request(id)`, `import_bundle(bundle)`을 제공한다. 현재 실제 구현·검증한 저장소는 SQLite뿐이다. 파일 저장 모드는 추후 구현 대상이며, MongoDB는 기존 운영 기반 등 조건이 맞을 때, PostgreSQL은 중앙 기록 운영의 후보로 검토한다. 이들의 어댑터·저장소 선택 설정은 아직 구현하지 않았다. 비교 근거와 권고는 [저장 방식 선택](storage-options.md)을 따른다. 대형 공통 저장 엔진은 도입하지 않는다.

JSON bundle: `schema_version: 1`, `requests: []`, `actors: []`, `events: []`, 선택 `collections: []`. 예시는 [가상 bundle](../tests/fixtures/traces/sample.json), [가상 native snapshot](../tests/fixtures/traces/orca-snapshot.json)을 따른다. `export`는 같은 bundle을 stdout으로 반환하며 `--format jsonl`로 같은 교환 계약의 줄별 출력도 제공한다. 출력 리디렉션 파일은 사용자가 private 권한을 보장한다. JSONL 수입은 `.jsonl` 확장자로 선택하며 각 줄은 `{"schema_version":1,"type":"request|actor|event|collection","record":{...}}`다. bundle export를 JSONL로 변환할 때 각 배열 요소를 해당 type의 record로 감싸면 된다.

공통: `id`, `source`, `source_id`, `time`(timezone 포함 ISO 8601 또는 null), `completeness`(complete/partial/unknown 같은 출처 설명). ID는 영문·숫자 및 `_.:-`로 1–200자다.

- request: `title`, `status`, 가상 표시용 `sample: true`.
- actor(실행): `request_id`, `parent_id`, `role`, `environment`, `provider`, `status`, `requested: {model, effort}`, `observed: {model, effort}`. 요청과 확인값은 독립적이며 미확인은 null이다. native 수입은 workflow.json을 읽지 않으며 확인된 모델로 채우지 않는다.
- event: `request_id`, 선택 `actor_id`, `kind`, `detail`. kind는 user_request/decision/assignment/instruction/tool_call/tool_result/question/approval_request/approval_resolution/handoff/verification/explanation이다. 상세의 정규 JSON UTF-8 SHA256을 `detail_sha256`으로 저장한다. 원본 파일 전체 바이트의 해시와 다르다. 원문 지시는 detail에 보존하되 내부 추론은 제외한다. 사후 요약은 detail에 `recorded_retrospectively: true`와 작성시각·근거를 명시하고 당시 판단처럼 표현하지 않는다.
- usage: `source`, `scope`, 비어 있지 않은 `measurements`가 있어야 measured이며 없거나 `measurements={}`이면 unavailable이다. 0으로 채우지 않고 요청/실행 간 합산하지 않는다. measurements는 비음수 수치다. 공급자의 누적/턴 범위 해석과 중복제거는 입력 작성자가 명시한다.
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
python3 -m unittest discover -s tests -v
```

보안 검증은 권한·Git 경로·symlink·XSS payload 보존과 CSP·SQL injection 검색·임의 파일 경로·cross-origin·읽기 전용 응답을 포함한다. 브라우저 시각 검증은 실행별 비공개 인계 보고에 별도 기재한다.
