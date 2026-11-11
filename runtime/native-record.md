# Orca·Codex 실행 메타데이터 확인

`scripts/native_record.py`는 **명시한 로컬 세션 파일 한 개**에서 작업 배정·실제 승인 모드·모델·effort·누적 사용량을 확인하고 비공개 JSON을 만든다. 선택적으로 Orca의 `worker-show --full --json` snapshot을 대조해 원래 시작 요청값도 기록한다. 새 에이전트 실행, 권한 설정 변경, 전체 세션 폴더 탐색, 실시간 구독은 하지 않는다.

## 입력과 실행

다음 경로와 식별자는 가상 예시다. `--project`는 세션 원본 `session_meta.cwd`와 일치하는 실제 작업 디렉터리이며, 수집기를 실행하는 ai-workflow 디렉터리와 다를 수 있다. `--session-id`는 Codex 원본 세션 ID로 Orca 채팅 ID와 구분한다. 필요한 파일·ID는 해당 위임의 기존 근거와 공식 조회로 확인하고 무관한 세션을 일괄 탐색하지 않는다.

```sh
python3 scripts/native_record.py \
  --project /path/to/target-project \
  --session-file /path/to/private/session.jsonl \
  --session-id session_example \
  --task-id task_example \
  --dispatch-id ctx_example \
  --worker-snapshot /path/to/private/worker-show.json \
  --expect-reviewer auto_review \
  --expect-approval-policy on-request \
  --output /path/to/private/native-records/inspection.json
```

`--worker-snapshot`과 `--expect-*`는 선택 항목이다. snapshot 없이도 실제 설정·사용량을 기록하지만 시작 요청값의 출처는 `unavailable`이다. snapshot은 명시한 dispatch의 성공한 `worker-show --full --json` 응답이어야 한다. 이 CLI가 Orca를 호출하거나 snapshot을 대신 만들지는 않는다.

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

`usage.scope=session_cumulative`, `task_attributed=false`다. 여러 배정이 같은 세션을 쓰면 서로 다른 파일의 수치를 합산하지 않는다. `measurement_key`와 측정 행/시각으로 같은 세션임을 확인하며, 이 도구는 업무별 차감이나 여러 파일 합계 계산을 하지 않는다. 조율자·자동 승인 검토 사용량도 별도이며 작업 전체 비용으로 보고하지 않는다. [사용량 해석](execution-records.md)을 따른다.

| 종료 코드 | 의미 |
| --- | --- |
| 0 | 입력 연결·저장 성공. 지정한 검사가 있다면 통과; 없으면 `not_requested` |
| 1 | 연결·저장은 성공했지만 기대값 불일치 또는 필요한 실제값 미확인. 결과 JSON은 보존 |
| 2 | 입력 형식·ID/프로젝트/배정 연결·저장 오류. 성공 기록으로 취급하지 않음 |

stdout은 저장 경로와 검사/사용량 상태만 반환한다. 전체 메타데이터는 선택한 비공개 파일에서 확인한다. 오류에 원문 내용을 그대로 출력하지 않는다.

## 검증과 남은 범위

```sh
python3 -m unittest discover -s tests -p test_native_record.py -v
```

가상 기록으로 연결 불일치·자동 검토 세션 제외·모드 변경·미측정·누적 중복·후속 배정·개인정보 제외·비공개 저장과 CLI 반환 코드를 검사한다. Codex CLI 0.167.0의 보존된 실제 기록에서 대신 승인 적용과 과거 사용자 승인 불일치도 재현했고, Orca 1.4.256의 해당 종료 worker snapshot으로 시작 요청값 연결을 확인했다. 새로운 버전이나 다음 실제 위임의 형식은 다시 확인해야 한다.

조율자가 완료 알림 후 호출하는 [완료 처리기](completion.md)는 공식 worker 조회와 이 검사기를 연결하고 중복 처리를 막는다. 전역 hook, 새 세션 경로의 공식 조회, SQLite/조회 화면 수입, native 공통 어댑터는 남아 있다. 결과는 기존 trace bundle 형식이 아니므로 그대로 `trace_store`에 수입하지 않는다. 상세 우선순위는 [개선 목록](harness-roadmap.md)을 따른다.
