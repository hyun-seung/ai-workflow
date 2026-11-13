# Orca 입력과 최소 라우팅 상태 연결

상태: Codex 입력 hook용 어댑터·단위 검사 구현. 현재 Orca 대화에 설치·신뢰·활성화하지 않았으며 실제 native 입력 수신은 미검증이다. 플러그인과 공급자 hook의 적용 범위를 구분한다.

## 확인한 지원 범위

2026-11-13 Orca 1.4.256의 설치 버전 가이드 `orca-plugin-authoring` / `references/host-api.md`를 확인했다. `agent.prompt.before`는 Orca가 터미널 agent에 전달하는 시작·위임 등의 프롬프트를 대상으로 하며 사용자가 직접 제출한 프롬프트는 대상이 아니다. 이를 모든 사용자 메시지의 입력 지점으로 사용하지 않는다. [Orca 공식 host API](https://github.com/stablyai/orca/blob/main/docs/reference/plugin-host-api.md#hooks)

같은 Orca 안의 Codex 입력에 대해서는 공급자의 `UserPromptSubmit`이 프롬프트 제출 시점의 session_id·turn_id·cwd·transcript_path와 추가 문맥 반환을 제공한다. 다른 환경이나 단독 Codex 실행으로 대체하는 것은 아니다. Orca native 대화에서의 실제 이벤트·신뢰 UI 지원은 별도로 검증해야 한다. [OpenAI Docs: UserPromptSubmit](https://learn.chatgpt.com/docs/hooks#userpromptsubmit)

현재 `orca status`의 caller는 Orca 세션 ID와 live 상태를 제공하며 native Codex 세션 ID·transcript 경로는 제공하지 않았다. 두 세션 ID를 이름으로 변환·추측하지 않고 원본 hook이나 공식 실행 메타데이터의 정확한 값을 사용한다. 경로를 찾으려고 다른 대화·전체 세션 폴더를 탐색하지 않는다.

같은 날 Orca 1.4.257에서 설정 화면과 입력 명령 후보도 확인했으나 이 대화에 적용할 공급자 hook 검토·신뢰 경로를 확보하지 못했다. 이것만으로 앱 전체의 지원 불가를 단정하지 않는다. 설정값을 변경하거나 시험 메시지를 제출하지 않았고 검색용 초안도 제거했다. 정확한 native 바인딩과 공식 신뢰 경로가 확인될 때 실제 연결 검증을 재개한다. 같은 UI 탐색을 반복하거나 신뢰 우회로 대신하지 않는다.

## 구현한 최소 경로

`scripts/routing_hook.py`는 정확히 지정된 프로젝트·native 세션·transcript 경로에만 작동하는 읽기 전용 어댑터다. worker가 부모 session_id를 공유할 수 있으므로 subagent 표시가 있거나 transcript가 다르면 적용하지 않는다.

- 요청 원문은 hook 입력 형식을 확인하는 데만 사용한다. 본문을 분류·기록·재출력하지 않고 transcript 파일도 읽지 않는다.
- 명시한 대화 제어와 현재 요청의 **미확인 힌트**만 전달한다. 원문에서 제어를 자동 추출하지 않으며 실제 승인·선택을 저장하지 않는다.
- 빈 기본 상태·비활성·다른 대상에는 stdout이 비어 있다. 전달할 힌트가 있을 때만 512문자 이내의 추가 문맥을 반환한다. 문자 제한은 토큰 측정치가 아니다.
- 별도 모델·네트워크·subprocess를 호출하지 않는다. Python hook 프로세스 자체의 실행 비용은 있으며 요청 전체 토큰이 0이라는 뜻은 아니다.
- 잘못된 상태·입력은 짧은 고정 경고만 반환한다. 원래 요청을 차단하거나 모델·effort·위임·권한을 바꾸지 않는다. 이 hook은 보안·승인 강제 장치가 아니다.

데이터 흐름:

```text
사용자 입력 → Orca 안의 Codex UserPromptSubmit
             → 대상 일치·비공개 snapshot 검사
             → 빈 상태면 출력 없음 / 명시한 제어·미확인 요청 힌트만 추가
             → 현재 AI가 원래 요청과 함께 의미 판단
```

새 목표인지 같은 요청의 후속 대화인지는 현재 AI가 판단한다. 힌트를 제공받았다는 이유로 이전 요청의 제어나 승인을 새 작업에 자동 적용하지 않는다. 이 단계에서 실제 사용자 선택·중복 실행·영구 선호는 관리하지 않는다.

## 명시 snapshot 준비

[양식](../templates/routing-context.json)을 **Git·대상 프로젝트 밖의 비공개 폴더**로 복사해 채운다. 이 어댑터가 상태를 자동 생성하거나 영구 저장하지 않는다. 실제 경로·식별자·제어는 공개 저장소에 넣지 않는다.

- 폴더는 소유자 전용 0700, 파일은 0600으로 보관한다. symlink·비정상 파일·다른 소유자·Git 내부·프로젝트 내부는 거부한다.
- `project`, `session_id`, `transcript_path`는 공식 근거의 정확한 값으로 채운다. `enabled`는 boolean이며 양식 기본은 false다.
- `conversation_controls`는 [로컬 추천 도구](request-router.md#입력-v1)의 제어 필드를 사용한다. 실제 사용자가 현재 대화에 지정한 값만 적는다. 승인 플래그·실행 명령은 허용하지 않는다.
- `request_hint`는 null 또는 `{id, revision, controls}`다. 목표·범위·관련 지침이 바뀌면 revision을 올리거나 힌트를 제거한다. 이것은 선택이나 후속 여부의 증명이 아니다.

예를 들어 대화에서 명시한 위임 금지와 현재 요청의 모델 고정을 전달하려면 아래 필드만 snapshot에 넣는다. 식별자와 값은 가상 예시다.

```json
{
  "conversation_controls": {"delegation": "forbid"},
  "request_hint": {"id": "request_example", "revision": 1, "controls": {"model_locked": true}}
}
```

## 정의 생성과 실제 활성화의 구분

```sh
python3 scripts/routing_hook.py --context /private/path/routing-context.json --print-hook
```

명령은 설정 후보만 출력한다. 경로는 실제 비공개 위치로 바꾸며, 정확한 Python·스크립트·context 경로는 shell quoting해 고정한다. `UserPromptSubmit` command handler 하나, timeout 2초다. 생성 결과는 로컬 경로를 포함하므로 공개하지 않는다.

활성화 전에 다음을 확인한다.

1. 선택한 Orca 대화에서 프로젝트 계층의 hook과 해당 이벤트가 실제 지원되는지 확인한다.
2. 기존 프로젝트 `.codex/hooks.json`을 보존하며 정확한 handler만 병합한다. 기존 Stop·다른 hook을 덮어쓰지 않는다. 전역 hook·managed 정책·권한 기본값은 변경하지 않는다.
3. 공식 hook 검토 화면에서 **추가한 정확한 정의**를 검토·신뢰한다. 새 정의는 신뢰 전 실행되지 않을 수 있다. 검토 UI가 없으면 보류하며 신뢰 우회 플래그·다른 앱으로 대체하지 않는다. [OpenAI Docs: Review and trust hooks](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks)
4. 이후의 실제 사용자 입력 한 건에서 대상 일치·빈 상태의 문맥 생략·힌트 전달·원문 비저장을 확인한다. synthetic stdin 재현은 실제 native 이벤트 발생의 증명이 아니다.

현재는 native 식별자·경로 확보와 해당 대화의 hook 신뢰/실제 이벤트가 남아 있어 설치하지 않았다. 다음 단계에서 확인 없이 완료 상태로 승격하지 않는다. 지원 여부 조사와 이 코드 준비를 전역 입력 연결 완료라고 보고하지 않는다.

## 검증과 남은 범위

```sh
python3 -m unittest discover -s tests -p test_routing_hook.py -v
```

13개 검사에서 대상 불일치·subagent 제외, 빈 상태 문맥 생략, 요청/경로 비노출, 크기 제한·JSON 검증, 비공개 파일 조건·비정상 파일의 비차단 거부, 모델/네트워크/subprocess 미호출, 비차단 오류와 정의 생성의 비설치를 확인했다. 상태 snapshot은 호출자가 준비한 자료이므로 그 진위·자연어 추출 정확도·권한을 증명하지 않는다.

명시 snapshot 자동 갱신, native 입력 실제 수신, 자연어 제어의 상태 반영, 선택 UI·역할 적용, 위임·모델 변경, 실제 시간·토큰 비교는 후속 범위다. 별도의 [로컬 선택 기록·역할 지침 준비](routing-choices.md)는 구현했지만 이 어댑터와 자동 연결하지 않았다. 어댑터 준비와 3단계의 실제 연결 완료를 구분한다.
