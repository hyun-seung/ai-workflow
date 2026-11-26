# 기본 개발 흐름의 유한 상태 컨트롤러

`scripts/development_workflow.py`는 **현재 AI가** 다음에 실행할 역할과 native 동작을 확인하는 작은 로컬 컨트롤러다. 스크립트 자체는 모델·worker·프로세스를 생성하지 않으며 모델 API, 셸 실행, daemon, 자동 공개 기능이 없다. 별도 Codex 실행기로 fallback하지 않는다. 현재 AI가 의미를 판단하고 공식 native 도구로 실행·상태·종료를 확인한 뒤 아래 API에 관측값을 전달한다. 지침 연결만으로 모든 앱에 자동 적용되는 기능은 아니다.

## 명시적 개발 진입과 보고

복합 개발의 현재 진입은 `scripts/development_entry.py`의 `Entry` API와 기존 `development_workflow.py entry/report` CLI다. 정책 없는 복합 요청에서 절차를 생략해 단독 구현하는 것은 누락이다. 도구 미지원과 진입·선택 누락은 별개다. 이 API가 생기기 전 작업을 사후 기록해 사전 실행으로 바꾸지 않는다.

| 연결 | 담당 / 현재 강제 범위 |
| --- | --- |
| Orca 입력 → 의미 판단 | 현재 AI 수동 판단, 별도 분류 모델 없음; 앱 자동 interceptor 없음 |
| 판단 → entry | AGENTS 지침에 따른 현재 AI의 명시 호출; 모든 대화를 강제로 통과시키지는 못함 |
| entry → 정책·수용·native | 프로그램 검사: 서로 별도 기록, unknown은 확인 대기, 파일만으로 수용 추정 금지 |
| 수용 → begin/next → reserve | 프로그램 검사와 기존 v2 컨트롤러; entry는 실제 begin/next를 호출 |
| reserve → 공식 guard dispatch | 현재 AI가 최초 submit_once에서 기존 공식 경로 호출; 새 launcher 없음 |
| 결과 → attach/complete → 독립 검토 | 컨트롤러의 실제 실행·actor·영수증·snapshot 검사 |
| report → 최종 전달 | 프로그램이 완료 가능 여부·검토 수행 상태·누락을 계산, 현재 AI가 결과 전달; 앱 발화 강제 없음 |

Python 예제(가상 값은 실제 확인한 참조로 바꾼다):

```python
from development_workflow import Controller, policy_snapshot
from development_entry import Entry
c = Controller('/path/to/product', 'request_example', 'native_coordinator_example',
               '/private/development/controller.json', 'example-provider/coordinator_session_example')
e = Entry(c, '/private/development/entry.json')
p = policy_snapshot(c.project)  # 파일 확인만으로 사용자 수용을 추정하지 않는다.
# 다음 객체는 사용자가 실제로 이 프로젝트/정책/한도를 수용한 뒤에만 작성한다.
accepted = {'ref': 'explicit-user-choice-reference', 'project': str(c.project),
            'sha256': p['sha256'], 'limits': p['value']}
args = dict(kind='development',
            choice={'mode': 'workflow', 'reason': 'independent implementation/review required',
                    'instruction_ref': 'bounded-user-instruction-reference'},
            native={'status': 'unknown', 'source_ref': None, 'observer': None},
            acceptance={'AC1': 'original completion criterion'}, files=['src/app.py'],
            review_required=True, policy_acceptance=accepted)
v = e.enter(**args)  # native_check_required; no controller launch
# 현재 AI가 공식 도구에서 해당 기능·대상 접근을 확인한 후:
args['native'] = {'status': 'available', 'source_ref': 'official-tool-observation-reference',
                  'observer': c.binding['coordinator']}
v = e.enter(**args)  # 실제 begin 또는 기존 next; launch_permission=none
# 기존 reserve/guard/attach/complete 계약을 아래 절대로 생략하지 않는다.
r = e.report(self_tests='unverified', skills=['actually-applied-skill'], perspectives=[])
assert r['workflow_complete'] is False  # 아직 독립 검토 전
```

수용 전에는 `policy_acceptance=None`을 전달해 opt_in_required를 받고 프로젝트 범위·한도를 선택한다. 파일을 자동 생성하지 않는다. native.status는 unknown/available/unavailable이며 available/unavailable은 실제 공식 관측의 source_ref와 현재 coordinator observer가 필수다. 앱 설치를 확인한 것만으로 available이라고 쓰지 않는다. 현재 AI가 확인한 근거의 기록이지 암호학적 인증이나 보안 승인 대체물이 아니다. 정책 수용 객체는 정확히 ref/project/sha256/limits이며 limits는 정책 객체 전체(version/enabled/max_dispatches/max_fix_rounds)다. active concurrency는 1이다.

CLI는 API args를 비공개 `/private/development/entry-input.json`에 저장한 뒤 호출한다:

```sh
python3 scripts/development_workflow.py entry --project /path/to/product \
  --request-id request_example --coordinator native_coordinator_example \
  --coordinator-actor example-provider/coordinator_session_example \
  --state /private/development/controller.json --entry-state /private/development/entry.json \
  --input /private/development/entry-input.json
python3 scripts/development_workflow.py report --project /path/to/product \
  --request-id request_example --coordinator native_coordinator_example \
  --coordinator-actor example-provider/coordinator_session_example \
  --state /private/development/controller.json --entry-state /private/development/entry.json
```

`development_entry.py`도 같은 CLI를 제공한다. entry 입력은 위 args 전체다(기본 review_required=true, policy_acceptance=null). report 입력은 선택적으로 `{self_tests, skills, perspectives}`이며 실행 참조를 입력받지 않는다. solo self_tests는 pass/fail/unverified/not_performed, skills/perspectives는 각 최대 32개·512자 라벨이다. 참조·이유는 512자로 제한하며 전체 원문 대화를 넣지 않는다. 보고는 같은 private 경로의 `entry.report.json`에 0600으로 원자 저장한다. entry와 controller 및 파생 report 경로는 달라야 한다. CLI 성공은 작업 완료가 아니라 호출 성공이며 workflow_complete를 별도로 확인한다.

Entry journal version 1은 v2 controller와 별도다. 정확한 최상위 필드는 version/binding/controller/request/choice/policy_acceptance/native/policy_observation/observation/created/updated/integrity다. request는 kind/acceptance/files/review_required, choice는 mode/reason/instruction_ref다. mode=direct는 명시 직접 처리 지시이며 정책을 읽거나 위임 수용을 기록하지 않는다. 원래 review_required=true이면 충돌을 보고한다. policy_observation은 null 또는 sha256/value, observation은 before_controller/late_observation이다. 요청·선택·controller 경로·binding은 고정한다. 같은 요청의 `policy_acceptance=None`은 저장된 수용을 재사용하므로 재질문하지 않는다. pending에서 최초 수용·native 확인을 추가할 수 있지만 기존 수용이나 범위·정책 hash·한도 변경은 거부한다. 복구는 원래 요청/operation을 보존·조정하고 변경 범위에 대한 명시 선택을 새 request/별도 상태로 기록한다. 새 요청은 이전 active 실행·상한을 초기화하는 수단이 아니다.

프로젝트 밖 owner 전용 경로에 기존 lifecycle private/locked/atomic IO를 사용한다. 무결성 hash는 손상 검사이며 서명이 아니다. Entry는 수용한 sha256/limits의 복사본을 `begin(expected_policy={sha256, limits})`에 전달한다. 기존 begin의 admission 잠금 안에서 실제 정책 재읽기와 이 수용값을 대조하고 일치하기 전에는 새 controller 상태나 ready/예약 권한을 만들지 않는다. 정책 내용 또는 한도 변경은 accepted_policy_changed_before_admission으로 거부하며 파일 변경을 새 동의로 추정하지 않는다. expected_policy는 저수준 호환 API에서 선택 인자이고, 기존 상태 재사용 때에는 고정된 policy/source와 대조한다. Entry가 먼저 choice를 내구 저장하고 begin을 호출하므로 crash 재시작은 기존 controller next/같은 stage로 복구하며 재배정하지 않는다. 기존 v2 파일은 migration·역사 수정하지 않는다. 저수준 decision/begin은 호환 API로 유지되며 자체적으로 사용자 수용 journal을 요구하지 않는다. 따라서 **복합 개발의 권장 진입은 entry이며 저수준 API 우회는 report에서 missing/late로 발견**된다. native_available은 True/False/None이고 None은 native_check_required다. 기존 next()의 기본 True는 호환용이며 entry는 항상 실제 tri-state를 명시한다.

저장된 intent/native/receipt의 비지원 client는 무결성 hash가 다시 계산됐어도 거부한다. 실제로 집계하는 실행은 모두 지원되는 Orca guard 대조를 통과해야 한다. 비지원 reserve의 unavailable/무변경 응답은 실행 기록이 아니다. 보고는 controller binding·원래 AC·파일·정책과 실제 guard 참조, terminal receipt, 물리 provider/session 독립성, 현재 snapshot을 확인한다. 자체 테스트와 독립 검토를 분리하며 스킬/관점은 별도 actor가 아니다. 필수 검토 없음·미통과·동일 actor·stale snapshot·직접 처리 충돌·missing/late entry는 workflow_complete=false다. 다른 request/project/coordinator/actor의 completed 상태는 수용하지 않는다. entry 없는 report는 read-only 감사로 controller를 바꾸거나 entry를 만들지 않으며 별도 감사 보고만 저장한다. 사후 entry도 late_observation으로 남아 역사적 사전 실행을 주장할 수 없다. independent_review는 실제 reviewer native/result에 따라 not_performed(실제 검토자 없음, 예약·ready만으로 수행 아님), pending(연결된 검토자 결과 대기), changes_requested(수정 요청), failed(실패·비성공 종료), unverified(영수증·독립성 등 통과 근거 미확인), pass(유효한 독립 통과), stale_snapshot(통과 후 파일 변경)으로 구분한다. 자체 테스트·스킬만 있는 보고는 not_performed다. workflow_complete는 유효한 pass와 누락 없는 사전 entry를 모두 요구한다. 실제 Stop/정확한 exit·사용량 마감은 기존 공식 도구와 조율자의 별도 책임이다.

## 프로젝트별 명시 선택

대상 프로젝트 루트의 `.ai-development-policy.json`만 읽는다. `workflow.json`, `.ai-workflow.json`, provider/model/effort 설정과 별개다. 다음은 필수 필드 전체이며 추가 필드·중복 JSON 키·비유한 숫자를 거부한다.

```json
{"version": 1, "enabled": true, "max_dispatches": 4, "max_fix_rounds": 1}
```

version은 정수 1, enabled는 boolean, max_dispatches는 정수 2..8, max_fix_rounds는 정수 0..3이다. boolean을 정수 대신 사용할 수 없다. active concurrency는 요청당 1로 고정하며 변경 필드가 없다. 정책 파일은 실행 상한 설정이며, 명시 수용은 아래 entry journal의 별도 기록으로 연결한다. 파일의 존재나 enabled 자체는 보안 권한, native 접근 가능성, 전역 위임 승인, 자동 공개 승인이 아니다. 조율자는 최초 연결 때 필요한 선택과 실제 권한을 확인하고 기록하며 요청마다 같은 선택을 재확인하지 않는다. 기존 요청의 정책·AC·파일 목록은 begin에서 고정한다. 새 admission은 no-follow descriptor로 읽은 enabled 정책 snapshot의 원문 SHA256·version·inode/mtime/ctime/size를 확보하고 상태 잠금 안에서 실제 파일을 다시 열어 대조한다. true→false나 삭제는 opt_in_required, 내용/상한/파일 정체성 변경과 symlink 교체는 오류로 거부하며 상태를 생성하지 않는다. 이미 시작한 요청은 저장된 승인 snapshot과 상한을 유지하며 begin 재호출도 새 정책을 채택하지 않는다. 정책 파일 편집은 실행 중 worker를 중단하는 명령이 아니므로 취소는 cancel과 공식 native stop으로 처리한다.

현재 AI는 `conversation`, `small_change`, `development` 중 의미를 직접 공급한다. 키워드 분류기나 분류 모델을 호출하지 않는다. `direct=true`는 직접 처리/위임 금지 사용자 선택을 뜻하며 가장 우선한다. conversation, small_change, direct 경로는 정책·역할 본문을 읽거나 상태 디렉터리를 생성하지 않고 direct를 반환한다. development에 정책이 없거나 enabled=false이면 opt_in_required, native 도구가 없으면 unavailable이다. 이 둘 모두 상태를 만들지 않는다.

## Python API와 CLI

### 지속 승인과 요청별 한도

누가 배정 방식·한도를 결정할지는 [하네스 설정](harness.md#배정-방식과-한도의-결정-주체)의 `delegation_decision_maker`가 정한다. 기본 `coordinator`에서는 승인된 요청 범위 안에서 조율자가 필요한 작은 한도를 정해 정책·명시 수용 참조를 준비한다. `user`에서는 유효한 기존 선택을 재사용하고, 없을 때만 사용자에게 방식·범위·한도를 선택받는다. 현재 사용자의 직접 지시와 더 좁은 제한을 우선하며, 설정 변경은 이미 실행 중인 요청의 고정된 선택을 바꾸지 않는다. 아래 로컬 문서는 실제 사용자 지시와 승인 범위의 근거일 뿐, 하네스의 결정 주체 값을 덮어쓰지 않는다.

배정 방식·숫자 선택이 반복되는 문제는 공통 workflow 진입에서 처리한다. 사용자가 개별 프로젝트가 아닌 공통 workflow에 결정을 맡겼다면 공통 저장소의 `.ai-development-preferences.local.md`에 그 원래 지시와 적용 범위·운영 상한·제외 범위를 저장한다. 연결된 프로젝트에서 최초 선택을 묻기 전에 이 기록과 프로젝트별 선택을 확인한다. `coordinator` 모드이고 적용 범위에 포함되며 더 좁은 제한이나 취소가 없다면, 정책이 아직 없는 프로젝트에서도 같은 질문을 반복하지 않고 조율자가 요청별 정책과 명시 수용 참조를 준비한다. `user` 모드로 바꿨다면 과거 조율자 위임 선호만으로 새 요청의 한도를 대신 결정하지 않는다. 사용자가 요청하지 않은 새 업무를 자율 생성하라는 뜻은 아니다.

공통 선택과 프로젝트 선택은 개인의 로컬 기록이며 Git으로 배포하지 않는다. 다른 사용자의 복제본에는 선택이 없으므로 기존 최초 선택 절차가 적용된다. 대상 프로젝트의 사용자·조직 제한이나 직접 처리 지시를 공통 선호로 덮어쓰지 않는다. 현재 AI가 실제 사용자 지시를 대조한 뒤 기존 policy_acceptance의 ref/project/sha256/limits를 공급하는 운영 연결이며, 컨트롤러가 두 문서를 자동 해석하거나 설정 파일만으로 동의를 인증하는 기능은 아니다. 현재 진행 중인 요청의 고정된 정책·한도는 유지한다.

사용자가 향후에도 조율자가 한도를 정해 진행하도록 명시적으로 맡긴 경우, 매 요청마다 같은 업무 승인을 다시 묻지 않는다. 프로젝트 로컬 `.ai-development-consent.local.md`에 대상 프로젝트, 원래 사용자 지시와 참조, 조율자가 선택 가능한 동시·누적·수정 상한, 제외 범위와 취소 방법을 남긴다. 이 파일은 사람의 선택을 이어 읽기 위한 기록이며 새 실행 권한이나 프로그램이 인증한 동의가 아니다. 공개 저장소에는 개인의 승인 기록과 활성 정책을 배포하지 않는다.

조율자는 각 요청을 시작하기 전에 기존 승인 범위 안에서 필요한 작은 한도를 선택하고, 실제 정책 hash/limits와 원래 지속 승인 참조를 기존 Entry의 policy_acceptance에 연결한다. 동일 요청에서는 저장된 수용을 재사용한다. 다른 요청을 시작할 때 정책을 바꾸더라도 기존 요청의 고정된 한도·실행·실패 이력은 그대로 유지한다. 소진된 같은 작업을 새 요청으로 옮겨 상한을 우회하지 않는다. 추가 범위가 승인 상한 안에서 허용됐는지 판단하고 그 근거를 별도로 기록한다.

적용 가능한 공통·프로젝트 지속 승인이 모두 없을 때 기존 최초 선택 절차를 유지한다. 공통 선택이 명시한 연결 프로젝트 범위와 요청한 업무 안에서만 재사용하고, 모델·권한·전역 설정, 외부 공개 같은 제외 범위는 지속 위임 동의로 확대하지 않는다. 사용자는 취소하거나 더 좁은 한도를 지정할 수 있으며 새 지시를 우선한다. 업무 승인 재사용은 공식 도구의 보안 승인·실제 native 지원 확인을 생략하지 않는다. 컨트롤러가 로컬 동의 문서를 자동 해석하거나 앱 입력을 강제하는 기능은 없다.

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

begin 이후 명령도 같은 project/request-id/coordinator/coordinator-actor/state를 지정한다. `--input`은 API 키워드 인자 객체다. decision 입력은 정확히 `{kind, direct, native_available}`, begin은 `{kind, direct, native_available, acceptance, files}`와 선택적 `expected_policy: {sha256, limits}`, reserve는 `{stage_id, intent}`, attach는 `{stage_id, execution}`, next는 `{}` 또는 `{native_available: false}`, cancel은 `{}`다. complete의 입력은 키워드 wrapper 없이 receipt 객체 자체다. import와 `--help`는 실행 부작용이 없다.

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

37개 컨트롤러 집중 테스트에서 합성 normalized 영수증으로 직접 경로/미동의/도구 부재, 정상 독립 검토, 수정 재검토와 상한, 동일 물리 세션, 잘못된/오래된 콜백과 replay, 파일 변경, 취소 종료 대기, private/symlink/상한, lock과 CLI import를 확인한다. 기존 guard 실행 함수를 mock한 unknown-receipt → 재시작 → 동일 operation replay의 실제 호출 수 1, 예약 재호출 허가 재발급 금지, 잘못된 attach/예약 충돌, 실제 두 정책 snapshot 읽기 사이 비활성화·상한 변경·동일 내용 파일 교체·symlink 교체, 재시작한 수정/완료 맥락, v1 상태의 무변경 거부도 확인한다. 실제 native worker launch/exit 통합 검증이나 영수증 진위 검증을 수행한 결과는 아니다.

신규 진입 검사는 `python3 -m unittest discover -s tests -p test_development_entry.py -v`로 실행한다. 진입 24개와 상속한 컨트롤러 37개, 총 61개가 정책 수용/unknown/직접 지시/선택 고정/누락 감사/실제 controller 호출/현재 snapshot/보고 게이트/private IO를 검사한다. guard 회귀는 `python3 -m unittest discover -s tests -p test_dispatch_guard.py -v`다. 합성 단위 검사와 실제 입력·native 통합 검증은 별도로 보고한다.

## 실제 Orca 검증 범위

2026-11-18, 같은 프로젝트에서 동시 1명·누적 2배정·수정 배정 0회의 읽기 전용 실증을 마쳤다. 각 역할의 실행 전에 v2 예약을 저장하고 공식 guard로 실행했으며, 개발자의 집중 검사 34개 통과와 서로 다른 실제 provider 세션의 독립 검토를 확인했다. 검증자는 추가 replay 검사 3개를 통과했다. 두 배정의 실제 종료·Stop 수신·SQLite 수입과 settlement 재처리, 최종 컨트롤러 완료 영수증의 재처리 불변성을 확인했다.

이 실증에서 제품 파일은 바꾸지 않았다. 수정·재검증 분기의 실제 실행, 앱 입력 이벤트 자동 연결, Codex·Paseo 실행 adapter와 전체 요청 사용량 수집은 검증한 범위에 포함하지 않는다. 검증 전용 정책은 보관 후 제거했으며 사용자 프로젝트의 지속 opt-in으로 확대하지 않는다. 전체 Python 회귀 816개 통과와 별도 코드 재검토 근거는 같은 제품 버전에 대해 보존했다. 실제 세션·경로·로그·DB는 공개 문서에 포함하지 않는다.

## 명시 역할 계획 (선택적 v3)

기본 plan 생략 API와 기존 v2 controller/Entry 기록은 backend developer → verifier 계약을 그대로 유지한다. 기존 이력에 plan을 덧붙이거나 v3로 migration하지 않는다. 아래 계약은 새 요청에서 `entry(..., plan=...)` 또는 저수준 `begin(..., plan=...)`을 명시한 경우에만 적용한다. 저수준 begin은 기존처럼 정책 명시 수용을 증명하지 않으므로 실제 사용자는 Entry로 진입한다. Entry journal version 1의 request에 선택적 plan을 보존하며, 보고서는 계획 경로에서 version 2를 사용한다. 새 컨트롤러 상태 version 3은 v2 필드에 plan/plan_hash/design_decisions를 추가한다. 모델 분류기·앱 입력 가로채기·자동 opt-in은 없다.

```json
{"version":1,"implementation":["frontend","backend"],"pre_design":"fresh","human_selection":true,"visual_review":true,"artifact":"design.txt","reuse":null,"selection_ref":"explicit-user-role-choice"}
```

implementation은 backend 또는 frontend 하나, 혹은 두 역할의 명시 순서다. mixed는 각각 실제 구현 stage를 실행하며 같은 backend 작업에 이름만 붙이지 않는다. pre_design은 none/fresh/reuse다. none이면 artifact/reuse=null, human_selection=false다. fresh는 designer의 별도 실행이고 artifact는 명시한 프로젝트 상대 경로다. reuse는 실행하지 않으며 기존 artifact의 정확한 `{path,version,sha256,selection_ref}`를 reuse에 전달한다. sha256은 파일 원문 해시다. 빈 선택 근거나 달라진 파일은 거부한다. 모든 실제 역할은 native-default를 유지하고 guard usage_role을 frontend/backend/designer/verifier로 검사한다. 역할 문서와 적용 스킬을 각 stage에 연결한다. 현재 AI의 디자이너 관점은 report perspectives이며 native stage나 재사용 승인을 대신하지 않는다.

fresh designer → (필요 시 사람 선택) → 명시한 구현 순서 → (선택적 designer visual_review) → 독립 verifier가 기본 순서다. 모든 단계는 같은 request/정책의 예약·dispatch/fix 한도를 소비한다. 최소 필수 실제 단계 수가 max_dispatches를 초과하면 admission을 거부한다. 실패 수정은 전체 순서를 다시 수행하는 보수적 정책이다. design/visual/functional 실패와 unverified는 같은 max_fix_rounds를 사용하며 남은 필수 재실행 전체가 한도에 들어오지 않으면 exhausted다. 이전 pass로 현재 재검토를 대체하지 않는다. terminal 실패/취소는 failed/cancelled로 남긴다.

v3 result는 기존 outcome/self_test/snapshot/acceptance/findings에 artifact/visual_evidence를 더한다. 모든 stage가 원래 AC 전체 결과와 관련 source snapshot을 제출한다. pre_design만 artifact 객체를 제출하며 나머지는 null이다. visual_evidence는 최대 32개의 strict `{kind,subject,provenance,implementation_stage_id,source_ref,snapshot,observer}` 객체 목록이다. source_ref는 비어 있지 않은 최대 4096자 참조이며 snapshot/observer는 결과 snapshot/coordinator와 일치해야 한다. `subject=implementation`은 `kind=image, provenance=implementation_capture` 또는 `kind=real_ui, provenance=real_ui_observation`만 허용하고, implementation_stage_id는 검토 직전 마지막 필수 구현 단계의 ID여야 한다. 그 단계의 자체 검사/pass와 snapshot도 검토 결과에 일치해야 한다. 혼합 구현이면 두 구현 의무가 끝난 최종 구현 단계에 연결한다. 생성 시안·설계 참고는 `subject=design_reference, provenance=generated_mockup|design_reference, implementation_stage_id=null`로 구분한다. image라는 kind만으로 구현 캡처라고 추정하지 않는다. 실제 현재 구현 관측이 하나도 없으면 design 참고가 있더라도 visual pass로 승격하지 않고 unverified로 남긴다. 로컬 테스트는 화면 품질을 증명하지 않는다. 자료를 직접 진위 인증하는 도구가 아니므로 coordinator는 실제 관측만 기록해야 한다.

human_selection은 design 결과 이후, 구현 reserve 이전의 독립 gate다. `select_design(decision=...)`은 정확히 `{plan_hash,artifact,proposal_stage_id,decision:"approved",instruction_ref,observer}`를 받는다. artifact는 보고된 proposal 객체 그대로, proposal_stage_id는 fresh pre_design stage ID 또는 reuse의 경우 `reuse`, observer는 coordinator다. instruction_ref는 해당 시안/버전을 사람이 명시 선택한 원래 지시의 참조다. 파일 존재·에이전트 pass·보안 승인·일반적인 진행 지시로 만들지 않는다. 이는 조율자의 관측 주장이지 사용자 인증이나 보안 권한이 아니다. 시안 변경은 예약/연결을 막고 보고에 stale을 남긴다. 변경된 제안은 새 명시 계획 요청 또는 실패에 따른 fresh design 재실행과 새 선택이 필요하다. 기존 승인에서 해시만 바꾸지 않는다.

Entry는 선택된 plan과 native tri-state를 저장한다. 같은 요청에서 plan/범위/choice를 바꾸지 못한다. 미수용은 opt_in_required, unknown은 native_check_required, 확인된 부재는 unavailable이다. 재시작은 같은 예약 operation만 reconcile하며 새 launch_permission을 발급하지 않는다. direct/위임 금지는 우선하며 required review와의 충돌은 omissions로 보고한다. 계획은 승인 범위를 자동 확장하지 않는다.

보고서는 plan, 실제 guard에서 확인한 executions, reused_design, design_decisions, self_tests_by_stage, design_outcome, human_selection, visual_review, independent_review와 gates를 구분한다. required gate가 모두 현재 관측 snapshot에서 pass일 때만 workflow_complete다. source 변경은 완료 후에도 visual/functional을 stale로 만든다. pre-design source snapshot은 설계 당시의 근거이며 구현 후 동일할 수 없으므로 현재 시안 artifact/선택을 별도로 검사한다. 명시 files/artifact 밖의 변경이나 관측 직후의 외부 파일 변경은 잠그지 못한다. 새 전체 frontend native pipeline의 실제 실행은 합성 테스트와 별도로 검증해야 한다.

fresh/reuse 기준 시안 artifact가 변경되면 report와 controller view 모두 design_selection을 stale로 표시하고, 그 시안에 대한 기존 visual_review pass도 stale로 표시한다. 사람 선택을 요구했다면 human_selection도 stale이며 전체 완료는 false다. source가 그대로인 독립 기능 검토는 이 변경만으로 무효화하지 않는다. 화면 검토가 없거나 미수행·대기·실패·unverified이면 그 상태를 유지하며 independence_collision의 unverified가 stale보다 우선한다. 저장된 승인·receipt·과거 pass와 상태 파일은 관측으로 수정하지 않으며 변경된 시안을 자동 승인하지 않는다.

화면 근거의 subject/provenance를 생략한 초기 v3 result는 strict schema 오류로 거부한다. 기존 기록에 구현 관측을 추정해 넣거나 소급 변경하지 않는다. v2의 API·이력·결과 schema는 그대로다. 이 명시 provenance도 조율자의 관측 주장이며 OCR·이미지 진위 인증·새 캡처 엔진이나 보안 승인을 요구하지 않는다.

독립성은 attach뿐 아니라 report/view의 공통 gate 판정에서도 확인한다. functional reviewer 또는 visual reviewer의 canonical provider/session이 구현자·pre_design 작성자·coordinator와 충돌하면 해당 공개 상태와 gate는 unverified이며 `independence_collision:<role>:<stage_id>` omission을 남긴다. 결과와 receipt의 identity를 일치시켜 integrity를 재계산해도 pass로 표시하지 않는다. source 변경 여부와 무관하게 충돌을 표시하며 실제 저장된 guard 실행 참조는 그대로 보고한다. 현재 source가 달라진 정상 독립 검토는 기존대로 stale이다.

### 엄격 UI 품질 계약 (plan v2)

이전 v3는 AC·역할 pass와 실제 구현 capture provenance를 검사했지만 같은 비교 조건·항목별 시각 관찰은 강제하지 않았다. plan.version=2는 아래 시각 검토 gate를 도입한 과거 경로다. **새 UI 구현/수정은 아래 plan v3 프론트엔드 선행 계약까지 전달한다.** 저장 controller v3/Entry journal v1은 유지한다. plan v1·계획 없는 v2·과거 receipt는 그대로 읽고 소급 수정하지 않는다. 비 UI는 기존 경로다. 자동 UI 분류는 없으며 조율자가 판단한다. 계획 생략/v1은 strict 미활성, plan v2는 시각 검토만의 범위이며 새 전체 UI 품질 인증을 주장하지 않는다.

plan v2는 v1의 정확한 필드에 `visual_quality`만 추가하며 frontend 의무와 visual_review=true가 필수다. 계약의 정확한 필드는 `{version:1,required:[...],context:{viewport,theme,content,state}}`. required는 중복 없는 여섯 차원 `theme_reference,hierarchy,typography,spacing_density,alignment,consistency`의 부분집합이며 최소 기준/테마·위계·간격/밀도·일관성 네 항목을 포함한다. 나머지도 원래 요청에 따라 필수로 고정한다. context 값은 비어 있지 않은 최대 4096자 문자열 또는 null(미확인). viewport에는 논리적 크기/배율 등 비교 조건을, content/state에는 같은 데이터와 정상/오류 등 상태를 특정한다. 계약 전체는 Entry request와 plan_hash에 실행 전에 동결하고 resume 변경을 거부한다.

엄격 결과는 기존 v3 result를 유지하고 선택 필드 `visual_quality`를 추가한다. 없거나 null이면 visual gate는 unverified와 `strict_visual_quality_missing` omission이다. 구현/기능 검토 stage에는 생략할 수 있다. 객체의 정확한 필드는 다음과 같다.

- `version:1`, `reference_context`, `implementation_context`: 계약과 같은 context 네 필드.
- `observations`: 위 여섯 차원 모두를 키로 가지며 각각 정확히 `{status,detail,reference_ref,implementation_ref}`. status=pass/fail/unverified, detail은 비어 있지 않은 최대 4096자 구체적 비교 관찰, 이미지 참조는 같은 한도의 문자열 또는 null이다. 기준/테마, 정보 위계, 타이포 가독성, 행/화면 간격과 밀도, 레이블/값/화살표 정렬, 컴포넌트/토큰 일관성을 따로 관찰한다.
- `inspections`: `full_actual,full_enlarged,detail_actual,detail_enlarged` 네 키에 각각 정확히 `{source_ref,observation}`. 각 값은 위 한도의 문자열 또는 null이며 현재 실제 구현 이미지 참조여야 한다. 같은 원본을 실제 크기·확대로 볼 수 있으나 수행한 관찰만 기록한다. full_actual은 현재 전체 정상 화면 검토다.
- `prior_findings_rechecked`: boolean. 수정 라운드에는 이전 지적과 현재 전체 정상 화면까지 재확인해야 true다. 상세 crop만 고쳤다면 false와 누락 inspection을 남긴다.
- `device`: 정확히 `{status,source_ref,detail}`. status=observed/unverified/not_performed, detail은 위 한도의 문자열, source_ref는 문자열 또는 null. observed는 실기기 관측 참조가 필수다. Web/에뮬레이터를 실기기로 추정하지 않는다. 이 계약이 실기기 pass를 자동 요구하지는 않는다.

필수 관찰의 기준/구현 이미지 참조는 같은 result의 visual_evidence에 있어야 한다. 기준은 kind=image/subject=design_reference, 구현은 kind=image/subject=implementation/provenance=implementation_capture다. 생성 mockup은 기준으로만 쓰며 구현 이미지가 될 수 없다. real_ui 관측만으로 이미지 요구를 대체하지 않는다. 기존 R1의 마지막 필수 구현 stage ID·snapshot·observer·자체 검사, R2 독립성, R3 설계/소스 freshness를 함께 유지한다. 모든 inspection도 현재 구현 이미지에 연결한다.

고정/기준/구현 context의 네 값이 명시적으로 모두 같아야 strict pass다. null/unknown/unset/unverified 또는 불일치는 보수적으로 비교 전체를 unverified로 두고 `visual_comparison_context_unverified_or_mismatched`를 보고한다. 필수 관찰 fail/unverified/이미지 누락, inspection 누락, 이전 지적 미확인도 이름 있는 omissions로 surfaced되며 완료를 막는다. 구조·터치·테스트 pass로 덮지 않는다. 합의 시안 불일치는 취향으로 기각하지 않는다.

view/Entry report는 `strict_visual_quality_active`, `visual_quality_coverage`, `functionality`, `visual_quality`, `reference_match`, `real_device`, `visual_quality_review`를 분리한다. plan v2는 visual_only_v1_frontend_unverified로 원래 시각 gate 범위만 밝힌다. plan v1은 legacy_not_strictly_certified이며 workflow_complete/visual_review의 과거 pass를 유지해도 새 visual_quality/reference_match는 unverified다. 계획 없는/직접 경로는 not_activated다. 원본 계약과 관찰을 함께 보존한다. 기능은 별도 reviewer gate, 시각은 visual gate, 실기기는 관측 기록 범위다. 보고는 과거 state/result bytes를 바꾸지 않는다.

strict plan의 `reference_match`는 최신 화면 검토의 `theme_reference` 관찰을 별도로 보고한다. 같은 명시 context, 현재 snapshot, 성공 receipt, 독립 검토, 유효한 시안 선택, 구현 선행 gate와 최종 구현 단계의 이미지 capture provenance, 참조 이미지·inspection·이전 지적 재확인이 유효하면 해당 관찰의 pass/fail을 유지한다. 누락·unverified·오래된 근거·다른 단계의 capture는 unverified다. spacing_density 등 다른 품질 항목의 실패만으로 유효한 참조 비교 pass를 낮추지 않으며, 전체 visual_quality·완료 gate는 그대로 실패/미확인 상태를 유지한다. 이는 기존 조율자 관측의 보고이며 이미지 진위 인증이나 실제 미감·실기기 검증을 새로 제공하지 않는다.

컨트롤러는 관측 주장의 schema·연결을 검사하며 픽셀 심미 평가·이미지 진위 인증·자동 캡처 엔진이 아니다. 합성 fixture 검사를 실제 제품 디자인 pass나 native frontend chain 실증으로 확대하지 않는다. 이 개선의 bootstrap은 기존 고정 컨트롤러를 사용하며 새 gate를 실제 사용했다고 소급 주장하지 않는다.

### 명시적 PNG 캡처 사전 검사

UI 캡처를 완료 근거로 제출하는 호출자는 완료 보고 전에 `python3 scripts/ui_capture_evidence.py --manifest /private/tmp/capture-manifest.json`을 실행하고 JSON 결과와 종료값을 비공개 근거로 보관한다. 아래 checked completion을 명시 선택하면 같은 helper 검사를 완료 제출에 연결한다. 기본 complete는 helper를 자동 강제하지 않으며 기존 결과 schema·이력은 변경하거나 소급 보정하지 않는다.

manifest는 정확히 `{"version":1,"captures":[...]}`이며 captures는 1~32개다. 항목은 정확히 `expected_receipt_id, expected_page_id, receipt_path, native_image_path, image_path, image_sha256` 여섯 필드다. ID는 기대하는 실제 성공 영수증 ID와 page ID, 세 경로는 명시적 절대 파일 경로(상위 이동 금지), hash는 보존 PNG의 소문자 SHA256 64자다. 원본 native PNG도 실제로 있어야 하며 두 PNG의 signature·hash가 기대값과 일치해야 한다. JSON 파일별 1 MiB, PNG별 32 MiB, 실행 전체 읽기 256 MiB 상한을 적용한다. 링크 경로·비정규 파일·읽는 중 변경을 거부하며 macOS `/tmp`·`/var` 시스템 별칭만 `/private` 아래로 정규화한다.

receipt_path는 저장한 공식 실행 wrapper다. 정확한 필수 키는 `command, exit, stdout, stderr`, 선택 키는 `time`이다. command는 `["orca","screenshot","--page","page-example","--json"]` 형태이며 executable의 절대 경로와 `full-screenshot`도 허용한다. 중복 flag·추가 인자는 거부한다. exit는 정수 0, stderr는 빈 문자열, stdout은 공식 응답을 JSON 인코딩한 문자열이어야 한다. 응답 필수 키는 `id, ok, result`(ok=true), 선택 키는 `_meta`(선택 문자열 runtimeId만)다. result는 `format="png", path`와 선택 문자열 `expiresAt`만 허용한다. 선택 time도 문자열이다. ID와 page는 정확히 비교하고 result.path는 명시한 native_image_path와 정규 경로를 비교할 뿐 영수증의 임의 경로를 열지 않는다. expiresAt는 참고 메타데이터이며 삭제·유효성 증거가 아니다. 누락·만료 뒤 사라진 원본은 통과할 수 없다.

stdout 결과는 `status=pass|fail|invalid`, `scope=metadata_consistency_only`와 capture별 index/status/code다(입력 자체가 invalid이면 일반 code). 종료값은 pass=0, 나머지=1이다. 호출자는 실제 관측과 함께 결과를 연결한다. 이 검사는 메타데이터·파일 바이트 일치만 확인하며 영수증 진위·이미지 심미성·실기기·PNG 전체 디코딩을 증명하지 않는다. 캡처·복사·변환·네트워크·native 실행·DB 쓰기를 수행하지 않는다.

완료 제출에 사전 검사를 연결할 때는 `controller.checked_complete(receipt, capture_manifest="/private/tmp/capture-manifest.json")` 또는 기존 `development_workflow.py complete` 명령의 binding·state·input 인자에 `--capture-manifest /private/tmp/capture-manifest.json`을 추가한다. 이 옵션은 complete에만 허용된다. manifest를 한 번 읽고 검증하는 같은 helper 호출 안에서 모든 capture의 image_path와 receipt.result.visual_evidence 중 kind=image, subject=implementation, provenance=implementation_capture인 source_ref를 정규 경로로 정확히 대응한다. 현재 참조가 1개 이상 필요하며 누락·추가·중복 참조, 중복 image/native 경로·영수증 ID와 잘못된 타입을 거부한다. 디자인 기준 이미지와 real_ui 관측은 구현 PNG를 대신하지 않는다.

현재 checked CLI는 **독립 검토 후 수정 대기**다. 깊게 중첩된 manifest에서 traceback이 나오고, 없는 manifest의 파일명이 오류에 노출되는 두 문제가 확인됐다. 두 경우 컨트롤러 상태는 보존됐지만 완료된 오류 처리로 취급하지 않는다. 명시 검사 도구 자체의 고정 JSON 오류 처리와 구분하며, 수정·독립 재검토 전에는 민감한 경로를 포함한 실패 출력을 외부에 공유하지 않는다.

사전 검사·대응 실패는 정상 complete나 controller 상태 변경 전에 거부한다. 성공 뒤에는 기존 complete의 역할·stage·snapshot·provenance·독립성·전체 품질 검사를 그대로 적용한다. 메타데이터 일치만으로 품질 gate를 통과시키지 않으며 인증 필드를 저장하지 않는다. checked replay도 매번 원본 native PNG의 현재 가용성을 포함해 재검사하므로 이전 성공 뒤 파일이 사라지면 실패할 수 있다. 이는 해당 호출 시점의 검사이며 파일시스템을 원자적으로 잠그지 않는다. 검사 뒤 파일·manifest가 변경될 수 있고 진위 인증도 제공하지 않는다. 기본 API/CLI complete, legacy/no-UI 기록에는 기존 동작을 유지하며 새 품질 인증을 소급 부여하지 않는다. 이 선택 경로는 전역 강제나 앱 입력 자동 hook이 아니다.

### 프론트엔드 선행 계약 (plan v3)

F1 보완 전 plan v2의 frontend pass는 self_test만 요구해 구현 전 기준과 자체 비교 없이 독립 검토로 진행할 수 있었다. **새 UI 명시 Entry의 plan.version=3**은 plan v2 필드에 `frontend_baseline`을 추가하고 frontend 결과에 `frontend_comparison`을 연결한다. 기준은 Entry/begin에서 실행 예약 전에 accepted request/plan_hash에 동결한다. 실행 후 결과만으로 기준을 재구성할 수 없으며 resume에서 바꿀 수 없다. 기존 시각 계약·provenance·독립성·소스/설계 freshness·수정 후 전체 재검토는 모두 유지한다.

`frontend_baseline`의 정확한 필드는 `{version:1,reference_ref,source_ref,context,decisions}`다. reference_ref는 합의한 정확한 기준 이미지 버전 참조, source_ref는 컴포넌트/토큰/레이아웃 결정의 수용 근거, context는 visual_quality 계약과 같은 viewport/theme/content/state다. decisions는 정확히 `components,tokens,layout,shared_components,separate_label_value_chevron` 다섯 키에 각각 `{decision,allowed_reason}`을 가진다. decision은 공통 컴포넌트 선택/재사용, 토큰, 배치, 레이블/값/chevron 별도 요소 정렬 등 구체적 수용 결정을 적는다. allowed_reason은 사전 허용한 차이의 정확한 근거 문자열 또는 null이다. 참조·결정·근거는 비어 있지 않은 최대 4096자 문자열이다. context의 null/unknown/unset/unverified와 미확인 source/reference는 actionable omission을 내고 frontend reserve/attach를 차단한다. 필드 누락은 admission schema 오류다. 파일/이미지의 진위는 자동 인증하지 않는다.

frontend 결과의 선택 필드 `frontend_comparison`이 없거나 null이면 그 frontend stage는 통과하지 않으며 독립 검토로 전환하지 않는다. 정확한 객체 필드는 `{version:1,baseline_hash,stage_id,snapshot,context,comparisons}`다. baseline_hash는 동결된 baseline 객체의 기존 canonical digest, stage_id는 현재 frontend stage, snapshot은 result와 같은 현재 소스 해시, context는 baseline과 정확히 같은 조건이다. comparisons는 decisions와 같은 다섯 키이며 각각 정확히 `{status,observation,difference,allowed_reason,reference_ref,implementation_ref}`다. status는 match/allowed_difference/fail/unverified, observation과 difference는 최대 4096자의 구체적 비교·차이 기록(차이 없으면 명시), 나머지는 같은 한도의 문자열 또는 null이다. allowed_difference는 baseline에 사전 수용한 allowed_reason과 정확히 일치해야 한다. 실행 뒤 만든 허용 사유로 차이를 덮지 않는다.

모든 다섯 비교는 필수다. pass 가능한 것은 match 또는 사전 수용 이유가 일치하는 allowed_difference뿐이다. reference_ref는 동결 reference_ref이며 같은 result에 image/design_reference 증거가 있어야 한다. implementation_ref는 같은 result의 image/implementation_capture 증거로 현재 frontend stage_id/snapshot/coordinator observer에 연결한다. mockup이나 이전 stage/crop을 실제 현재 구현으로 대체하지 않는다. 잘못된 shape/범위는 거부하고, 누락/실패/미확인/바인딩·이미지 불일치는 `frontend_self_comparison_*` omissions로 통과를 거부한다. self_test/기하 검사 pass는 이를 대체하지 않는다. 별도 label/value/chevron과 공통 컴포넌트 사용 기록은 감사 가능한 주장이지 AST/픽셀 자동 인증이 아니다.

view/Entry report에 `frontend_quality_active`, `frontend_prerequisite`, `frontend_baseline`, `frontend_baseline_hash`, `frontend_comparisons`를 노출한다. plan v3만 frontend_quality_active=true 및 coverage=strict_v2_with_frontend_prerequisites다. plan v2는 원래 state/result schema와 완료 의미/바이트를 그대로 유지하되 coverage=visual_only_v1_frontend_unverified, frontend_quality_active=false, frontend_prerequisite=unverified다. 과거 pass를 프론트엔드 선행 계약까지 검증한 것으로 소급 인증하지 않는다. plan v1/no-plan/direct/backend 경로도 기존 의미를 유지한다. 변경된 계획은 새 명시 진입이 필요하며 자동 이관하지 않는다.

### v3 검증 범위

최종 코드에서 집중 Python 검사 137개와 전체 919개가 통과했고, 별도 검증자가 독립적으로 선택한 합성 경계 사례 43개를 통과했다. 생성 시안만으로 화면 검토를 통과시키는 문제, 동일 작성자의 검토를 독립 통과로 표시하는 문제, 기준 시안 변경 후 화면 검토가 pass로 남는 문제를 수정하고 재검토했다. 과거 실패 기록은 보존했다.

이 수정 작업의 실제 개발자·검증자는 기존 v2 경로로 실행했고, 두 배정의 Stop 수신·정확한 종료·SQLite 수입 및 재처리 불변성을 확인했다. 이는 새 v3의 프론트엔드 → 화면 검토 → 독립 기능 검토 전체 실환경 실행을 증명하지 않는다. 다음 실증에서는 새 Entry에 역할 계획을 먼저 고정하고 실제 역할별 실행과 화면 근거를 연결해야 한다. 시험 정책은 보관 후 제거했으며 지속 위임 동의로 확대하지 않는다.

후속 v3 실증에서는 기존 가상 화면을 수정하지 않고 새 Entry·실행 전 예약 → 실제 프론트엔드 자체 검사 3개 → 별도 디자이너의 390/1280 CSS px 구현 화면 관측 → 별도 검증자의 독립 검토까지 실행했다. 검증자는 화면 캡처와 소스를 대조하고 합성 경계 검사 7개를 통과했다. 세 배정의 정확한 종료와 소유 서버·탭 정리를 확인했다. 새 UI 구현, 시안 생성·사람 선택, 혼합 구현이나 앱 입력 자동 연결의 검증으로 확대하지 않는다.

이 실증은 **부분 마감**이다. 앞 두 배정은 실제 Stop·SQLite 수입과 재처리를 확인했지만, 앱 재시작을 거친 마지막 검증자는 Stop 영수증과 canonical provider 세션 ID를 공식 조회에서 확보하지 못했다. 독립 검토 보고서는 보존하되 마지막 attach/complete를 만들지 않았고 최종 workflow_complete는 false다. 원인을 확정하지 않았으며 새 실행이나 원본 탐색으로 누락을 숨기지 않는다. 공식 식별자와 실제 Stop 근거가 확보돼야 같은 배정의 최종 연결을 재개할 수 있다. 지속 위임은 별도의 명시 사용자 지시로 선택했으며, 이 실증에서는 해당 로컬 정책을 유지했다.
