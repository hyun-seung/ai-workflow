# 기본 개발 흐름의 유한 상태 컨트롤러

`scripts/development_workflow.py`는 **현재 AI가** 다음에 실행할 역할과 native 동작을 확인하는 작은 로컬 컨트롤러다. 스크립트 자체는 모델·worker·프로세스를 생성하지 않으며 모델 API, 셸 실행, daemon, 자동 공개 기능이 없다. 별도 Codex 실행기로 fallback하지 않는다. 현재 AI가 의미를 판단하고 공식 native 도구로 실행·상태·종료를 확인한 뒤 아래 API에 관측값을 전달한다. 지침 연결만으로 모든 앱에 자동 적용되는 기능은 아니다.

## 프로젝트별 명시 선택

대상 프로젝트 루트의 `.ai-development-policy.json`만 읽는다. `workflow.json`, `.ai-workflow.json`, provider/model/effort 설정과 별개다. 다음은 필수 필드 전체이며 추가 필드·중복 JSON 키·비유한 숫자를 거부한다.

```json
{"version": 1, "enabled": true, "max_dispatches": 4, "max_fix_rounds": 1}
```

version은 정수 1, enabled는 boolean, max_dispatches는 정수 2..8, max_fix_rounds는 정수 0..3이다. boolean을 정수 대신 사용할 수 없다. active concurrency는 요청당 1로 고정하며 변경 필드가 없다. 정책 파일은 **사용자가 명시적으로 선택한 프로젝트 범위의 동의를 기록**한다. 파일의 존재나 enabled 자체는 보안 권한, native 접근 가능성, 전역 위임 승인, 자동 공개 승인이 아니다. 조율자는 최초 연결 때 필요한 선택과 실제 권한을 확인하고 기록하며 요청마다 같은 선택을 재확인하지 않는다. 기존 요청의 정책·AC·파일 목록은 begin에서 고정한다. 새 admission은 no-follow descriptor로 읽은 enabled 정책 snapshot의 원문 SHA256·version·inode/mtime/ctime/size를 확보하고 상태 잠금 안에서 실제 파일을 다시 열어 대조한다. true→false나 삭제는 opt_in_required, 내용/상한/파일 정체성 변경과 symlink 교체는 오류로 거부하며 상태를 생성하지 않는다. 이미 시작한 요청은 저장된 승인 snapshot과 상한을 유지하며 begin 재호출도 새 정책을 채택하지 않는다. 정책 파일 편집은 실행 중 worker를 중단하는 명령이 아니므로 취소는 cancel과 공식 native stop으로 처리한다.

현재 AI는 `conversation`, `small_change`, `development` 중 의미를 직접 공급한다. 키워드 분류기나 분류 모델을 호출하지 않는다. `direct=true`는 직접 처리/위임 금지 사용자 선택을 뜻하며 가장 우선한다. conversation, small_change, direct 경로는 정책·역할 본문을 읽거나 상태 디렉터리를 생성하지 않고 direct를 반환한다. development에 정책이 없거나 enabled=false이면 opt_in_required, native 도구가 없으면 unavailable이다. 이 둘 모두 상태를 만들지 않는다.

## Python API와 CLI

저장소 `scripts`를 import 경로에 둔 Python API:

```python
from development_workflow import Controller, decision, snapshot
c = Controller(project, request_id, coordinator, state_path, coordinator_actor)
v = c.begin(kind='development', direct=False, native_available=True,
            acceptance={'AC1': '원래 완료 기준'}, files=['src/app.py'])
v = c.next()  # 역할 본문을 읽지 않고 해당 역할 지침의 절대 경로만 반환
v = c.next(native_available=False)  # 상태 보존, unavailable/pending 반환
grant = c.reserve(stage_id, intent)  # 반드시 native 실행 호출보다 먼저
# grant.launch_permission == submit_once인 최초 응답에서만 정확한 guard operation을 제출
# 현재 AI가 공식 도구를 호출하고 실제 참조를 확인한다.
v = c.attach(stage_id, execution)
v = c.complete(receipt)
v = c.cancel()
```

coordinator는 실제 native 조율자 handle(Orca에서는 ORCA_AGENT_SESSION_ID)이다. coordinator_actor는 공식 도구에서 확인한 canonical `provider/provider-session-id` 문자열이다. app handle과 provider 세션은 서로 다른 식별자다. request_id는 원래 요청의 안정된 ID다. 테스트용 문자열을 실사용 식별자로 대체하지 않는다.

CLI의 모든 명령은 JSON을 출력하며 성공 0, 입력/IO 오류 2다. JSON은 기존 lifecycle JSON 파서로 크기·중복 키·비유한 숫자를 검사한다.

```sh
python3 scripts/development_workflow.py decision --project /path/to/product --input /private/decision.json
python3 scripts/development_workflow.py begin --project /path/to/product \
  --request-id request_example --coordinator native_coordinator_example \
  --coordinator-actor example-provider/session_example \
  --state /private/development/request_example.json --input /private/begin.json
```

begin 이후 명령도 같은 project/request-id/coordinator/coordinator-actor/state를 지정한다. `--input`은 API 키워드 인자 객체다. decision 입력은 정확히 `{kind, direct, native_available}`, begin은 `{kind, direct, native_available, acceptance, files}`, reserve는 `{stage_id, intent}`, attach는 `{stage_id, execution}`, next는 `{}` 또는 `{native_available: false}`, cancel은 `{}`다. complete의 입력은 키워드 wrapper 없이 receipt 객체 자체다. import와 `--help`는 실행 부작용이 없다.

## 실행 전 intent와 불확실한 결과의 복구

상태 schema는 **version 2**다. policy schema는 계속 version 1이며 서로 다른 버전이다. version 1 workflow state는 `unsupported_workflow_schema_preserve_original_no_migration`으로 거부한다. 원본 상태를 보존하고 새 요청/상태에서 시작한다. 기존 실행을 과거에 예약한 것처럼 자동 migration하거나 역사적 intent를 만들지 않는다. 이 스크립트 업데이트로 이미 실행된 worker를 사전 예약했다고 주장할 수 없다.

begin/next의 ready는 실행 권한이 아니다. native_action은 먼저 reserve하라고 안내하며 launch_permission=none이다. Orca intent의 필수 필드 전체:

```json
{
  "client": "orca", "guard_directory": "/private/guard-directory",
  "guard_operation_id": "operation_example", "usage_actor_id": "registered_actor_example"
}
```

현재 AI는 기존 dispatch_guard에 사용할 정확한 private directory/operation/usage actor를 선택해 **native 호출 전에** `reserve(stage_id, intent)`를 실행한다. 새 stage의 예약은 atomic save와 파일·부모 디렉터리 fsync로 저장된 다음, 최초 성공 응답에서만 launch_permission=submit_once를 반환한다. 예약된 client/reference는 같은 stage에서 바꿀 수 없으며 기존 ledger operation을 나중에 가져와 사전 예약한 것처럼 만들 수도 없다. 스크립트는 guard ledger operation을 새로 만들거나 native 도구를 호출하지 않는다. 이 intent가 먼저 저장된 뒤 현재 AI가 기존 dispatch_guard start를 동일한 directory/operation-id/request-id와 정책 상한으로 호출한다. guard는 자체 잠금·예약·native 호출·영수증 저장의 기존 계약을 유지한다. 다른 operation-id로 start하거나 bare worker-start로 우회하면 안 된다.

reserve의 최초 응답을 잃었거나 native 실행 결과가 불확실하면 **제출됐을 수 있음**으로 취급한다. 재시작한 next와 동일 reserve 재호출은 launch_permission=none과 저장된 intent, reconcile/observe 동작만 반환한다. 새 operation·stage·키로 launch를 시도하지 않는다. 기존 guard의 공식 status/refresh 및 정확한 native 조회로 같은 operation을 조정한다. guard에 unknown/launching이 남으면 그대로 pending이며, 지원되는 guard replay도 동일 operation을 사용한다. guard ledger가 없다고 이미 받은 submit_once 응답을 재발급하지 않는다. 최초 허가를 받은 조율자만 한 번 제출할 수 있다. 허가 응답 전 crash라면 실제 호출이 없었더라도 자동 재실행하지 않는 보수적 at-most-once 경계다. 조율자는 공식 도구로 상황을 확인하고 수동 정리해야 한다. 승인된 동일 operation의 native 지원 재조정·명확한 미시작 재시도 이외에는 새 호출을 만들지 않는다.

attach는 intent의 client/directory/operation/usage actor와 정확히 같은 실제 execution만 받는다. 이후 task/dispatch는 기존 guard ledger와 대조한다. cancel은 저장된 intent를 지우지 않으며 receipt 불명 상태에서도 같은 operation의 종료 확인이 필요하다. workflow 완료나 cancel_pending은 guard 슬롯 해제 근거가 아니다.

Paseo/Codex의 공식 idempotency/reconciliation adapter는 이 버전에서 구현·검증하지 않았다. 해당 client로 reserve하면 unavailable, launch_permission=none을 반환하고 상태는 바꾸지 않는다. unsupported intent는 `{client: "paseo" 또는 "codex", guard_directory: null, guard_operation_id: null, usage_actor_id: null}`이다. capability=true 같은 자기 선언으로 실행을 허용하지 않으며 가상의 공통 native RPC를 제공하지 않는다. 현재 앱에서 지원된다는 이유만으로 임의 token을 만들지 않는다. 향후 실제 공식 도구의 재조정 계약을 검증해 별도 adapter를 구현하기 전까지 자동 위임 경로는 unavailable이다.

## 실행 참조와 종료 영수증 계약

execution은 다음 필드 전체를 가진다. 문자열 값은 비어 있지 않은 최대 4096자다. client는 orca/paseo/codex 중 하나며 provider와 provider_session_id는 실제 provider의 canonical 물리 세션 식별자다. 서로 다른 앱·handle이라도 같은 provider/session으로 검토자를 연결할 수 없다. 이전 모든 developer와 조율자의 물리 세션을 reviewer에서 거부한다. 조율자는 별칭이나 앱 표시명을 이용해 동일 세션을 다른 provider로 표시하면 안 된다.

```json
{
  "client": "orca", "handle": "native_agent_example",
  "provider": "example-provider", "provider_session_id": "session_example",
  "task_id": "task_example", "dispatch_id": "dispatch_example",
  "guard_directory": "/private/guard-directory",
  "guard_operation_id": "operation_example", "usage_actor_id": "registered_actor_example"
}
```

Paseo/Codex 참조 형식의 마지막 다섯 필드는 반드시 null이다. 이 버전은 해당 앱의 사전 실행 재조정 adapter가 없으므로 reserve에서 unavailable이며 attach도 허용하지 않는다. Orca ID를 Paseo/Codex handle로 복제하지 않는다. 지원 확인이 안 되면 unavailable/pending으로 남기며 같은 AI의 자체 검토를 독립 검토로 표시하지 않는다. 모델·effort는 native 기본값을 유지한다.

Orca에서 마지막 다섯 필드는 필수 문자열이다. 현재 AI는 기존 `dispatch_guard.py`를 통해 요청의 max_active_workers=1과 max_dispatches_per_request를 정책과 같게 등록하고 실행한다. 등록된 요청·actor를 사용하고 developer는 `--usage-role backend`, reviewer는 `--usage-role verifier`, 모두 `--usage-actor-id`, `--usage-task-title`을 명시한다. model/effort 인자는 지정하지 않는다. attach는 실제 guard ledger의 coordinator/request/project/operation/task/dispatch, bound/released 상태, 모델 기본값과 usage actor/role/title 및 상한을 대조한다. 기존 guard/harness/사용량 등록 요건을 이 정책으로 우회하지 않는다. 기존 native source 수용량 제한도 그대로 지킨다. 이 컨트롤러는 hook/DB/기존 세션 원본을 탐색하거나 수정하지 않는다.

reserve에서 submit_once를 받은 뒤 정확한 guard operation을 한 번 제출하고, 공식 실행 도구에서 실제 참조를 얻은 뒤 attach한다. launch와 attach 사이에 연결이 끊기면 새 worker를 만들지 말고 원래 stage/operation을 공식 도구로 재조회해 동일 실행을 연결한다. attach가 거부돼도 이미 시작된 worker의 종료나 슬롯 회수가 증명되는 것은 아니다. 조율자는 그 실행을 공식 도구로 추적·종료해야 한다.

receipt는 다음 필드 전체다. binding은 next의 binding을 그대로 사용하며 native도 attach한 execution과 정확히 같아야 한다. stage_id는 현재 stage의 고유 ID, id는 안정된 영수증 ID다. observer는 실제 관측한 조율자의 native handle, source_tool은 실제 사용한 공식 status/exit 도구의 참조다. raw native 로그나 세션 원문을 넣지 않는다.

```json
{
  "id": "receipt_example", "binding": {
    "project": "/path/to/product", "request_id": "request_example",
    "coordinator": "native_coordinator_example",
    "coordinator_actor": "example-provider/coordinator_session_example"
  },
  "stage_id": "next에서 받은 실제 stage ID", "native": {},
  "observer": "native_coordinator_example", "source_tool": "실제 공식 도구 관측 참조",
  "terminal": "succeeded",
  "result": {
    "outcome": "pass", "self_test": "pass", "snapshot": "실제 snapshot SHA256",
    "acceptance": {"AC1": "pass"}, "findings": []
  }
}
```

위 native 빈 객체는 축약 표시다. 실입력은 execution 전체여야 한다. terminal은 succeeded/failed/cancelled/stopped, outcome은 pass/changes_requested/fail, self_test는 pass/fail/not_applicable이다. developer의 pass는 self_test=pass와 현재 snapshot 일치를 요구한다. reviewer는 원래 모든 AC ID에 pass/fail/unverified를 명시하고 정확한 검토 snapshot을 반환한다. finding은 정확히 `{ac_id, required, resolved, detail}`이며 ac_id는 원래 AC, required/resolved는 boolean, detail은 비어 있지 않은 문자열이다. reviewer pass는 모든 AC pass와 required 미해결 finding 0개를 요구한다. developer 실패를 reviewer pass로 승격하지 않는다.

**JSON 영수증은 암호학적 증거가 아니다.** verification은 coordinator_observed_assertion_not_cryptographic_proof다. 조율자가 실제 공식 native 도구에서 정확한 실행의 terminal을 관측하고 제출해야 한다. 스크립트는 도구를 호출하거나 source_tool 문자열의 진위를 증명하지 않는다. provider-session 식별자 역시 실제 도구에서 확인해야 하며 임의 생성한 값은 독립성 근거가 아니다.

## 상태·스냅샷·취소 경계

정상 전이는 developer → 독립 reviewer → completed다. reviewer changes_requested는 developer fix → reviewer로 돌아가며 fix/dispatch 상한에 도달하면 exhausted다. stage는 고유 UUID와 증가하는 attempt를 갖는다. 매 stage는 하나의 native 실행만 연결하며 terminal 영수증 이전에 다음 stage를 만들지 않는다. failed/completed/exhausted/cancelled는 새 실행으로 재개할 수 없다. 동일 receipt ID와 내용의 재전송은 저장 파일을 바꾸지 않으며 내용이 충돌하거나 오래된 stage/다른 worker 콜백이면 거부한다. 새로운 업무는 새 request와 새 private state로 시작한다.

private state는 Git/대상 프로젝트 밖의 소유자 전용 디렉터리에 둔다. 기존 lifecycle_io.private_file/locked/save로 경로·symlink·파일 권한, 비차단 단일 writer 잠금과 atomic replace를 적용한다. version 2의 최상위 필드는 정확히 version/binding/policy/policy_source/acceptance/files/phase/round/stages/receipts/integrity다. policy_source는 path/version/sha256으로 승인 시 실제 정책 경로·policy version·원문 SHA256을 보존한다. binding은 위 네 필드, stages 원소는 id/role/attempt/intent/native/snapshot/result, receipts는 ID→위 receipt다. policy는 위 strict policy를 복사한다. integrity는 나머지 상태의 canonical JSON SHA256이며 손상 검사일 뿐 접근 통제나 서명이 아니다. 잘못된 입력과 lock 충돌은 저장 전 실패한다. 쓰기 실패 시 기존 파일을 부분 수정하지 않는다.

files는 프로젝트 상대 product 파일 1..32개를 명시한다. 파일 내용 합계는 최대 2 MiB다. 파일명·bytes·각 SHA256의 정렬된 canonical 목록을 다시 SHA256해 snapshot을 만든다. 실제 파일을 읽으며 디렉터리 descriptor와 O_NOFOLLOW로 symlink 통과를 차단한다. 절대/상위 경로, 숨김 경로(.git 포함), secret/credential/token/password/private-key 이름과 pem/key/p12를 거부한다. 비밀 파일 자동 탐지는 아니므로 조율자가 실제 product 파일만 선정해야 한다. developer 완료 시 파일이 존재해야 하며 삭제 파일·목록 밖 파일의 변경은 이 snapshot의 검증 범위가 아니다. 새 파일은 begin 목록에 미리 명시하고 developer 완료까지 생성할 수 있다. 일반 개발 전체 tree를 고정하는 도구로 확대해석하지 않는다. reviewer 연결 시 developer snapshot과 일치해야 하고, reviewer pass 제출 시 현재 파일이 다시 정확히 일치해야 한다. 외부 편집 프로세스를 잠그는 도구는 아니며 관측 시점 뒤의 파일 변경까지 막지는 않는다.

cancel은 cancel_pending만 기록한다. 연결된 exact handle은 계속 보존되며 공식 stopped/terminal 관측을 complete로 제출하기 전에는 cancelled로 바뀌지 않는다. 아직 attach되지 않은 launch도 발생했을 수 있으므로 unbound 취소 역시 자동 종료 처리하지 않는다. 조율자는 공식 도구로 원래 실행을 찾아 attach하고 terminal을 확인해야 한다. 실행 자체가 없음을 확인한 unbound 요청은 pending으로 남겨 수동 정리한다. 완료 상태는 native 프로세스 stop 증거가 아니며 컨트롤러가 worker 슬롯을 회수하거나 새 실행을 대신 생성하지 않는다. Orca guard refresh/stop, 실제 Stop/exit·사용량 마감은 조율자가 소유한다.

next/status 출력은 phase/role/stage_id/attempt, launch_intent/launch_permission, native_action, 관련 두 역할 지침 경로, 원래 AC, 명시 파일과 snapshot, native-default 프로필을 제공한다. last_review는 가장 최근 완료된 검토의 stage_id/attempt/result를 반환하며 결과는 원래 AC·snapshot·최대 128개 finding(각 detail 최대 4096자)으로 제한된다. unresolved_required_findings는 그 결과에서 required=true/resolved=false인 항목이다. 수정 단계에서는 현재 AI가 재시작해도 이 검토 결과와 원래 AC를 개발자에게 전달한 뒤 새 snapshot을 별도 검토자에게 재검토시킨다. 최종 completed에서도 마지막 검토 결과를 결과 종합에 사용할 수 있다. 현재 AI는 반환된 해당 역할 문서만 읽고 공식 native 동작을 실행한다. completed는 기능 흐름 완료만 뜻한다. usage_status=unmeasured, usage_complete=false를 유지하며 다른 공식 수집 도구의 실제 측정을 별도로 보고한다. 이 컨트롤러는 전체 요청의 토큰 완전 수집이나 출판·배포 완료를 주장하지 않는다.

## 집중 검증

`python3 -m unittest discover -s tests -p test_development_workflow.py -v`

34개 집중 테스트에서 합성 normalized 영수증으로 직접 경로/미동의/도구 부재, 정상 독립 검토, 수정 재검토와 상한, 동일 물리 세션, 잘못된/오래된 콜백과 replay, 파일 변경, 취소 종료 대기, private/symlink/상한, lock과 CLI import를 확인한다. 기존 guard 실행 함수를 mock한 unknown-receipt → 재시작 → 동일 operation replay의 실제 호출 수 1, 예약 재호출 허가 재발급 금지, 잘못된 attach/예약 충돌, 실제 두 정책 snapshot 읽기 사이 비활성화·상한 변경·동일 내용 파일 교체·symlink 교체, 재시작한 수정/완료 맥락, v1 상태의 무변경 거부도 확인한다. 실제 native worker launch/exit 통합 검증이나 영수증 진위 검증을 수행한 결과는 아니다.

## 실제 Orca 검증 범위

2026-11-18, 같은 프로젝트에서 동시 1명·누적 2배정·수정 배정 0회의 읽기 전용 실증을 마쳤다. 각 역할의 실행 전에 v2 예약을 저장하고 공식 guard로 실행했으며, 개발자의 집중 검사 34개 통과와 서로 다른 실제 provider 세션의 독립 검토를 확인했다. 검증자는 추가 replay 검사 3개를 통과했다. 두 배정의 실제 종료·Stop 수신·SQLite 수입과 settlement 재처리, 최종 컨트롤러 완료 영수증의 재처리 불변성을 확인했다.

이 실증에서 제품 파일은 바꾸지 않았다. 수정·재검증 분기의 실제 실행, 앱 입력 이벤트 자동 연결, Codex·Paseo 실행 adapter와 전체 요청 사용량 수집은 검증한 범위에 포함하지 않는다. 검증 전용 정책은 보관 후 제거했으며 사용자 프로젝트의 지속 opt-in으로 확대하지 않는다. 전체 Python 회귀 816개 통과와 별도 코드 재검토 근거는 같은 제품 버전에 대해 보존했다. 실제 세션·경로·로그·DB는 공개 문서에 포함하지 않는다.
