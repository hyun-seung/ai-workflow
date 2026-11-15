# 마감 manifest 초안 생성

`scripts/draft_request_completion.py`는 등록 정보로 [일괄 마감](request-completion.md)의 입력 초안을 만든다. 지정한 hook 설정·작업 장부·읽기 전용 SQLite·자원 등록부를 읽고 비공개 manifest 파일 하나를 추가한다. 작업 실행, 토큰 수집, DB 수입, 보관·삭제와 권한 변경은 수행하지 않는다.

## 명시 입력

조율자가 비공개 JSON을 준비한다. 다음 경로와 식별자는 가상 예시다. 알 수 없는 필드와 잘못된 JSON은 거부한다.

```json
{
  "schema_version": 1,
  "id": "completion-example-v1",
  "hook_config": "/private/tmp/example-private/hook.json",
  "request_id": "request-example",
  "expected_reviewer": "auto_review",
  "resource_directories": ["/private/tmp/example-private/resources"],
  "acceptance_file": "/private/tmp/example-private/accepted.md",
  "closeout_file": "/private/tmp/example-private/closeout.json",
  "coordinator_turns": [],
  "retry_pending": false
}
```

- 프로젝트는 hook 설정에서, 최상위 조율자는 해당 요청의 SQLite 담당자 기록에서 가져온다. 조율자 후보가 없거나 여럿이면 거부한다. 현재 세션과 설정·장부의 조율자 연결도 대조한다.
- `acceptance_file`은 이미 확인한 수용 문서다. `closeout_file`은 기존 마감 형식의 명시 `completed`·`pass` 자료이며, 모든 담당자 배정을 한 번씩 포함해야 한다. 생성기는 문서 내용에서 합격이나 삭제 승인을 추론하지 않는다.
- `expected_reviewer`는 원하는 검토 정책을 명시한다. 예시의 `auto_review`를 실제 권한 설정이나 적용 확인으로 해석하지 않는다.
- 자원이 없으면 `resource_directories`는 빈 배열이다. 여러 등록부를 지정할 수 있지만 중복 경로·불완전 등록·한도 초과는 일부만 선택하지 않고 거부한다.
- 조율자 턴도 명시 배열만 사용한다. 필요하면 일괄 마감의 `{session_file, session_id, binding_file, task_title}` 형식으로 입력한다. 세션 본문을 읽거나 턴을 자동 탐색하지 않으며, 열린 턴을 완료로 만들지 않는다.

```sh
python3 scripts/draft_request_completion.py \
  --input /private/tmp/example-private/draft-input.json \
  --output /private/tmp/example-private/completion-draft.json
```

출력 부모 디렉터리는 미리 준비한다. 입력 자료는 기존 비공개 파일을 사용하며, 출력은 공개 프로젝트·원본·보관본·설정·등록부와 분리한다. 심볼릭 링크와 공유 inode 입력·출력을 거부한다. 출력은 mode `0600`으로 배타적으로 생성한다. 같은 내용의 재호출은 파일을 다시 쓰지 않으며, 다른 내용이 있으면 보존하고 충돌로 멈춘다.

## 자동으로 채우는 값

| 대상 | 기준 |
| --- | --- |
| 작업 목록 | 해당 요청에 토큰 수집 대상으로 등록된 native 배정 전체. 확정되지 않은 배정은 거부 |
| 모델·effort 기대값 | 원래 실행 계획에 명시한 값. 실제 관측값이나 모델 기본값으로 대체하지 않음 |
| 후속 배정 프로필 | 같은 요청·프로젝트·실행 에이전트 종류의 정확한 이전 task/dispatch/run 관계와 해제 상태를 검증한 뒤 상속 |
| 새 실행·재시도 프로필 | 해당 실행의 명시값 필수. 이전 배정 값을 자동 상속하지 않음 |
| 자원 목록 | 지정 등록부 바로 아래의 유효한 등록 파일 중 같은 요청에 속한 자원 전체 |
| 자원별 writer 목록 | 기존 일괄 마감 계약에 맞춰 요청의 전체 배정 ID 사용 |

장부와 자원 목록은 각각 정해진 한도 안에서 읽으며 작업·턴·자원 입력은 최대 100개다. 등록부의 검사 대상 파일도 합계 100개로 제한한다. 요청과 무관한 폴더를 찾거나 기존 프로젝트를 새로 등록하지 않는다. 생성·보관 도중인 자원은 명시 점검을 요구한다. 이미 정리된 등록은 원래 연결을 보존하되, 실제 보관본 검증은 일괄 마감의 재실행 단계에서 수행한다.

stdout은 `status: draft`, `requires_review: true`, `execution_authorized: false`와 항목 수·프로필의 원래 배정 ID를 반환한다. 파일·출력 요약 모두 실제 경로와 식별자가 포함될 수 있으므로 비공개로 보관한다. 성공은 종료 0, 입력·검증·출력 충돌은 2다.

## 검토와 실행 경계

생성된 파일은 기존 일괄 마감 manifest와 같은 형식이다. 조율자가 요청·배정·프로필·자원·수용 문서를 검토한 뒤 별도로 `complete_request.py`에 전달한다. 생성 시점 이후의 입력은 고정되지 않는다. 실제 실행기는 현재 영수증·정확한 종료·프로필·원본 토큰·보관본·정리 정책을 다시 검사한다.

초안 생성 성공을 기능 수용, 현재 worker 종료, 실제 승인 모드, 사용량 완전성이나 정리 가능 판정으로 사용하지 않는다. `retry_pending`도 별도 마감 실행에서만 작동한다. 생성기 자체는 전체 토큰을 측정하거나 추정하지 않는다.

## 검증

```sh
python3 -m unittest discover -s tests -p test_draft_request_completion.py
```

가상 등록 정보의 프로필 상속·누락·충돌·계보 불일치, 요청·역할·자원 범위, JSON 한도, 입력 불변과 출력 충돌·재실행을 검사한다. 실제 파서와 SQLite fixture로 생성한 초안을 기존 `complete_request.prepare`에 전달하는 검사를 포함하며 native 실행과 실제 마감은 구분한다.

로컬의 이전 등록 요청에서 생성한 프로필을 원래 수용 자료와 대조했고, 새 요청에서도 자동 생성한 초안을 명시 검토한 뒤 별도 일괄 마감 명령에 사용했다. 생성 단계의 DB 불변, 정리 뒤 초안 재실행, 마감 재실행의 journal·토큰 불변과 기존 요청 보존·복원을 확인했다. 입력 recipe와 제품 수용·마감 판단은 조율자가 준비했으며, 모든 작업의 자동 마감이나 총 시간·토큰 절감 효과를 검증한 것은 아니다.
