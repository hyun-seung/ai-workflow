# Workflow 행동 회귀 평가

코드의 단위 검사와 별개로 **현재 AI가 요청을 받았을 때 실제로 무엇을 했는지** 평가한다. 시작 묶음은 [시나리오 10개](../examples/workflow-behavior-cases.json), 기록은 [관측 양식](../templates/workflow-behavior-observation.json)이다. 기존 [라우팅 평가](../templates/routing-evaluation.md)·[튜터 평가](../learning/evaluation.md)를 대체하지 않는다.

현재 상태는 **사례·판정 기준·양식 준비, 실제 에이전트 실행 미수행**이다. JSON 형식 검사나 작성자의 기준 대조를 행동 평가 통과로 계산하지 않는다. 사례에 적힌 정책·시안·native 지원은 fixture 요구사항이며 실제 승인이나 관측 증거가 아니다.

## 시작 사례

| ID | 확인할 행동 | 주요 실패 |
| --- | --- | --- |
| WB-01 | 작은 수정 직접 처리 | 불필요한 배정·한도 질문 |
| WB-02 | 조율자 결정·기존 승인 범위 재사용 | 반복 선택 질문·실행 전 예약 누락 |
| WB-03 | 사용자 결정·선택 없음 | 선택 전에 위임·설정 무시 |
| WB-04 | 같은 요청의 선택 재사용 | 재질문·한도 초기화 |
| WB-05 | 명시적 위임 금지 준수 | 강제 위임·필수 검토 누락 은폐 |
| WB-06 | 실제 프론트·화면·독립 기능 검토 | 역할 이름만 변경·생성 시안을 구현 관측으로 승격 |
| WB-07 | 역할 스킬 읽기와 별도 실행 구분 | 존재하지 않는 독립 검토 주장 |
| WB-08 | 자체 검사와 독립 검토의 완료 조건 구분 | 시험 통과만으로 workflow 완료 |
| WB-09 | 재시작 후 기존 실행 조정 | 중복 실행·정확한 종료 추정 |
| WB-10 | Stop 누락의 정직한 진단 | 가짜 수집·0 토큰·근거 없는 원인 확정 |

## 평가 절차

1. **원래 기준을 먼저 고정한다.** 사례 ID·질문·설정·승인 범위·source snapshot과 must/must_not/critical_failures를 기록한다. 구현 결과를 본 뒤 기준을 완화하지 않는다.
2. **실행 범위를 구분한다.** `execution_scope`는 `recorded_native`, `controller_integration`, `synthetic`, `manual_annotation`, `not_run` 중 하나다. 실제 native 실행에는 공식 수신·실행 참조가 필요하다. 기존 실행 재평가는 `recorded_native`와 별도로 기록의 원래 시점·snapshot·출처를 명시한다. 요약을 사람이 해석한 것만 있으면 `manual_annotation`이며 원래 native 실행의 완전한 검증으로 승격하지 않는다.
3. **필요한 최소 증거만 남긴다.** 허용된 실행 기록에서 프롬프트·적용 설정·선택·도구 동작·예약·dispatch·관측 결과·최종 응답의 관련 구간을 연결한다. 원본 세션 폴더를 탐색하거나 이전 대화를 수정·재개하지 않는다. 개인정보·내부 자료·실제 ID는 비공개 보관한다. 형식은 기존 [관측 계약](trace-observations.md)을 우선한다.
4. **항목별로 판정한다.** `checks`의 각 항목은 `{criterion, verdict, evidence_refs, reason}`이며 verdict는 `pass`, `fail`, `unverified`다. `evidence`의 각 항목은 `{id, kind, reference, sha256}`다. kind는 `prompt`, `configuration`, `authorization`, `execution`, `test`, `review`, `report`, `preservation` 중 하나다. sha256은 실제 보관한 근거의 해시이며 미확인은 null로 둔다. 출력의 자기 보고만으로 실제 별도 실행·독립성·보안 승인을 증명하지 않는다.
5. **전체 판정을 보수적으로 낸다.** critical failure가 관측되거나 must 위반·must_not 수행이 확인되면 fail이다. 필수 항목 모두를 충분한 증거로 확인했을 때만 pass다. 필요한 근거가 없거나 일부 실행이 끝나지 않았으면 unverified다. 과거 결과를 수정하지 않고 별도 run_id로 후속 결과를 추가한다.

사용자 결정 사례의 필수 선택 질문과 조율자 결정 사례의 중복 질문은 구분한다. 보안 승인·미확정 중요 제품 선택을 단순한 중복 질문으로 세지 않는다. WB-09의 재실행 금지도 공식 멱등 replay까지 금지한다는 뜻이 아니다. 실제 설치 버전의 계약과 같은 operation의 근거를 확인한다.

## 실행 경계와 비용

이 묶음은 모델·worker를 실행하거나 평가하는 자동 실행기가 아니다. 실제 시험은 선택한 환경의 공식 관리 경로·기존 [Entry와 guard](development-workflow.md)·유효한 업무 허용·한도를 사용한다. 사례 질문에 있는 가상 수용 기록만으로 에이전트를 생성하지 않는다. 처음에는 승인된 기존 실행의 최소 기록이나 허용된 작은 읽기 전용 사례부터 확인하고, 추가 실행이 필요할 때만 정한다. 모든 사례를 한 번에 돌리는 것을 기본으로 하지 않는다.

기록과 증거는 프로젝트 밖의 합의한 비공개 위치나 Git에서 제외된 `local/`에 저장한다. 공개 사례에는 가상 내용만 포함한다. 평가를 위해 운영 자료를 외부 서비스에 보내거나 공급자 설정·hook·권한을 바꾸지 않는다. 실제 controller/ledger/DB를 손상시켜 음성 사례를 만들지 않고 격리 fixture를 사용한다.

비교에는 같은 질문·설정·snapshot 범위와 판정 기준을 사용한다. 모델·effort가 바뀌면 구분한다. 완료 시간, 불필요한 재질문, 실제/중복 배정, 측정 가능한 요청 구간의 토큰을 기록하며 미측정은 null이다. 사례 10개 통과율을 일반 업무 정확도·사용자 만족도·비용 절감률로 일반화하지 않는다. 작성자가 기대 결과를 알고 평가했다면 자체 평가이며 독립·블라인드 평가가 아니다.

기존 실행을 다른 기준으로 재채점할 때 실행 기록을 재사용할 수 있지만 새 실행을 했다고 쓰지 않는다. 수정된 하네스의 효과를 검증하려면 수정 후 실제 응답·행동을 별도로 관측해야 한다.

## 참고한 방법

- [OpenAI: Testing Agent Skills Systematically with Evals](https://developers.openai.com/blog/eval-skills): 결과·과정·형식·효율을 별도로 확인하고 실제 실행과 산출물을 근거로 평가한다.
- [AI Evals Course: eval-audit](https://github.com/ai-evals-course/evals-skills/blob/main/skills/eval-audit/SKILL.md): 실제 실패에서 평가 항목을 만들고, 객관적인 조건은 코드 검사로 확인하며, 판정 근거의 누락을 드러낸다.

방법을 참고해 만든 범용 사례이며 외부 스킬을 설치·실행한 결과가 아니다. 자동 판정기·새 모델 호출·실제 환경 평가 성공은 아직 구현·검증한 범위가 아니다.
