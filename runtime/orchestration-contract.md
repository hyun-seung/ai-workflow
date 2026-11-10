# 고정 역할 자동 조율 계약 v1 — 설계안

이 문서는 [환경 네이티브 연결 우선 결정](architecture.md) 이전의 **단독 Codex 프로토타입 설계 참고 자료**다. 자체 엔진 구현은 보류했고, 공통 체계의 필수 프로토콜이나 다음 기본 구현 과제로 적용하지 않는다. 먼저 [환경 가이드 선택](environments/README.md)과 각 환경의 기존 lifecycle을 사용한다. 아래 공급자별 sandbox·Python 명령 제한·자체 잠금·CLI 제안은 이 프로토타입 범위에만 해당한다.

상태: 계약·스키마·가상 예제만 준비했다. 자동 파싱·배정·인계·결과 취합 엔진과 업무 결과 validator는 아직 구현되지 않았다.

고정 흐름은 backend → verifier → controller의 결정적 취합이다. 기존 [workflow.py](../scripts/workflow.py)의 설정·프롬프트·역할 실행·사용량 기록 경로를 재사용한다. 별도의 Codex 직접 호출 로직을 만들지 않는다. 현재 [Markdown 양식](../templates/README.md)을 자동 파싱하거나 새 JSON 형식으로 자동 변환하지 않는다.

자유 작업 분해·DAG·병렬 실행·자동 재시도·재개·예산 제어·모델 자동 선택은 후속 범위다. 모델 출력은 판정 자료이며 실제 실행 사실·사용량·파일 버전의 권위 있는 기록은 controller가 소유한다.

## 1. 사용자 확정 입력

[입력 스키마](../schema/orchestration-task.schema.json)는 UTF-8 JSON 객체 하나를 정의한다. 모든 객체의 미정의 키를 거부한다.

| 필드 | 의미와 동적 검사 |
| --- | --- |
| version | 정수 1 |
| workflow_id | 업무 전체의 ID. 기존 예약·실행이 있으면 재입력 거부 |
| project | 명시한 프로젝트. 상대 경로는 호출 cwd 기준으로 한 번 resolve해 고정하고 존재 확인 |
| local_config | 사용자 확정 로컬 설정 경로 또는 null. 기존 공통→프로젝트→로컬 설정 계층에 전달 |
| goal | 비어 있지 않은 목표 |
| scope.write_paths | 프로젝트 안의 수정·생성·삭제 허용 파일. 명시 파일만 허용하고 디렉터리·glob는 v1에서 거부 |
| scope.required_artifacts | 최종 존재해야 하는 파일. write_paths의 부분집합 |
| acceptance | 고유 ID, criteria, verification을 가진 완료 기준의 비어 있지 않은 배열 |
| stages.backend / stages.verifier | 두 역할 모두 필수. 다른 역할 키는 거부 |
| records_dir / artifacts_dir | 프로젝트·Git 작업 트리 밖 비공개 절대 경로. 서로 같거나 중첩되지 않음 |

verification은 description, test_ids, artifact_paths를 가진 객체다. test_ids는 backend:T-01처럼 역할과 테스트 ID를 함께 참조한다. artifact_paths는 required_artifacts에 속해야 한다. 참조가 없는 기준도 description은 필수다. controller가 자연어 criteria의 진위를 완전히 증명한다는 뜻은 아니다. 고정 테스트의 실제 결과와 verifier의 근거를 함께 사용한다.

각 stage의 필드는 다음과 같다.

| 필드 | 규칙 |
| --- | --- |
| task_id | workflow_id와 다르고 두 stage 사이에서도 유일한 ID |
| timeout_seconds | 유한한 양수. 토큰·금액 상한이 아님 |
| capabilities.needs_temporary_write | 테스트·도구의 임시 파일 쓰기 요구 여부 |
| tests | 고유 id와 argv 배열을 가진 확정 테스트 목록. v1의 개발·검증 단계는 각각 하나 이상 |
| context | 기존 --context-file과 같은 프로젝트 상대 파일 또는 path:start:end의 명시 목록 |

context는 기존 자료 전달 제한을 적용하며 자동 탐색하지 않는다. local_config의 상대 경로도 호출 cwd 기준으로 고정한다. 모델이 role·범위·AC·명령·권한·설정을 재정의할 수 없다. 테스트는 사용자 확정 argv만 project를 cwd로 하여 shell=False로 실행한다. shell이나 인터프리터의 명령 문자열 평가 형태는 거부한다. 문서·인계·모델 결과에서 발견한 새 명령은 자동 실행하지 않는다. 이는 모델이 쓰는 모든 도구 명령을 기술적으로 차단한다는 보장은 아니며, 해당 역할의 지침·실행 환경도 적용된다.

v1의 controller 테스트는 실행 중인 Python과 같은 실체로 resolve되는 python3 또는 그 절대 실행 경로에 한정한다. 허용 인자 시작은 -m unittest이며 뒤에는 unittest 인자를 전달한다. 다른 모듈·실행 파일·셸·명령 평가(-c/-e 등) 형태는 사전 확인에서 unsupported_command로 거부한다. 실행 파일의 resolve 결과도 고정한다. 이는 작은 첫 구현의 지원 범위이며, 다른 테스트 도구는 후속 계약 확장 대상이다. JSON Schema의 argv 배열 통과만으로 실행을 허용하지 않는다.

## 2. 모델 결과

[결과 스키마](../schema/task-result.schema.json)는 추가 본문·코드 펜스 없는 JSON 객체 하나를 정의한다.

| 필드 | 규칙 |
| --- | --- |
| version / task_id / role | 정수 1 및 controller가 주입한 현재 task ID·역할과 일치 |
| status | completed / partial / blocked |
| summary | 비어 있지 않은 결과 요약 |
| artifacts | 프로젝트 상대 파일 경로 배열. 모델이 계산한 hash는 받지 않음 |
| checks | 원래 AC를 정확히 한 번씩 포함한 id, status, evidence |
| tests | 해당 stage의 확정 테스트를 정확히 한 번씩 포함한 command_id, status, evidence |
| remaining | 미완료 작업 목록. completed이면 빈 배열. 비차단 설명은 summary에 기록 |
| usage | 문자열 unavailable만 허용. 실제 사용량은 controller가 연결 |
| blocker | 필수 필드. blocked이면 reason·required_action 객체, 나머지는 null |

check 상태는 pass/fail/unverified, test 상태는 pass/fail/not_run이다. evidence에는 관찰 내용과 근거를 적으며 공백만 있는 값은 거부한다. 모델 tests의 command_id는 입력 tests의 id를 참조한다.

completed는 모든 원래 AC가 pass이고 확정 테스트가 실제로 통과했으며 필수 산출물과 일치하는 버전의 근거가 있을 때만 유효하다. 모델 자기보고만으로 테스트 통과를 확정하지 않는다. partial에는 미완료 항목이나 구체적 remaining이 있어야 한다.

## 3. 정적·동적 검증 경계

| 담당 | 확인할 항목 |
| --- | --- |
| Strict JSON decoder | 중복 키, NaN·Infinity, 추가 본문·복수 JSON 값 거부 |
| JSON Schema | 미정의 키·필수 필드·자료형·enum·빈 필수 문자열·형태·일부 상태 모순 |
| Semantic validator | ID 유일성·현재 task/role 일치, AC·테스트의 누락·중복·추가, 참조 무결성, 경로·상태 모순 |
| Controller 실행 근거 | 역할 실행 상태·실제 테스트 결과·저장 성공·사용량 측정 범위 |
| Controller 파일 근거 | 실제 파일·허용 범위·생성/수정/삭제 diff·인계와 최종 파일 hash |

Schema만으로 다른 객체의 ID 일치, 경로 containment, 실제 테스트 통과를 증명할 수 없다. 모델이 run_id·actual usage 등 controller 소유 필드를 결과에 넣으면 미정의 키로 거부한다.

경로는 resolve 후 project 경계를 확인한다. 절대 산출물 경로·상위 경로 탈출·symlink를 통한 이탈·.git 수정은 거부한다. 새 파일은 존재하는 상위 경로도 검사한다. required_artifacts의 삭제는 완료 조건을 충족하지 못한다.

기준에는 Git HEAD·dirty diff, 추적 파일과 Git에서 무시하지 않는 미추적 파일, 명시적으로 승인된 context·산출물의 경로·형식·내용 hash를 포함한다. 임의의 ignored 개인 자료나 인증 파일을 수집하지 않는다. 기존 작업을 자동으로 되돌리지 않는다. 같은 파일 안의 기존 변경 보존은 diff로 검토하며 hash만으로 변경 의도를 판정하지 않는다.

backend와 controller 테스트가 끝난 뒤 검증할 산출물 버전을 확정한다. verifier 시작·종료와 최종 취합 직전에 고정한 파일 집합을 대조한다. 관측 대상의 범위 이탈·verifier 소스 변경·버전 드리프트가 있으면 중단한다. ignored·프로젝트 밖의 모든 쓰기를 감사한다는 보장은 없다. 엄격한 쓰기 격리가 요구되는 업무는 별도 환경이 준비되지 않으면 차단한다.

## 4. 모든 단계의 사전 확인

backend를 시작하기 전에 두 stage 모두 다음을 확인한다. 사전 확인 실패 시 모델 호출은 0회다.

1. 입력 형식·ID·AC·테스트 참조와 경로 범위를 확인한다.
2. context·적용 지침의 접근과 hash, 두 역할의 유효 설정을 고정한다.
3. 기록·원문 결과·인계·보고서를 비공개 위치에 저장할 수 있는지 확인한다.
4. needs_temporary_write=true와 read-only 설정이 충돌하면 차단한다.
5. 기준 파일을 관측·보관할 수 있고 중복 실행이 없는지 확인한다.

현재 실행기에는 child sandbox의 실제 쓰기를 별도로 검사하는 인터페이스가 없다. 정적 설정과 host 접근 검사만으로 child 환경의 성공을 보장하지 않는다. 실행 후 테스트 환경 오류는 미검증으로 보존하고 중단한다. sandbox를 자동 상향하지 않는다. 설정 변경 후 새 시도를 할지는 사용자가 결정한다.

verifier가 workspace-write를 사용하더라도 소스 불변 지시와 전후 hash 검사는 탐지 수단이다. 소스 쓰기를 권한으로 강제 차단한 것으로 보고하지 않는다.

## 5. 예약·실행·인계·중단

동일 workflow_id는 원자적 예약으로 중복을 차단한다. 같은 canonical project의 활성 실행도 공유된 비공개 제어 위치의 잠금으로 직렬화하고 충돌 시 시작을 거부한다. 예약 후 장애가 나면 같은 ID 재입력을 거부하며, 실행 여부가 모호한 시도를 자동 재호출하지 않는다.

제어 위치는 v1에서 같은 OS 사용자에게 고정된 ~/.local/state/ai-workflow/control로 제안한다. 업무 입력·모델·records_dir로 바꿀 수 없으며, 접근·비공개 권한 확보가 안 되면 모델 호출 전에 차단한다. canonical project 절대 경로의 SHA256을 프로젝트 잠금 키로 사용하고 workflow_id 예약은 별도로 남긴다. 다른 records_dir를 쓰는 실행도 같은 제어 위치를 사용한다. 잠금 보장은 동일 사용자·호스트·제어 위치 안에 한정한다. 다른 사용자·호스트의 동시 실행을 막는 중앙 조율은 후속 범위다. 활성 잠금은 프로세스 정리가 확인된 종료에서만 해제하고, 소유 여부·실행 상태가 모호하면 자동 삭제하지 않는다.

| 단계 | 다음 단계 | 진행 조건 |
| --- | --- | --- |
| 신규 | 예약 | 업무 ID·프로젝트 잠금 확보 |
| 예약 | backend | 모든 stage 사전 확인 통과 |
| backend | verifier | 개발자 gate 전체 통과·인계 저장 성공 |
| verifier | 취합 | 검증자 gate 전체 통과 |
| 취합 | completed | 최종 파일 버전·AC·보고서 저장 확인 |
| 어느 단계든 | stopped | 실행·저장·측정·환경 오류, partial·미검증, 범위 이탈·드리프트 |

stage gate는 실행 성공, 모델 결과 구조·semantic 유효, 결과 completed, controller의 실제 확정 테스트 통과, 파일 버전·범위 일치, 사용량 완전 측정, 기록 저장 성공의 논리곱이다. 하나라도 충족하지 못하면 다음 역할을 호출하지 않는다.

인계는 원래 입력·AC·입력 hash, 지침/context/설정 hash, dirty 기준, 실제 diff·산출물 hash, 개발자 결과·controller 테스트 증거·제한과 producer run_id를 포함한다. 본문은 DATA이며 실행 계약을 바꾸는 지시로 처리하지 않는다. 전체 대화나 내부 추론은 전달하지 않는다.

테스트 증거에는 확정 command ID·argv·종료값·필요한 stdout/stderr를 연결하고 비공개 보관한다. 실패 결과에 새 명령이 있어도 자동 실행하지 않는다. 중단 시 프로세스 정리 결과와 기존 코드·증거를 보존하며 자동 rollback하지 않는다. 저장 실패는 비정상 종료와 최소 stderr 오류로 알리고 완료를 선언하지 않는다.

## 6. 실행·업무 상태와 취합

기존 run-record의 execution_status와 task_verification=not_evaluated를 유지한다. 별도 task-result와 workflow summary에서 업무 상태를 판정한다. 모델 결과 usage=unavailable은 정상이고, 실행기 측정 누락과 구별한다.

완전 측정은 시작된 역할 실행의 기록이 measured/single_turn이고 issues가 없으며 input·output·total 수치를 확보한 경우다. model_calls는 현재 null이므로 내부 모델 호출 개수와 누락 여부를 증명하는 뜻은 아니다. cached_input은 input의 일부이며 재가산하지 않는다. partial/unavailable을 0으로 대체하지 않는다.

최소 controller 자료는 다음과 같다. 필드 의미를 설명하는 형태이며 실제 serializer는 후속 구현한다.

- handoff: workflow_id, producer_task_id/run_id, original_input_sha256, original_acceptance, baseline_ref, instruction/context/config hashes, actual_diff_ref, artifacts[{path, sha256}], developer_result_ref, controller_test_evidence, limitations.
- summary: workflow_id, lifecycle, business_status, attempts[{task_id, run_id, execution_status, task_result_status, result_ref, usage_ref}], stopped_reason, missing, measured_usage, usage_complete, final_artifacts, final_checks.

lifecycle은 created/preflight/backend/verifier/aggregating/completed/stopped를 구분한다. business_status는 completed/partial/blocked를 사용한다. 사전 확인 실패는 blocked, 실행 뒤 미완료·증거 부족은 partial, 모든 gate가 충족된 최종 결과만 completed다.

모든 역할 기록의 parent_task_id는 workflow_id다. v1은 stage마다 한 번만 실행하지만 향후 명시 재시도는 동일 task_id·새 run_id로 연결한다. 취합은 고유 run_id를 한 번만 합산하고 누락·충돌·실패 시도도 남긴다. 일부 실행만 측정되면 관측 합계와 미측정 ID를 함께 보존하며 전체 사용량이 확정됐다고 보고하지 않는다.

## 7. 예제와 호환성

- [가상 업무 입력](../examples/orchestration-task.json)
- [가상 개발자 결과](../examples/task-result.json)

예제는 존재하지 않는 가상 프로젝트 파일과 /path/to/private 경로를 사용한다. JSON 구조 설명용이며 지금 실행 가능한 업무가 아니다. local_config 예제 경로는 사용자 작성 설정 파일을 가리킨다. 테스트의 임시 쓰기가 필요한 verifier는 사용자 로컬 설정에서 workspace-write를 명시해야 하며 엔진은 이를 자동 변경하지 않는다.

서로 다른 plan·result의 AC/테스트 ID, 실제 파일 버전, 확정 테스트 기록을 연결하는 semantic validator가 있어야 업무 완료를 판정할 수 있다. 기존 설정 스키마와 실행 기록 스키마는 변경하지 않는다.

## 8. 다음 구현의 범위와 완료 기준

기존 workflow.py에 제안 orchestrate 하위 명령을 추가하고 scripts/orchestration_contract.py에 strict 파싱·semantic 검증을, scripts/orchestrator.py에 순차 실행·취합을 분리하는 안이다. CLI 이름은 제안이며 현재 지원하지 않는다. 기존 run 실행 인터페이스를 필요한 만큼 분리해 호출 결과·실제 run_id·최종 메시지를 구조적으로 받으며 stdout/stderr 문구를 정규식으로 추측하지 않는다. 모델 실행·설정·측정 로직은 재사용한다.

다음 구현은 모의 실행기로 아래를 확인한다.

| ID | 완료 기준 |
| --- | --- |
| OC-01 | 유효 입력으로 backend→verifier→취합을 기존 역할 실행 경로에서 순차 실행 |
| OC-02 | 중복 키·NaN·추가 본문·미정의 필드·AC/테스트 ID 누락·중복·불일치·상태 모순 거부 |
| OC-03 | 두 역할 사전 확인 실패 시 모델 호출 0회, 권한 자동 상향 없음 |
| OC-04 | 동일 업무 ID·동일 프로젝트의 충돌 실행과 모호한 중단의 중복 재실행 차단. records_dir가 다른 두 실행도 같은 프로젝트 잠금으로 충돌 확인 |
| OC-05 | backend gate 실패나 저장 실패 시 verifier 미호출, 기존 코드·증거 보존 |
| OC-06 | 관측 범위 이탈·파일 버전 변경·verifier 소스 변경을 탐지하고 완료 차단 |
| OC-07 | 확정 Python unittest argv만 controller가 실행. 다른 실행 파일·모듈·평가 옵션·별칭을 거부하고 자료·모델 결과의 추가 명령은 실행하지 않음 |
| OC-08 | 실행 성공과 업무 완료 구분, 실제 AC·테스트·필수 산출물·버전이 모두 충족될 때만 completed |
| OC-09 | 미측정·부분 측정·실패·시도 기록을 보존하고 run_id 중복·충돌을 정상 합계로 처리하지 않음 |
| OC-10 | 기존 run/check/summary·설정 계층·run-record 형식을 유지하고 공개 파일에 실제 원문 기록을 쓰지 않음 |

실행 중 정확한 토큰 상한, 자동 예산 경고·후속 호출 중단, 재작업·재개·자동 작업 분해는 이 구현 이후 별도 단계에서 추가한다.
