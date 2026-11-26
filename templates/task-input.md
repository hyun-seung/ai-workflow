# 작업 입력

UI 명시 계획에는 [plan v3 엄격 UI 계약](../runtime/development-workflow.md#프론트엔드-선행-계약-plan-v3)을 연결한다. Entry 전에 원래 필수 차원과 viewport/theme/content/state를 동결한다. 기준/현재 구현 이미지 참조·provenance, 전체/상세 실제 크기·확대 관찰, 수정 후 전체 정상 화면 재검토를 담당 범위에 넣는다. 미활성/legacy와 실기기 요구·지원 범위를 명시한다.

빈 양식이다. `<...>`를 실제 값으로 바꾸고 선택 항목은 필요한 것만 남긴다. 작성·판정 기준은 [작업 입출력 양식](README.md)을 따른다.

직접 처리하는 작은 작업은 [네 줄 요청](README.md#직접-요청하는-작은-작업)을 사용해도 된다. 아래는 위임·기록을 연결할 때의 양식이다.

## 필수

- task_id: <고유한 작업 ID>
- role: <실행 설정의 역할 ID>
- goal: <완료했을 때 달라져야 하는 결과>
- scope:
  - 포함: <대상 기능·자료·수정 경로>
  - 제외: <이번 작업에서 다루지 않는 범위>

### acceptance

| ID | 완료 기준 | 확인 방법 |
| --- | --- | --- |
| AC-01 | <관찰 가능한 결과> | <테스트·출처 대조·화면 확인 등> |

## 선택

- parent_task_id: <상위 작업 ID>
- task_type: <development / research / design-review / writing 등>
- depends_on: <선행 작업 ID, 필요한 결과·계약과 인계 가능 상태>
- context: <인계가 필요하면 [다섯 항목](README.md#다음-담당자에게-먼저-전달할-내용): 목표·결정·현재 상태·검증·다음 행동. 기존 goal/acceptance/constraints 등과 겹치는 내용은 정확한 참조로 연결>
  - 필요한 확장: <관련 파일·심볼과 읽을 이유, 외부 입력의 실제 접근 경로·버전/해시, host 차이·미확인 등 이번 담당자에게 필요한 부분만>
- constraints: <기술·디자인·호환성·허용 환경 등>
- skills: <필요한 경우 역할 적용 스킬의 접근 가능한 경로, 원본 ID·버전·사용 모드, 스택·선택 이유. 설치·권한과 실제 사용 여부는 별도 확인>
- execution_profile: <공급자 / 모델·effort 요청값과 사용자 선택 근거 / native 기본값 사용 여부 / 실제 확인값·출처 또는 unknown. 실행 설정을 자동 적용하지 않음>
- delegation: <여러 작업을 배정할 때만 작성. [병렬 정책](../runtime/parallelism.md) 참조>
  - 담당 경계·선행 조건: <동일 파일 수정은 한 담당자. 독립 병렬 후보와 선택 이유>
  - max_active_workers: <조율자가 관리하는 모든 요청·Run의 합계 동시 상한. 환경 한도 안에서 정하며 고정 기본값 없음>
  - max_dispatches_per_request: <후속 배정·재시도를 포함한 누적 상한>
  - 적용 방식: <dispatch_guard.py와 같은 조율자의 공유 비공개 ledger. 관리 경로 밖의 실행까지 통제한다고 주장하지 않음>
- execution_permissions: <필요할 때 [권한 인계 양식](execution-permissions.md)의 허용 범위·실제 설정·준비 근거·남은 차이를 첨부. 권한 부여 설정이 아님>
- escalation: <계약 변경 등 담당 범위를 벗어나는 판단이 필요할 때 전달할 내용과 대상>
- deliverables: <산출물 형태와 합의된 전달·보관 위치>
- cleanup: <이번에 만드는 서버·임시 공간이 있을 때만 생성 목적·소유권·유지/종료·보관/삭제 권한과 담당>
  - auto_cleanup: <Y / N. 기본 Y, 보관본·권한·종료가 검증된 정리 계획에만 적용>
  - 완료 연결: <임시 공간이면 정확한 대상·보관본·정리 권한 근거와 관련 배정 ID. [정리 계획](../runtime/completion.md)을 완료 시 만들고 결과와 함께 제거/보류 상태 보고>
- additional_checks: <이번 acceptance에 포함하지 않는 선택적 확인과 이유. 미수행을 숨기지 않음>
- budget:
  - 예상 범위·단위·근거·불확실성: <정보가 없으면 미정>
  - 배정 한도·집계 단위: <합의된 경우만 작성>
  - 중단·재계획 조건: <반복 실패·의존성 변경·한도 도달 시의 조치>

## 결과 요청

동일한 `task_id`로 `status`, `summary`, `artifacts`, 완료 기준별 `checks`, `remaining`, `usage`를 반환한다. `status`는 `completed / partial / blocked`, `checks`는 `pass / fail / unverified`를 사용한다. 모든 완료 기준이 통과한 경우에만 `completed`로 보고한다. `blocked`이면 `blocker`에 원인·시도한 내용·필요한 조치와 담당자를 적는다. 미측정 사용량은 `unavailable`로 표시한다. 중요한 결정이나 다른 역할의 작업이 필요하면 해당 내용만 추가한다. 함께 전달된 결과 양식이 있으면 그 형식을 따른다.
