# ai-workflow

**설정 파일과 역할 문서를 조합해 프로젝트마다 적용하는 AI 작업 템플릿입니다.**

역할별 모델·effort·권한·지침 파일을 `workflow.json`에서 정하고, 프로젝트에서는 필요한 값만 덮어씁니다. 실행기는 이 설정과 역할 문서를 읽어 선택한 역할을 Codex CLI로 실행합니다. 현재 버전은 한 번에 한 역할을 실행하며, 전체 업무의 자동 분배는 후속 범위입니다.

## 무엇을 바꾸면 되나요?

| 경로 | 용도 |
| --- | --- |
| [workflow.json](workflow.json) | 공통 실행값: 역할별 모델, effort, sandbox, 역할 문서 경로 |
| [roles/](roles/README.md) | 각 역할의 책임·결과물·검증 기준 |
| [AGENTS.md](AGENTS.md), [principles/privacy.md](principles/privacy.md) | 모든 역할에 전달하는 공통 원칙 |
| [templates/project.workflow.json](templates/project.workflow.json) | 대상 프로젝트의 `.ai-workflow.json`으로 사용할 설정 양식 |
| [schema/workflow.schema.json](schema/workflow.schema.json) | 설정 파일의 필드·타입·허용값 |
| [scripts/workflow.py](scripts/workflow.py) | 설정 검증, 적용값 확인, Codex 실행, 로컬 기록 집계 |
| [runtime/execution-records.md](runtime/execution-records.md) | 실제 사용량·진단 수집, 비공개 기록, 자료 전달, 집계 |
| [templates/](templates/README.md) | 작업 입력·담당자 결과·전체 보고의 공통 양식 |
| [examples/task-contract.md](examples/task-contract.md) | 개발·글쓰기 작업의 가상 입력과 결과 |

## 사용하기

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


## 설정 적용 순서

1. 템플릿의 `workflow.json`
2. 대상 프로젝트의 `.ai-workflow.json` — 존재할 때 자동 적용
3. `--local-config`로 명시한 별도 JSON 파일 — 개인별 변경에 선택적으로 사용

뒤의 설정이 같은 역할의 필드를 덮어씁니다. 공통 `instructions`는 순서대로 추가하고, 역할별 `instructions`는 지정한 배열로 교체합니다. 파일 경로는 각각의 설정 파일이 있는 디렉터리를 기준으로 해석합니다. 필드 규칙과 예시는 [실행 설정](runtime/profiles.md)에 있습니다.

모델·effort는 CLI 옵션으로 전달하고, 공통·프로젝트·역할 문서는 작업 요청과 함께 표준 입력으로 전달합니다. Codex 자체의 기존 프로젝트 지침과 사용자·조직 정책도 적용됩니다. 이 템플릿은 해당 설정을 변경하거나 승인 절차를 우회하지 않습니다.

## 현재 동작 범위

- 설정 파일 검증, 모델·effort·sandbox 전달, 지침 파일 로딩, 단일 역할 실행을 제공합니다.
- 실행별 실제 토큰 사용량 수집, 선택적 JSON 기록, 상위 작업·작업 유형 연결, 선택적 실행 시간 제한을 제공합니다. 실패·중단·시간 초과와 미측정을 구분합니다.
- 저장된 기록의 역할·요청 모델·effort별 집계와 작업 ID 필터를 제공합니다. 입력 문자 수와 완료된 도구 작업 수를 본문 없이 기록합니다. 모델 호출 횟수나 파일 읽기 횟수는 측정하지 않습니다.
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
