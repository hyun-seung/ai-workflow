# 작업 입력

빈 양식이다. `<...>`를 실제 값으로 바꾸고 선택 항목은 필요한 것만 남긴다. 작성·판정 기준은 [작업 입출력 양식](README.md)을 따른다.

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
- depends_on: <선행 작업 ID와 인계에 필요한 결과·상태>
- context: <필요한 파일·문서·결정의 참조와 관련 부분, 필요한 경우 버전>
- constraints: <기술·디자인·호환성·허용 환경 등>
- deliverables: <산출물 형태와 합의된 전달·보관 위치>
- budget:
  - 예상 범위·단위·근거·불확실성: <정보가 없으면 미정>
  - 배정 한도·집계 단위: <합의된 경우만 작성>
  - 중단·재계획 조건: <반복 실패·의존성 변경·한도 도달 시의 조치>

## 결과 요청

동일한 `task_id`로 `status`, `summary`, `artifacts`, 완료 기준별 `checks`, `remaining`, `usage`를 반환한다. `status`는 `completed / partial / blocked`, `checks`는 `pass / fail / unverified`를 사용한다. 모든 완료 기준이 통과한 경우에만 `completed`로 보고한다. 미측정 사용량은 `unavailable`로 표시한다. 중요한 결정이나 다른 역할의 작업이 필요하면 해당 내용만 추가한다. 함께 전달된 결과 양식이 있으면 그 형식을 따른다.
