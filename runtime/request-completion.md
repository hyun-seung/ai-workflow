# 요청 단위 일괄 마감

`scripts/complete_request.py`는 조율자가 수용한 한 요청의 수집·보관·정리·SQLite 마감을 한 번에 연결한다. 기존 [Stop 수집기](completion-hook.md), [상태 점검](completion-recovery.md), [종료 검사](completion.md), [임시 자원 관리](workspace-lifecycle.md), [결과 마감](trace-closeout.md)을 호출한다. 별도 에이전트나 상시 감시 프로세스를 만들지 않는다.

## 준비와 실행

작업·기대 프로필·자원 목록의 반복 입력은 [manifest 초안 생성기](manifest-draft.md)로 준비할 수 있다. 초안은 자동으로 실행되지 않으며 아래 수용·실행 검증은 그대로 필요하다.

기능 검증과 필요한 독립 검토를 먼저 마친다. 현재 Orca 조율자 세션, hook 설정의 조율자, 관리 ledger가 일치해야 한다. 요청과 담당자는 SQLite에 등록하고, 정리할 임시 폴더는 생성할 때부터 자원 관리기에 등록한다. 기존 임의 폴더를 자동 편입하지 않는다.

비공개 JSON manifest의 필드는 다음과 같다. 알 수 없는 필드, 중복 키, 잘못된 경로·프로필·참조는 거부한다.

| 필드 | 값 |
| --- | --- |
| `schema_version`, `id` | 정수 `1`, 실행 식별자 |
| `project`, `request_id`, `coordinator_id` | 프로젝트 절대 경로, 등록된 요청·최상위 조율자 ID |
| `hook_config` | 기존 비공개 hook 설정 파일의 절대 경로 |
| `operations` | `{operation_id, expected: {model, effort, reviewer}}` 배열. 해당 요청의 opt-in 배정 전체와 일치 |
| `coordinator_turns` | `{session_file, session_id, binding_file, task_title}` 배열. 명시 귀속한 닫힌 턴만 포함하며 미사용 시 빈 배열 |
| `resources` | `{directory, resource_id, acceptance, operation_ids}` 배열. 등록부 경로, 자원 ID, 수용 문서 경로, 배정 ID 목록 |
| `closeout_file` | 기존 `trace_closeout` 형식의 `completed`·`pass` 수용 자료 경로 |
| `retry_pending` | 검증된 기존 pending 영수증을 각 한 번 재처리할지 정하는 boolean |

수용 문서는 소유한 일반 파일이어야 하며 JSON과 Markdown을 지원한다. 각 배열은 최대 100개다. 초기 버전은 writer 누락을 피하기 위해 자원마다 요청 전체 배정 ID를 요구한다. 개별 writer만 연결하는 최적화는 제공하지 않는다. 원본 세션·설정·manifest·출력 journal·보관본·정리 대상의 경로는 겹치면 안 된다. 결과 artifact는 변경될 trace SQLite와 `-wal`·`-shm`·`-journal`, ledger와 잠금 파일, hook 출력·batch 출력·자원 등록부 하위 경로를 가리킬 수 없다. hardlink로 같은 inode를 공유하는 artifact도 거부하며 삭제 직전까지 다시 확인한다. 같은 상위 폴더에 있는 별도 보관 결과·안정된 보고서·수용 문서는 허용한다. 이 경계는 수집·보관 전에 먼저 검사하므로 mutable DB를 결과 파일로 지정한 오류가 원본 삭제 뒤 발견되지 않는다.

```sh
python3 scripts/complete_request.py \
  --manifest /private/tmp/example-private/batch.json \
  --directory /private/tmp/example-private/batch-output \
  --orca-cli /example/bin/orca
```

경로는 가상 예시다. 출력은 공개 저장소 밖의 비공개 디렉터리를 지정한다. 정리 정책은 기존 설정의 우선순위를 그대로 적용하며 기본은 `Y`다. `--auto-cleanup N`이면 검증·보관·마감까지 진행하고 원본 폴더를 유지한다. 정책을 바꾸는 재호출은 같은 고정 실행으로 취급하지 않는다.

## 처리 순서

1. manifest·정책·수용 문서·operation·영수증 귀속·자원 식별을 고정하고 요청 잠금을 획득한다.
2. 배정마다 연결된 영수증 한 건을 확인한다. 허용한 pending만 한 번 재처리한다. 정확한 worker 종료·실제 프로필·현재 원본의 측정 구간을 다시 검사하고 중복 없이 수입한다.
3. 지정한 조율자 턴의 명시 binding과 현재 원본을 검사해 수입한다. 열린 턴·중단된 턴·불명확한 귀속을 수치로 만들지 않는다.
4. **모든 자원을 먼저 보관**한다. 결과 파일 해시를 고정하고 별도 SQLite 사본에서 실제 마감 검증을 수행한다.
5. 실효 정책이 `Y`일 때 보관본·입력·정확한 종료를 재확인하고 등록된 임시 원본을 정리한다. 이후 실제 SQLite에서 선택한 요청·담당자를 완료 처리하고 결과 마감을 연결한다.

stdout JSON은 `complete` 또는 짧은 오류 코드를 반환한다. 성공은 종료 0, 입력·검증·실행 오류는 2다. 비공개 journal에 단계·시각·경과 시간과 진단을 남기며 원문 대화나 상세 실패 메시지는 새로 복제하지 않는다. 원래 hook·사용량 근거는 해당 도구의 보관 규칙을 따른다.

## 재실행과 실패

같은 입력의 재호출도 현재 원본·프로필·보관본을 검사한다. 성공 journal은 바꾸지 않고 별도 replay 점검 기록을 남긴다. DB가 복원됐을 때도 동일한 검증 근거만 다시 수입하며 토큰을 중복 가산하지 않는다. 유효한 후속 턴 추가는 허용하고 기존 측정 구간 변경은 거부한다.

영수증 부재·복수 후보·불명확한 귀속·원본 변경·잘못된 마감 참조는 삭제 전에 중단한다. 연결되지 않은 정상 이벤트는 진단에 남기고 worker로 추정하지 않는다. 손상된 전역 영수증은 보수적으로 차단한다. 첫 실행에 없던 영수증이 나중에 도착하면 기존 journal의 입력 집합과 달라지므로 자동 편입하지 않는다. 새 근거를 확인해 별도 출력 디렉터리에서 명시적으로 재개하며 기존 실패 기록은 보존한다.

파일·프로세스·SQLite 전체가 하나의 트랜잭션은 아니다. 일부 원본 정리 후 다음 자원에서 실패하면 보관본과 단계별 영수증을 보존하고 같은 입력으로 확인한다. 정리 중 격리 경로에 파일이 남은 경우에는 기존 정리기의 명시 점검 요구를 따른다. 잠금이나 journal을 지워 강제로 재시작하지 않는다.

완료는 제품 수용과 등록 자료의 마감을 뜻한다. `full_usage_complete`는 항상 false다. 조율자의 모든 턴·전달되지 않은 Stop·다른 공급자·원격 실행·자동 승인 검토 사용량은 이 명령만으로 수집하지 않는다. 앱 전역 완료 hook, 승인 알림 연결, 임의 프로젝트 탐색·TTL 삭제·프로세스 강제 종료도 포함하지 않는다.

## 검증

```sh
python3 -m unittest discover -s tests -p 'test_complete_request*.py'
```

단계 연결 검사와 함께 가상 native 원본을 실제 파서·수입기·SQLite·보관기·정리기에 연결한다. Y/N, pending, 불완전·변경 원본, replay, 마감 검증 실패, 복수 자원 부분 실패, 정리 중단, 별도 프로세스 잠금을 검사한다. 이 가상 검사를 실제 Orca 작업 마감과 구분해서 보고한다.

등록된 로컬 요청에서 실제 종료 worker와 명시한 조율자 닫힌 턴을 검사하고, 여러 임시 자원의 전체 보관·기본 Y 정리·SQLite 마감을 한 명령으로 확인했다. 같은 입력의 재호출에서 성공 journal과 요청·토큰 기록이 유지됐고, 기존 요청 보존과 새 DB 복원도 대조했다. 입력 manifest 준비와 제품 수용은 조율자가 수행했으며 전역 자동 마감을 검증한 것은 아니다. 이 결과만으로 전체 업무 시간이나 토큰 절감 효과를 단정하지 않는다.
