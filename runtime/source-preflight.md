# 명시적 소스 사전 확인

`python3 scripts/source_preflight.py --project /canonical/project --record-file /private/metadata.json`

기존 프로젝트와 명시적으로 저장한 schema 1 `orca_codex_metadata` 한 개만 입력한다. 메타데이터는 크기 제한, 유한 JSON, 중복 키, 프로젝트·세션 식별자와 변경 여부를 검사한다. 메타데이터는 프로젝트 및 Git 작업 트리 밖에 두며 소유자, 정규 파일, 단일 링크, 별칭 없는 절대 경로와 group/other 비트가 없는 권한을 요구한다. 지정한 원본은 내용 읽기 없이 파일 속성만 검사한다.

정식 준비는 사용자가 정확히 선택한 메타데이터와 원본의 소유권·접근 권한·보관 위치를 확인하는 작업이다. 필요한 권한 변경은 해당 파일만 대상으로 별도 승인된 절차에서 수행한다. 이 명령은 chmod, 복사, 검색, 앱 변경 또는 승인 처리를 하지 않는다. 명시적 소스로 완료를 진행하기 전에 실행한다.

종료 0은 파일시스템 전제만 충족함을 뜻한다. 종료 1은 missing, unreadable, wrong_owner, alias, hardlink, public_mode 등의 진단이며 종료 2는 잘못된 입력이다. ready는 원본 최신성, 측정 정확성, 프로필, native 종료, 권한 부여 또는 작업 수락의 증거가 아니다. 실제 완료 경로는 기존 전체 측정 검증을 계속 수행한다.

실패 응답의 `target.kind`는 준비가 필요한 논리적 파일(`metadata` 또는 `session_source`)이고 `stage`는 실패한 검사 단계다. 경로나 운영체제 오류 원문은 출력하지 않는다. `session_id`는 메타데이터의 프로젝트·종류·환경·공급자와 세션 ID 검증이 끝난 뒤에만 포함한다. 메타데이터를 읽기 전 실패한 응답은 세션이 검증되었다고 뜻하지 않는다.

메타데이터 0644 / 원본 0600인 경우 종료 1의 응답 예:

```json
{"status":"unmet","ready":false,"scope":"filesystem_prerequisites_only","checks":["public_mode"],"stage":"metadata_attributes","target":{"kind":"metadata"}}
```

메타데이터 0600 / 원본 0644이며 검증된 가상 세션 ID가 `synthetic`인 경우:

```json
{"status":"unmet","ready":false,"scope":"filesystem_prerequisites_only","checks":["public_mode"],"stage":"source_attributes","target":{"kind":"session_source","session_id":"synthetic"}}
```

`missing`, `alias`, `unreadable` 등도 같은 단계·대상 정보를 제공한다. 메타데이터 열기 중 접근이 거부되면 `metadata_read`, 검증 완료 후 마지막 메타데이터 속성 확인에서 실패하면 `metadata_recheck`로 표시한다. 입력 형식이 잘못된 종료 2 응답은 검증된 세션을 주장하지 않는다.

실제 소유자 전용 파일의 ready와 별도 실제 0644 원본의 `session_source` 진단을 확인했다. ready 확인 중에는 원본 열기·쓰기·chmod를 금지했고, 두 원본의 내용·권한은 유지했다. 이는 파일 전제 진단의 확인이며 실제 측정 수용이나 권한 변경 성공을 뜻하지 않는다.
