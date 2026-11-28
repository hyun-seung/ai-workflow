# Orca·Codex의 완료 시 토큰 수집 연결

조율자가 등록한 배정만 대상으로 Codex의 `Stop` 이벤트에서 기존 [수집기](native-record.md)와 [SQLite 연결기](trace-usage.md)를 호출한다. 수집기는 원본의 닫힌 native 턴·생성된 Orca task/dispatch 머리말·측정 증가분을 다시 확인한다. 모델의 자기 보고 토큰이나 터미널의 `done` 표시를 합계로 사용하지 않는다.

## 지원 계약과 활성화 상태

2026-11-12 확인: Orca 1.4.256의 공식 플러그인 `agent.status.changed`는 터미널 상태 이벤트이며 structured worker의 확정 완료·원본 사용량 경로를 제공하지 않는다. 이를 전역 완료 hook으로 대체하지 않는다. Codex CLI 0.167.0 설치를 확인했고, [OpenAI Docs의 hook 계약](https://learn.chatgpt.com/docs/hooks)은 `Stop`에 session_id·turn_id·cwd·transcript_path를 제공한다. 원본 형식은 안정 API가 아니므로 기존 파서와 지원 출처 검사는 유지한다. [Orca host API](https://github.com/stablyai/orca/blob/main/docs/reference/plugin-host-api.md), [Orca hook 가이드](https://orca.dev/docs/agents/hooks-memory)도 함께 확인한다.

등록된 로컬 Orca structured Codex 작업 한 건에서 프로젝트 hook 설치·공식 UI의 정확한 정의 신뢰·실제 Stop 발생·자동 SQLite 수입을 연결해 확인했다. 조율자와 worker는 수집기를 수동 호출하지 않았고, hook이 받은 원본 경로의 세션/배정/턴 ID·snapshot 해시·각 카운터 증가분을 DB 값과 대조했다. 이는 한 환경·한 실제 배정의 검증이다.

코드는 명시 config 기반의 opt-in 연결이다. 파일을 배포하거나 문서를 읽었다는 이유만으로 활성화되지 않는다. **프로젝트의 hook 등록·신뢰와 실제 Orca structured worker 이벤트 발생·자동 저장을 별도로 확인해야 한다.** 이 확인 전에는 운영 자동 수집이 검증됐다고 보고하지 않는다. 다른 공급자·원격/Windows·조율자 일반 턴·자동 승인 검토 사용량은 이번 범위에 포함하지 않는다.

## 프로젝트별 준비

1. 대상 요청과 actor를 기존 비공개 SQLite에 등록한다. DB·관리 ledger는 이미 존재해야 한다.
2. [가상 설정](../templates/completion-hook.config.json)을 복사해 프로젝트·같은 조율자의 ledger 파일·DB·별도 비공개 출력 폴더·조율자 ID를 정한다. 설정과 결과는 프로젝트/Git 밖의 비공개 경로에 둔다. 다른 프로젝트의 경로로 자동 대체하지 않는다.
3. 배정 시 [관리 실행기](delegation.md)에 아래 세 값을 함께 전달한다. 누락·잘못된 역할·다른 공급자는 native 호출 전에 거부한다. 기존 배정의 계획은 바꾸지 않으며 새 operation부터 적용한다.

```text
--usage-role backend
--usage-actor-id backend_example
--usage-task-title '서버 구현'
```

이는 token 수치를 입력하는 옵션이 아니다. 완료 시 측정 구간을 연결할 요청·역할·기존 actor·작업명이며 실제 task/dispatch는 native 영수증으로 확인한 ledger 값을 사용한다. opt-in이 없는 배정과 다른 프로젝트의 hook은 수입하지 않는다. 모든 작업이 자동 수집된다는 뜻은 아니다.

4. 검토용 hook 정의를 생성한다. 출력에는 실제 비공개 경로가 포함되므로 공개하지 않는다.

```sh
python3 scripts/completion_hook.py --config /private/path/completion-hook.json --print-hook
```

`--print-hook`은 정의만 출력한다. `.codex/hooks.json`을 설치하거나 신뢰·권한 설정을 바꾸지 않는다. 생성된 정의는 `Stop` command handler 하나로, `async: true`, `timeout: 30`이다. 정확한 Python 실행 파일·수집기·config의 절대 경로를 shell quoting해 실행하며 입력 JSON에서 명령을 조립하지 않는다.

5. 기존 프로젝트 `.codex/hooks.json`과 충돌 여부를 확인하고 해당 handler만 추가한다. 사용자/Orca가 관리하는 전역 hook은 덮어쓰지 않는다. Codex는 여러 출처의 matching hook을 함께 실행한다. 프로젝트 `.codex/` 계층과 새 hook 정의를 공식 UI에서 검토·신뢰한다. `/hooks` 또는 Codex가 제공하는 hook 검토 화면을 사용하며, 해당 화면이 Orca structured chat에서 제공되는지는 실제 환경에서 확인한다. hook 신뢰 우회 플래그·managed 정책 가장·전역 승인 완화는 사용하지 않는다. 새 정의는 신뢰 전까지 실행되지 않을 수 있다.

## 이벤트 처리

- stdin은 최대 1 MiB JSON이며 중첩 깊이는 64단계로 제한한다. ledger·영수증의 컨테이너 형식도 검사하고 JSON 파서의 과도한 중첩 오류를 경고 경로로 처리한다. `Stop`·정확한 ID·절대 경로·stop 상태 형식과 유한 숫자를 검사한다. 마지막 응답·권한·모델 등의 원문 필드는 보관하지 않는다.
- hook이 제공한 **한 원본 파일**만 읽으며 세션 폴더를 탐색하지 않는다. session_meta의 Orca 출처·session ID·cwd를 대조하고 task/dispatch에 맞는 opt-in ledger operation을 찾는다. ledger 조율자·프로젝트·Codex 공급자·수입 역할을 확인한다.
- 비동기 hook이 native `task_complete` 기록보다 먼저 도착할 수 있다. 등록 영수증과 원본 flush를 각각 최대 5회, 0.5초 간격으로만 확인한다. 별도 서버·상시 감시·추가 모델 호출·Orca inbox polling은 없다. 맞는 배정이 없으면 ignored, 등록된 턴이 닫히지 않거나 측정 근거가 없으면 pending이며 DB에 0 토큰을 넣지 않는다.
- session/turn별 잠금과 입력 바인딩을 사용한다. 배정·계획은 pending 단계부터 고정한다. DB 수입 전에 event/config·operation identity/plan·snapshot/hash를 담은 복구 가능한 intent를 먼저 영수증에 보관하고, snapshot 파일과 기존 `import_usage`를 순서대로 처리한다. 원본 snapshot 파일을 쓰기 전 중단돼도 intent에서 복원하며 해시·계획이 바뀌면 거부한다. 저장 뒤 영수증을 남기며 파일/DB 중간 실패는 같은 입력으로 재시도한다. 성공 replay에서도 snapshot 해시와 operation 바인딩을 확인하고 멱등 수입한다.
- stdout은 JSON만 반환하고 작업 계속/중단/권한 결정 필드는 출력하지 않는다. 실패 시 경고를 반환하며 작업을 재실행하거나 자동 승인하지 않는다. pending/실패는 미수집으로 보고한다. DB 충돌·잘못된 출처는 재해석해서 성공으로 바꾸지 않는다.

직접 재시도할 때도 보관한 동일 Stop payload를 stdin으로 전달한다. 다른 세션·경로·설정·run·계획으로 기존 영수증을 덮어쓰지 않는다. intent 없이 남은 고아 metadata 파일은 자동 채택하지 않고 명시 점검을 요구한다. 원본이 없거나 형식이 바뀐 경우 명시 점검이 필요하다. source 또는 ledger가 바뀐 상황을 자동 복구한다고 가정하지 않는다.

## 토큰 완료와 업무 완료의 구분

`Stop`은 프로세스 종료나 기능 AC 통과를 뜻하지 않는다. 이 경로의 `worker_exit_verified=false`, `task_acceptance_evaluated=false`를 유지한다. [기존 완료 처리기](completion.md)의 정확한 worker 종료 확인·프로필 확인·수용/보관·기본 Y 정리 정책은 별도로 적용한다. 이미 수집한 물리 구간은 같은 수치를 다시 수입해도 요청 합계에 중복으로 더하지 않는다.

Codex는 세션 종료 때 미완료 background hook을 취소할 수 있다. 갑작스러운 종료·신뢰 미승인·출처 누락·짧은 배정의 영수증 race·다중 작업/후속 턴의 모호한 귀속까지 완전 수집한다고 보장하지 않는다. 마감 시 해당 배정의 영수증·측정 이벤트 유무를 확인하고 기존 명시 수집 경로로 보완한 사실을 구분해 보고한다.

## 기록 범위와 개인정보

hook 정의에는 실행 명령·비공개 config 경로·비동기/제한 시간이 들어 있다. config에는 프로젝트·ledger·DB·결과 경로와 조율자 ID가 들어 있다. 저장된 측정/근거에는 작업명·세션/배정 식별자·원본 파일 경로·해시와 정규화한 실행 프로필도 포함된다. 경로에 사용자명이 들어갈 수 있으므로 개인정보가 전혀 없는 자료라고 표현하지 않는다. 모두 비공개 기록 정책으로 보관하며 실제 hook 등록 파일은 개인 절대 경로가 있어 Git에서 제외한다.

현재 수집 스키마는 입력·출력·캐시 입력·추론 출력·총 토큰 다섯 항목이다. 원본이 추가 카운터(예: `cache_write_input_tokens`)를 제공하더라도 자동으로 기존 필드에 섞지 않는다. 미지원 항목과 도구별 읽기 토큰 등 미측정 세부값은 별도 범위다. 캐시 입력은 입력의 부분값, 추론 출력은 출력의 부분값이며 총합에 재가산하지 않는다. 실제 검증 측정값과 원본 경로는 비공개 보고서에 남기고 공개 예시의 값으로 재사용하지 않는다.

등록된 배정별 수집 상태 점검과 한 건 재처리는 [복구 도구](completion-recovery.md)를 사용한다. replay도 현재 원본의 측정 구간·baseline/증가분·context·배정을 보존 snapshot과 대조한다. 변경된 카운터나 재검증할 수 없는 원본을 오래된 측정값으로 재수입하지 않으며, 유효한 후속 배정 추가는 허용한다. 영수증 확인은 전체 턴 수집 완료를 뜻하지 않는다.

## 영수증 이전의 호출 관찰

업데이트된 스크립트의 **앞으로 발생하는 정상 hook CLI 호출**은 기존 v1 config를 모두 검증한 뒤, stdin 읽기·해석과 원본·등록 검사 전에 비공개 `directory/invocations/events-v2.jsonl`에 진입을 추가한다. 기존 `observations.json`은 읽기 전용으로 보존한다. 새 config 필드나 설치·신뢰 변경은 필요하지 않으며 생성되는 hook 정의·command·async·timeout은 그대로다. import, `--help`, `--print-hook`, 직접 `collect()` API 및 복구 도구의 재처리는 호출 관찰을 만들지 않는다. config 검사는 디렉터리를 생성하지 않는다.

관찰은 `entry → payload → source → registration → collection`의 각 첫 진입과 마지막 outcome을 기록한다. 기존의 유한 재시도는 반복 단계 기록을 추가하지 않고, 오류 outcome에는 마지막으로 시도한 단계를 남긴다. outcome은 `started/collected/ignored/pending/error`, category는 `none/input/source/registration/collection/internal`이다. reason은 코드에 열거된 고정 집합만 허용하며 알 수 없는 오류는 `unknown`이다. JSON 해석·깊이·크기, 원본 identity/크기, 등록 ledger 오류 등 영수증 이전 실패를 구분한다. 마지막 `started`만 있으면 `incomplete`이며 현재 실행 중인지, 중단됐는지, 관찰 저장이 실패했는지는 확정하지 않는다. `pending`은 호출 처리는 돌아왔지만 측정 수집은 미완료라는 뜻이다.

`source` 단계에서는 `FileNotFoundError/NotADirectoryError`를 `source_unavailable`, `PermissionError`를 `source_access_denied`, `IsADirectoryError` 또는 정확한 `session_not_regular_file`을 `source_not_regular`로 분류한다. 기존 파서의 고정 코드 `unsupported_assignment_header/ambiguous_assignment_header`는 `source_assignment_rejected`, `multiple_session_meta/duplicate_json_key/nonfinite_json`은 `source_parser_rejected`다. 예외의 경로·메시지 일부를 추출하지 않으며 동적 줄 번호가 붙은 코드와 미등록 오류는 `unknown`으로 남긴다. 이 제한된 분류는 새 관찰에만 적용하고 원본 파싱·귀속·사용량 계산이나 과거 `unknown`의 원인을 바꾸지 않는다.

공개 inspector 출력은 version 1과 기존 호출/기록 목록을 유지한다. 새 내부 저장 형식은 한 줄에 `version=2`, 무작위 호출 `id`, 호출 내 `index`(0–5), 기존 `record`, 앞의 네 필드에 대한 `sha256`만 넣는다. record는 phase/outcome/category/reason이며 이벤트 검증 뒤 session/turn 쌍의 SHA-256 `event_key`만 추가할 수 있다. stdin·응답·작업명·환경 값·원본/오류 경로·임의 예외 메시지·사용량은 기록하거나 진단 출력하지 않는다. 원본은 기존 수집기만 읽으며 크기·identity·닫힌 턴·귀속 검사는 유지한다. 관찰의 `collected`는 수집기의 반환값을 기록한 **로컬 주장**이며 genuine Stop, 영수증 검증, DB 사용량, worker 종료, 업무 수용이나 진위의 증명이 아니다.

두 저장 형식을 합친 한도는 config별 호출 128개, 호출당 기록 6개, record JSON 512바이트다. v2 한 줄은 마지막 개행을 포함해 최대 1 KiB, 최대 768줄/768 KiB다. 읽기 전용 v1은 기존 512 KiB 한도를 유지하므로 공존 시 데이터 파일은 합계 최대 1,280 KiB(1.25 MiB)다. 정상 파일은 v1(있을 때), v2, 빈 `writer.lock`의 최대 세 개이며 새 임시 파일은 만들지 않는다. 디렉터리는 최대 네 항목만 열거해 초과·예상 밖 파일을 거부한다. 각 파일 읽기는 한도+1바이트 이내이고, 크기·소유권·일반 파일 여부·hardlink 수를 먼저 검사한다.

경로는 최대 256개 구성요소를 `O_NOFOLLOW`로 열고, 생성도 열린 부모에 대한 상대 `mkdir`만 사용한다. 새 디렉터리/파일은 0700/0600이다. 기록자는 디렉터리 inode·그 안의 lock 파일·열린 v2 파일을 비대기로 잠근다. v2 파일은 `O_APPEND`와 no-follow로 한 번 열어 그 **동일 descriptor로 기존 내용을 읽고 검증한 뒤** 최대 한 줄을 한 번 추가한다. 새 파일은 배타 생성하며, 최종 검증 뒤 다른 pathname을 다시 열거나 임시 파일을 승격하지 않는다. replace/rename/unlink/truncate는 기록·오류 정리에 사용하지 않는다. 정상 append는 앞의 모든 바이트를 유지하며 파일과 디렉터리를 fsync한다. capacity에 도달하면 새 호출 관찰만 생략하고, 이미 진입한 v2 호출의 남은 단계는 한도 안에서 추가할 수 있다.

원자성은 **완전한 기록을 해석하는 규칙**으로 한정한다. 마지막 개행·엄격한 JSON 스키마·체크섬·연속 순번·유효한 단계 상태가 모두 맞는 줄만 완전한 기록이다. OS 쓰기가 찢어지지 않는다고 보장하지 않는다. 짧은 쓰기는 재시도하지 않고, 부분 꼬리·체크섬 불일치·읽는 동안 변경된 파일은 `attention_required`로 처리한다. 이전의 유효한 바이트는 그대로 남지만 잘못된 저장소를 잘라서 성공 목록으로 반환하지 않는다. 자동 절단·수리·재수입은 없다. 체크섬은 인증 수단이 아니며 임의의 동일 소유자 변조를 인증하거나 방지한다고 주장하지 않는다.

v1은 자동 이동·재작성·삭제하지 않고 읽기만 한다. v1만 있는 경우에도 기존 출력 의미를 유지하며, v1/v2에 같은 호출 ID가 있거나 v2에 중복·충돌 순번이 있으면 합쳐 채택하지 않는다. 이전 v1의 incomplete를 v2에서 소급 완성하지 않는다. 과거 임시 잔여물·예상 밖 파일·잘못된 권한·손상·한도·잠금/IO 오류에는 추가 관찰을 중단하고 저장된 바이트를 보존한다. 이 실패는 수집 결과·stdout·종료 코드나 사용자 턴 제어를 바꾸지 않는다. 빈 v2 파일만 있으면 관찰은 `unknown`이다. 각 IO의 수와 크기는 유한하지만 OS 파일시스템의 응답 시간까지 보장하지는 않는다. 기존 30초 hook 제한은 유지한다.

읽기 전용 점검:

```sh
python3 scripts/completion_diagnostics.py --config /private/path/completion-hook.json inspect
```

config의 기존 검증(기존 DB의 읽기 전용 열기 포함) 뒤 검증된 diagnostic destination의 v1/v2 기록만 읽는다. 점검은 파일을 만들지 않고 비대기 읽기 잠금을 사용하며, 기록자가 사용 중이면 주의 상태일 수 있다. 영수증·ledger 내용·transcript는 읽거나 바꾸지 않으며 DB 수입·재처리·연결 재귀속도 하지 않는다. 출력의 `observed`는 유효한 최종 관찰이 있다는 뜻이다. `attention_required`는 손상·미완료·pending/error, `unknown`은 관찰 부재다. CLI 종료는 각각 정상 최종 관찰 0, 미완료/부재/주의 1, config 오류 2이며 수집 성공을 보증하지 않는다. `capacity_reached`도 확인한다. 모든 출력에서 `root_cause=undetermined`와 영수증/사용량/worker 종료/업무 수용 검증 false를 유지한다.

관찰 부재는 미호출의 증거가 아니다. 프로세스 시작·Python/import/인자 처리 실패, 유효하지 않거나 읽을 수 없는 config, 관찰 저장 전 중단·IO 실패·잠금 충돌·capacity는 기록되지 않을 수 있다. config 검증 실패에는 fallback 경로나 환경 값으로 저장 위치를 고르지 않는다. 이전 missing 및 미귀속 영수증을 소급 기록하거나 이번 배정에 연결하지 않으며 과거 원인은 `undetermined`로 남긴다.

`test_completion_diagnostics.py`는 TemporaryDirectory의 일반 합성 입력과 CLI로 기존 바이트 보존·완전한 이벤트 재구성·부분 프레임 거부·v1/v2 공존·중복/한도/권한/스키마·개인정보·읽기 전용·수집 호환성을 검사한다. 더 이상 쓰지 않는 임시 파일/replace 경합 시험과 과거 실패·통과 증거는 별도 기록에 보존하며 이번 저장 방식의 검증으로 재실행하지 않는다. 정적 검사로 정상 기록 경로에 replace/rename/unlink/truncate가 없음을 확인한다. 합성 테스트는 OS의 실제 torn write나 런타임 Stop 전달 성공 증명이 아니다.
