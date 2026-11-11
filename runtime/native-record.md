# Orca·Codex 실행 메타데이터 확인

`scripts/native_record.py`는 **명시한 로컬 세션 파일 한 개**에서 작업 배정·실제 승인 모드·모델·effort·누적 사용량과 확인 가능한 배정별 사용량을 비공개 JSON으로 기록한다. 선택적으로 Orca의 `worker-show --full --json` snapshot을 대조해 원래 시작 요청값도 기록한다. 새 에이전트 실행, 권한 설정 변경, 전체 세션 폴더 탐색, 실시간 구독은 하지 않는다.

## 입력과 실행

다음 경로와 식별자는 가상 예시다. `--project`는 세션 원본 `session_meta.cwd`와 일치하는 실제 작업 디렉터리이며, 수집기를 실행하는 ai-workflow 디렉터리와 다를 수 있다. `--session-id`는 Codex 원본 세션 ID로 Orca 채팅 ID와 구분한다. 필요한 파일·ID는 해당 위임의 기존 근거와 공식 조회로 확인하고 무관한 세션을 일괄 탐색하지 않는다.

```sh
python3 scripts/native_record.py \
  --project /path/to/target-project \
  --session-file /path/to/private/session.jsonl \
  --session-id session_example \
  --task-id task_example \
  --dispatch-id ctx_example \
  --request-id request_example --role backend \
  --worker-snapshot /path/to/private/worker-show.json \
  --expect-reviewer auto_review \
  --expect-approval-policy on-request \
  --output /path/to/private/native-records/inspection.json
```

`--worker-snapshot`과 `--expect-*`는 선택 항목이다. snapshot 없이도 실제 설정·사용량을 기록하지만 시작 요청값의 출처는 `unavailable`이다. snapshot은 명시한 dispatch의 성공한 `worker-show --full --json` 응답이어야 한다. 이 CLI가 Orca를 호출하거나 snapshot을 대신 만들지는 않는다.

`--request-id`는 조율자가 연결한 사용자 요청 ID, `--role`은 `workflow.json`의 역할 ID이며 함께 지정한다. 요청 ID는 원본에서 발견한 값으로 위장하지 않고 `request_context.source=explicit_coordinator_input`으로 구분한다. 둘을 생략한 기존 호출도 지원하지만 요청별 합산 대상으로 표시하지 않는다.

`--expect-model`, `--expect-effort`, `--expect-sandbox-mode`도 지정할 수 있다. 기대값은 실행 설정을 바꾸지 않는다. 조직이 승인한 다른 정책을 쓰는 경우 해당 기대값만 지정한다. 명시하지 않은 기대값을 자동으로 성공 처리하지 않는다.

| 결과 항목 | 의미 |
| --- | --- |
| `binding`, `session`, `project` | 배정 머리말의 task/dispatch/Orca 채팅과 원본 Codex 세션·작업 디렉터리 대조 |
| `source` | 명시 입력 경로·SHA-256·바이트/행 수·다음 배정 경계. 실제 경로이므로 비공개 |
| `requested` | 선택한 native snapshot의 원래 모델·effort 요청. 출처가 확인된 null은 기본값 사용, 출처 없는 null은 미확인 |
| `expected` | 검사자가 CLI로 전달한 기대값. 원래 시작 요청과 별개 |
| `observed_contexts` | 배정 구간의 실제 승인 검토자·정책·모델·effort·sandbox·네트워크 설정과 원본 행·시각 |
| `verification` | 기대값 및 확인된 native 모델·effort 요청과 실제값 대조. `pass / fail / unverified / not_requested` |
| `usage` | 출처가 있는 마지막 누적 측정값. `measured / partial / unavailable`과 세션 범위·집계 키 |
| `dispatch_usage` | 닫힌 배정 구간의 입력·출력·캐시·추론 측정값, 요청/역할/세션/작업/dispatch 연결, 기준값·종료값·증가분과 원본 행 |

기대값은 배정 구간의 모든 `turn_context`에 대조한다. 중간에 모드가 바뀌면 마지막 값 하나만 보고 통과시키지 않는다. native 시작 요청값이 확인된 경우 그 모델·effort도 별도 검사한다. 작업의 기능 AC 통과, 현재 live 권한, 실제 승인 허용 여부는 이 결과로 판정하지 않는다.

## 연결과 개인정보 경계

- `session_meta`의 ID·cwd·`originator=orca_desktop`·`source=vscode`를 확인한다. 다른 공급자·별도 자동 승인 검토 세션은 거부한다.
- 사용자 메시지의 Orca 배정 머리말에서 task ID와 조율 명령의 dispatch ID를 연결한다. 작업 본문에서 단순히 언급한 ID는 배정 근거로 쓰지 않는다. 선택 snapshot은 task/dispatch와 구조화 채팅의 `processIncarnation`까지 대조한다.
- 문서화한 머리말·메타데이터 형식만 지원한다. 같은 배정이 여러 번 나타나거나 경계를 구분할 수 없으면 실패한다. 후속 Orca 배정의 직전 context부터는 앞 작업에 포함하지 않는다. 머리말 없는 수동 후속 대화의 독립 업무 귀속은 판정하지 않는다.
- 결과에는 필요한 메타데이터만 저장한다. 요청 본문·도구 출력·내부 추론·인증 설정 원문은 복사하지 않는다. 입력 파일을 JSON으로 읽어 구조를 검사하는 과정과 본문 저장을 구분한다. 이 검사는 원본의 암호학적 진위를 증명하는 기능은 아니다.
- 원본은 수정하지 않는다. 읽는 동안 변경·잘못된 JSON·중복 키·비정상 수치를 구분한다. 세션 최대 128 MiB, 한 행과 선택 snapshot 최대 8 MiB이며 실행 중 계속 바뀌는 파일은 일관된 snapshot으로 인정하지 않는다.
- [인계 도구의 비공개 경로 검사](harness.md)를 재사용한다. 저장소·대상 프로젝트·Git 안의 출력, 공개 권한 디렉터리, 출력 경로 symlink를 거부한다. 새 디렉터리 0700·파일 0600, 기존 파일 덮어쓰기 금지다. 새 관측은 다른 출력 파일에 저장한다.

## 사용량과 반환 코드

`event_msg.token_count.info.total_token_usage`의 마지막 유효 값을 한 번 기록한다. 누적 이벤트를 합산하지 않는다. 입력·출력·캐시·추론 세부값은 검증하며, 누락·감소·상충을 숨기지 않는다. 사용량만 없다고 유효한 모드 검사를 실패시키지는 않고 `usage.status`를 별도로 확인하게 한다.

기존 `usage`는 `scope=session_cumulative`, `task_attributed=false`로 유지한다. 여러 배정이 같은 세션을 쓰면 이 누적값을 서로 더하지 않는다. 조율자·자동 승인 검토 사용량도 별도이며 작업 전체 비용으로 보고하지 않는다. [사용량 해석](execution-records.md)을 따른다.

### 배정 구간의 사용량

`scripts/dispatch_usage.py`는 다음 근거가 있는 한 배정을 `dispatch_usage.scope=dispatch`로 기록한다.

1. 같은 native `turn_id`의 `task_started`와 `task_complete` 사이에 정확히 하나의 생성된 Orca 배정 머리말이 있다. 중첩·ID 재사용·미종료·중단·머리말 없는 후속 턴은 귀속하지 않는다.
2. 이전 턴의 마지막 유효 누적값을 기준으로 사용한다. 첫 턴은 상속 표시가 없고 첫 증가분이 `last_token_usage`와 일치할 때만 0 기준을 인정한다.
3. 각 누적 증가분을 원본 `last_token_usage`와 대조한다. 같은 누적 통지는 중복 합산하지 않는다. 감소·누락·상충·턴 밖 측정이 있으면 `unavailable`과 이유를 남긴다. 없는 측정을 0으로 만들지 않는다.
4. 작업 중 추가된 사용자/조율 메시지는 원본 metadata에 같은 native `turn_id`가 명시된 경우 그 구간에 포함한다. 메시지 본문의 발신자 주장으로 연결하지 않는다. 서로 다른 논리 요청을 한 native 턴에서 나누는 기능은 없다.

성공하면 `measurements`에 입력·출력·합계와 제공된 캐시 입력·추론 출력을 저장한다. 캐시·추론은 포함 관계이며 합계에 다시 더하지 않는다. 선택 세부값이 없으면 null이다. `increments`는 원본의 확인된 카운터 증가분이며 파일 읽기/검색/명령별 토큰이 아니다.

조율자의 요청 ID·역할까지 연결된 성공 기록만 `task_attributed=true`다. `measurement_key`는 물리 세션과 native 턴에 기반하므로 다른 파일·요청 이름으로 같은 구간을 다시 읽어도 변하지 않는다. 여러 기록을 합칠 때 이 키로 중복을 확인해야 한다. 구간 측정 성공과 요청 연결, 프로필 검사 성공은 서로 다른 상태다.

이 수집기는 배정별 비공개 JSON을 만든다. [DB 수입과 요청/역할 집계](trace-usage.md)는 별도 연결기가 담당하고 기존 actor 값은 보존한다. 여러 native 턴에 걸친 한 배정, 다른 공급자, 개별 도구별 귀속, 조율자·별도 자동 승인 검토자의 전체 수집은 지원하지 않는다. 이미 존재하는 누적 기록이나 원본 세션을 수정하지 않는다.

| 종료 코드 | 의미 |
| --- | --- |
| 0 | 입력 연결·저장 성공. 지정한 검사가 있다면 통과; 없으면 `not_requested` |
| 1 | 연결·저장은 성공했지만 기대값 불일치 또는 필요한 실제값 미확인. 결과 JSON은 보존 |
| 2 | 입력 형식·ID/프로젝트/배정 연결·저장 오류. 성공 기록으로 취급하지 않음 |

stdout은 저장 경로와 검사/사용량 상태만 반환한다. 전체 메타데이터는 선택한 비공개 파일에서 확인한다. 오류에 원문 내용을 그대로 출력하지 않는다.

## 검증과 남은 범위

```sh
python3 -m unittest discover -s tests -p test_native_record.py -v
python3 -m unittest discover -s tests -p test_dispatch_usage.py -v
```

가상 기록으로 연결 불일치·자동 검토 세션 제외·모드 변경·미측정·누적 중복·후속 배정·개인정보 제외·비공개 저장과 CLI 반환 코드를 검사한다. Codex CLI 0.167.0의 보존된 실제 기록에서 대신 승인 적용과 과거 사용자 승인 불일치도 재현했고, Orca 1.4.256의 해당 종료 worker snapshot으로 시작 요청값 연결을 확인했다. 새로운 버전이나 다음 실제 위임의 형식은 다시 확인해야 한다.

보존된 실제 기록에서는 첫 배정·같은 세션의 후속 배정을 원본 누적 구간과 대조했고, 종료 worker의 [완료 처리기](completion.md) 호출·재호출로 저장과 replay를 확인했다. 이 수집기의 자체 회귀와 구간 대조이며 독립 검토자의 판정은 아니다. 전역 hook, 새 세션 경로의 공식 조회, native 공통 어댑터는 남아 있다. 결과는 trace bundle 자체가 아니므로 `import-usage` 연결기를 사용한다. 상세 우선순위는 [개선 목록](harness-roadmap.md)을 따른다.
