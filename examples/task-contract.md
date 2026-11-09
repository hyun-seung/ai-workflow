# 작업 입출력 가상 예시

아래 프로젝트·작업 ID·경로·검사 결과는 양식을 설명하기 위한 가상 데이터다. 실제 구현이나 테스트 실행 기록이 아니며 실행 사용량도 측정하지 않았다. [양식의 필드와 상태 정의](../templates/README.md)를 함께 참고한다.

## 예시 1: 백엔드 개발 후 일부 검증이 남은 경우

### 입력

- task_id: DEMO-101
- role: backend
- goal: 목록 API에서 완료된 항목만 조회할 수 있게 한다.
- scope:
  - 포함: `server/`의 목록 조회 API와 관련 테스트
  - 제외: 화면 변경, 데이터베이스 스키마 변경
- task_type: development
- context: `docs/list-api.md`의 필터·응답 계약

| acceptance ID | 완료 기준 | 확인 방법 |
| --- | --- | --- |
| AC-01 | 완료 필터가 있으면 해당 상태의 항목만 반환한다. | 완료·미완료가 섞인 데이터의 API 테스트 |
| AC-02 | 필터가 없으면 기존 목록 조회 동작을 유지한다. | 기존 목록 API 회귀 테스트 |

### 결과

- task_id: DEMO-101
- status: partial
- summary: 완료 필터 구현과 해당 API 테스트를 마쳤지만 기존 회귀 테스트는 실행하지 못했다.
- artifacts: `server/src/ListHandler.java`, `server/test/ListApiTest.java`의 가상 변경본 `demo-revision-1`

| checks ID | 상태 | 수행한 확인과 증거·대상 버전 | 판정 주체 |
| --- | --- | --- | --- |
| AC-01 | pass | 가상 보고서 `reports/filter-test.txt`에서 혼합 데이터의 필터 결과 확인. 대상 `demo-revision-1`. | 자체 |
| AC-02 | unverified | 회귀 테스트용 데이터베이스가 없어 실행하지 못함. | 자체 |

- remaining: 승인된 테스트 환경을 사용할 수 있게 되면 AC-02를 검증한다. 그전에는 이 작업을 완료로 처리하지 않는다.
- usage: unavailable — 가상 예시이며 측정 자료가 없다.

검증자가 인계받으면 이 결과와 원래 완료 기준, 해당 변경본을 함께 확인한다. AC-01은 증거의 적절성과 현재 변경본에 대한 유효성을 확인하고, AC-02는 환경을 준비한 뒤 검증한다. 자동으로 모든 검사를 반복하거나 미검증을 통과로 바꾸지 않는다.

## 예시 2: 글쓰기 자료가 부족해 진행할 수 없는 경우

### 입력

- task_id: DEMO-201
- role: verifier
- goal: 기술 글 초안의 핵심 주장을 출처와 대조한다.
- scope:
  - 포함: 사용자가 제공한 초안과 출처의 정확성 검토
  - 제외: 새로운 자료 조사, 본문 수정, 외부 게시
- task_type: writing
- deliverables: 주장별 검토 결과를 응답으로 전달

| acceptance ID | 완료 기준 | 확인 방법 |
| --- | --- | --- |
| AC-01 | 핵심 주장마다 출처의 적용 조건과 초안의 표현이 일치하는지 판정한다. | 제공된 초안과 원문 대조 |

### 결과

- task_id: DEMO-201
- status: blocked
- summary: 초안과 출처가 아직 제공되지 않아 대조를 시작할 수 없다.
- artifacts: 없음

| checks ID | 상태 | 수행한 확인과 증거 | 판정 주체 |
| --- | --- | --- | --- |
| AC-01 | unverified | 검토할 초안과 원문을 전달받지 못함. | 자체 |

- remaining: 사용자 또는 조율자가 검토 범위의 초안·출처를 제공하면 진행한다.
- usage: unavailable — 가상 예시이며 측정 자료가 없다.

자료가 없을 때 내용을 추정해 작성하거나 출처를 만들어 채우지 않는다. 해결 조건을 명시해 조율자가 다음 행동을 정할 수 있게 한다.
