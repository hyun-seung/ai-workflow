# ai-workflow

**공통 작업 규칙·실행 환경 연결·프로젝트 지침을 분리해 재사용하는 AI 작업 체계입니다.**

작업 분리·역할·완료 기준·검증·결과 보고는 공통 규칙으로 유지합니다. 에이전트 생성·전달·상태·결과 수신은 선택한 Orca/Paseo 환경의 기능을 사용하고, 개발 규칙과 테스트는 대상 프로젝트에서 적용합니다. 환경과 AI 공급자·모델은 별개입니다. [설계 결정과 기존 계획의 변경](runtime/architecture.md)을 참고하세요.

현재는 **환경 가이드 선택·연결 확인**, 단일 Codex 역할 실행·기록·집계, 관리 경로의 위임 한도, 명시적으로 수입한 실행 기록의 SQLite 대시보드를 제공합니다. Orca 네이티브 위임·결과 반환과 새 Codex worker의 대신 승인 적용은 제한된 실제 작업에서 확인했습니다. 닫힌 단일 native 턴의 배정별 토큰을 완료 기록에 저장하고, 지정된 DB에 중복 없이 연결해 요청·역할·작업별로 표시합니다. 조율자 등 누락된 역할의 전체 수집, 자동 업무 분해, Paseo 실제 위임, 복수 저장소 통합은 남아 있습니다. [하네스 개선 상태와 다음 작업](runtime/harness-roadmap.md)에 구현·운영 규칙·미검증을 구분했습니다.

## 무엇을 바꾸면 되나요?

| 경로 | 용도 |
| --- | --- |
| [workflow.json](workflow.json) | 기존 단독 Codex 실행값: 역할별 모델, effort, sandbox, 역할 문서 경로 |
| [runtime/architecture.md](runtime/architecture.md) | 공통 규칙·환경·프로젝트 책임 경계와 계획 변경 |
| [runtime/environments/](runtime/environments/README.md), [scripts/startup.py](scripts/startup.py) | 세션 근거·연결 확인 후 Orca/Paseo 가이드 선택 |
| [projects/](projects/README.md), [templates/project.startup.json](templates/project.startup.json) | 상위 폴더와 자식 저장소의 프로젝트 지침 연결 |
| [roles/](roles/README.md) | 각 역할의 책임·결과물·검증 기준 |
| [skills/](skills/README.md), [역할 스킬 유지관리](runtime/skill-maintenance.md) | 역할별 적용 스킬, 고정 원본의 로컬 설치·선택, 주간 신규·인기·대체 검토와 복구 가능한 은퇴 |
| [AGENTS.md](AGENTS.md), [principles/privacy.md](principles/privacy.md) | 모든 역할에 전달하는 공통 원칙 |
| [runtime/session-practices.md](runtime/session-practices.md) | 대화 분리·계획·캐시·검증의 실무 기준 |
| [runtime/guidance-maintenance.md](runtime/guidance-maintenance.md) | 공식 문서 주간 점검·변경안 제안; 로컬 Orca 예약 시험 완료, 자동 적용·병합 없음 |
| [templates/project.workflow.json](templates/project.workflow.json) | 대상 프로젝트의 `.ai-workflow.json`으로 사용할 설정 양식 |
| [schema/workflow.schema.json](schema/workflow.schema.json) | 설정 파일의 필드·타입·허용값 |
| [scripts/workflow.py](scripts/workflow.py) | 설정 검증, 적용값 확인, Codex 실행, 로컬 기록 집계 |
| [runtime/execution-records.md](runtime/execution-records.md) | 실제 사용량·진단 수집, 비공개 기록, 자료 전달, 집계 |
| [runtime/native-record.md](runtime/native-record.md), [scripts/native_record.py](scripts/native_record.py) | 명시한 Orca/Codex 기록의 승인/모델·세션 누적값·배정별 토큰과 비공개 JSON 저장 |
| [runtime/completion.md](runtime/completion.md) | 완료 알림의 단발성 기록·중복 방지, 보관본이 검증된 임시 폴더의 명시적 정리 |
| [runtime/trace-viewer.md](runtime/trace-viewer.md), [runtime/trace-closeout.md](runtime/trace-closeout.md) | 요청→역할→작업 흐름, 토큰 표시·상세, 결과 파일·검증 근거와 마감 연결 |
| [runtime/trace-usage.md](runtime/trace-usage.md), [scripts/trace_usage.py](scripts/trace_usage.py) | 원래 actor를 보존하는 배정별 토큰 DB 수입, 물리 구간 중복·충돌 검사와 집계 |
| [runtime/trace-observations.md](runtime/trace-observations.md) | 실행자·제품 담당자·검증 대상·판정자를 구분하는 관측 생성과 저장 전 검사 |
| [runtime/coordinator-usage.md](runtime/coordinator-usage.md) | 배정 ID 없는 조율자 단일 턴의 명시 연결·토큰 수집, 부분 측정 구분 |
| [harness.json](harness.json), [runtime/delegation.md](runtime/delegation.md) | 임시 공간 자동 정리 Y/N(기본 Y), 작업별 동시·누적 위임 한도와 native 호출 전 예약 |
| [runtime/harness.md](runtime/harness.md), [scripts/prepare_handoff.py](scripts/prepare_handoff.py) | 작업 크기에 맞는 조율, 중복 대기 처리 규칙, 명시 입력 파일의 인계 준비 |
| [runtime/approval-waits.md](runtime/approval-waits.md), [scripts/approval_wait.py](scripts/approval_wait.py) | 승인·질문 대기 관측, 확인된 요청의 중복 억제, 알림 전달 확인과 단발성 Orca 조회 |
| [runtime/permission-policy.md](runtime/permission-policy.md), [권한 인계 양식](templates/execution-permissions.md) | 공식 운영 사례, 사전 허용 범위·예외 승인·실제 적용 확인. 권한을 자동 부여하는 설정 파일은 아님 |
| [runtime/storage-options.md](runtime/storage-options.md) | Markdown·JSON/JSONL·SQLite·MongoDB·PostgreSQL의 역할과 선택 기준 |
| [runtime/orchestration-contract.md](runtime/orchestration-contract.md) | 보존한 단독 Codex 자동 조율 프로토타입 설계. 구현 보류 |
| [experiments/](experiments/README.md) | 토큰 효율 개선 절차와 공개용 실험 요약·재현 방법 |
| [templates/](templates/README.md) | 작업 입력·담당자 결과·전체 보고의 공통 양식 |
| [examples/task-contract.md](examples/task-contract.md) | 개발·글쓰기 작업의 가상 입력과 결과 |

## 사용하기

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

역할 ID는 `coordinator`, `architect`, `designer`, `backend`, `frontend`, `verifier`입니다. 역할 추가는 `workflow.json`에서, 기존 역할의 프로젝트별 변경은 `.ai-workflow.json`에서 합니다.

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
- 조율자의 자동 업무 분배, 역할 간 인계, 결과 보고서 자동 생성, 토큰 예산 강제는 아직 구현하지 않았습니다. 실행기 밖의 사용량이나 전체 업무의 누락된 기록을 자동 수집하지 않습니다.
- `check`와 `--dry-run`은 설정만 확인합니다. 모델 접근 권한·effort 지원 여부·응답 품질은 실제 실행 시 확인해야 합니다.
- 기본 read-only 역할에서 파일을 생성하는 테스트나 시안 작업이 필요하면 프로젝트 설정에서 해당 역할의 sandbox를 `workspace-write`로 명시합니다.
- [작업 입출력 양식](templates/README.md)은 현재 Markdown 기반의 권장 계약입니다. 작업 요청과 함께 전달할 수 있지만, 실행기가 필드·완료 상태·예산을 자동 검증하지는 않습니다.

## 비공개 정보와 기록

실행기는 선택한 지침과 작업 요청을 AI 도구에 전달하므로 필요한 파일만 지정합니다. 개인 설정은 저장소 밖에 두고 `--local-config`로 연결할 수 있습니다. 인증 정보는 설정 파일에 넣지 않습니다.

실행 기록은 기본적으로 저장하지 않습니다. `--records-dir`에 사용자가 선택한 비공개 경로를 지정하면 실행별 JSON을 남깁니다. 해당 경로는 템플릿·대상 프로젝트·Git 작업 트리 밖이어야 합니다. `--task-id`, `--parent-task-id`, `--task-type`으로 업무를 연결하고 `--result-ref`로 별도 결과 보고서를 참조할 수 있습니다. 상세 사용법과 기록 형식은 [실행 기록](runtime/execution-records.md)에 있습니다.

기록이 쌓이면 모델 호출 없이 집계합니다. 경로는 실제 비공개 위치로 바꿉니다. `--parent-task-id WORK-001` 또는 `--task-id TASK-001`로 범위를 좁히고, `--json`으로 구조화된 결과를 출력할 수 있습니다.

```sh
python3 scripts/workflow.py summary --records-dir /path/to/private/ai-workflow-runs
```

요청 원문·코드·모델 응답·도구 로그 본문은 자동 저장하지 않습니다. 최종 에이전트 메시지는 stdout, 사용량 요약과 CLI 오류는 stderr로 출력합니다. `--ephemeral`로 Codex 세션 기록의 영구 저장을 비활성화하지만 도구·서비스 전체의 보관 정책을 대신하지는 않습니다. `check`와 `--dry-run` 출력에도 로컬 경로가 있으므로 공개 전에 확인합니다.

`--timeout-seconds`는 선택적 경과 시간 제한입니다. 토큰 한도와 다르며 종료 정리 시간만큼 초과할 수 있습니다. macOS/Linux에서 Ctrl+C·SIGTERM·시간 초과 시 실행 프로세스 그룹을 정리합니다. 업무 완료 기준의 충족 여부는 별도 결과·검증 보고서에서 판단합니다.

작업 텍스트가 셸 이력에 남는 것을 피하려면 `--task -`로 표준 입력에서 전달할 수 있습니다. `.gitignore`와 로컬 보관만으로 접근·전송이 차단되지는 않습니다. 상세 기준은 [공개·비공개 정보 관리](principles/privacy.md)에 있습니다.

## 검증

```sh
python3 -m unittest discover -s tests -v
```

설정 우선순위, 경로 경계, dry-run, 대체 Codex 프로세스의 명령·입력·종료 코드 전달과 사용량 수집을 확인합니다. 정상·실패·중단·시간 초과·중복·저장 오류 외에 집계의 미측정 처리, 충돌 기록 제외, 자료 전달 범위와 민감한 본문 제외를 검증합니다. 실제 모델 호출과 업무 품질 검증은 이 테스트에 포함하지 않습니다.

- [요청 실행 기록 DB·로컬 화면](runtime/trace-viewer.md): 명시적 bundle/Orca snapshot 수입과 읽기 전용 조회.
