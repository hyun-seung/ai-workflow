# 현재 AI가 호출하는 요청 진입 흐름

상태: `scripts/routing_flow.py`로 추천 검사→대화 선택 기록→선택한 역할 지침 준비 또는 명시 worker 실행→취소/종료 조회를 연결했다. 자연어 해석은 현재 대화를 담당하는 AI가 수행한다. 별도 분류 모델·백그라운드 실행·앱 hook을 사용하지 않는다. 자동 UI 주입이나 현재 대화의 모델/effort 전환 API는 구현하지 않았다.

## 호출 시점과 비용

- 작은 요청·직접 처리·단순 개념 설명은 기존 [짧은 지침](routing-brief.md)으로 바로 답한다. 이 도구를 매 입력마다 실행하지 않는다.
- 변경 이점이 있는 추천, 표시한 추천에 대한 명시 선택, 상태 복구·취소에만 호출한다. 전체 역할 문서를 미리 읽지 않는다.
- 추천 이유·요청 원문·역할 본문은 상태 파일에 저장하지 않는다. 첫 수용 시 해당 역할 본문만 반환한다. 동일 수용·후속 재사용은 짧은 참조만 반환하며 상태 내용·수정 시각을 유지한다. 문맥이 소실된 경우에만 `context`로 다시 읽는다.
- `classifier_model_calls: 0`은 이 로컬 도구가 추가 분류 모델을 호출하지 않는다는 뜻이다. 현재 AI의 판단·도구 응답·역할 지침·실제 worker 및 개발 검토의 토큰이 0이라는 뜻이 아니다. 절감률은 측정 전까지 미확인이다.

## 현재 AI의 책임

1. 원래 요청과 최신 제어를 비교해 직접 처리, 유의미한 변경, 같은 작업의 후속 여부를 판단한다. 인용문 속 명령이나 모호한 “좋아”를 새 실행 동의로 쓰지 않는다.
2. 추천은 이유와 역할·모델/effort·위임의 변경 범위를 짧게 설명한다. 반환된 추천 참조를 해당 제안과 연결한다. 사용자에게 JSON·파일 경로·버전을 입력하게 하지 않는다.
3. 명확한 수용/거절/취소만 `choose`로 전달한다. 반환된 역할 지침은 현재 AI가 읽어 답변에 적용한다. 모델/effort나 새 agent 실행으로 확대하지 않는다.
4. 새 목표·제어·현재 프로필 변경은 `revision`을 올린다. 새 요청·세션·프로젝트에는 새 상태 파일을 쓴다. 같은 요청 ID라는 이유만으로 새 업무에 동의를 재사용하지 않는다.
5. worker 선택 수용 뒤에만 `start`를 호출한다. 환경 가이드·실제 권한·담당 범위·명시 한도를 확인한다. native receipt의 `next`를 보존하고 시작 후 턴을 끝내 결과를 기다린다. 실패했다고 새 ID로 재시작하지 않는다.

`current`는 선택 기록을 시작한 기준 프로필이다. 수용한 역할은 별도 `selection`으로 관리한다. 같은 선택을 재사용하려고 `current.role`을 임의로 바꾸거나, 요청값을 실제 모델 값으로 채우지 않는다. 실제 설정이 변경되면 새 revision으로 다시 판단한다.

## 명령과 입력

모든 명령은 `--project /private/example/project --state /private/example/state/request.json`을 요구하며 stdin JSON 한 개를 받는다. 상태·입력·spec·ledger는 Git과 대상 프로젝트 밖의 비공개 위치에 보관한다. 아래는 가상 예시다.

| 명령 | stdin | 동작 |
| --- | --- | --- |
| `request` | `{context, followup, analysis?, reconsider?}` | 문맥 변경 반영, 유효한 후속 선택 재사용, 추천 검사·기록. 직접 처리에는 새 상태 파일을 만들지 않음 |
| `choose` | 추천 참조 + `choice: accept/reject/cancel` | 첫 현재 AI용 수용에만 역할 지침 반환. worker 수용은 실행 대기 |
| `context` | `{selection: 추천 참조}` | 현재 수용한 역할만 다시 준비. 문맥 복구용 |
| `start` | `{selection, directory, operation_id, spec_file, limits, writes?, depends_on?, collection?}` | 정확한 수용·세션·프로필과 native 연결을 재검사하고 기존 관리 실행기에 전달 |
| `refresh` | `{selection: 추천 참조}` | 연결된 정확한 worker만 조회. 종료가 증명될 때만 활성 슬롯 해제 |
| `stop` | `{selection: 추천 참조}` | 명시 취소. 만료된 선택의 기존 worker도 정확한 참조로 취소 가능 |
| `finish` | `{selection, directory, source?, trace?}` | 명시한 원본만 기존 완료 수집기에 전달. 프로필 대조·배정별 토큰·선택 DB 수입과 비교용 파일 참조 반환 |
| `status` | `{}` | 선택·추천·실행 연결 여부 조회. native 프로세스의 현재 상태 조회는 아님 |

`context` 형식은 [선택 기록](routing-choices.md)의 `init`과 같다. `followup`, `reconsider`는 boolean이며 전자는 필수다. `analysis`를 생략하면 유효한 동일 AI 선택을 먼저 재사용하고, 없으면 `needs_analysis`를 반환한다. 새 `analysis`를 제공하면 이전 선택보다 이번 판단을 먼저 검사한다. 새 판단을 이유 없이 반복 생성하지 않는다.

현재 AI가 필수 자료 부족을 판단해 `analysis: {"needs_input": true}`를 주면 `request`는 후보·역할 본문 준비를 건너뛰고 `needs_input`을 반환한다. 새 상태나 제안을 만들지 않으며, 같은 문맥의 기존 선택은 내용·수정 시각을 유지한다. 이 판정은 기존 동의 취소나 worker 중지가 아니다. 자료 확보 뒤 후속 여부·문맥을 다시 확인하며, 목표나 제어가 바뀌면 기존 revision 규칙을 적용한다. 일반 입력마다 이 도구를 호출하라는 뜻은 아니다.

```json
{
  "context": {
    "project": "/private/example/project",
    "session_id": "session_example",
    "request_id": "request_example",
    "revision": 1,
    "current": {"model": "gpt-6.1-sol", "effort": "medium"},
    "controls": {"delegation": "forbid"}
  },
  "followup": false,
  "analysis": {"recommendation": {"role": "tutor", "reason": "명시한 학습 방식으로 설명 준비"}}
}
```

`request`가 돌려준 `selection` 객체를 그대로 선택과 후속 명령에 사용한다. 참조는 `session_id`, `request_id`, `revision`, `proposal_id`, `proposal_version` 다섯 필드다.

```json
{"session_id":"session_example","request_id":"request_example","revision":1,"proposal_id":"proposal_1","proposal_version":1,"choice":"accept"}
```

같은 대상이 대기 중이면 새 추천이나 전체 안내를 반복하지 않는다. 같은 revision에서 거절·취소한 대상을 다시 제안하지 않는다. 사용자가 다시 검토하라고 명시한 경우만 `reconsider: true`를 사용한다. 그때도 새 추천·명시 수용이 필요하다.

## 지원 경계와 실행

- **현재 AI의 역할:** 첫 수용의 `guidance`를 현재 AI가 읽는다. `runtime_applied: false`는 실제 주입·준수 여부를 로컬 코드가 증명하지 못한다는 뜻이다. 중복 수용에는 `guidance: null`이며 필요한 경우 명시적으로 복구한다.
- **현재 대화의 모델/effort 변경:** `action: manual`, `reason: manual_profile_change_required`, `executable: false`로 반환한다. 앱의 수동 선택이 필요하며 worker로 바꾸거나 변경 성공을 기록하지 않는다. 수동 변경이 확인되면 새 revision/current로 판단한다.
- **별도 worker:** 현재 Orca 세션과 선택의 세션이 같고 모델·effort가 명확해야 추천을 기록한다. 이 단계는 provider 접근·실제 권한 보장이 아니다. `start`는 같은 CLI의 읽기 전용 준비 조회에서 세션 확인을 요구한 뒤 [관리 실행 연결](routing-dispatch.md)을 사용한다. 현재 worktree의 Codex worker만 지원한다.
- `start.limits`에는 양의 정수 `max_active_workers`, `max_dispatches_per_request` 두 값을 명시한다. `spec_file`은 절대 경로의 비공개 UTF-8 파일, `writes`는 프로젝트 안의 쓰기 범위 목록, `depends_on`은 관리 ledger의 선행 operation ID 목록이다. 쓰기 목록은 조율 계약이며 OS sandbox를 설정하지 않는다. 한 수용은 한 ledger/operation/계획에만 연결된다.
- `start` replay는 같은 실행을 조회한다. 추가 모델 호출·새 worker가 아니다. 승인된 한도를 새 요청 ID·ledger로 초기화해 추가 시험하지 않는다.
- 완료 후 원본 세션 경로가 공식적으로 제공되면 아래 `finish`로 기존 [완료 처리기](completion.md)를 연결한다. 기존 독립 명령도 유지한다. 이 진입점은 원본 경로를 추측·탐색하거나 없는 실제 프로필/토큰을 채우지 않는다.

## 완료 수집과 비교 자료 연결

DB 반영을 원하면 실행 시작 전에 기존 비공개 DB에 요청과 담당자를 준비하고, `start`에 `collection: {"actor_id":"actor_example","task_title":"가상 학습 준비"}`를 추가한다. 역할은 수용한 선택에서 가져온다. 이 연결은 실행 계획·선택 binding에 고정하며 동일 operation의 재조회에서 바꿀 수 없다. actor 등록·hook 설치·신뢰 승인은 자동 수행하지 않는다.

`finish`의 가상 입력:

```json
{
  "selection": {
    "session_id": "session_example",
    "request_id": "request_example",
    "revision": 1,
    "proposal_id": "proposal_1",
    "proposal_version": 1
  },
  "directory": "/private/example/completions",
  "source": {
    "session_file": "/private/example/native/session.jsonl",
    "session_id": "worker_session_example"
  },
  "trace": {"db": "/private/example/trace.sqlite3"}
}
```

`selection.session_id`는 선택을 기록한 조율 세션, `source.session_id`는 원본 로그의 worker 세션이다. 원본은 공식 근거로 확인한 정확한 파일·ID를 전달한다. 이 도구가 경로의 공식 제공 여부를 자동 인증하지는 않으며 수집기가 프로젝트·세션·task/dispatch·native 구간을 대조한다. `trace`는 선택 사항이고 실행 시작에 `collection`을 고정한 경우만 지원한다. DB 경로는 이미 존재하는 비공개 파일이어야 한다. 새로운 DB나 다른 담당자를 자동으로 만들지 않는다.

- 원본을 생략하면 `collection_deferred`를 반환한다. 정확한 종료를 확인할 수 있으면 관리 슬롯만 해제하며 수집 완료·실제 모델·사용량을 만들어 내지 않는다. timeout 등의 시작 불명은 `native_binding_unconfirmed`, 미실행 취소는 `worker_not_started`로 구분한다.
- 수집 후 `profile`은 요청·실행환경 적용·실제 관측을 대조한다. 누락/불일치는 `unverified/fail`로 보존한다. 배정별 측정이 없으면 `usage.measurements: null`이며 0으로 채우지 않는다. 확인된 worker 종료와 측정 실패는 별개다.
- 실제 프로필 대조와 귀속 가능한 배정별 측정이 확보되어야 `collection_status: collected`다. 그 외는 `partial`이다. deferred/partial의 CLI 종료 코드는 1, 잘못된 입력·접속·DB 오류는 2다. 여기서 collected는 설명·구현 품질 합격 판정이 아니다.
- `trace`는 실행 계획에 고정된 actor·업무명으로 기존 수입기를 호출한다. 물리 구간 중복과 잘못된 요청/담당자 귀속은 기존 검사를 유지한다. DB 실패에도 완료 메타데이터와 영수증을 보존하므로 같은 입력으로 재시도할 수 있다. 측정이 없으면 DB 반영도 보류한다.
- `comparison_reference: {path, sha256}`는 [비교 입력](routing-comparison.md)의 `measurements`에 사용할 수 있다. 파일 바이트의 SHA-256이며 완료 영수증의 JSON 내용 digest와 구분한다. 같은 완료의 재호출은 수집 파일·측정 키·DB 항목을 중복 생성하지 않는다. 원본이 제거돼도 저장한 영수증/메타데이터가 유지되면 동일 연결의 replay는 원본을 다시 읽지 않는다.
- `request_total_measured: false`, `quality_evaluated: false`를 유지한다. 이 worker 한 건만으로 조율·추천·선택·검토·재작업 등 전체 요청 비용이나 품질을 측정했다고 하지 않는다. 비교의 완료 기준·단계별 범위·품질 근거는 별도로 준비해야 한다.
- 공통 진입점의 `finish`는 정리 계획을 받거나 파일·worktree를 삭제하지 않는다. 기존 독립 완료 명령의 정리 기능은 별도 명시 경로로 유지한다.

수집 파일을 쓰기 전에 요청·선택·operation/계획 digest·원본 연결을 담은 비공개 소유 기록을 잠근다. 기존 완료/metadata/snapshot 이름에 다른 파일이 있으면 덮어쓰지 않는다. 같은 소유 기록이 있는 중단된 수집만 이어서 처리한다. 원본·선택·ledger·DB 및 SQLite 보조 파일과 출력 경로가 겹쳐도 거절한다. 소유 기록이 없는 과거 완료 디렉터리는 기존 독립 명령으로 조회하거나 새 비공개 디렉터리를 사용한다. 잘못된 원본 연결을 고치려는 경우에도 기존 기록을 수정·삭제하지 않고 새 디렉터리를 지정한다.

## 취소와 장애 복구

현재 AI용 선택의 취소는 이후 역할 지침 준비를 막는다. 이미 읽은 문맥을 삭제하거나 과거 답변을 되돌리지는 못한다. 새 답변은 최신 사용자 제어를 따른다.

worker가 연결된 선택을 `choose: cancel`하면 먼저 이후 실행 동의를 취소하고, 저장한 ledger/operation/계획과 정확한 native task/dispatch를 대조한 뒤 guarded `worker-stop`을 요청한다. 취소 요청은 native 호출 전에 ledger에 기록한다. 동시 호출·재시작·timeout 이후에도 stop을 자동 반복하지 않는다. 원본 영수증과 후속 `worker-show`를 확인하며 Keep open·custody 거절을 우회하지 않는다.

- 실제 배정된 worker의 `state: released`는 기존 exact-worker 종료 검사에 통과한 경우다. stop 성공 영수증만으로 종료를 확정하지 않는다.
- native worker의 `stopped`도 정확한 로컬 신원·종료된 dispatch·`exactWorker: true`·`status: exited`가 함께 확인되면 해제한다. outcome은 `stopped`로 보존하며 성공한 의존 작업으로 취급하지 않는다. [실제 취소 점검](routing-cancellation-check.md)에서 발견한 상태 조합이다.
- 시작 전 `runtime_access_denied`·실패 종료 코드·`not_started` 상태·모든 native ID 부재가 함께 확인되면 취소 시 예약만 해제한다. native 조회/중지 호출 없이 `cancellation_evidence.kind: not_started_permission_denied`, `stop_request.native_call: false`를 남긴다. worker의 종료를 관측했다는 뜻이 아니며 `exit_evidence`를 만들지 않는다. 원래 거부 영수증과 누적 한도는 보존하고 같은 operation의 재실행은 막는다.
- `release: unconfirmed`나 `cancellation_pending`이면 물리적 중지는 미확인이다. 활성 슬롯을 유지하고 재시도·새 배정을 막는다. 시작 자체가 unknown이고 dispatch ID가 없으면 공식 영수증 확인이 먼저다.
- 수용 취소는 저장됐지만 환경·권한·조회가 막힌 경우 `selection_cancelled: true`, `runtime_stopped: false`로 구분한다. 같은 원본 환경에서 해당 참조의 `stop`/`refresh`로 복구한다. 알 수 없는 stop을 무작정 다시 호출하지 않는다.
- revision 증가·직접 처리 전환은 이전 선택을 만료시키지만 실행 중 worker를 자동 중지하지 않는다. 기존 연결은 보존하며 명시 취소할 때 해당 이전 참조의 `stop`을 사용한다.

조회 응답의 객체·신원·host·상태·관측 형식이 잘못됐으면 중지 대상을 추측하거나 슬롯을 해제하지 않는다. 이미 중지를 요청한 뒤 잘못된 종료 응답을 받았어도 미확인을 유지하며 stop을 자동 반복하지 않는다. CLI의 stop 출력에는 해당 중지 결과 또는 미실행 취소 근거만 내보낸다. 과거 start 거부를 새로운 stop 실패처럼 출력하지 않는다.

중지 계약은 설치 Orca 1.4.257의 `orchestration worker-stop --help`와 `skills get orchestration --reference references/recovery-and-cleanup.md`에서 확인했다. `--dispatch`로 정확한 대상을 지정하고 `stop_unknown`에는 자동 반복하지 않는 계약이다. 이번 개발에서는 실제 worker 생성·중지를 추가 시험하지 않았다.

## 검증과 남은 범위

2026-11-15의 [같은 AI 운영 점검](routing-operation-check.md)에서는 가상 입력 24건을 매번 새 CLI 프로세스로 실행해 후속·취소·거절·목표/제어 변경·파일 불변성과 수동 프로필 처리 경계를 확인했다. 앱 자동 연결·실제 native 중지·사용자 평가와는 구분한다.

2026-11-16 [실제 취소 점검](routing-cancellation-check.md)은 worker 한 건의 중지·정확한 종료·슬롯 해제·취소 replay를 확인했다. 자식 IPC 차단으로 준비 신호는 실패했으며 해당 정상 경로는 미검증이다. `stopped` 호환성 수정과 관련 회귀 106개를 확인했고 추가 실행·중지 재시도는 하지 않았다.

IPC 인계 보완 뒤 별도 선택받은 후속 한 건에서는 정상 준비 신호부터 취소·정확한 종료·슬롯 반환·replay까지 확인했다. worker의 권한 자기 보고와 실제 질문 수신을 보존하되, 정규화된 조회에 없는 escalation 옵션·승인 검토 영수증은 미확인으로 남긴다. 앞선 실패 기록과 각 시험의 한도는 별도로 보존한다.

필수 자료 부족 분기는 신규 검사 6개를 포함한 추천 검사기 26개·진입 흐름 21개·선택 기록 26개·위임 연결 19개·완료 연결 21개, 관련 회귀 113개를 통과했다. 작성된 정책 입력 17개도 기대 action과 대조했다. 현재 AI가 준 표시를 전달하는 검증이며 실제 의미 판단·실행 차단·독립 콘텐츠 재평가가 아니다.

```sh
python3 -m unittest discover -s tests -p test_routing_flow.py -v
python3 -m unittest discover -s tests -p test_dispatch_stop.py -v
python3 -m unittest discover -s tests -p test_routing_finish.py -v
```

비공개 임시 파일의 실제 CLI 추천→수용과 가상 native 응답의 위임→중복→취소→종료를 확인한다. 오래된 동의·제어 변경·거절 반복·역할 문맥 복구·다른 환경·계획 변조·불명확한 시작/중지·crash 뒤 중복 방지를 포함한다. 앱 hook·선택 UI·현재 대화 프로필 자동 전환·실제 중지·자연어 판단 정확도·학습 효과·절감률은 이 로컬 검증으로 확인되지 않는다.

신규 흐름 18개·중지 6개와 전체 Python 검사 444개를 확인했다. 최초 전체 실행의 localhost bind 제한 한 건은 해당 검사만 정식 권한 경로로 재실행해 통과했다. 별도 코드 검토와 동시 취소·timeout의 추가 모의 점검도 통과했다. 원본 요청·세션·사용량·실제 worker 기록은 이 문서에 포함하지 않는다.

후속 취소·복구 보완에서는 미실행 예약 해제·누적 한도 보존·취소 뒤 재실행 차단·비정상 응답의 보수적 처리를 추가했다. 현재 흐름 19개·중지 10개를 포함한 관련 검사 102개와 독립 코드 검토가 통과했다. 실제 native worker 생성·중지는 추가 실행하지 않았다.

완료 연결은 실제 수집기·임시 SQLite DB와 가상 native 응답을 사용하는 신규 검사 21개를 포함한 관련 회귀 216개를 통과했다. 프로필/사용량 누락·불일치·잘못된 원본/DB 담당자·DB 오류 재시도·중복 방지·원본 없는 replay·기존 명세/출력 충돌·소유한 중간 파일 복구를 확인했다. 독립 검토에서 발견한 기존 명세 덮어쓰기 결함을 소유 기록과 충돌 검사로 수정하고 재검토를 통과했다. 실제 업무의 추가 모델/worker 실행·원본 수집·토큰 절감·비용/품질 비교는 수행하지 않았다.
