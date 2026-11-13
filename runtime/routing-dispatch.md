# 수용한 위임·프로필과 관리 실행 연결

상태: 명시한 선택 파일을 기존 `dispatch_guard.py start`에 연결하는 opt-in 경로와 완료 프로필 대조 구현. 가상 native 응답으로 검사했으며 이 경로를 통한 실제 worker 실행·모델 전환은 아직 검증하지 않았다. 앱의 입력·추천·선택 UI 자동 연결도 별도다.

기존 [선택 기록](routing-choices.md), [관리 실행기](delegation.md), [프로필 수집](execution-profiles.md)을 연결한다. 실행기를 새로 만들거나 현재 대화의 모델을 바꾸지 않는다. 선택 파일 옵션이 없으면 기존 실행 경로를 사용한다.

## 지원 범위와 실행 전 조건

2026-11-13 설치 Orca 1.4.257의 `orchestration` 배포 가이드와 `orchestration worker-start --help`에서 Codex 새 worker의 `--model`·`--effort`, effort의 명시 모델 요구를 확인했다. 이는 CLI 인자 지원 근거이며 계정별 모델 접근·모델과 effort 조합의 가용성·실행 성공을 보장하지 않는다. 배포 가이드는 사용자가 모델을 지정한 경우에만 `--model`을 추가하도록 하므로, 사용자에게 구체적인 모델·effort를 제시하고 수용한 선택만 연결한다. 이 코드가 자연어 동의를 증명하지는 않는다.

다음 조건을 모두 충족해야 한다.

- [시작 가이드](environments/README.md)와 [Orca 실행·권한 준비](environments/orca.md)를 확인한 조율자가 호출한다. 조율자의 Orca 세션과 선택 파일의 세션이 일치해야 한다. 코드의 문자열 대조만으로 실제 프로젝트 바인딩·권한을 증명하지 않는다.
- 같은 프로젝트·요청·revision의 현재 수용된 추천 ID/버전을 지정한다. 직접 처리·고정·위임 금지는 실행 전 다시 검사한다. 역할 선택만으로 worker 실행을 허용하지 않는다.
- 대상은 `execution: worker`, 명령은 `start`, 공급자는 `codex`, worktree는 `current`만 지원한다. 다른 selector·새 worktree·원격·retry·followup에는 이 연결을 적용하지 않는다.
- 수용한 프로필의 모델·effort가 모두 명시되어 있어야 하며 CLI의 `--model`, `--effort`도 그 값과 정확히 일치해야 한다. 역할 기본값이나 native 기본값으로 빈 값을 채우지 않는다.
- 기존 관리 실행기의 동시·누적 한도, 담당 수정 경로, 선행 배정과 실제 권한 검사를 유지한다. 사용량 역할을 지정하면 선택한 역할과 일치해야 한다.

같은 AI의 모델/effort 변경 선택을 worker 위임으로 바꾸지 않는다. 해당 대화의 공식 전환 경로·적용 근거가 없는 경우 미연결 상태로 둔다. 미지원이나 실패를 다른 공급자·모델·단독 CLI로 대체하지 않는다.

## 명시 실행 예시

아래 경로·ID·한도는 가상 예시다. 앞선 동일 AI용 선택 파일을 재사용하지 않고 별도 위임 추천을 수용해야 한다. 추천에는 예를 들어 다음 대상 전체를 표시한다.

```json
{"role":"tutor","model":"gpt-6.1-sol","effort":"medium","execution":"worker"}
```

해당 요청의 선택 파일에 수용을 기록한 뒤 기존 실행기를 호출한다. 선택 파일은 Git·프로젝트 밖의 비공개 파일이다.

```sh
python3 scripts/dispatch_guard.py \
  --project /private/example/project --directory /private/example/coordinator-ledger \
  start --request-id request_example --operation-id explain_example \
  --max-active-workers 1 --max-dispatches-per-request 2 \
  --spec-file /private/example/task.md --worktree current --agent codex \
  --model gpt-6.1-sol --effort medium \
  --routing-choice-file /private/example/worker-choice.json --routing-revision 1 \
  --routing-proposal proposal_example --routing-proposal-version 1
```

공통 지침의 정확한 경로와 대상 프로젝트·상위 지침을 함께 확인하도록 연결한다. 선택한 역할의 설정된 지침만 읽어 원본 경로·상대 참조 해석 기준과 함께 작업 명세 앞에 넣는다. 역할이 없어도 공통 지침 참조는 전달한다. 다른 역할 본문은 읽지 않으며 합친 UTF-8 명세는 64KiB 이내여야 한다. 명세·수정 범위·프로필·선택 참조의 digest를 관리 계획에 고정한다. 모델 호출을 통한 재분류는 없다. 실제 참조 읽기·역할 지침 준수·결과 품질은 별도 검증한다.

## 중복·취소·불명 상태

한 수용 추천을 한 ledger·operation·계획 digest에 묶는다. 다른 operation/ledger나 바뀐 명세로 같은 동의를 다시 사용할 수 없다. 새로운 실행에는 새 추천·선택이 필요하다. 원래 operation의 같은 입력 재조회는 관리 실행기의 replay를 사용하며 새 worker를 만들지 않는다.

선택 파일 잠금을 먼저 잡고 관리 ledger를 예약한 뒤, native 호출 직전에 `dispatch_binding`을 비공개 선택 파일에 저장한다. 한도 보류는 선택을 소비하지 않는다. 예약·선택 기록·native 호출은 파일/외부 앱 전체를 아우르는 원자적 트랜잭션이 아니므로 중간 실패는 예약을 보수적으로 남길 수 있다. 수동 상태 확인 없이 파일을 지우거나 operation을 바꿔 재실행하지 않는다. 기존의 정확한 시작 전 권한 거부만 같은 operation으로 정식 승인 재호출할 수 있다.

선택 파일 잠금은 native 제출 결과가 돌아올 때까지 유지하며 기존 호출 timeout은 90초다. 이 동안 동시 선택/취소 명령은 비차단 잠금 오류로 실패할 수 있다. 오류를 취소 접수로 표시하지 않는다. 제출 전 취소된 선택은 실행을 막고, 이미 제출된 배정의 취소는 native 중단·정확한 종료 확인을 사용해야 한다. 이 연결이 실행 중 worker를 중단하거나 모델을 원복하지 않는다. 취소·만료 후에도 과거 `dispatch_binding`은 보존한다.

응답 유실·timeout·모순된 receipt는 기존 관리 실행기의 불명 상태·점유를 유지한다. 다른 모델·새 worker로 자동 재시도하지 않는다. 직접 CLI·다른 ledger를 사용하는 모든 실행을 차단하는 보안 경계는 아니다.

## 완료와 실제 적용값 대조

기존 `finish_worker.py`에 `--dispatch-directory`·`--operation-id`를 함께 전달하면 수집 전에 task/dispatch 바인딩을 대조한다. 선택이 연결된 배정은 선택한 모델·effort를 검사 기대값으로 연결한다. 사용자가 다른 기대값이나 요청/역할을 전달하면 거부한다.

```sh
python3 scripts/finish_worker.py \
  --project /private/example/project --directory /private/example/completion \
  --session-file /private/example/native-session.jsonl --session-id session_example \
  --task-id task_example --dispatch-id dispatch_example \
  --request-id request_example --role tutor \
  --dispatch-directory /private/example/coordinator-ledger --operation-id explain_example
```

native 세션·task/dispatch ID는 실제 공식 근거의 정확한 값으로 채우며 폴더 전체를 탐색하거나 추측하지 않는다. 사용량/프로필 자동 수집을 쓰려면 기존의 actor 등록·usage 옵션·프로젝트 완료 hook 준비가 별도로 필요하다.

수집한 metadata에서 다음 세 단계를 선택값과 대조해 `routing_profile=pass|fail|unverified`를 출력한다.

1. native 시작 요청 `requested`
2. Orca 실행환경 적용 `launch_effective`
3. 해당 배정 구간의 실제 `observed_contexts`

모델·effort가 세 단계 모두 일치해야 pass다. 명확한 불일치는 fail, 누락·미제공·빈 실제 관측은 unverified이며 기대값으로 채우지 않는다. fail/unverified이면 완료 명령은 코드 1을 반환하고 정리를 진행하지 않는다. 확인된 worker 종료에 따른 관리 슬롯 반환·근거 보관은 실행 설정 일치 여부와 별개다. 실제 관측은 공급자 턴 컨텍스트의 값이며 물리 모델 서버의 독립 계측을 뜻하지 않는다.

이 요약은 CLI 판정이며 별도의 전역 감시기·자동 재계획을 설치하지 않는다. 원래 metadata·기대값과 선택이 연결된 ledger 계획을 비공개 근거로 보관한다. task 수용·품질 판정은 별도다.

## 검증과 남은 범위

`tests/test_routing_dispatch.py`의 19개 가상 native 응답 검사에서 정확한 인자/역할·공통 지침 참조 전달, 합친 명세 제한, 같은 동의의 중복 실행 방지, 다른 ledger·변경된 범위·취소·오래된 선택·권한 제어·잠금·한도 보류·응답 유실, 완료 기대값과 세 단계의 누락/불일치를 확인했다. 독립 검토에서 발견한 공통 지침·원본 경로 전달 누락을 수정하고 재검토를 통과했다. 실제 native 실행 검증과 구분한다.

실제 추천→선택→이 경로의 native 배정→완료 대조 한 건, 현재 대화의 프로필 전환, 앱 입력/선택 UI, 전체 비용·품질 비교와 제한 자동화는 남아 있다. 같은 단계의 단위 검사를 실제 전환 성공이나 토큰 절감으로 보고하지 않는다.
