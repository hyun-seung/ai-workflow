# 요청별 추천과 사용자 선택 기록

상태: 로컬 선택 기록·선택한 역할 지침 준비·명시한 후속 대화의 선택 재사용 구현. 수용한 worker 선택의 [명시 관리 실행 연결](routing-dispatch.md)도 제공한다. Orca의 실제 선택 UI·자연어 동의 해석·동일 AI에 지침 주입은 아직 연결하지 않았다. [입력 어댑터](orca-routing-input.md)의 snapshot과도 자동 동기화하지 않는다.

`scripts/routing_choices.py`는 [추천 검사기](request-router.md)의 판정을 받아 요청별 추천과 수용·거절·취소를 비공개 파일에 기록한다. 모델·네트워크·worker를 호출하거나 모델/effort·권한·앱 설정을 바꾸지 않는다. 선택 기록은 실행 권한 승인이 아니다.

## 처리 계약

| 명령 | 입력과 결과 |
| --- | --- |
| `init` | 프로젝트·세션·요청·revision·현재 프로필·제어로 새 상태 생성 |
| `offer` | `{proposal_id, analysis}`를 추천 검사기에 전달. `propose`일 때만 버전이 있는 대기 추천 저장 |
| `choose` | 정확한 세션·요청·revision·추천 ID·추천 버전과 `accept`, `reject`, `cancel`을 대조해 기록 |
| `advance` | 같은 요청의 목표·범위·제어·현재 프로필 변경 시 revision 증가. 기존 선택과 대기 추천 만료 |
| `show` | 명시한 상태 조회. 실제 실행 결과가 아님 |
| `role-context` | 수용한 현재 AI용 역할의 설정된 지침만 읽어 반환. `runtime_applied: false` 유지 |
| `reuse` | 호출자가 후속 여부와 현재 요청 문맥을 명시하면 저장된 선택과 대조. 역할 본문을 읽거나 상태를 수정하지 않고 재사용 가능 여부 반환 |

- 새 요청·다른 프로젝트·다른 세션은 별도 상태 파일을 사용한다. 제어의 대화 전체 상속이나 영구 선호는 구현하지 않았다.
- 새 추천은 이전 대기 추천을 만료시킨다. 같은 추천 ID로 다른 대상·revision을 제안할 수 없다. 이미 수용한 선택은 새 추천을 수용하거나 기존 선택을 취소·만료할 때까지 유지된다.
- 같은 선택의 재전달은 상태를 바꾸지 않는다. 거절·취소·만료된 추천은 뒤늦게 수용할 수 없다. 새 추천을 제시해야 한다.
- 변경된 제어·범위는 `advance`로 먼저 반영한다. 수용 시 정책을 다시 검사하고, 역할 지침 준비 때도 현재 제어·목록을 재검사한다.
- 취소는 이후의 지침 준비를 중단한다. 이미 주입한 문맥 제거·실행 중 작업 중지·설정 원복을 뜻하지 않는다.
- “응”, “좋아” 같은 자연어를 이 도구가 동의로 해석하지 않는다. 실제 연결부가 표시한 추천과 답변의 대응을 확인해 구조화해야 한다. 그 연결은 남아 있다.
- worker 또는 모델/effort 변경 추천을 수용해도 `role-context`는 거부한다. 별도의 지원 확인·실제 실행 연결이 필요하다.
- [명시 관리 실행 연결](routing-dispatch.md)은 수용한 worker 추천에 `dispatch_binding`을 추가해 한 ledger·operation·계획 digest와 묶는다. 같은 추천을 다른 실행에 재사용하지 못하며 취소·만료 뒤에도 이 참조는 보존한다. 같은 AI의 프로필 변경은 이 연결로 대체하지 않는다.

## 비공개 입력 예시

모든 명령에 `--project`와 `--state`를 명시한다. 아래 경로·식별자는 가상 값이며 실제 입력 파일과 출력도 Git 밖에 보관한다. 프로젝트 경로는 존재하는 절대 경로여야 한다.

`init`의 stdin:

```json
{
  "project": "/private/example/project",
  "session_id": "session_example",
  "request_id": "request_example",
  "revision": 1,
  "current": {"role": null, "model": "gpt-6.1-sol", "effort": "medium", "execution": "current"},
  "controls": {"model_locked": true, "effort_locked": true, "delegation": "forbid"}
}
```

`offer`의 stdin:

```json
{"proposal_id":"proposal_one","analysis":{"recommendation":{"role":"tutor","reason":"명시한 학습 방식에 맞는 설명 준비"}}}
```

`choose`의 stdin:

```json
{"session_id":"session_example","request_id":"request_example","revision":1,"proposal_id":"proposal_one","proposal_version":1,"choice":"accept"}
```

사용 예:

```sh
python3 scripts/routing_choices.py init --project /private/example/project --state /private/example/state/choice.json < /private/example/inputs/context.json
python3 scripts/routing_choices.py offer --project /private/example/project --state /private/example/state/choice.json < /private/example/inputs/offer.json
python3 scripts/routing_choices.py choose --project /private/example/project --state /private/example/state/choice.json < /private/example/inputs/choice.json
python3 scripts/routing_choices.py role-context --project /private/example/project --state /private/example/state/choice.json
```

## 후속 대화의 짧은 재사용 경로

`reuse`의 stdin은 `init`의 전체 문맥에 boolean `followup`을 더한 객체다. 필드를 생략하거나 `"yes"`·`1` 같은 값을 후속 확인으로 쓰지 않는다. 호출하는 현재 AI가 원래 요청과 비교해 후속 여부·revision·제어를 먼저 판단한다. 단지 직전 선택이 있다는 이유로 `followup: true`를 넣지 않는다. 원문·의도 자동 분류 기능은 없다.

```sh
python3 scripts/routing_choices.py reuse --project /private/example/project --state /private/example/state/choice.json < /private/example/inputs/followup.json
```

- 프로젝트·세션·요청·revision·현재 프로필·정규화한 제어가 저장 당시와 일치하고, 수용한 현재 AI용 선택이 있을 때만 `action: reuse`를 반환한다. 역할 목록의 현재 유효성도 다시 검사한다.
- 새 요청·제어/프로필 변경·미확인 후속·선택 없음에는 재사용하지 않는다. 추가 의미 판단이 필요하면 `needs_analysis`, 직접 처리나 전체 고정이면 `direct`, 자동 처리면 기존 미지원 판정을 반환한다. 이 경로는 역할 목록도 읽지 않는다.
- 새 `direct` 지시를 우선한다. 이 읽기 전용 호출만으로 저장된 선택이 취소되는 것은 아니다. 제어를 지속 반영하려면 `advance`, 선택 취소는 `choose`를 사용한다. 실제 연결부는 새 지시를 버리고 예전 문맥으로 돌아가지 않도록 해야 한다.
- worker·모델/effort 변경 선택은 `requires_separate_runtime_connection`으로 재사용을 보류한다. 동의 기록을 실제 전환 성공의 근거로 쓰지 않는다.
- 성공해도 빈 추천 메시지·작은 프로필만 반환하고 역할 지침은 읽거나 반환하지 않는다. `runtime_applied: false`이며 실제 주입 여부·현재 AI가 지침을 보유하는지는 증명하지 않는다. 문맥이 소실됐다면 `role-context`로 필요한 역할만 별도 준비한다.
- 같은 입력을 다시 조회해도 파일 내용·수정 시각은 바뀌지 않는다. 자연어 판단과 hook 자동 호출은 별도이며 이 명령을 모든 입력의 필수 절차로 추가하지 않는다.

## 비용·보관과 한계

상태는 Git·대상 프로젝트 밖의 소유자 전용 폴더 0700, 파일 0600에 저장한다. 기존 비공개 경로 검사·잠금·원자적 저장을 재사용한다. 요청 원문·추천 이유·대화 transcript는 저장하지 않는다. 프로젝트 경로·식별자·프로필 자체도 비공개 정보이므로 출력·상태·lock 파일을 커밋하지 않는다.

추가 모델 호출은 없고, 추천·선택·상태 변경 때 명시적으로 실행한다. 매 입력마다 이 도구나 모든 역할 문서를 호출하지 않는다. 역할 지침은 수용 후 필요한 시점에 선택한 역할만 준비하며, 중복 주입 방지는 후속 앱 연결부의 책임이다. 현재 AI의 자연어 판단·프로세스 실행·지침 주입 비용까지 0이라는 뜻은 아니다.

요청당 최대 64개 추천, 입력·직렬화한 상태는 64KiB로 제한한다. 지침 읽기는 합계 65,536문자 이내다. 한도 초과는 오류이며 이력을 조용히 삭제하지 않는다. 역할 지침·프로젝트 설정은 읽는 시점의 파일이며 수용 당시의 해시를 고정하거나 변경을 자동 감지하지 않는다. 의미 있는 변경 시 revision을 올리고 다시 제안해야 한다.

## 검증

```sh
python3 -m unittest discover -s tests -p test_routing_choices.py -v
```

26개 검사로 중복 선택, 오래된 세션/요청/버전, 제어 변경·만료, 거절/취소 뒤 수용 방지, 선택 일관성, 정책 재검사, 이전 선택 보존, 지침의 선택적 읽기, 위임/모델 변경의 적용 거부, 보관 한도와 CLI 파일 권한·프로젝트 경계를 확인했다. 후속 재사용의 문맥 대조·직접 처리 우선·역할 본문/모델/네트워크 미호출과 CLI 반복 조회의 파일 내용·수정 시각 불변도 확인했다. 실사용 동의 해석 정확도·Orca 입력 연결·문맥 주입·실제 토큰 절감은 이 검사의 대상이 아니다.
