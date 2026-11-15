# Orca 입력과 최소 라우팅 상태 연결

상태: Codex 입력 어댑터와 한정된 실제 수신 probe를 준비했다. 2026-11-14 프로젝트 시험 정의를 등록했으나 실제 입력/완료 영수증이 생성되지 않았고 공식 검토 경로도 확보하지 못해 시험 정의를 제거했다. 실제 native 입력 수신은 미검증이다. [입력·역할·위임 실제 검증](routing-integration-check.md)에서 확인/미확인을 구분한다.

이 문서는 **선택적 native 상태 연결**을 다룬다. 지침이 연결된 현재 AI가 요청을 읽고 같은 응답에서 판단·제안·대화 선택을 처리하는 기본 진입은 [요청 라우팅 계약](request-routing.md)을 따른다. native hook 검증 때문에 기본 경로나 같은 AI의 지침 기반 역할 적용을 보류하지 않는다.

## 확인한 지원 범위

### 2026-11-14 공식 웹 문서 재확인

**Orca의 일반 hook 지원은 공식 문서에 명시되어 있다.** [Agent hooks & memory](https://orca.dev/docs/agents/hooks-memory)는 저장소의 `.claude/`·`.codex/` 설정을 읽고 worktree에서 에이전트를 시작할 때 기존 hook을 실행한다고 설명한다. 따라서 이 기능을 포괄적으로 “Orca 미지원”으로 분류하지 않는다.

같은 문서의 **Agent status hooks**는 Orca가 관리하는 working/waiting/done 보고 기능이며 설정 경로와 `orca agent hooks status|on|off --json`을 안내한다. 현재 설치 CLI의 help와 `status` 조회에서 `enabled: true`, Codex `state: installed`, `managedHooksPresent: true`를 확인했다. 조회 응답은 `appliedBy: offline`인 설정/설치 상태이며, 사용자 프로젝트의 hook 신뢰·native 입력 수신을 증명하지 않는다. `on/off`나 설정 변경은 하지 않았다.

반면 [플러그인 host API](https://github.com/stablyai/orca/blob/main/docs/reference/plugin-host-api.md#hooks)의 `agent.prompt.before`는 터미널에 제출하는 시작/위임 프롬프트 대상이며 사람이 직접 보낸 입력을 제외한다. [native chat 문서](https://orca.dev/docs/agents/native-chat)에서도 현재 Codex structured chat에서 사용자 프로젝트의 `UserPromptSubmit`·`Stop`을 검토/활성화하는 구체적 UI 계약은 확인하지 못했다. **일반 지원 확인**과 **현재 native 경로의 미검증**을 구분한다. 후자는 기존 실제 시험 결과를 유지하며 같은 probe·worker를 반복하지 않는다.

### 설치 버전 확인과 실제 시험

후속 설정 진단에서는 도구 프로세스에 제공된 `CODEX_HOME`이 사용자 `.codex`와 일치했고 해당 `config.toml`의 프로젝트 폴더 신뢰는 `trusted`였다. 사용자 hook에는 `enabled`와 `trusted_hash`가 저장되어 있었지만, 앞서 시험한 현재 프로젝트 hook의 승인 항목은 없었다. **폴더 신뢰와 개별 hook 정의 승인은 별개**다. 저장된 해시의 존재만으로 현재 정의의 해시 일치·활성 세션 로딩까지 확인한 것으로 보지 않는다.

현재 도구의 부모 계보에서 Codex app-server와 별도 외부 listener 인자 부재를 확인했다. 설치된 Orca CLI·배포 가이드에는 같은 native 런타임의 hook 목록을 읽는 창구를 찾지 못했다. OpenAI의 [app-server 계약](https://learn.chatgpt.com/docs/app-server)은 `hooks/list`를 제공하지만, 새 서버를 띄워 조회하는 것은 현재 세션 상태의 검증이 아니므로 대체하지 않았다. 실행 인자의 hook 관련 override와 실제 로딩된 계층도 완전히 해석하지 못했다.

따라서 승인 기록 부재는 앞선 미수신을 설명할 장애 요소지만 단일 원인으로 확정하지 않는다. 다음 조건은 정확한 프로젝트 정의의 **공식 검토·승인 경로**와 동일 런타임의 목록/상태 확인이다. 이를 확보한 뒤 입력 한 건으로 재검증하며, 현재 상태에서 반복 설치·새 worker 시험·`trusted_hash` 직접 작성은 하지 않는다. 진단에서 설정·신뢰·hook을 변경하지 않았다. 원시 설정과 진단 경로는 비공개로 보관한다.

2026-11-13 Orca 1.4.256의 설치 버전 가이드 `orca-plugin-authoring` / `references/host-api.md`를 확인했다. `agent.prompt.before`는 Orca가 터미널 agent에 전달하는 시작·위임 등의 프롬프트를 대상으로 하며 사용자가 직접 제출한 프롬프트는 대상이 아니다. 이를 모든 사용자 메시지의 입력 지점으로 사용하지 않는다. [Orca 공식 host API](https://github.com/stablyai/orca/blob/main/docs/reference/plugin-host-api.md#hooks)

같은 Orca 안의 Codex 입력에 대해서는 공급자의 `UserPromptSubmit`이 프롬프트 제출 시점의 session_id·turn_id·cwd·transcript_path와 추가 문맥 반환을 제공한다. 다른 환경이나 단독 Codex 실행으로 대체하는 것은 아니다. Orca native 대화에서의 실제 이벤트·신뢰 UI 지원은 별도로 검증해야 한다. [OpenAI Docs: UserPromptSubmit](https://learn.chatgpt.com/docs/hooks#userpromptsubmit)

현재 `orca status`의 caller는 Orca 세션 ID와 live 상태를 제공하며 native Codex 세션 ID·transcript 경로는 제공하지 않았다. 두 세션 ID를 이름으로 변환·추측하지 않고 원본 hook이나 공식 실행 메타데이터의 정확한 값을 사용한다. 경로를 찾으려고 다른 대화·전체 세션 폴더를 탐색하지 않는다.

같은 날 Orca 1.4.257에서 설정 화면과 입력 명령 후보도 확인했으나 이 대화에 적용할 공급자 hook 검토·신뢰 경로를 확보하지 못했다. 이것만으로 앱 전체의 지원 불가를 단정하지 않는다. 설정값을 변경하거나 시험 메시지를 제출하지 않았고 검색용 초안도 제거했다. 정확한 native 바인딩과 공식 신뢰 경로가 확인될 때 실제 연결 검증을 재개한다. 같은 UI 탐색을 반복하거나 신뢰 우회로 대신하지 않는다.

2026-11-14에는 현재 런타임의 Codex 세션/스레드 ID 후보를 확인하고 해당 세션만 대상으로 프로젝트 probe를 등록했다. 이후 실제 사용자 선택 입력에서도 영수증이 생성되지 않았다. 공식 세션 검색은 꺼져 있어 진행하지 않았으며, UI 조회는 macOS 접근성 오류로 실패했고 사용자도 별도 hook 검토 안내를 확인하지 못했다. 신뢰 우회나 OS/전역 설정 변경 없이 시험 정의를 제거했다. 로딩·신뢰·이벤트 제공 중 원인은 아직 미확인이다.

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

원본 경로를 아직 모르는 경우 `scripts/routing_probe.py --project /path/to/project --session-id session_example --receipt /private/path/input.receipt.json --print-hook`으로 현재 세션 하나의 수신 후보를 준비할 수 있다. 실제 런타임이 제공한 식별자만 사용하며 이 명령도 설치·신뢰하지 않는다. probe는 prompt/transcript 내용을 읽지 않고 이벤트의 경로 메타데이터만 한 번 기록한다. 현재 환경의 시험에서는 수신이 확인되지 않았으므로 같은 정의를 반복 설치하지 않는다.

현재 남은 조건은 native 원본 경로와 해당 대화의 공식 hook 로딩·신뢰·실제 이벤트다. 확인 없이 완료 상태로 승격하지 않는다. 지원 여부 조사와 코드 준비를 전역 입력 연결 완료라고 보고하지 않는다.

## 검증과 남은 범위

```sh
python3 -m unittest discover -s tests -p test_routing_hook.py -v
```

13개 검사에서 대상 불일치·subagent 제외, 빈 상태 문맥 생략, 요청/경로 비노출, 크기 제한·JSON 검증, 비공개 파일 조건·비정상 파일의 비차단 거부, 모델/네트워크/subprocess 미호출, 비차단 오류와 정의 생성의 비설치를 확인했다. 상태 snapshot은 호출자가 준비한 자료이므로 그 진위·자연어 추출 정확도·권한을 증명하지 않는다.

명시 snapshot 자동 갱신, native 입력 실제 수신, 자연어 제어의 상태 반영, 선택 UI·역할 적용, 위임·모델 변경, 실제 시간·토큰 비교는 후속 범위다. 별도의 [로컬 선택 기록·역할 지침 준비](routing-choices.md)는 구현했지만 이 어댑터와 자동 연결하지 않았다. 어댑터 준비와 3단계의 실제 연결 완료를 구분한다.
