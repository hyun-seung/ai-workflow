# 완료 수집 상태 점검과 명시 재처리

등록된 배정과 이미 받은 Stop 영수증을 대조한다. [완료 hook](completion-hook.md)은 그대로 사용하고, 새 도구는 조율자가 필요할 때 한 번 실행한다. 상시 서버·주기 폴링·에이전트 추가·세션 폴더 탐색은 없다.

## 상태 점검

```sh
python3 scripts/completion_recovery.py --config /private/path/completion-hook.json status
python3 scripts/completion_recovery.py --config /private/path/completion-hook.json \
  status --request-id request_example
```

동일 config의 ledger·비공개 영수증/메타데이터·읽기 전용 SQLite를 대조한다. transcript_path는 열지 않고, 입력의 경로를 따라 다른 파일을 탐색하지 않는다. 출력 JSON에는 배정별 상태와 영수증별 원인이 있다. 출력도 실제 식별자가 포함되는 비공개 기록으로 취급한다.

| 상태 | 확인한 의미 |
| --- | --- |
| collected | 저장된 메타데이터 해시와 해당 DB 사용량 이벤트의 요청·actor·역할·물리 구간·값이 일치 |
| pending | 받은 이벤트의 처리가 미완료. 닫히지 않은 턴 또는 미완료 수집, 보존된 수입 intent를 구분 |
| missing | 수집 대상으로 등록된 배정에 연결된 Stop 영수증이 없음. 토큰 0이나 실행 실패라는 뜻은 아님 |
| not_ready | native 배정 등록이 확정되지 않음. 수집 성공으로 계산하지 않음 |
| needs_attention | 변경된 binding·손상된 기록·DB 누락/불일치·소유권/경로 문제 등 확인 필요 |

`expected_operations`는 opt-in 배정 수이고 `received_receipts`는 실제 받은 Stop 수다. 한 배정에 여러 영수증이 있으면 모두 확인하며 하나라도 미완료이면 숨기지 않는다. 등록되지 않은 이벤트, 모호한 연결, 형식이 잘못된 영수증은 별도 오류로 남긴다. 요청 필터는 알려진 다른 요청의 영수증을 제외하지만 출처를 확인할 수 없는 영수증을 임의로 무시하지 않는다.

**collected는 받은 영수증의 확인이며 그 배정의 모든 턴이 수집됐다는 보증이 아니다.** Stop 자체가 전달되지 않은 추가 턴은 이 목록만으로 발견할 수 없다. `full_usage_complete`는 항상 false다. 조율자·다른 공급자·원격·자동 승인 검토의 토큰을 추가 추정하지 않는다. native 작업 성공·정확한 프로세스 종료·제품 수용도 이 도구의 판정 대상이 아니다.

## 한 건 재처리

```sh
python3 scripts/completion_recovery.py --config /private/path/completion-hook.json \
  retry --operation-id operation_example --receipt-key '64자리-영수증-key'
```

이미 operation에 고정된 영수증 한 건만 지정한다. 새 세션 경로·요청·역할을 입력하거나 binding을 바꿀 수 없다. 같은 원래 이벤트를 기존 hook 수집기에 전달해 한 번 시도한다. 수집기는 원본의 닫힌 턴·출처·배정·카운터를 다시 검사하고 같은 측정 키를 재사용한다. 재처리는 사용량이나 actor를 덮어쓰지 않는다.

원본이 아직 닫히지 않았거나 중단된 턴이면 pending을 유지한다. DB가 이전 백업으로 복원돼 이벤트가 빠졌다면 같은 저장 근거를 재수입할 수 있다. 원본 파일이 없거나 원래 binding·메타데이터가 달라졌다면 멈춘다. 완료된 영수증 replay도 현재 원본을 다시 검사해 저장된 측정 interval·baseline/증가분·카운터 및 해당 구간의 context/배정과 대조한다. 유효한 다른 배정 턴이 뒤에 추가된 경우는 전체 파일 해시만 다르다는 이유로 거부하지 않는다. 기존 파서가 미귀속 후속 턴 등으로 원본을 재검증하지 못하면 거부한다. 수집기가 원본을 요구하는 조건을 snapshot만으로 우회하지 않는다. 영수증이 아예 없는 missing은 이 명령으로 자동 복원하지 않으며, 확인된 공식 원본 경로와 별도 귀속 근거가 필요하다.

`recovery/<key>.json`에 동일 binding의 최근 재처리 결과·시각·횟수·제한된 오류 코드를 남긴다. 실패 메시지 원문·대화 본문은 저장하지 않는다. 수집 전 running을 저장하므로 프로세스가 중단되면 다음 점검에서 running이 남을 수 있다. 이는 현재 실행 중/중단 중 어느 쪽인지 확정하지 않는다. 잠금이 해제된 뒤 명시 재호출할 수 있고, 같은 key의 동시 재처리는 거부한다. 횟수와 입력 목록은 각각 1,000개로 제한하고 초과 시 잘라서 성공으로 표시하지 않는다. 이 파일은 최근 상태 기록이며 전체 시도 이력 저장소는 아니다.

CLI는 확인된 수집/재처리 성공에 0, 미완료·누락·주의 상태에 1, config·명시 입력 오류에 2를 반환한다. 한 건의 오류가 업무를 자동 취소하거나 승인·삭제를 유발하지 않는다. 영수증·DB·ledger를 검사하는 사이 다른 수집이 진행될 수 있으므로 상태는 출력 시점의 관측이다.

## 검증과 한계

```sh
python3 -m unittest discover -s tests -p test_completion_recovery.py -v
python3 -m unittest discover -s tests -p test_prepare_handoff.py -v
```

가상 세션과 로컬 subprocess/SQLite로 동시 수입, commit 전후 중단·새 프로세스 복구, DB 복원, replay와 변경된 카운터·context·귀속 거부를 검사한다. 실제 Orca·Codex 첫/후속 배정의 자동 수입과 단건 replay도 확인했다. 상세 실행 로그·원본·사용량은 비공개 보고서에 보관한다.

Orca 앱 자체 재시작·실제 업무 취소·Stop 미전달 턴의 복원, 다른 공급자·원격 환경은 미검증이다. 도구가 확인한 영수증과 전체 실행의 수집 완전성을 구분한다.
