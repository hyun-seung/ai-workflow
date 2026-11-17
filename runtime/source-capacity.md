# 원본 용량 진단

`python3 scripts/source_capacity.py --project /canonical/existing/project --session-file /canonical/explicit/source.jsonl`은 명시한 원본의 속성만 확인한다. 프로젝트는 기존 디렉터리, 원본은 현재 사용자 소유의 단일 링크 일반 파일이어야 하며 모든 경로 구성요소는 절대·정규 경로이고 심볼릭 링크가 없어야 한다. 원본 내용을 열거나 읽거나 해시·복사·chmod하지 않으며 세션 탐색, DB 기록, native 호출, 새 세션 생성이나 자동 조치는 없다.

한도는 기존 `native_record.MAX_BYTES`(128 MiB)를 가져온다. 이는 수집 harness 구현의 원본 크기 상한이며 모델의 context window나 token budget이 아니다. 경고선 `(MAX_BYTES * 9) // 10`은 저장소 운영상 조기 경고 기준이며 공급자의 공식 권고나 admission 변경이 아니다.

| status | 크기 | 종료 |
| --- | --- | --- |
| within_limit | 0 이상, 경고선 미만 | 0 |
| near_limit | 경고선 이상, 한도 이하 | 0 |
| over_limit | 한도 초과 | 1 |

0바이트와 정확히 한도인 파일도 크기 조건만 만족한다. JSON 유효성, token accounting, 턴 종료, 비공개 권한, 원본 admission은 검사하지 않는다. 0644 원본도 용량 진단할 수 있지만 private 또는 admitted라고 주장하지 않는다. 저장 원본의 비공개 메타데이터 선행 조건은 기존 `source_preflight.py`의 별도 책임이다.

성공한 속성 결과에는 `scope=source_capacity_only`, `source_bytes`, `max_source_bytes`, `warning_at_bytes`, `remaining_bytes=max(0,limit-size)`, `fits_parser_limit`이 있다. `measurement_validated`, `session_identity_verified`, `permission_granted`는 항상 false다. 오류는 짧은 JSON이며 경로·OS 오류 원문을 출력하지 않는다. 잘못된 CLI/경로 형식이나 유효하지 않은 프로젝트는 종료 2다. 원본 부재·비일반 파일·소유자·하드링크·경로 alias·접근 실패는 종료 1이다. 프로젝트의 링크 alias도 종료 1이다.

반환 전에 구성요소의 no-link/정규 경로와 lstat 속성을 두 번 확인하고 dev/inode/size/mtime_ns/ctime_ns/uid/mode/nlink를 대조한다. 두 번째 검사 실패나 변경은 `source_changed`, 종료 1이며 성공 용량을 반환하지 않는다. 외부 파일 잠금이나 반환 후 성장 방지 보장은 아니다. 접근 가능한 메타데이터만으로 파일 내용 읽기 권한을 보장하지 않는다.

경고선에 이르면 현재 승인된 한정 작업을 마무리하고 검증한다. 기존 원본과 비공개 증거를 보존하고 지원되는 동안 수집 가능한 종료 턴을 명시적으로 수집한다. 수집 불가능한 구간은 미측정으로 남긴다. 한도를 넘으면 기존 수집기는 전체 원본을 계속 거부한다. 한도 상향, 원본 slicing/truncation/filtering/copy로 우회하거나 전체 identity를 생략하거나 오래된 snapshot을 가져오지 않는다.

필요할 때 지원되는 Orca 제품 UI로 새 대화를 만든다. 프로젝트 지침, 승인된 결정, 파일 경로, 남은 완료 기준, 이미 수집한 measurement key와 미해결 공백을 짧게 인계한다. 검증하지 않은 CLI/API reset·rotation을 지시하거나 세션 사이 누적 counter 연속성을 약속하지 않는다. 새 세션은 실제 native/session 연결을 확인해야 하고 ledger/coordinator 및 hook 설정도 기존 명시적 지원 설정 절차로 검토·재연결해야 한다. coordinator 자동 전환, 이력 재작성, request/usage/Stop 초기화를 하지 않는다. 자동 rotation이나 128 MiB 초과 수집 지원을 제공하는 기능이 아니다.

기존 원본·보고서·사용자 프로젝트는 보존한다. 임시 정리는 해당 작업의 합성 fixture만 대상으로 한다.

합성 경계 검사와 독립 검토에서 정확한 경고선·상한, 입력 오류, 링크와 변경 감지 및 내용 접근 금지를 확인했다. 실제 선택한 원본에서도 내용을 열거나 해시하지 않고 한도 근접 상태를 진단했으며 원래 inode와 권한을 유지했다. 이는 속성 진단의 검증이며 해당 세션의 토큰 수집이나 한도 초과 원본 수용을 검증한 것은 아니다.
