# 추천 전용 최소 기능

상태: 2단계 로컬 도구 구현. [제어 계약](request-routing.md)의 확정된 입력을 검사하고 추천/추천 없음·기존 선택 재사용을 반환한다. 모델 호출·worker 생성·설정 변경·파일 저장은 하지 않는다. 자연어 원문을 자동 분류하는 앱 입력 hook이나 지속 상태 저장기는 아니다.

## 비용과 사용 순서

일상적인 직접 답변마다 이 CLI를 호출할 필요는 없다. 현재 AI는 [짧은 판단 지침](routing-brief.md)으로 먼저 판단한다. 도구가 필요한 경우에도 구조화된 직접 처리·전체 변경 금지는 카탈로그를 읽지 않고 끝낸다. 의미 판단이 필요할 때만 짧은 목록을 확인하며 상세 역할 문서를 읽거나 별도 분류 모델을 호출하지 않는다.

```sh
python3 scripts/request_router.py catalog
python3 scripts/request_router.py route < examples/request-route.json
```

`catalog`는 `routing-catalog.json`의 짧은 책임 설명과 `workflow.json`의 실행 프로필을 합쳐 반환한다. 역할별 Markdown 본문은 읽지 않는다. `--project /path/to/project`, `--local-config /path/to/config.json`으로 기존 실행 설정의 병합 규칙을 사용할 수 있다. 별도 라우팅 설정을 `harness.json`에 추가하지 않는다. 프로필은 설정된 시작 제안이며 현재 대화의 실제값·계정 접근·최적 설정·모델별 effort 지원을 보장하지 않는다.

`route`는 stdin의 최대 32 KiB JSON을 받아 압축 JSON 하나를 stdout에 출력한다. 요청 원문을 입력 필드로 요구하지 않으며 내부 로그를 저장하지 않는다. 오류는 원문·개인 경로를 출력하지 않고 종료 2, 정상적인 판정은 종료 0이다. 결과도 외부로 공개하기 전에 입력한 메타데이터·이유에 민감한 내용이 없는지 확인한다.

## 입력 v1

| 필드 | 의미 |
| --- | --- |
| `version` | 정수 `1` |
| `session_id`, `request_id` | 호출자가 정한 대화·논리 요청 식별자. 서로 다른 요청을 합치지 않음 |
| `revision` | 1 이상의 정수. 목표·범위·관련 지침이 바뀌면 호출자가 증가시킴 |
| `current` | 선택적 현재 프로필. `role`, `model`, `effort`의 미확인 값은 생략 또는 null. `execution` 기본은 `current` |
| `controls` | 선택적 제어. 아래 기본값에서 필요한 항목만 변경 |
| `followup` | 기본 false. 같은 요청의 후속 대화인지 현재 AI/연결 계층이 판단한 값 |
| `previous` | 선택적 기존 선택 snapshot. 아래 재사용 조건 적용 |
| `analysis` | 현재 AI가 이미 수행한 의미 판단의 구조화된 결과. 생략하면 `needs_analysis` 반환 |

제어 기본값:

```json
{"mode":"propose","frequency":"changes","role_locked":false,"model_locked":false,"effort_locked":false,"delegation":"propose"}
```

`mode`는 `direct`, `propose`, `auto`다. auto는 현재 미지원 상태를 반환하며 활성화하지 않는다. `frequency`는 `changes` 또는 `always`, `delegation`은 `forbid` 또는 `propose`다. 잠금은 boolean이며 역할·모델·effort에 독립적으로 적용한다. 직접 처리의 명시 역할 적용은 호출자가 `current`에 반영해 전달한다. 이 도구가 역할 지침을 적용하거나 실제 실행값을 확인하는 것은 아니다.

`analysis`는 다음 두 형태를 사용한다.

```json
{"recommendation":null}
```

```json
{"recommendation":{"role":"architect","reason":"설계 대안과 장애 조건을 같은 기준으로 비교하는 데 역할 지침이 도움이 될 수 있습니다."}}
```

null은 현재 AI가 검토한 결과 변경 이점이 없다는 뜻이다. 후보에는 변경할 `role`, `model`, `effort`, `execution`과 240자 이하 한 줄 `reason`을 넣는다. 생략한 프로필 필드는 유지하며 역할 기본값의 모델·effort를 자동 복사하지 않는다. 새 역할은 카탈로그, 새 모델은 병합된 실행 설정에 등록된 값에서 선택한다. effort 형식은 기존 실행기의 `low/medium/high/xhigh/max` 범위이며 실제 모델 지원은 별도 확인한다. `execution`은 같은 AI의 `current` 또는 별도 담당자 제안인 `worker`다.

고정 항목을 바꾸거나 위임 금지에서 worker를 제안하면 `revise`로 돌려준다. 후보를 조용히 바꾸어 원래 이유와 다른 제안을 만들지 않는다. 모르는 역할·모델, 필드 오타·잘못된 타입·중복 JSON 키·비유한 값은 입력 오류다. 미확인 프로필에 실행기 기본값을 채워 넣지 않는다.

## 후속 선택의 재사용

`previous`는 아래처럼 **제안이 아니라 사용자가 이미 선택한 내용**을 호출자가 전달한다. 네 가지 프로필 필드를 모두 포함해야 하며 미확인은 null로 표시한다.

```json
{
  "session_id":"session_example",
  "request_id":"request_example",
  "revision":1,
  "controls":{},
  "selection":{"role":"tutor","model":"gpt-6.1-sol","effort":"medium","execution":"current"}
}
```

`followup=true`이며 대화·요청·revision·정규화한 제어가 모두 같고 현재 제어와 충돌하지 않을 때만 `reuse`다. 조건이 다르면 새 판단을 요구하거나 이번 입력의 `analysis`를 사용한다. 직접 처리 지시가 최우선이다. 목표 변화·동의 여부를 이 도구가 원문에서 검출하지 않으며, 입력 snapshot의 진위나 승인 권한도 증명하지 않는다. 실제 승인 저장·만료·중복 실행 방지는 3단계에서 연결한다.

## 결과와 사용 경계

| `action` | 의미 | 사용자에게 표시할 내용 |
| --- | --- | --- |
| `direct` | 직접 처리·변경 금지·추천 없음·설정 동일 | 기본 `message`는 빈 문자열. 본문만 답함. `always`의 추천 없음은 짧은 유지 제안 |
| `reuse` | 같은 요청의 기존 선택 재사용 | 빈 문자열. 같은 선택을 다시 묻지 않음 |
| `needs_analysis` | 현재 AI의 의미 판단 필요 | 빈 문자열. 별도 모델을 자동 호출하지 않음 |
| `propose` | 제어 조건을 통과한 변경 제안 | 역할·모델·effort·처리 방식의 변경점, 이유·이번 요청 범위·선택지를 한 메시지로 전달 |
| `revise` | 후보가 잠금·위임 금지와 충돌 | 잘못된 제안은 공개하지 않고 현재 AI가 후보를 수정하거나 직접 처리 선택 |
| `unsupported` | 자동 실행 모드는 아직 미지원 | 현재 AI가 제한을 설명하고 가능한 대안을 안내 |

모든 결과는 `executable=false`, `runtime_support_verified=false`, `router_model_calls=0`이다. 이는 이 도구가 모델을 호출하거나 실제 실행을 승인하지 않는다는 뜻이며 전체 대화의 토큰이 0이라는 뜻은 아니다. `profile`은 판정에 사용하는 프로필이며 런타임 적용 확인값이 아니다. JSON 자체를 채팅에 그대로 출력할 필요는 없다.

자동 추천 품질·앱 연결·실제 절감률은 아직 미검증이다. 현재 도구는 입력된 의미 판단을 검증·정리하는 단계이며 그것의 사실적 타당성까지 증명하지 않는다. 상태 저장·실행·모델 전환을 추가하기 전에도 이 경계를 유지한다.

## 검증

```sh
python3 -m unittest discover -s tests -p test_request_router.py -v
```

직접 경로의 카탈로그 읽기 생략, 의미 판단 요청, 역할과 프로필의 분리, 독립 잠금·위임 금지, 대화/요청/버전/제어 변경 시 재사용 방지, 엄격한 JSON·입력 크기·오류의 본문 비노출을 확인한다. subprocess·네트워크 호출 금지와 상세 Markdown 미로딩도 별도 검사한다. 자연어 분류 정확도·사용자 선택의 진위·원래 업무 품질은 이 테스트 범위가 아니다.
