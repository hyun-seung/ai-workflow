# 서버 없이 결과 모으기

`scripts/feedback_exchange.py`는 기존 trace SQLite의 한 요청에서 최소 요약 파일을 만들고, 받은 파일을 **별도 feedback SQLite**에 합친다. 로컬 SQLite와 원본 검증 자료는 유지한다. 네트워크 전송·중앙 서버·앱 설정·hook은 만들거나 변경하지 않는다.

## 처음 한 번 준비

저장소 밖 비공개 디렉터리(소유자 전용 0700)를 준비한다. 아래 경로·ID·커밋은 가상 예시이며 실제 값으로 바꾼다. 도구는 기존 파일을 덮어쓰거나 권한을 수정하지 않는다.

```sh
mkdir -m 700 /private/example-feedback
python3 scripts/feedback_exchange.py init-key \
  --out /private/example-feedback/exchange.key
```

키는 32바이트 무작위 값이며 파일 권한은 0600이다. 같은 사용자의 여러 PC는 **동일한 키**를 별도 안전한 경로로 복사해서 쓴다. 키를 결과 파일과 함께 배포하거나 Git에 넣지 않는다. 키를 알고 있는 사람은 원래 ID 후보와 가명 ID를 대조할 수 있으므로 결과는 익명 공개 자료가 아니다. 다른 키의 결과는 같은 feedback DB에 섞지 않는다. 키를 잃거나 교체하면 기존 자료와의 중복 연결을 자동 복구하지 못한다.

## 작업 결과 내보내기

```sh
python3 scripts/feedback_exchange.py export \
  --db /private/example-feedback/local-trace.sqlite3 \
  --request-id request_example \
  --project-id project_example \
  --key /private/example-feedback/exchange.key \
  --workflow-commit 0123456789abcdef0123456789abcdef01234567 \
  --app orca --verification passed --rework none \
  --out /private/example-feedback/result.json
```

출처는 기존 `trace_store.Store`가 읽을 수 있는 DB와 명시 요청 하나다. 원본 세션 파일을 찾거나 읽지 않는다. 같은 프로젝트의 PC들은 같은 `--project-id` 별칭을 사용하고, 프로젝트 안의 요청 ID는 서로 다른 실행에서 재사용하지 않는다. 별칭 자체도 가명화한다. 프로젝트를 바꿔도 같은 물리 측정은 중복으로 거부한다. `--workflow-commit`과 `--app`은 사용자가 명시하는 실행 맥락이며 자동 탐지나 증명이 아니다. `--verification`은 `passed/failed/unknown`, `--rework`는 `none/requirements/implementation/verification/environment/unknown`이며 **사용자 선언**으로 저장한다. 기본값은 unknown이다. 실제 테스트 로그를 확인한 경우에만 passed를 지정한다.

마무리된 결과를 한 번 내보내는 방식을 권장한다. 동일 요청의 내용이 나중에 바뀌면 자동 덮어쓰기하지 않고 충돌로 거부한다. 두 파일과 원본을 보존해 검토한다. 정정·버전 이력 수입은 후속 범위다. 변경된 결과를 다른 요청으로 가장하여 충돌을 우회하지 않는다.

## 다른 PC로 옮겨 합치기

승인된 비공개 파일 전달 경로를 이용해 `result.json`만 주로 쓰는 PC에 복사한다. 받은 디렉터리도 0700, 파일은 0600이어야 한다. 전달 방식이 권한을 보존하지 않으면 그 복사본의 권한을 확인한다. 도구는 업로드하거나 파일을 자동 전달하지 않는다.

```sh
python3 scripts/feedback_exchange.py import \
  --db /private/example-feedback/combined-feedback.sqlite3 \
  --file /private/example-feedback/result.json
python3 scripts/feedback_exchange.py summary \
  --db /private/example-feedback/combined-feedback.sqlite3
```

첫 수입은 `imported:true`, 같은 내용 재수입은 `duplicate:true`다. summary는 요청 수·담당 실행 수·측정 있는 실행 수·측정 구간 수·측정된 토큰 합계, 역할/가명 모델/관측 effort별 집계와 요청별 선언 결과를 보여 준다. 측정이 전혀 없으면 토큰 합계는 `null`이며, 실제 측정 0과 구분한다. 합계는 가져온 요약의 범위다. `full_usage_complete`는 항상 false이며 전체 작업 사용량이나 미관측 실행 수를 추정하지 않는다.

기존 trace DB와 다른 파일을 지정해야 한다. feedback DB는 별도 스키마 버전 101이고 기존 대시보드·`trace_viewer.py import`와 호환되는 원문 bundle이 아니다. 기존 대시보드에 합쳐 보이거나 자동 분석·규칙 변경을 실행하는 기능은 아직 없다.

## 옮기는 것과 제외하는 것

| 자료 | 처리 |
| --- | --- |
| 요청·담당 실행·provider 세션·측정 구간 식별자 | 영역을 구분한 HMAC-SHA256 가명 값; 같은 키로 같은 원래 ID를 처리하면 앱·PC와 무관하게 동일 |
| 역할 | 알려진 workflow 역할만, 나머지는 unknown |
| 모델·effort | actor의 observed만 사용; 모델명은 가명화, effort는 허용된 값만 유지; requested/default를 실제값으로 승격하지 않음 |
| 토큰 | 기존 native usage 이벤트의 계약 검증을 통과한 구간 수치만; nullable 세부 수치 보존 |
| workflow 커밋·앱·검증·재작업 | 명시한 값과 선언이라는 구분 보존 |
| 제목·메시지·도구 입력/출력·코드·경로·원래 ID·정확 시각·원문 해시·원본 증거 | 복사하지 않음 |

이 첫 버전은 `codex.token_count.cumulative_delta.v1` usage 이벤트만 요약한다. legacy actor 수치와 임의 앱 로그는 토큰으로 승격하지 않는다. profile 이벤트의 별도 측정값·시간·비용·자유형 재작업 설명도 옮기지 않는다. 자료가 없으면 미측정으로 남긴다. Paseo/Codex 앱의 자동 수집 adapter는 여전히 미구현이다. `--app paseo`를 지정해도 adapter가 실행되지 않는다.

## 중복·충돌·신뢰 경계

- 같은 요청·같은 내용은 중복으로 처리한다. 같은 요청의 다른 내용은 전체 거부한다.
- 다른 요청이어도 같은 물리 측정 키 또는 같은 세션의 겹치는 원본 줄 구간은 거부한다. 앱 이름을 바꿔 이중 합산하지 않는다.
- 중복 키 JSON·허용되지 않은 필드·잘못된 합계·없는 담당 실행·1 MiB 초과 입력은 거부한다. 요청당 담당 실행과 측정 구간은 각각 최대 1000개다.
- 원문 DB는 읽기 전용이며, 수입은 별도 DB의 단일 transaction이다. 기존 DB의 충돌 거부 시 기록과 파일 바이트를 보존한다. 개인 파일·심볼릭 링크·저장소 내부 출력도 검사한다.
- 파일의 SHA256은 내용 일관성 검사다. 서명·발신자 인증·암호화가 아니다. 원본 구간 증거는 제외하므로 받은 쪽은 원본 측정을 다시 검증할 수 없다. 모든 요약은 `local-validation-summary-only`로 표시하며 신뢰하는 본인의 PC에서 만든 파일만 합친다. 악의적 작성자의 허위 수치를 막는 기능이라고 주장하지 않는다.

완료 기준은 합성 자료의 내보내기→파일 복사→수입→재수입, 원문/경로 제외, 같은 구간의 중복·충돌 방지, 원본 DB 보존이다. 실제 두 PC 전달·앱별 자동 수집은 별도 실증이 필요하다. 로컬 백엔드 스킬과 기존 수입 계약을 적용했으며 외부 catalog는 미설치로 사용하지 않았다. 자체 검증과 독립 검토를 구분한다.
