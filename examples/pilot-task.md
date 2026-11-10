# 사용량 기록의 첫 실제 검토

공개 코드와 가상 기록을 검토하는 작은 작업이다. 실행기 연결을 확인하기 위한 실험이며 전체 구현의 안전성이나 업무 품질을 보증하는 검토가 아니다.

## 입력

- task_id: PILOT-USAGE-001
- parent_task_id: PILOT-001
- task_type: design-review
- role: verifier
- goal: 사용량 기록의 세 가지 계약이 코드와 예시에 일치하는지 정적으로 확인한다.
- scope:
  - 포함: 아래 세 파일과 지정한 심볼·필드의 읽기 및 짧은 검토 결과
  - 제외: 파일 수정, 테스트 재실행, 전체 저장소 탐색, 외부 검색, 추가 에이전트 호출
- context:
  - `scripts/run_records.py`: `UsageCollector.result`, `UsageCollector.consume`, `new_record`만 우선 확인한다.
  - `examples/run-record.json`: 모든 값은 가상 예시이며 실제 측정값이 아니다.
  - `schema/run-record.schema.json`: `usage`, `execution_status`, `task_verification` 정의를 확인한다.
  - 위 경로는 대상 프로젝트 루트 기준이다. 이 실험의 대상 프로젝트는 ai-workflow 저장소다.
  - 현재 작업 사본의 내용으로 검토하며, 커밋된 코드와 동일하다고 가정하지 않는다.
- constraints:
  - 현재 실행의 실제 사용량은 실행기가 종료 후 기록한다. 예시 수치나 스스로 계산한 수치를 이번 호출의 사용량으로 보고하지 않는다.
  - 추가 자료가 필요하면 그 이유를 적고 해당 항목을 미검증으로 남긴다.
  - 입력 양식의 budget 필드가 실행기의 토큰 강제 제한이라고 가정하지 않는다.

## 완료 기준

| ID | 확인할 계약 | 확인 방법 |
| --- | --- | --- |
| AC-01 | total_tokens는 input_tokens + output_tokens이며 캐시·추론 세부값을 다시 더하지 않는다. | 계산 코드와 가상 예시를 대조한다. |
| AC-02 | 미측정 값은 null로 남고 실제 측정된 0과 구분된다. | 초기값, 값 검사와 usage.status 판정 코드를 확인한다. |
| AC-03 | 실행 성공만으로 업무 검증 통과를 기록하지 않는다. | task_verification 초기값과 스키마를 확인한다. |

## 반환 형식

한국어로 600자 안팎의 짧은 결과를 stdout에 반환한다. 파일은 생성하지 않는다.

- task_id: PILOT-USAGE-001
- status: completed / partial / blocked
- checks: AC-01~03 각각 pass / fail / unverified, 확인한 함수 또는 필드와 간단한 근거
- remaining: 미검증·실패 항목 또는 없음
- usage: unavailable — 이번 실행의 실제 사용량은 종료 후 실행기 기록에서 확인

`completed`는 위 정적 검토 기준을 모두 확인했을 때만 사용한다. 실제 모델 연결·사용량 수집·파일 보관의 성공 여부는 실행 후 별도로 확인한다.
