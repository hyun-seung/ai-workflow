# 다른 앱에서 지침 적용 확인

이 절차는 **기존 프로젝트에서 공통 지침을 읽는지 확인하는 읽기 전용 시험**이다. 앱 설치·접속 성공, 파일을 명시적으로 읽은 결과, 시작 시 자동 지침 로드는 서로 다른 근거다. 결과 자동 수집과도 구분한다. 이 문서가 있다는 것만으로 실제 앱 시험을 통과했다고 보지 않는다.

## 대상 준비

- 기존 프로젝트 폴더와 사용할 ai-workflow 커밋을 정한다. 다른 프로젝트에서는 공통 저장소의 실제 접근 가능한 절대 경로를 전달한다.
- 원래 프로젝트의 `AGENTS.md`와 사용자 변경은 보존한다. 공통 문서 전체를 복제하거나 전역 설정에 붙이지 않는다. 연결 파일을 수정해야 한다면 정확한 위치와 내용을 먼저 검토한다.
- 앱에 기존 로컬 폴더가 연결돼 있는지 확인한다. 같은 이름의 프로젝트라도 원격 host·폴더가 다르면 재사용하지 않는다. 새 프로젝트 등록·대화/에이전트 생성·worktree 생성이 금지된 업무라면 그 범위를 먼저 해결한다.
- 이미 진행 중인 다른 대화에 시험을 보내거나 중단하지 않는다. 승인된 시험 세션 하나만 사용하고, 모델·권한을 더 넓게 바꾸지 않는다.

## Paseo

설치 버전의 `--help`와 [공식 CLI 문서](https://paseo.sh/docs/cli.md)를 대조한다. 최소 조회는 다음과 같다. `--host`에는 실제 선택한 daemon의 확인된 주소를 지정한다.

```sh
paseo --version
paseo status --json
paseo project ls --host 127.0.0.1:6767 --json
paseo workspace ls --host 127.0.0.1:6767 --json
```

위 주소는 로컬 연결의 가상 예시다. 실제 응답의 serverId·desktopManaged·connectedDaemon과 workspace의 cwd를 비공개로 대조한다. 기본 로컬 조회를 사용자가 보고 있는 원격 host의 결과로 간주하지 않는다. daemon을 재시작하거나 새 host를 등록해 연결 확인을 대신하지 않는다.

기존 폴더 등록과 시험 에이전트 실행이 승인된 경우에만 `paseo project create <기존 경로>`와 `paseo workspace create --isolation local --path <기존 경로> --project <등록 ID>`를 사용한다. 반환된 cwd와 `isolation:local`을 확인한다. 시험은 그 workspace에서 한 번만 실행하고 유한 `--wait-timeout`을 지정한다. 공개 CLI에 profile 조회 기능이 없으면 profile을 확인했다고 주장하지 않는다. 공급자 기본 모델·thinking·권한을 유지한 경우 그 사실과 나중에 관측한 metadata를 구분해 기록한다.

[Paseo provider](https://paseo.sh/docs/providers.md)는 설치·인증된 agent CLI를 실행한다. Codex를 공급자로 사용할 경우 그 공급자의 지침 기능을 따르되 실제 읽기 결과를 확인한다. 이 저장소의 `startup.py --environment paseo`는 경로·환경 연결 확인용이며 모델 실행이나 실제 지침 주입을 증명하지 않는다. Orca의 ledger/dispatch ID를 Paseo에 위조하여 붙이지 않는다.

## Codex 앱

[공식 프로젝트 문서](https://learn.chatgpt.com/docs/projects.md)에 따라 **로컬 폴더에 연결된 프로젝트**인지 확인한다. 업로드된 파일을 사용하는 ChatGPT 프로젝트와 로컬 프로젝트는 같지 않다. 프로젝트 이름만으로 파일 접근을 확정하지 않는다. 기존 checkout을 사용하고, 지침 시험을 위해 worktree를 만들지 않는다.

[AGENTS.md 발견 규칙](https://learn.chatgpt.com/docs/agent-configuration/agents-md.md)에 따라 전역/프로젝트 지침, override와 cwd를 구분한다. 필요한 부분만 실제로 읽고, 전역 지침 원문을 보고서에 복사하지 않는다. 지침 자동 로드 근거가 없으면 명시 읽기만 확인한 것으로 남긴다. `startup.py --environment codex`라는 미지원 값을 만들거나 단독 CLI 시험을 앱 시험으로 보고하지 않는다.

설치된 `codex app --help`가 지원하면 `codex app /path/to/existing-project`는 기존 폴더를 앱으로 여는 공식 경로다. [공식 딥링크](https://learn.chatgpt.com/docs/reference/commands.md#chats)의 `codex://new?path=<절대경로>&prompt=<프롬프트>`도 사용할 수 있다. 두 값은 각각 URL 인코딩한다. 딥링크는 초안을 준비할 뿐 자동 전송하지 않는다. 앱의 실제 프로젝트와 시험 문구를 대조한 뒤 한 번만 보낸다. 전송 동작 성공과 실제 대화 생성·응답 확인을 구분한다. 사용자가 다른 대화에서 입력 중이면 초안을 건드리거나 화면 전환을 반복하지 않고, 시험 대화를 확인할 수 있는 시점을 기다린다.

## 시험 프롬프트

아래 두 경로를 실제 값으로 바꾼 뒤 승인된 시험 세션에서 한 번만 보낸다. 비공개 경로가 포함된 실제 프롬프트와 응답은 공개 저장소에 저장하지 않는다. 공통 저장소 자체를 시험한다면 두 경로는 같은 위치다.

```text
목표: 기존 프로젝트에서 공통 workflow 지침을 읽고 적용할 수 있는지 확인한다.
대상 프로젝트: /path/to/existing-project
공통 workflow 저장소: /path/to/ai-workflow

파일 수정·삭제, 설치, 네트워크, 서버, 다른 에이전트 생성,
commit/push, hook·전역 설정 변경은 하지 않는다.
다른 세션이나 원본 로그를 탐색하지 않는다. 비밀 환경변수를 출력하지 않는다.

1. 실제 cwd와 대상 프로젝트의 관계를 확인한다. 다르면 작업하지 말고 차이를 보고한다.
2. 이미 로드된 지침 출처와 이번에 명시적으로 읽는 파일을 구분한다.
   대상 프로젝트 AGENTS.md와 필요한 더 구체적인 지침,
   공통 AGENTS.md·projects/README.md·runtime/feedback-exchange.md를 읽는다.
   없는 파일은 없다고 보고한다. 자동 로드됐다고 추측하지 않는다.
3. 읽은 지침에서 이 시험에 적용되는 원칙 세 가지를 근거 파일과 함께 설명한다.
4. feedback_exchange.py의 export/import/summary 목적과
   기존 trace DB를 보존해야 하는 이유를 설명한다.
   --help 조회는 가능하지만 실제 DB·세션 원본·교환 키는 열지 않는다.
5. 사용량 자동 수집·앱 adapter·실제 두 PC 전달 중 확인하지 않은 항목을 명시한다.

최종 답변은 읽은 문서의 상대 경로, 세 원칙, 세 명령의 역할,
실제로 수행한 조회와 미확인 항목으로 짧게 작성한다.
provider/model/effort/토큰은 공식 실행 근거가 없으면 unknown/미측정으로 둔다.
```

## 수용 기준과 기록

| 확인 항목 | 통과 근거 |
| --- | --- |
| 앱·host·프로젝트 | 실제 앱의 로컬 폴더 또는 daemon workspace가 지정 대상과 일치 |
| 지침 읽기 | 대상 파일과 그 안의 구체적인 규칙을 정확히 설명; 자동 로드와 명시 읽기를 구분 |
| 작은 작업 | export는 최소 요약 생성, import는 별도 DB의 중복/충돌 검사, summary는 부분 범위 집계임을 설명 |
| 보존 | 시험 전후 Git 상태와 대상 파일 해시 불변; 다른 작업을 변경하지 않음 |
| 측정 한계 | 사용량 실측·앱 adapter·두 PC 전달을 확인한 것으로 확대하지 않음 |

앱별 결과는 pass/fail/not-run으로 기록하고, 버전·사용한 커밋·실제 대상·세션 근거·응답·파일 보존 확인을 비공개로 보관한다. 모델 요청값과 실제 관측값을 구분한다. 후속 수집 adapter를 구현하기 전에는 계정 quota를 작업 토큰으로 수입하지 않는다.
