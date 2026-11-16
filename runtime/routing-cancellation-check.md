# 실제 worker 취소·종료 점검

2026-11-16, Orca 1.4.259에서 사용자가 선택한 **Sol · medium, 현재 worktree, 읽기 전용 검증자 1회, 동시 1·누적 1·자동 재시도 없음**으로 실시했다. 중복으로 도착한 같은 사용자 선택은 한 건의 허용으로 처리했다. 이전 시험의 허용을 초기화하지 않았다.

초기 시험 결과는 **부분 확인**이다. 실제 한 건의 중지·정확한 종료·관리 슬롯 해제·취소 재전달은 확인했다. worker의 준비 신호 전송은 IPC 권한 문제로 실패해 준비 신호를 통한 정상 경로는 확인하지 못했다. 추가 worker·질문 재전송·native 중지 재시도는 하지 않았다. 이후 별도로 선택받은 시험의 정상 수신 결과는 아래에 구분한다.

## 결과와 근거

| 항목 | 결과 | 근거·한계 |
| --- | --- | --- |
| 현재 조율자·작업 공간 | 확인 | 공식 연결 조회의 세션 신원과 `worktree current`의 프로젝트 경로 일치 |
| 요청/실행환경 적용 프로필 | 일치 | 정확한 worker snapshot의 `launch.requested/effective`가 모두 `gpt-6.1-sol / medium` |
| 공급자 실제 모델·effort·토큰 | 미확인·미측정 | 원본 경로를 탐색하거나 요청/적용값을 공급자 관측으로 복사하지 않음 |
| 준비 신호 | 실패 | worker가 `ask`를 시도했으나 `runtime_access_denied`·EPERM·종료 코드 1을 보고. 알림은 정상 질문이 아닌 미보고 종료 escalation으로 수신 |
| 실제 중지 | 확인 | 승인된 같은 worker만 한 번 중지, native receipt의 `state: stopped`와 대상 일치 |
| 물리적 종료 | 확인 | 정확한 같은 task/dispatch의 로컬 조회에서 `exactWorker: true`, `status: exited` |
| 관리 슬롯 | 확인 | 종료 상태 호환성 수정 후 동일 배정을 조회해 `released` 전환 |
| 취소 재전달 | 확인 | `replay: true`, 선택·ledger의 내용과 수정 시각 불변, 누적 배정 1 유지 |
| 정상 완료·성공 판정 | 하지 않음 | dispatch는 `failed`, worker는 `stopped`. checkpoint 실패를 성공으로 바꾸지 않음 |

읽기 전용은 worker의 업무 범위이며 OS 권한을 별도로 설정했다는 뜻은 아니다. worker는 프로젝트 변경 없음과 실제 프로필 unknown을 보고했다. 원본 영수증·snapshot·선택·ledger·권한 실패 보고는 소유자 전용 비공개 임시 기록에 보관하고 실제 경로·식별자·대화 원문은 공개하지 않는다.

## 실제 응답에서 발견한 수정

기존 `scripts/lifecycle_io.py`의 종료 검사는 worker 상태 `succeeded/failed/cancelled`만 인정했다. 이번 실제 응답은 **dispatch가 failed이고 worker가 stopped이며 정확한 종료가 관측된 조합**이었다. 따라서 native 중지 후에도 관리 슬롯이 미확인으로 남았다.

종료 상태 목록에 `stopped`를 추가했다. 로컬 host, 정확한 task/dispatch/worker 신원, terminal dispatch, `exactWorker: true`, `status: exited` 조건은 유지한다. `stopped` 문자열이나 중지 성공 영수증만으로 해제하지 않는다. 기록의 outcome은 `stopped`로 보존하므로 성공한 선행 작업으로 취급하지 않는다.

신규 회귀 두 개는 이 실제 상태 조합의 해제·재전달·성공 의존성 차단과, 실행 중·신원 불일치·원격 host·미종료 dispatch·실패 응답에서의 해제 거부를 확인한다. 중지 12개·lifecycle 27개·관리 실행 27개·진입 흐름 19개·완료 연결 21개, **관련 검사 총 106개가 통과**했다. 이는 가상 자료를 사용하는 코드 회귀 검사이며 독립 검토나 실제 비용·품질 비교가 아니다.

수정 후 기존 worker에 대해 종료 조회와 취소 replay만 수행했다. 새 worker를 띄우거나 두 번째 native 중지로 결과를 만들지 않았다. 위 코드·회귀 검사·문서 보완 외에 환경 설정이나 기존 사용자 작업을 변경하지 않았으며 worktree·파일 정리도 수행하지 않았다.

## 남은 조건

- **자식 권한의 상세 근거:** 초기 실패 이후 [IPC 요약](orca-ipc-brief.md)·[작업 양식](../templates/orca-ipc-checkpoint.md)으로 새 배정 재시도와 동일 명령의 정식 승인 요청 범위를 구분했다. 아래 후속 시험에서 정상 질문 수신은 확인했지만 승인 요청 옵션·검토 영수증은 별도로 관측하지 못했다. 부모의 조회 성공이나 worker의 자기 보고만으로 자식의 권한 설정을 인증하지 않는다.
- **추가 시험:** 이번 한도는 소진됐다. 같은 시험을 새 요청 ID·operation으로 자동 반복하거나 질문을 다시 보내지 않는다. 이후 실행은 새로운 명시 선택·범위 안에서만 진행한다.
- **실제 비교:** [공식 원본 연결](native-source-access.md)이 확보되어야 공급자 관측·전체 요청 비용 비교를 진행할 수 있다. 물리적 중지 성공으로 원본·토큰 수집 성공을 대신하지 않는다.
- **앱·사용성:** 현재 대화 모델 전환·hook/자동 상태 전달·튜터 사용성은 이번 점검 범위 밖이다. [개발 현황](../learning/development.md#남은-작업)에 별도로 유지한다.

기본 운영과 취소 계약은 [공통 진입 흐름](routing-flow.md), 실제 권한 준비는 [실행 권한 정책](permission-policy.md)을 따른다.

## IPC 인계 보완 후 별도 승인 시험

같은 날 사용자가 새로 선택한 Sol/medium 읽기 전용 검증자 한 건으로 실시했다. 동시 1·누적 1, worker 자동 재시도·질문 재전송 없음, checkpoint 명령의 정식 권한 요청 한 건과 같은 worker의 중지 한 번을 허용한 별도 범위다. 이전 한도를 재사용하지 않았다.

| 확인 항목 | 결과 |
| --- | --- |
| 준비 신호 수신 | native question의 task/dispatch가 이번 배정과 일치. `IPC_CHECKPOINT_READY` 정상 수신 |
| 질문 명령 결과 | 해당 worker의 제한 조회에 checkpoint 명령 한 건과 종료 코드 0이 보임 |
| 자식 권한 자기 보고 | reviewer는 `auto_review`, sandbox는 `workspace-write`, approval policy는 unknown |
| 정식 승인 절차의 독립 근거 | 미확인. 정규화된 transcript에는 shell의 command/cwd와 종료 코드만 있으며 escalation 옵션·승인 검토 영수증은 노출되지 않음. 질문 수신을 Auto-review 실행 증거로 바꾸지 않음 |
| 요청/실행환경 적용 프로필 | 정확한 종료 snapshot의 requested/effective가 모두 `gpt-6.1-sol / medium` |
| 같은 worker 중지·종료·슬롯 반환 | native 중지 한 번, 정확한 로컬 worker의 exited 관측, 관리 상태 released 확인 |
| 취소 replay | 추가 native 중지 없이 기존 결과 재사용. 선택·ledger의 내용/수정 시각 불변, 누적 배정 1 유지 |
| native 결과 보존 | dispatch의 failed·worker의 stopped·관리 outcome stopped를 보존. 관측 시험의 결과를 업무 성공으로 덮어쓰지 않음 |

이 범위에서는 **준비 신호→취소→정확한 종료→재전달의 실제 경로를 확인**했다. 공급자 실제 모델·effort·토큰, 세부 승인 과정, 상시 무인 운영 준비는 여전히 미확인이다. 이를 위해 추가 worker나 승인 시험을 자동 반복하지 않는다. 기존 59개 관련 검사 이후 코드는 변경하지 않았고 이번에는 실제 결과와 문서만 갱신했다.
