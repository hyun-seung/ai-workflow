# Paseo 개발 흐름 연결의 사전 조사

2026-11-18 설치 CLI·SDK·서버 0.16.0의 도움말과 배포 구현을 읽은 결과다. 실제 agent를 생성하거나 앱 설정을 바꾸지 않았다. 현재 컨트롤러의 Paseo adapter는 계속 `unavailable`이다.

## 확인한 계약

| 항목 | 확인한 지원 | 연결 시 주의점 |
| --- | --- | --- |
| 생성 | `paseo run --workspace <id> --no-wait --label <key=value> <prompt>` | 실행 전 예약과 정확한 host/workspace 고정 필요 |
| 응답 유실 후 조회 | `paseo ls --all --label <key=value> --json` | label 필터는 지원하지만 유일성·중복 생성 방지는 보장하지 않음 |
| 정확한 agent 조회 | `paseo inspect <id> --json` | 전체 ID로 조회. 현재 CLI 출력에는 canonical provider session ID가 없음 |
| 완료 대기 | `paseo wait <id>` | idle은 프로세스 종료나 원래 AC 통과의 증거가 아님 |
| 중단 | `paseo stop <id>` | 실행 중이면 interrupt, idle이면 no-op. 정확한 종료 증거와 구분 |
| 사용량 | inspect의 `LastUsage` | 누락된 세부값을 0으로 표시하는 변환이 있어 원본 측정 여부·요청 귀속을 별도로 확인해야 함 |

확인 근거는 `run/ls/inspect/wait/stop --help`와 배포 패키지 `@getpaseo/cli/dist/commands/agent/`의 같은 이름 `.js` 파일이다. run 구현은 labels를 createAgent에 전달한다. inspect의 출력 변환은 provider·agent ID·cwd·상태를 노출하지만 provider 세션 ID를 노출하지 않는다. 이 사실을 서버 전체 API의 기능 부재로 확대하지 않는다. CLI에 사용자가 지정하는 생성 idempotency key는 확인되지 않았다.

## 다음 구현의 작은 경계

먼저 원본 provider 세션 ID와 turn 종료를 반환하는 공식 API 계약을 확보한다. 현재 환경에는 Paseo MCP 도구가 연결돼 있지 않으므로 설치돼 있다는 이유만으로 MCP 기능을 사용했다고 보고하지 않는다. CLI/SDK의 실제 지원과 대상 host·workspace를 대조한 다음 구현 범위를 확정한다. 다른 앱의 세션 파일을 탐색해 빈 계약을 메우지 않는다.

labels는 복구 후보를 찾는 수단으로만 설계한다. 제안 경계는 다음과 같으며 아직 구현·실증하지 않았다.

1. 비공개 예약에 host/workspace/request/stage/operation과 고유 label을 저장하고 최초 한 번만 제출한다. label에 개인 정보·프롬프트를 넣지 않는다.
2. 응답 유실 시 같은 label로 같은 host에서 조회한다. 0건이면 pending, 2건 이상이면 충돌로 남기고 재생성하지 않는다. 1건도 cwd/workspace/provider·생성 범위와 정확한 ID를 대조하기 전에는 연결하지 않는다.
3. 실제 canonical provider 세션 ID로 개발자·검증자·조율자가 서로 다른 세션인지 검사한다. agent ID만 다르다고 독립 검토로 인정하지 않는다.
4. 작업 turn의 실제 종료·결과와 프로세스 종료를 각각 기록한다. Paseo의 유지형 세션을 Orca의 종료 모델에 억지로 맞추지 않으며, idle/interrupt를 exited로 변환하지 않는다.
5. 사용량은 검증된 요청 구간만 수입한다. 계정 quota·누적/최근 사용량·누락값의 표시상 0을 배정별 측정값으로 복제하지 않는다.

## 수용 기준

구현 전에는 공식 응답 schema와 오류·응답 유실·조회 지연의 계약을 보관한다. 구현 검증에는 재시작 후 중복 제출 없음, 조회 0/1/복수건, host/workspace 불일치, 동일 provider 세션 거부, permission 대기, idle과 종료 구분, 원래 AC/snapshot 연결, 사용량 누락·replay를 포함한다. 실제 생성 실증은 등록된 별도 요청과 명시한 배정 한도를 사용한다. 이전 Orca 실증의 2배정 한도를 재사용하지 않는다.

[개발 컨트롤러](development-workflow.md) · [Paseo 환경 가이드](environments/paseo.md)

## 읽기 전용 SDK 관찰 bridge (0.16 계약)

`scripts/paseo_observation.mjs`는 의존성 없는 ESM 라이브러리다. CLI나 launcher가 아니며 컨트롤러의 Paseo adapter는 계속 `unavailable`이다. import는 연결·파일 쓰기·구독·타이머를 시작하지 않는다. 호출자가 이미 연결한 공식 public SDK client만 전달한다. 개발자·검증자의 검사는 합성 SDK로 수행했다. 이후 조율자가 기존 종료 시험 대화를 실제 SDK로 조회했으며 아래 부작용과 제한을 확인했다. 새 에이전트·프롬프트는 만들지 않았다.

### 호출 계약

- `await observeAgent(client, binding)`은 `client.agents.ref(binding.agentId)`의 `address.serverId/agentId`를 대조하고 `refresh()` → `timeline.refetch({direction:'after', cursor, projection:'canonical', limit})` → `refresh()`를 한 번씩 호출한다. 주소를 각 단계에서 다시 대조한다. 별도 transport, 자동 연결, 활성 세션 추정, pagination, polling, subscribe, 생성·전송·중단은 없다. SDK 자체 요청 timeout에 의존하며 라이브러리 자체 대기 루프는 없다.
- `binding`은 비어 있지 않은 `serverId`, `agentId`, `workspaceId`, `cwd`, `provider`, `providerSessionId`, `turnId`, `epoch` 문자열과 `{epoch,seq}` cursor를 요구한다. 두 epoch는 같아야 한다. 문자열은 최대 4096자이고 제어 문자를 허용하지 않는다. seq는 0 이상의 safe integer다. 선택적 `limit`은 정수 1–200이며 기본 200이다. 선택적 `forbiddenIdentities`는 최대 200개의 `{provider,providerSessionId}` 쌍이다. 바인딩한 쌍이 포함되면 거부한다. cwd·ID는 정확히 비교하며 경로 정규화나 추측을 하지 않는다.
- public SDK의 실제 반환형은 `refresh(): {agent,project}|null`, `timeline.refetch(): FetchAgentTimelineResponse.payload`다. 전체 wire message나 임의 completion 요약을 대신 받지 않는다. page의 `agent`도 반드시 요구한다.
- `normalizeObservation({binding, source:{serverId,agentId}, before, page, after})`는 같은 검증을 수행하는 순수 함수다. before/after는 `AgentSnapshotPayload`, page는 위 payload다. source는 호출자가 명시한 출처이며 암호학적 인증이나 독립적인 서버 증명이 아니다. wrapper는 public handle 주소에서 출처를 대조한다.

snapshot의 agent/workspace/cwd/provider와 runtimeInfo/persistence의 canonical provider session을 비교한다. 둘 중 하나라도 알려진 세션이 있어야 하며 알려진 값끼리 다르면 거부한다. 페이지 snapshot도 같은 바인딩이어야 한다. updatedAt의 관찰 순서가 역전되면 거부한다. pendingPermissions, activeTurn, status, backgroundWork, providerUnavailable, archivedAt, lastError와 attentionReason은 안정 상태 판단에 사용한다. backgroundWork 자체가 없으면 작업이 없다고 추정하지 않는다.

페이지는 같은 epoch, after 방향, canonical projection, null error, 명시적인 boolean flags를 요구한다. seq·window·start/end cursor·sourceSeqRanges·collapsed를 검사하며 cursor 바로 뒤부터 중복/역전/누락 없이 단일 canonical seq 행이어야 한다. 응답 수는 limit 이하로 제한한다. 이 보수적인 부분집합에 맞지 않는 응답을 임의 복구하지 않는다. 입력은 JSON 형태의 plain object/array로 제한하고 accessor, cycle, 비유한/unsafe 숫자, 깊이 24 초과, 노드 50,000 초과, 문자열 합계 1,048,576자 초과, 단일 문자열 262,144자 초과를 거부한다. SDK 객체나 함수는 정규화 입력으로 받지 않는다.

### 중요한 계약 차이: lifecycle은 canonical 행이 아니다

설치된 공식 SDK 0.16의 `FetchAgentTimelineResponseMessageSchema`는 `entries[].item: AgentTimelineItemPayloadSchema`를 반환한다. 이 union에는 user/assistant 메시지, reasoning, tool_call, todo, error, notification, compaction, plugin이 있다. `turn_started`, `turn_completed`, `turn_failed`, `turn_canceled`, permission 이벤트는 별도 `AgentStreamEventPayloadSchema`에 속하며 이 canonical page의 행 타입이 아니다. 따라서 이 bridge의 허용된 단발성 public 호출만으로 **정확한 turn의 시작→완료를 증명할 수 없다**. plugin data나 assistant 텍스트에 같은 이름이 있어도 lifecycle 증거로 승격하지 않는다. stream 이벤트를 canonical 행처럼 넣으면 invalid다.

이 계약 차이를 숨기는 합성 성공 경로는 구현하지 않았다. canonical 조회가 정상이어도 결과는 `pending/lifecycle_unavailable`이다. 향후 완료 판정에는 공식적으로 지원되는 별도 lifecycle 증거와 정확한 epoch/seq/turn/provider 연결 계약이 필요하다. canonical 경로는 그 수집을 구현하지 않는다. 별도 수정 계약의 유한 구독 관찰은 아래 절에서 구분한다. 실제 페이지에서도 lifecycle 부재가 확인되는지는 별도의 허용된 live capture로 확인해야 한다. 설치 schema 확인과 fake public SDK 테스트는 실제 연결 실증을 대신하지 않는다.

### 반환과 pending/invalid

반환은 `status`, 고정된 `reason`, `observed_turn_completed:false`, `task_acceptance:null`, `process_exit:null`, `usage:null`, `usage_complete:false`다. 유효한 관찰에는 whitelist한 identity 6개 필드와 요청 turnId/epoch/cursor만 추가된다. 프롬프트·응답·도구·오류 원문·환경·metadata·model·snapshot lastUsage는 반환하지 않는다. SDK 예외와 입력 오류는 고정된 `invalid/invalid_observation`으로 반환한다.

- `pending/incomplete_timeline`: reset/staleCursor/gap/hasNewer 또는 페이지가 window 끝에 도달하지 않음. 추가 페이지를 자동으로 읽지 않는다.
- `pending/unsettled_snapshot`: permission/active turn/background work/비 idle/오류 등 안정 상태 증거가 부족함.
- `pending/lifecycle_unavailable`: 위 검사를 통과했지만 canonical 계약에 요청 turn의 lifecycle 증거가 없음.
- `invalid/invalid_observation`: 누락·충돌한 신원, 잘못된 shape/숫자/epoch/cursor/seq/projection/error, 초과 입력 또는 SDK 호출 실패. raw 오류를 반영하지 않는다.

어느 반환도 원래 작업의 AC 통과, 프로세스 종료 또는 실제 stop을 뜻하지 않는다. 사용량은 정확한 `turn_completed`의 숫자 측정값만 수입할 수 있으나 현재 page에는 그 이벤트가 없으므로 항상 null이다. lastUsage·계정 quota·plugin payload를 복사하거나 누락을 0으로 바꾸지 않는다. 음수·비유한·boolean 값을 담은 가짜 completion도 거부한다. `usage_complete`는 항상 false다.

### 생성 경로와 검증 범위

공식 public SDK의 creation 경로는 서버 `creationLifecycle` 지원 시 key와 초기 prompt를 보존한다. 미지원 legacy 분기는 초기 prompt를 `initialPrompt`로 보내면서 key를 버린다. 따라서 key 기반 재시도 안전성을 이 legacy 경로에 주장할 수 없다. 이 사실은 설치 코드와 조율자의 mocked probe 근거이며 이 bridge는 creation을 구현하지 않는다. launcher, 예약 adapter, hook, DB, 전역 설정은 활성화하지 않았다.

자체 검증: `node --test tests/test_paseo_observation.cjs`, `node --check scripts/paseo_observation.mjs`, `node --check tests/test_paseo_observation.cjs`, 프로젝트 설정 검사, diff 검사. 테스트는 fake public SDK의 호출 순서·정확한 주소·실제 반환형, 신원 충돌, cursor/epoch/seq, truncation, permission/background/active 상태, schema 차이와 가짜 lifecycle/usage 거부, 민감 원문 제외와 import 무부작용을 확인한다. 완료 성공 경로는 공식 계약상 사용할 수 없으므로 통과했다고 보고하지 않는다.

## 별도 수정 계약: 유한 live window 관찰

원래 AC와 첫 구현·검토의 **failed 이력은 유지한다**. 위 `normalizeObservation`/`observeAgent`의 canonical 경로는 계속 pending이며 원래 AC2와 canonical 사용량 수입은 FAILED/UNAVAILABLE이다. 아래 추가 API는 명시적으로 수정한 별도 범위이며 원래 AC3의 구독 금지를 소급해서 통과했다고 주장하지 않는다. 컨트롤러 Paseo launcher는 여전히 unavailable이다.

`await observeTurnWindow(client, binding, {deadlineMs, maxCallbacks})`는 이미 연결된 public SDK client를 받는다. binding의 필드/제한/금지 세션 쌍은 위와 같다. epoch/cursor는 호출자 바인딩 형식으로만 유지하며 이 함수의 lifecycle 증거에는 사용하거나 반환하지 않는다. deadlineMs는 필수 정수 1–30,000ms, maxCallbacks는 필수 정수 1–200이다. callback은 첫 거부 이후 처리하지 않으며 raw payload를 배열 등에 보관하지 않는다.

public 호출 순서는 정확한 ref/address와 getConnectionState의 connected 확인 → before refresh → timeline.subscribe(callback) → subscription.ready → 같은 turn/provider의 명시적 started/complete 쌍 → after refresh → finally subscription.release다. SDK의 실제 subscription은 callable에 ready/release/subscriptionId를 붙인 형태이며 callable 자체를 unsubscribe로 호출하지 않고 release 메서드를 사용한다. ready 전에 도착한 이벤트도 실패로 처리한다. callback 인자는 wire message가 아닌 `{agentId, subscriptionId?, event, timestamp, seq?, epoch?}` payload이며 control callback은 event.type이 subscription_restored/error/replacement인 public SDK 변환형이다. observable subscriptionId가 바뀌거나 callback의 ID와 다르면 거부한다. SDK가 ID를 노출하지 않으면 null을 그대로 유지하며 없는 신원을 만들지 않는다.

하나의 전체 deadline을 성능 시계로 계산한다. 그중 min(1000ms, 전체의 20%)를 release 확인에 남겨두고 나머지 구간 안에 setup/readiness/pair/after read를 마친다. 각 async 단계와 release 기다림은 남은 deadline으로 제한한다. 제한 시간이 지난 경우에도 얻은 subscription에 release를 요청한다. 확인할 시간이 없거나 reject/hang이면 `pending/cleanup_uncertain`, cleanup_confirmed=false다. subscribe가 예외를 던져 handle을 돌려주지 않으면 내부 자원 여부를 알 수 없으므로 같은 uncertainty를 반환한다. timer는 finally로 지우며 caller 소유 client를 dispose/close하지 않는다. 제한 시간은 SDK의 동기 blocking 호출을 선점하거나 이미 보낸 read를 취소하는 기능이 아니다. 예산 초과 후에는 completion을 반환하지 않는다.

성공은 그 armed callback window 안에서 명시적 동일 turnId/provider의 start 다음 complete를 본 경우에 한정한다. missing ID, 역순/중복/다른 turn, session 재시작(thread_started), provider/session 충돌, permission, failure/cancel, attention/control/unknown 이벤트, callback 초과, 알려진 연결 단절, 주소 변경, 잘못된 shape, before/after의 불안정 상태는 성공할 수 없다. finally의 release를 기다리는 동안에도 거부 callback을 처리한다. callback에서 usage 숫자를 즉시 복사해 이후 원본 객체 변경에 영향을 받지 않는다.

수정 API의 반환은 최소 whitelist다. 성공 시 status=observed, reason=live_window_pair_observed, observed_turn_completed=true와 identity/turnId/subscriptionId를 반환한다. **source_continuity_verified=false 및 admissible_for_controller=false는 모든 반환에서 고정**이다. task_acceptance/process_exit=null, usage_complete=false다. usage는 정확한 completed event의 공식 여섯 필드만 받으며 finite nonnegative number를 요구하고 없는 필드는 null이다. boolean/string/null을 숫자로 바꾸지 않으며 lastUsage/quota/DB import는 없다. 실패 시 usage=null이고 관찰 완료는 false다. refusal은 pending/window_refused, 기타 SDK/timeout/검증 부족은 pending/window_unverified, release 확인 실패·불명은 pending/cleanup_uncertain이다. cleanup_confirmed는 release resolve까지 확인한 경우에만 true이며 원격 연속 delivery의 증명은 아니다. raw 오류·프롬프트·도구·환경·metadata는 반환하지 않는다.

`handle.address`는 SDK가 처음 받은 serverId를 cache한다. getConnectionState는 현재 샘플일 뿐 공개된 서버 attestation이나 reconnect generation이 아니다. before/after canonical session이 같아도 중간의 숨은 재접속·세션 전환을 배제할 수 없다. subscription_restored를 보면 거부하지만 모든 단절에 대해 완료 전에 반드시 그 이벤트가 온다고 보장하지 않는다. 따라서 고정된 source client는 호출자 신뢰 전제이며 연속성/서버 진위/암호학적 인증/지속 canonical 이력을 인증하지 않는다. lifecycle 이벤트의 optional turnId는 여기서 필수로 요구하지만 schema에 없는 seq/epoch/providerSessionId는 만들지 않는다. 이 약한 관찰 결과는 컨트롤러 완료나 원래 AC 합격에 사용할 수 없다.

입력 검증 수정(F2)은 required snapshot scalar/persistence/capabilities/modes와 runtimeInfo의 non-null object, canonical session 상호 일치, tool_call의 status/callId/name/detail/error 판별 필드를 검사한다. tool detail은 공식 shell/read/edit/write/search/fetch/sub_agent/plain_text/plan/unknown 부분집합만 지원하며 worktree_setup, agentMessage, icon이 있는 detail은 보수적으로 거부한다. 복잡한 optional extension 전체의 완전한 Zod 재구현을 주장하지 않는다. 검사한 실패·예외는 fixed 오류만 반환한다.

검증은 fake SDK에 한정한다. 원래 독립 리뷰의 세 malformed 입력을 회귀로 추가하고 실제 callback payload/callable subscription·ready/release, 관찰 쌍과 숫자/누락 사용량, setup/ready 오류, 모든 refusing 이벤트, callback budget, timeout 및 hanging first/after refresh/release, 이벤트 객체 변경, source/session 변경, cleanup을 확인한다. 실제 Paseo 연결·live subscription·새 agent/model 실행은 하지 않았다. 원래 실패 증거와 별도 수정 AC 결과는 비공개 보고서에서 구분한다.

## 실제 SDK 조회에서 확인한 부작용

조율자의 제한된 실제 조회에서 기존 지침 확인용 대화가 타임라인 조회 전 `closed`, 조회 후 `idle`로 관측됐다. 완료 이벤트 관측·구독·프롬프트 전송은 없었고 정규화 결과는 `invalid`였다. 따라서 이 시험은 완료 판정이나 상태를 바꾸지 않는 읽기 전용 실행의 통과 근거가 아니다. SDK 조회가 서버의 세션 로딩을 일으킬 수 있으므로 메서드 이름만으로 무부작용을 보장하지 않는다.

조율자는 `observeAgent`가 첫 snapshot에서 `closed`를 확인하면 타임라인 조회와 두 번째 refresh 전에 거부하도록 보완하고, 타임라인을 호출하지 않는 회귀 검사를 추가했다. 수정 전 독립 검토는 154개 집중 검사와 독립 helper 검사 20개에 해당하며, 이 후속 보완은 조율자 검증 범위다. 임의 비공개 RPC·archive·삭제로 이전 상태를 복구하지 않았다. 후속 실제 시험은 이미 활성인 명시 대상의 상태 영향과 공식 종료 경로를 먼저 확인해야 하며, 현재 자동 실행 adapter는 계속 미지원이다.

## 서버 구현으로 확인한 조회·종료 의미

설치 서버의 `session.js:handleFetchAgentTimelineRequest`는 `ensureAgentLoaded(..., purpose:'history')`를 호출한다. 이어지는 `agent-manager.js:resolveResumePurpose`는 보관된 대화가 아니고 작업 폴더가 존재하면 요청 목적을 `interactive`로 바꾼다. 이때 세션을 복원하고 기록을 로딩하므로 `closed → idle` 관측과 맞는 경로다. 이는 새 프롬프트 전송이나 작업 완료의 증거가 아니다. 코드 분석으로 해당 경로를 확인했으며 실제 실행 전체를 추적하거나 토큰을 측정한 결과로 확대하지 않는다.

같은 타임라인 handler는 요청의 projection과 관계없이 응답을 `projected`로 지정한다. SDK·프로토콜 schema에 `canonical`이 있다는 사실만으로 서버 지원을 판단할 수 없다. 현재 설치 서버에서는 이 라이브러리의 canonical 정규화 경로가 실제 응답을 거부하는 것이 예상 동작이며, `projected`를 `canonical`로 바꿔 통과시키지 않는다. 따라서 이 경로는 현재 버전의 완료 판정 연결로 사용할 수 없다. 별도 이벤트 관측 함수의 실제 적용도 여전히 미검증이다.

종료의 의미도 구분한다. CLI `stop`은 현재 실행의 interrupt이며, 서버의 `close_items_request`는 agent에 대해 archive 경로를 사용한다. SDK 공개 handle의 `archive()`도 같은 보관 경로다. 서버 내부에는 runtime만 닫는 `closeAgent`가 있지만, 이를 공개 CLI/SDK 기능으로 간주하거나 임의 RPC로 호출하지 않는다. 단순히 이전 `closed` 표시를 복원하기 위해 보관 상태를 바꾸지는 않는다.

후속 연결에서는 다음을 기준으로 삼는다.

- 상태 확인에는 명시한 agent의 snapshot 경로를 우선하며, 기록 로딩을 완료 감시용 조회와 분리한다. 조회 경로별 상태 영향을 확인한다.
- 과거 기록을 자동으로 열어 감시 대상 세션을 깨우지 않는다. 이미 활성인 명시 대상의 이벤트 관측과 실제 작업 결과를 연결하는 방식부터 검증한다.
- `idle`, 턴 완료, 작업 AC 통과, runtime 종료, archive를 서로 대체하지 않는다.
- 서버가 제공하지 않는 projection·종료 증거는 미지원으로 남긴다.

설치된 실제 함수 본문에 메모리 의존성 stub을 주입한 검사 5개에서 폴더 존재/부재·archive 상태별 목적 선택과 canonical 요청의 projected 응답을 재현했다. 이 검사는 daemon·모델·agent를 실행하지 않았고 실제 SDK 재실증을 대신하지 않는다. 공개 문서에는 개인 식별자·경로·원문 로그를 포함하지 않는다.

## 상태 조회와 이벤트 관측 분리

`await observeStatus(client, identityBinding)`은 이미 연결된 public SDK에서 정확한 agent의 snapshot만 한 번 조회한다. identityBinding은 `serverId`, `agentId`, `workspaceId`, `cwd`, `provider`, `providerSessionId`이며 선택적 forbiddenIdentities 규칙은 동일하다. turnId·epoch·cursor를 요구하지 않고 timeline API·구독·생성·전송을 호출하지 않는다. 연결 상태와 주소를 조회 전후 대조하고, 반환형·canonical provider 세션을 확인한다. SDK 단발 요청 timeout에 의존한다.

반환에는 최소 identity와 `agent_status`, 실제 `active_turn_id`(없으면 null), 승인 대기 개수, background 작업 개수(알 수 없으면 null)만 추가한다. 상태 문자열은 설치 protocol의 다섯 lifecycle 값으로 제한한다. `closed`나 `idle`을 관측해도 observed_turn_completed는 false, task_acceptance/process_exit/usage는 null이고 source_continuity_verified/admissible_for_controller/usage_complete는 false다. 상태 확인과 완료 승인·사용량 수입을 구분한다.

`observeTurnWindow`는 identityBinding에 **이미 공식 근거로 확인한 정확한 turnId**만 추가하면 된다. epoch/cursor 없이 사용할 수 있으며 이 좌표를 얻으려고 과거 기록을 열 필요가 없다. 이전 호출자의 epoch/cursor가 있으면 기존 형식 검증은 유지하되 이벤트 lifecycle 증거로 쓰지 않는다. 아직 알려지지 않은 미래 turnId를 추정하지 않으며, 실제 요청과 turnId를 연결하는 실행 adapter는 후속 작업이다. `observeAgent`는 종전 canonical 기록 조회용이며 현재 설치 서버의 projection 차이를 우회하지 않는다.

조율자 검증: 집중 Node 170개 통과. 실제 public SDK에서 기존 지침 확인용 agent 한 개를 snapshot-only client로 조회해 `closed → closed`, history 호출 0·구독 0·새 agent 0·프롬프트 0을 확인했다. 이는 해당 상태 조회 경로의 제한된 실제 검증이며 이벤트 완료 관측이나 자동 실행의 성공을 뜻하지 않는다. 이 변경의 170개 검사와 실제 조회는 조율자 자체 검증이고, 이전 154개 버전에 대한 독립 재검토와 구분한다.

## 요청 메시지와 실제 턴 연결

`await observeRequestWindow(client, requestBinding, options)`는 identityBinding에 `requestId`와 **전송에 사용한 공식 메시지 ID인 `messageId`**를 추가해 사용한다. 미래 turnId를 미리 넣지 않는다. 이 API에는 turnId·epoch·cursor를 넣을 수 없다. 제한 시간·이벤트 수·준비 완료·정확한 출처 확인·finally release 규칙은 observeTurnWindow와 같다. 기록을 조회하거나 메시지를 전송하지 않는다.

설치 SDK의 `sendAgentMessage`는 `options.messageId`를 전송하며, 서버는 이를 처리한 메시지의 `clientMessageId`로 연결한다. 서버는 수용한 턴의 `turn_started` 뒤에 실제 turnId와 clientMessageId를 가진 사용자 메시지를 발행한다. 관측기는 다음 세 증거가 모두 같은 창에서 확인될 때만 연결한다.

1. 공식 `turn_started`에서 실제 턴 ID를 후보로 확보한다. 이 단계만으로 요청을 귀속하지 않는다.
2. 같은 턴의 `timeline/user_message`에 정확한 clientMessageId가 있어야 requestId → messageId → turnId를 연결한다. 프롬프트 내용·별도의 provider messageId가 같다는 이유로 연결하지 않는다.
3. 같은 실제 턴의 `turn_completed`와 정리 완료까지 확인한다. 연결 전 완료, 다른 턴/메시지, 중복 사용자 메시지나 추가 입력은 거부한다.

관측된 결과에는 requestId/messageId/실제 turnId와 request_matched=true가 추가된다. 그러나 서버는 여러 입력을 한 턴으로 묶을 수 있으므로 **요청이 해당 턴에 연결됨과 그 턴을 독점함은 다르다**. request_exclusive와 usage_attributed_to_request는 항상 false다. usage가 있으면 관측한 전체 턴의 참고값이며 요청별 사용량으로 자동 수입하지 않는다. source_continuity_verified/admissible_for_controller/usage_complete도 false를 유지한다.

이 함수는 전송을 소유하거나 전송자에게 준비 승인을 반환하는 자동 실행 진입점이 아니다. 전송과 관측 준비 순서·응답 유실의 안전한 재조정·재시작 후 기록은 실행 adapter에서 별도로 연결해야 한다. 관측에 실패했다고 메시지를 재전송하지 않는다. 메시지 ID가 있다는 사실을 생성·전송 idempotency의 보장으로 해석하지 않는다. SDK 모델 호출이나 새 실제 업무 전송은 이번 변경에서 수행하지 않았다.

조율자 자체 검사 189개 통과: 기존 170개와 요청 연결 성공/다른 메시지·턴/누락·중복/추가 입력/미리 추정한 turn 거부/관측 중 입력 변경 19개를 포함한다. 이는 합성 이벤트의 연결 검증이며 실제 요청 전송→완료 실증이나 이전 버전의 독립 재검토 범위를 확대하지 않는다.
