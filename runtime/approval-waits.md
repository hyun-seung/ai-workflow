# 승인·질문 대기와 알림 처리

`scripts/approval_wait.py`는 한 번의 관측을 저장하고 조율자가 전달할 알림을 반환한다. 같은 원본 요청의 반복, 새 요청·내용 변경, 대기 해소, 종료된 worker와 확인 불가를 구분한다. 에이전트 생성·권한 승인·설정 변경·외부 메시지 전송·반복 조회는 하지 않는다. 기존 SQLite 조회 화면과 독립된 작은 상태 DB를 사용한다.

실제 승인 요청을 줄이는 설정은 [실행 권한 정책](permission-policy.md)에서 먼저 다룬다. 이 도구의 중복 통지 억제는 실행 권한을 넓히거나 원본 승인 요청 자체를 제거하지 않는다.

## Orca 알림을 받았을 때

설치 버전의 가이드와 CLI를 확인한 조율자가 정확한 dispatch를 지정해 한 번 호출한다. 아래 경로와 ID는 가상값이다. `--scope`는 조율자가 확인한 host의 고유 이름이다. 현재 live 연결은 로컬만 지원한다.

```sh
python3 scripts/approval_wait.py --project /path/to/project \
  --state-db /private/path/approval-waits/state.sqlite3 \
  orca --scope orca.local --dispatch ctx_example --live --orca-cli /path/to/orca
```

명시 CLI가 없으면 시작 가이드의 CLI 선택 규칙을 재사용한다. 실제 호출은 `orchestration worker-show --dispatch ... --full --json` 한 번이며 timeout은 15초다. 조회가 거부되면 정상 승인 경로를 이용한다. 다른 실행 파일·호스트·직접 Codex 호출로 우회하지 않는다. native inbox/check를 소비하지 않는다.

`--snapshot /private/path/worker-show.json --observed-at <실제-수집시각>`으로 보존한 관측을 처리할 수도 있다. 수입 시각을 원본 관측 시각으로 쓰지 않는다. snapshot 재생은 과거 근거의 확인이며 현재 상태의 live 증명이 아니다.

## 판단과 전달

| 출력 | 조율자의 처리 |
| --- | --- |
| `deliver_pending_notice` | 현재 상태·대상·사용자에게 필요한 행동을 설명하고 전달 후 해당 notice를 ack |
| `suppressed` | 이미 전달한 동일 대기. 같은 안내를 반복하지 않음 |
| `replay` | 동일 관측 재처리. notice가 남아 있으면 이전 전달 여부 확인 후 처리 |
| `inspect_unknown` | 확인 불가. 마지막 알림이 남아 있어도 현재 대기라고 단정하지 않으며 관련 근거만 추가 확인 |
| `stale_or_conflicting_observation` | 오래됐거나 같은 시각의 상충 관측. 저장된 상태를 바꾸지 않음 |
| `inspect_conflicting_lifecycle` | 종료된 dispatch에 새 진행 관측이 들어옴. 새 dispatch 여부 확인 |
| `none` | 새로 전달할 대기 알림 없음 |

`state`는 pending/clear/unknown/ended다. 모든 결과의 `permission_decision`은 unknown이며, clear나 ack를 허용·거부 판정으로 바꾸지 않는다. 종료 task 정보만으로 프로세스가 끝났다고 판단하지 않는다.

Orca adapter는 dispatch 일치와 `observation.exactWorker`를 확인한다. `agentWait` 부재는 unknown, 명시 null은 clear, 비어 있지 않은 객체는 pending이며 정확한 exited 관측은 ended로 처리한다. malformed 값이나 exited와 pending의 상충은 unknown이다. 조회 실패는 오류이며 clear로 저장하지 않는다. 필드 부재/null의 의미는 [Orca 공식 호환성 문서](https://github.com/stablyai/orca/blob/main/docs/reference/remote-wire-compatibility.md)도 구분한다.

**현재 확인한 CLI 계약에는 대기의 안정적인 원본 요청 ID·내용 버전 규약이 없다.** 따라서 native pending을 같은 명령·같은 dispatch라는 이유만으로 자동 억제하지 않는다. `pending_identity_unavailable`로 남겨 실제 요청을 확인하게 한다. native의 종료된 worker·대기 해소·확인 불가 처리는 연결됐고, pending 원본 식별자 연결은 후속이다. 이 제한 때문에 현재 도구만으로 Orca의 모든 반복 알림이 없어지지는 않는다.

## 원본 요청 ID가 있는 관측

공통 `observe` 입력은 UTF-8 JSON 객체다. 실제 수집기가 제공하거나 조율자가 원본에서 확인한 값만 넣는다. 다음은 **가상 예시**이며 별도 native 필드나 자동 수집을 뜻하지 않는다.

```json
{
  "version": 1,
  "scope": "example.host",
  "dispatch_id": "example-dispatch",
  "source": "example.confirmed-source",
  "observed_at": "2026-01-01T00:00:00Z",
  "state": "pending",
  "kind": "approval",
  "request_id": "example-source-request-17",
  "revision": "example-payload-revision-1"
}
```

`kind`는 approval/question/unknown이다. `request_id`는 한 번의 대기를 식별하는 원본 ID, `revision`은 해당 요청의 명령·조건·질문이 달라지면 바뀌는 원본 버전 또는 내용 해시다. ID가 있으면 revision도 필수다. 둘 다 확인할 수 없으면 null로 둔다. 같은 명령 문자열의 해시를 request_id로 만들지 않는다. 같은 명령이 새 승인 요청으로 등장할 수 있다.

동일 host·dispatch·source에서 같은 요청 ID와 revision·kind가 유지되면 전달된 알림을 억제한다. ID·revision·kind가 달라지거나 clear 이후 다시 pending이면 새 알림이다. ID 없는 pending은 새 관측마다 알림을 반환한다. unknown은 기존 대기를 닫지 않는다. 오래된 관측은 해소 상태를 다시 열지 않는다.

```sh
python3 scripts/approval_wait.py --project /path/to/project --state-db /private/path/approval-waits/state.sqlite3 observe /private/path/observation.json
python3 scripts/approval_wait.py --project /path/to/project --state-db /private/path/approval-waits/state.sqlite3 pending
python3 scripts/approval_wait.py --project /path/to/project --state-db /private/path/approval-waits/state.sqlite3 ack <notice-id>
```

ack는 **알림 전달 확인**이다. 승인 버튼을 대신 누르거나 도구 권한을 부여하지 않는다. 전달하기 전에 ack하지 않는다. 프로세스가 다시 시작돼도 미확인 알림은 `pending`에 남는다. 해소·새 요청으로 대체된 이전 알림은 ack할 수 없다. unknown 관측 뒤 남은 알림은 현재 대기의 증명이 아니므로 최신 상태를 먼저 확인한다. 전달 직후 ack 전에 중단되면 재전달 가능성이 있으므로, 향후 외부 전송기는 notice ID로 중복 전송을 관리해야 한다.

## 저장·검증과 적용 범위

상태 DB는 템플릿·대상 프로젝트·Git 밖의 소유한 비공개 폴더에만 생성한다. 디렉터리 0700·DB 0600과 sidecar·symlink를 검사하고 다른 용도의 DB를 거부한다. 원문 명령·질문·transcript는 저장하지 않고 상태·해시·알림 ID·시각·명시 scope/dispatch만 보관한다. SQLite transaction으로 동시에 들어온 관측과 ack를 처리한다. 같은 OS 사용자의 악의적인 파일 교체에 대한 격리 기능은 아니다.

검증은 `python3 -m unittest discover -s tests -p test_approval_wait.py -v`로 수행한다. 가상 pending/clear/unknown 입력으로 중복·내용 변경·재시작·동시 처리·오래된 관측·CLI·비공개 저장을 확인한다. 실제 native 검증은 종료된 worker 조회를 별도 기록했으며, 실제 pending ID 수집·모바일 승인·외부 알림 전송은 미검증/미구현이다.

이 도구는 조율자가 알림 수신 시 호출하는 연결이다. background watcher나 Orca 알림 hook 설치는 없다. Orca 자체 메시지의 생성 빈도·UI 표시를 바꾸지 않는다. 신규 자식의 실제 승인 모드와 현재 설치 버전의 공식 지원을 확인하는 작업도 별도로 남는다. 전역 권한을 넓혀 알림 수를 줄이지 않는다.
