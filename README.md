# ai-workflow

**공통 작업 규칙·실행 환경 연결·프로젝트 지침을 분리해 재사용하는 AI 작업 체계입니다.**

## 처음 사용하시나요?

**권장은 [프로젝트 전체 연결 →](GETTING_STARTED.md)입니다.** 시작 메시지 하나로 AI에게 설정을 맡기고, 지원되는 판단·역할·검증·실행 기록을 프로젝트에 연결합니다. 설정 과정에서 필요한 경로·권한과 적용할 변경을 검토하며, 이후에는 평소처럼 작업을 요청합니다.

아래 메시지를 쓰던 AI 앱의 작업할 프로젝트에서 복사해 보내세요. 처음부터 앱·모델·설정 파일을 직접 고를 필요는 없습니다.

```text
이 프로젝트에 ai-workflow를 전체 연결해줘.
https://github.com/hyun-seung/ai-workflow

목표는 요청을 먼저 판단하고 필요한 역할·검증·지원되는 실행 및 기록을
하나의 작업 흐름으로 사용하는 것이야. 작은 요청은 현재 AI가 직접 처리해줘.
역할·모델 변경이나 위임은 기존 허용 범위와 이번 작업의 선택을 따라줘.

저장소에 접근할 수 있으면 runtime/project-onboarding.md를 읽고 진행해줘.
현재 프로젝트·실행 환경과 기존 지침을 먼저 확인하고,
파일·설정·권한을 바꾸기 전에 변경안을 보여줘.
지원되지 않거나 아직 검증하지 않은 기능은 구분해서 알려줘.
링크를 읽을 수 없으면 읽었다고 하지 말고 필요한 파일 전달 방법을 안내해줘.
```

지원하는 앱에서는 AI가 연결 절차를 안내하고 필요한 로컬 준비를 도울 수 있습니다. 저장소 접근이나 실행 도구가 없는 채팅에서는 [파일을 직접 받는 방법](GETTING_STARTED.md#파일을-직접-받아서-연결하기)을 사용합니다. **메시지를 보낸 것만으로 설치·모델 전환·자동 수집이 완료되는 것은 아닙니다.**

다운로드 없이 작업 방식만 먼저 적용할 수도 있지만, 역할별 문서·로컬 도구·기록까지 쓰려면 실행 host에서 저장소와 필요한 도구에 접근할 수 있어야 합니다. [전체 연결 항목과 현재 지원 범위](runtime/project-onboarding.md#기능별-연결-확인)를 확인하세요.

아래는 구현 범위와 상세 운영 문서입니다.

작업 분리·역할·완료 기준·검증·결과 보고는 공통 규칙으로 유지합니다. 에이전트 생성·전달·상태·결과 수신은 선택한 Orca/Paseo 환경의 기능을 사용하고, 개발 규칙과 테스트는 대상 프로젝트에서 적용합니다. 환경과 AI 공급자·모델은 별개입니다. [설계 결정과 기존 계획의 변경](runtime/architecture.md)을 참고하세요.

현재는 **환경 가이드 선택·연결 확인**, 단일 Codex 역할 실행·기록·집계, 관리 경로의 위임 한도, 명시적으로 수입한 실행 기록의 SQLite 대시보드를 제공합니다. Orca 네이티브 위임·결과 반환과 새 Codex worker의 대신 승인 적용은 제한된 실제 작업에서 확인했습니다. 닫힌 단일 native 턴의 배정별 토큰을 완료 기록에 저장하고, 지정된 DB에 중복 없이 연결해 요청·역할·작업별로 표시합니다. 조율자 등 누락된 역할의 전체 수집, 자동 업무 분해, Paseo 실제 위임, 복수 저장소 통합은 남아 있습니다. [하네스 개선 상태와 다음 작업](runtime/harness-roadmap.md)에 구현·운영 규칙·미검증을 구분했습니다.

학습 대화에는 튜터 지침과 공식 근거·연관 Q&A 기준을 제공합니다. 요청 라우팅은 별도 분류 AI 호출 없이 현재 AI의 판단을 추천·선택·역할 지침·명시 위임·취소/종료·완료 수집에 연결합니다. 필수 자료 부족을 표시하면 추천과 역할 목록 읽기를 건너뜁니다. 앱의 구조화된 상태 자동 전달·선택 UI·역할 자동 주입·현재 대화의 모델 전환·자동 위임은 아직 연결하지 않았습니다. 튜터의 대표 응답 자체 점검과 다섯 응답의 독립 콘텐츠 검토를 수행했으며 수정본은 자체 확인으로 구분합니다. 실사용 정확도·학습 효과·비용 절감은 미검증입니다. [통합 개발 순서](learning/development.md)를 기준으로 진행합니다.

수용한 worker 선택을 [기존 관리 실행기에 명시 연결](runtime/routing-dispatch.md)하는 경로도 제공합니다. 한 동의를 한 실행에 묶고 선택한 모델·effort를 정확히 전달하며 완료 시 요청·적용·실제 관측을 대조합니다. [실제 한 건](runtime/routing-integration-check.md)에서 역할 수신·Sol/medium 요청/실행환경 적용값·중복 재조회·정확한 종료를 확인했고, 별도 선택받은 [취소·IPC 시험](runtime/routing-cancellation-check.md)에서는 준비 신호·중지·정확한 종료·취소 replay를 확인했습니다. 공급자 실제 관측·토큰·세부 승인 영수증·앱 입력 hook은 미확인입니다.

기본 진입은 **지침이 연결된 현재 AI가 요청을 읽고 짧게 판단·제안하며 대화에서 선택을 받는 방식**입니다. 같은 AI의 역할은 필요한 지침을 읽어 적용합니다. native hook·별도 선택 UI를 선행 조건으로 두지 않으며, 해당 연결은 상태 자동 전달·측정 수집의 선택적 고도화입니다. 현재 대화의 모델/effort 전환에는 별도 공식 적용 경로가 필요합니다.

등록된 작업과 임시 자원으로 [마감 입력 초안](runtime/manifest-draft.md)을 만들고, 조율자가 수용한 요청은 [일괄 마감 명령](runtime/request-completion.md)으로 수집·전체 보관·사전 검증·정리·SQLite 마감을 연결할 수 있습니다. 초안 생성과 제품 수용·실행은 구분하며, 정리 정책은 Y/N 선택·기본 Y입니다. 실제 사용량은 확인한 구간만 집계합니다.

역할별 전문 스킬은 [운영·인계 가이드](skills/operations.md)에서 설치·작업별 선택·실제 사용 기록·문제 복구 순서로 연결합니다. 외부 원본의 고정 버전과 로컬 관리 상태를 사용하며, 문서 연결·설치·작업 적용·품질 검증을 구분합니다. 저장소 복제만으로 원본 라이브러리·활성 목록·주간 예약이 다른 설치에 자동 배포되지는 않습니다.

## 무엇을 바꾸면 되나요?

| 경로 | 용도 |
| --- | --- |
| [workflow.json](workflow.json) | 기존 단독 Codex 실행값: 역할별 모델, effort, sandbox, 역할 문서 경로 |
| [runtime/architecture.md](runtime/architecture.md) | 공통 규칙·환경·프로젝트 책임 경계와 계획 변경 |
| [runtime/environments/](runtime/environments/README.md), [scripts/startup.py](scripts/startup.py) | 세션 근거·연결 확인 후 Orca/Paseo 가이드 선택 |
| [projects/](projects/README.md), [templates/project.startup.json](templates/project.startup.json) | 상위 폴더와 자식 저장소의 프로젝트 지침 연결 |
| [roles/](roles/README.md) | 각 역할의 책임·결과물·검증 기준 |
| [learning/](learning/README.md), [통합 개발 순서](learning/development.md) | 튜터 사용법·출처 기준·심화 방식과 자체 평가·다음 작업 |
| [runtime/request-routing.md](runtime/request-routing.md), [짧은 진입 지침](runtime/routing-brief.md) | 직접 처리·역할/설정 추천·위임 제어·적용 범위·최소 토큰 원칙 |
| [scripts/request_router.py](scripts/request_router.py), [입출력 안내](runtime/request-router.md) | 짧은 목록과 구조화된 판단의 로컬 검사. 필수 자료 부족 시 추천 생략. 모델 호출·실행·설정 저장 없음 |
| [scripts/routing_choices.py](scripts/routing_choices.py), [선택 기록 안내](runtime/routing-choices.md) | 비공개 요청별 수용·거절·취소·만료와 선택한 역할 지침 준비. 앱의 자동 지침 주입은 미연결 |
| [scripts/routing_flow.py](scripts/routing_flow.py), [공통 진입 흐름](runtime/routing-flow.md) | 추천·선택·역할 지침·명시 위임·취소/종료·완료 수집/비교 자료 연결. 거절·실행·측정 중복 방지, 미지원 프로필 전환 구분 |
| [runtime/routing-dispatch.md](runtime/routing-dispatch.md), [실제 검증](runtime/routing-integration-check.md) | 수용한 worker 선택과 관리 실행의 opt-in 연결. 실제 요청/적용·재조회/종료 확인, 공급자 관측·토큰·앱 자동 입력은 미확인 |
| [scripts/compare_routing.py](scripts/compare_routing.py), [비교 안내](runtime/routing-comparison.md) | 기존 측정 파일의 직접/라우팅 한 쌍 비교. 누락·품질 미확인은 절감 판정 보류. 자동화 허용 없음 |
| [시간·단가 추정 안내](runtime/routing-metrics.md) | 선택적인 v2 입력의 요청 시간·관측 모델 단가 추정. 원본/요금 자동 탐색·실제 청구 인증 없음 |
| [공식 native 원본 연결 조사](runtime/native-source-access.md) | Orca 목록 API와 Codex thread/토큰 계약의 확인 범위, 현재 미확인인 export/bridge와 필요한 최소 연결 제안 |
| [짧은 운영 안내](runtime/routing-quickstart.md), [운영 점검](runtime/routing-operation-check.md), [비교·평가 준비 묶음](experiments/routing-pilot/README.md) | 최소 사용 흐름, 같은 AI의 CLI 점검, 네 가지 가상 입력·고정 완료 기준·평가 양식. 실제 비교 결과와 구분 |
| [skills/](skills/README.md), [역할 스킬 유지관리](runtime/skill-maintenance.md) | 역할별 적용 스킬, 고정 원본의 로컬 설치·선택, 주간 신규·인기·대체 검토와 복구 가능한 은퇴 |
| [스킬 운영·인계](skills/operations.md), [운영 준비 완료 기준](skills/rollout.md) | 작업별 진입 명령·새 설치 연결·실패 복구와 초기 구축의 마감 기준 |
| [Web 역할 시험 화면](examples/role-ui-trial/README.md), [시험 서버](scripts/role_ui_trial.py) | 가상 업무 화면·loopback API로 경계 데이터·초점·실패/재시도·중복 요청을 브라우저에서 재현 |
| [AGENTS.md](AGENTS.md), [principles/privacy.md](principles/privacy.md) | 모든 역할에 전달하는 공통 원칙 |
| [runtime/session-practices.md](runtime/session-practices.md) | 대화 분리·계획·캐시·검증의 실무 기준 |
| [runtime/guidance-maintenance.md](runtime/guidance-maintenance.md) | 공식 문서 주간 점검·변경안 제안; 로컬 Orca 예약 시험 완료, 자동 적용·병합 없음 |
| [templates/project.workflow.json](templates/project.workflow.json) | 대상 프로젝트의 `.ai-workflow.json`으로 사용할 설정 양식 |
| [schema/workflow.schema.json](schema/workflow.schema.json) | 설정 파일의 필드·타입·허용값 |
| [scripts/workflow.py](scripts/workflow.py) | 설정 검증, 적용값 확인, Codex 실행, 로컬 기록 집계 |
| [runtime/execution-records.md](runtime/execution-records.md) | 실제 사용량·진단 수집, 비공개 기록, 자료 전달, 집계 |
| [runtime/native-record.md](runtime/native-record.md), [scripts/native_record.py](scripts/native_record.py) | 명시한 Orca/Codex 기록의 승인/모델·세션 누적값·배정별 토큰과 비공개 JSON 저장 |
| [runtime/completion.md](runtime/completion.md) | 완료 알림의 단발성 기록·중복 방지, 보관본이 검증된 임시 폴더의 명시적 정리 |
| [runtime/workspace-lifecycle.md](runtime/workspace-lifecycle.md), [scripts/workspace_resources.py](scripts/workspace_resources.py) | 새 임시 폴더의 소유권 등록, 전체 보관본 검증과 정책에 따른 정리 |
| [runtime/completion-recovery.md](runtime/completion-recovery.md) | 선택 요청의 수집 상태·누락·미연결 영수증·조율자 관측 범위와 명시 재처리 |
| [runtime/request-completion.md](runtime/request-completion.md), [scripts/complete_request.py](scripts/complete_request.py) | 검증된 요청의 수집·보관·DB 사전 검증·정리·마감을 한 명령으로 연결 |
| [runtime/manifest-draft.md](runtime/manifest-draft.md), [scripts/draft_request_completion.py](scripts/draft_request_completion.py) | 등록 정보와 후속 배정 계보로 검토 가능한 마감 manifest 초안 생성 |
| [runtime/trace-viewer.md](runtime/trace-viewer.md), [runtime/trace-closeout.md](runtime/trace-closeout.md) | 요청→역할→작업 흐름, 토큰 표시·상세, 결과 파일·검증 근거와 마감 연결 |
| [runtime/trace-usage.md](runtime/trace-usage.md), [scripts/trace_usage.py](scripts/trace_usage.py) | 원래 actor를 보존하는 배정별 토큰 DB 수입, 물리 구간 중복·충돌 검사와 집계 |
| [runtime/trace-skills.md](runtime/trace-skills.md), [scripts/trace_skills.py](scripts/trace_skills.py) | 역할별 스킬 선택·참고·적용·실행 기록과 읽기 전용 라이브러리·정기 검토 표시 |
| [runtime/trace-observations.md](runtime/trace-observations.md) | 실행자·제품 담당자·검증 대상·판정자를 구분하는 관측 생성과 저장 전 검사 |
| [runtime/coordinator-usage.md](runtime/coordinator-usage.md) | 배정 ID 없는 조율자 단일 턴의 명시 연결·토큰 수집, 부분 측정 구분 |
| [runtime/request-usage-collection.md](runtime/request-usage-collection.md), [scripts/collect_request_usage.py](scripts/collect_request_usage.py) | 여러 명시 조율자 턴·확인된 worker 원본의 검증·원자적 수입, Stop 수신과 원본 회수 구분 |
| [harness.json](harness.json), [runtime/delegation.md](runtime/delegation.md) | 임시 공간 자동 정리 Y/N(기본 Y), 작업별 동시·누적 위임 한도와 native 호출 전 예약 |
| [runtime/harness.md](runtime/harness.md), [scripts/prepare_handoff.py](scripts/prepare_handoff.py) | 작업 크기에 맞는 조율, 중복 대기 처리 규칙, 명시 입력 파일의 인계 준비 |
| [runtime/approval-waits.md](runtime/approval-waits.md), [scripts/approval_wait.py](scripts/approval_wait.py) | 승인·질문 대기 관측, 확인된 요청의 중복 억제, 알림 전달 확인과 단발성 Orca 조회 |
| [runtime/permission-policy.md](runtime/permission-policy.md), [권한 인계 양식](templates/execution-permissions.md) | 공식 운영 사례, 사전 허용 범위·예외 승인·실제 적용 확인. 권한을 자동 부여하는 설정 파일은 아님 |
| [짧은 worker IPC 지침](runtime/orca-ipc-brief.md), [checkpoint 양식](templates/orca-ipc-checkpoint.md) | CLI 조율이 필요한 worker의 정식 승인·재시도 구분. 필요한 때만 참조하며 실제 권한을 부여하지 않음 |
| [runtime/storage-options.md](runtime/storage-options.md) | Markdown·JSON/JSONL·SQLite·MongoDB·PostgreSQL의 역할과 선택 기준 |
| [runtime/orchestration-contract.md](runtime/orchestration-contract.md) | 보존한 단독 Codex 자동 조율 프로토타입 설계. 구현 보류 |
| [experiments/](experiments/README.md) | 토큰 효율 개선 절차와 공개용 실험 요약·재현 방법 |
| [templates/](templates/README.md) | 작업 입력·담당자 결과·전체 보고의 공통 양식 |
| [examples/task-contract.md](examples/task-contract.md) | 개발·글쓰기 작업의 가상 입력과 결과 |

## 사용하기

처음 운영할 때는 [짧은 사용 흐름](runtime/routing-quickstart.md), 비용·품질을 평가할 때는 [비교 준비 묶음](experiments/routing-pilot/README.md)을 확인합니다. 일반 입력에 이 자료를 모두 주입하지 않습니다.

### 처리 방식을 직접 제어하기

[요청 라우팅과 사용자 제어](runtime/request-routing.md)에 직접 처리·제안 후 선택·허용 범위 내 자동 처리의 의미를 정의했습니다. “이번에는 바로 답해”, “튜터 방식으로 하되 새 에이전트는 만들지 마”, “이 대화에서는 모델과 effort를 고정해”처럼 역할·설정·위임과 적용 범위를 구분합니다. 작은 요청과 후속 질문에는 불필요한 제안을 반복하지 않습니다.

현재는 **사용자 제어 계약·[추천 검사](runtime/request-router.md)·[공통 진입 흐름](runtime/routing-flow.md)**을 제공합니다. `python3 scripts/request_router.py catalog`로 짧은 역할·프로필 목록을 확인하고 `python3 scripts/request_router.py route < examples/request-route.json`으로 현재 AI의 판단을 검사합니다. 추천 검사 자체는 실행하지 않으며 공통 진입점이 요청별 선택·역할 지침과 별도 수용한 관리 실행·완료 수집을 연결합니다. 앱 입력 자동 연결·선택 UI·현재 대화의 모델 전환과 실제 추천 품질 검증은 남아 있습니다. 새 제어 개념을 기존 `harness.json`의 지원 필드로 해석하지 않습니다.

라우팅 자체의 토큰 비용 최소화를 핵심 제약으로 둡니다. 기본 경로는 라우팅 전용 추가 모델 호출 없이 확정 제어·기존 선택을 재사용하고, 필요한 의미 판단은 현재 AI의 응답에서 수행하도록 설계합니다. 추천이 없으면 바로 본문을 답하며 모든 역할 문서를 매번 읽지 않습니다. 실제 절감 효과는 아직 미측정입니다.

튜터 개발과 추천 평가·Orca 입력 연결·사용자 선택·위임/모델 전환·비용 비교는 [통합 개발 순서](learning/development.md)에 따라 단계적으로 진행합니다.

[Orca 안의 Codex 입력 어댑터](runtime/orca-routing-input.md)는 선택적 연결입니다. 한정된 프로젝트 시험에서 실제 수신·공식 검토 경로를 확인하지 못해 시험 정의를 제거했습니다. 현재 AI의 명시 호출형 운영은 유지하며 native 입력 자동 연결은 보류합니다.

[사용자 선택 기록](runtime/routing-choices.md)은 요청·추천 버전을 대조해 수용·거절·취소를 보관합니다. 수용한 현재 AI용 역할의 지침만 준비하고, 명시한 후속 대화에는 역할 본문을 다시 읽지 않고 선택을 재사용할 수 있습니다. 앱에 주입하거나 모델·effort·위임을 실행하지 않습니다. 입력 어댑터와 선택 UI의 자동 연결은 남아 있습니다.

[공통 진입 흐름](runtime/routing-flow.md)은 현재 AI의 해석을 위 선택 기록·역할 지침·관리 위임에 연결합니다. 일반 입력에는 도구 실행을 요구하지 않으며, 거절한 추천·역할 본문·worker 실행의 반복을 막습니다. 연결된 worker의 명시 취소는 guarded stop과 정확한 종료 조회를 사용하며 한정된 실제 경로를 확인했습니다. 현재 대화의 모델 변경은 수동 설정으로 구분하고 앱 자동 연결은 미검증으로 유지합니다.

### 개념을 배우고 싶을 때

“이게 무슨 개념이야?” 같은 학습 요청은 **[학습 튜터(`tutor`)](learning/README.md)**라는 별도 진입점으로 처리합니다. 쉬운 설명·핵심부터 시작해 스티커 메모, 제1원리, 파인만 기법, 한 장 학습 시트, 마인드맵·도식으로 전환합니다. 이어서 궁금할 만한 **연관 질문과 답변**도 함께 제공하며, **공식 문서를 우선 확인해 핵심 설명과 각 답변에 근거 링크**를 연결합니다. 재구성한 설명·비유와 원문 인용을 구분합니다.

이 저장소에서는 `AGENTS.md`가 튜터 지침을 연결합니다. 다른 대화·프로젝트에는 튜터 문서를 명시적으로 연결하세요. 필요한 경우 개념 해설·학습 시각화·연관 질문 해설 자료를 병렬로 준비하고 튜터가 통합합니다. 앱의 전역 자동 라우팅이나 지속 문답 실행기를 설치한 것은 아닙니다. [사용 예시·병렬 조건·구현 범위](learning/README.md)를 확인하세요.

### 프로젝트 작업을 시작할 때

먼저 프로젝트를 명시하고 환경 가이드를 선택합니다. 이 명령은 모델을 호출하거나 앱을 시작하지 않습니다.

```sh
python3 scripts/startup.py --project /path/to/project
```

세션 정보가 없거나 충돌하면 `--environment orca` 또는 `--environment paseo`로 명시합니다. 선택한 환경의 연결 실패는 중단하며 다른 앱으로 fallback하지 않습니다. [시작 설정·확인 수준·실패 처리](runtime/environments/README.md)를 읽은 뒤 반환된 환경 가이드와 프로젝트 지침을 적용하세요. 앱이 이 파일들을 자동으로 읽거나 현재 프로젝트에 바인딩됐다고 가정하지 않습니다.

**Orca + Codex로 사람의 반복 승인 개입을 줄여 위임하려면 ‘대신 승인(Auto)’ 활성화와 새 worker의 실제 적용 확인을 필수 준비로 합니다.** 현재 채팅의 모드만으로 자식의 설정을 판단하지 않습니다. 수동 승인 운영은 별도로 명시하며, 설정 변경 범위·검사·재사용 기준은 [실행 권한 정책](runtime/permission-policy.md)을 따릅니다. `startup.py` 자체는 승인 모드나 모델·effort를 설정·검증하지 않습니다.

다음 작업에는 [운영 기준](runtime/harness.md)을 적용합니다. 같은 파일 변경은 한 담당자에게 묶고, 독립 작업은 요청별 한도 안에서 배정합니다. 준비 자료 재사용, 실제 모델·effort 대조, 필요한 범위의 검증, 결과 전달과 자원 정리를 함께 기록합니다. [관리 위임 경로](runtime/delegation.md)는 작업별 동시·누적 한도를 예약하지만 직접 CLI나 다른 조율자의 실행까지 차단하지는 않습니다.

### 기존 단독 Codex 실행 도구

아래는 명시적으로 선택하는 단독 CLI 경로입니다. Orca/Paseo 연결이 실패했을 때 자동 대체하는 경로가 아닙니다. 역할별 모델·effort·권한·지침을 `workflow.json`에서 정하고 필요한 값을 프로젝트 설정에서 덮어씁니다.

Python 3.10 이상이 필요하며 추가 Python 패키지는 없습니다. 실제 실행에는 사용할 모델에 접근 가능한 Codex CLI와 해당 CLI의 로그인 설정이 필요합니다. 실행 옵션은 Codex CLI 0.167.0에서 확인했습니다.

저장소를 복제한 뒤 아래 명령을 **ai-workflow 디렉터리에서** 실행합니다. `../my-project`는 실제 작업할 프로젝트 경로로 바꿉니다. 프로젝트에 `.ai-workflow.json`이 없으면 공통 설정을 그대로 사용합니다.

```sh
git clone https://github.com/hyun-seung/ai-workflow.git
cd ai-workflow
python3 scripts/workflow.py check --project ../my-project
```

대상 프로젝트의 `.ai-workflow.json`에 다음 내용을 두면 조율자의 effort가 `medium`으로 바뀝니다. 다른 역할은 공통 설정을 유지합니다.

```json
{
  "version": 1,
  "roles": {
    "coordinator": {"effort": "medium"}
  }
}
```

실제 호출 없이 선택한 역할의 적용값과 명령을 확인합니다.

```sh
python3 scripts/workflow.py run --project ../my-project --role coordinator --task "이 프로젝트의 작업을 나눠줘" --dry-run
```

`--dry-run`을 제거하면 설정된 모델로 실제 실행하며 계정 사용량이 발생합니다. 개발 역할의 기본 sandbox는 `workspace-write`이므로 대상 프로젝트 파일을 수정할 수 있습니다.

```sh
python3 scripts/workflow.py run --project ../my-project --role backend --task "완료 여부로 목록을 필터링하는 API를 구현하고 테스트해줘"
```

작업 역할 ID는 `coordinator`, `architect`, `designer`, `backend`, `frontend`, `verifier`입니다. 학습 역할은 `tutor`, `learning-concepts`, `learning-visuals`, `learning-connections`입니다. 역할 추가는 `workflow.json`에서, 기존 역할의 프로젝트별 변경은 `.ai-workflow.json`에서 합니다.

작업에 필요한 작은 파일이나 줄 범위가 이미 정해졌다면 `--context-file src/example.py:10:40`으로 내용을 함께 전달할 수 있습니다. 경로는 대상 프로젝트 기준이며 자동 탐색하지 않습니다. 충분한 자료가 전달되면 중복 읽기를 줄이도록 지시하지만, 토큰 절감은 실제 사용량과 검증 결과로 확인해야 합니다. [범위·제한과 비교 방법](runtime/execution-records.md)을 참고하세요.

[컨텍스트 전달 최적화 실험 001](experiments/001-context-delivery.md)에는 파일 참조와 직접 전달을 비교한 관측 결과와 재현 절차를 정리했습니다. 전체 토큰·비캐시 입력·시간을 구분하고, 품질 확인과 캐시·감사 한계를 함께 기록합니다.

## 설정 적용 순서

1. 템플릿의 `workflow.json`
2. 대상 프로젝트의 `.ai-workflow.json` — 존재할 때 자동 적용
3. `--local-config`로 명시한 별도 JSON 파일 — 개인별 변경에 선택적으로 사용

뒤의 설정이 같은 역할의 필드를 덮어씁니다. 공통 `instructions`는 순서대로 추가하고, 역할별 `instructions`는 지정한 배열로 교체합니다. 파일 경로는 각각의 설정 파일이 있는 디렉터리를 기준으로 해석합니다. 필드 규칙과 예시는 [실행 설정](runtime/profiles.md)에 있습니다.

모델·effort는 CLI 옵션으로 전달하고, 공통·프로젝트·역할 문서는 작업 요청과 함께 표준 입력으로 전달합니다. Codex 자체의 기존 프로젝트 지침과 사용자·조직 정책도 적용됩니다. 이 템플릿은 해당 설정을 변경하거나 승인 절차를 우회하지 않습니다.

## 현재 동작 범위

- 세션 근거 또는 명시적 설정과 연결 조회로 Orca/Paseo 가이드를 선택합니다. 프로젝트 상위·자식 저장소의 지침 경로를 반환하며, 실제 네이티브 에이전트 실행이나 프로젝트 바인딩 완료를 뜻하지 않습니다.
- 설정 파일 검증, 모델·effort·sandbox 전달, 지침 파일 로딩, 단일 역할 실행을 제공합니다.
- 실행별 실제 토큰 사용량 수집, 선택적 JSON 기록, 상위 작업·작업 유형 연결, 선택적 실행 시간 제한을 제공합니다. 실패·중단·시간 초과와 미측정을 구분합니다.
- 저장된 기록의 역할·요청 모델·effort별 집계와 작업 ID 필터를 제공합니다. 입력 문자 수와 완료된 도구 작업 수를 본문 없이 기록합니다. 모델 호출 횟수나 파일 읽기 횟수는 측정하지 않습니다.
- Orca/Codex의 확인된 단일 배정 구간은 요청·역할·task/dispatch·세션과 연결해 토큰을 수집합니다. 세션 누적 참고값과 구분하고 완료 재처리 시 기록을 재사용합니다. 여러 native 턴의 한 배정과 도구별 토큰 귀속은 지원하지 않습니다.
- 요청별 대시보드는 공통 역할·작업명, 명시 계획·지시·실제 명령 상세와 토큰 표시를 제공합니다. `import-usage` 또는 완료 처리의 `--trace-db`로 수집 JSON을 연결하며 미측정값을 0으로 채우지 않습니다. 결과·검증 파일은 해시로 연결하고 수집 범위와 마감 판정을 구분합니다.
- 등록된 로컬 Orca/Codex 요청의 마감 초안 생성과 명시 일괄 마감을 제공합니다. 모든 임시 자원을 먼저 보관하고 별도 SQLite 사본에서 검증한 뒤 실효 정책에 따라 정리합니다. 실제 종료·프로필·원본 구간을 확인하며 전체 요청 사용량의 완전 수집을 뜻하지 않습니다.
- 조율자의 자동 업무 분배, 역할 간 인계, 결과 보고서 자동 생성, 토큰 예산 강제는 아직 구현하지 않았습니다. 실행기 밖의 사용량이나 전체 업무의 누락된 기록을 자동 수집하지 않습니다.
- `check`와 `--dry-run`은 설정만 확인합니다. 모델 접근 권한·effort 지원 여부·응답 품질은 실제 실행 시 확인해야 합니다.
- 기본 read-only 역할에서 파일을 생성하는 테스트나 시안 작업이 필요하면 프로젝트 설정에서 해당 역할의 sandbox를 `workspace-write`로 명시합니다.
- [작업 입출력 양식](templates/README.md)은 현재 Markdown 기반의 권장 계약입니다. 작업 요청과 함께 전달할 수 있지만, 실행기가 필드·완료 상태·예산을 자동 검증하지는 않습니다.

## 비공개 정보와 기록

실행기는 선택한 지침과 작업 요청을 AI 도구에 전달하므로 필요한 파일만 지정합니다. 개인 설정은 저장소 밖에 두고 `--local-config`로 연결할 수 있습니다. 인증 정보는 설정 파일에 넣지 않습니다.

단독 Codex 실행 도구는 실행 기록을 기본적으로 저장하지 않습니다. `--records-dir`에 사용자가 선택한 비공개 경로를 지정하면 실행별 JSON을 남깁니다. 해당 경로는 템플릿·대상 프로젝트·Git 작업 트리 밖이어야 합니다. `--task-id`, `--parent-task-id`, `--task-type`으로 업무를 연결하고 `--result-ref`로 별도 결과 보고서를 참조할 수 있습니다. 상세 사용법과 기록 형식은 [실행 기록](runtime/execution-records.md)에 있습니다. Orca/Codex의 opt-in Stop 수집과 SQLite 기록은 [완료 hook](runtime/completion-hook.md)의 별도 비공개 설정을 사용합니다.

기록이 쌓이면 모델 호출 없이 집계합니다. 경로는 실제 비공개 위치로 바꿉니다. `--parent-task-id WORK-001` 또는 `--task-id TASK-001`로 범위를 좁히고, `--json`으로 구조화된 결과를 출력할 수 있습니다.

```sh
python3 scripts/workflow.py summary --records-dir /path/to/private/ai-workflow-runs
```

단독 실행 기록 JSON에는 요청 원문·코드·모델 응답·도구 로그 본문을 자동 저장하지 않습니다. 최종 에이전트 메시지는 stdout, 사용량 요약과 CLI 오류는 stderr로 출력합니다. `--ephemeral`로 Codex 세션 기록의 영구 저장을 비활성화하지만 도구·서비스 전체의 보관 정책을 대신하지는 않습니다. 별도 수입한 transcript나 결과·보관 파일에는 원문과 개인 경로가 포함될 수 있으므로 각 수입·보관 정책을 적용합니다. `check`와 `--dry-run` 출력에도 로컬 경로가 있으므로 공개 전에 확인합니다.

`--timeout-seconds`는 선택적 경과 시간 제한입니다. 토큰 한도와 다르며 종료 정리 시간만큼 초과할 수 있습니다. macOS/Linux에서 Ctrl+C·SIGTERM·시간 초과 시 실행 프로세스 그룹을 정리합니다. 업무 완료 기준의 충족 여부는 별도 결과·검증 보고서에서 판단합니다.

작업 텍스트가 셸 이력에 남는 것을 피하려면 `--task -`로 표준 입력에서 전달할 수 있습니다. `.gitignore`와 로컬 보관만으로 접근·전송이 차단되지는 않습니다. 상세 기준은 [공개·비공개 정보 관리](principles/privacy.md)에 있습니다.

## 검증

```sh
python3 -m unittest discover -s tests -v
```

설정 우선순위, 경로 경계, dry-run, 대체 Codex 프로세스의 명령·입력·종료 코드 전달과 사용량 수집을 확인합니다. 정상·실패·중단·시간 초과·중복·저장 오류 외에 집계의 미측정 처리, 충돌 기록 제외, 자료 전달 범위와 민감한 본문 제외를 검증합니다. 실제 모델 호출과 업무 품질 검증은 이 테스트에 포함하지 않습니다.

- [요청 실행 기록 DB·로컬 화면](runtime/trace-viewer.md): 명시적 bundle/Orca snapshot 수입과 읽기 전용 조회.
