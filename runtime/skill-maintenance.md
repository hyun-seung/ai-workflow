# 역할 스킬의 유지관리

상태: 역할별 연결·고정 소스 설치·로컬 관리 도구를 제공한다. 초기 설치는 source-reviewed `pilot`이며 업무 품질·native 발견·예약 실행의 실제 확인 범위는 설치별 기록으로 구분한다. 이 문서가 예약 등록 자체를 대신하지 않는다.

2026-11-14 한 로컬 Orca 설치에서 8개 원본 설치·7개 적용/관리 스킬 발견과 형식 검사, 관리 경로 11개·전체 Python 350개 검사, 별도 검토자의 3개 역할 시나리오를 확인했다. 최초 예약 시험은 재시작 후 추적 중단·네트워크/ID 표현 차이로 부분 확인이었고, 같은 예약의 두 번째 수동 시험에서 대상 ID·원문 접근·무결성·역할 선택·결과 저장과 Orca completed를 확인한 뒤 주간 예약을 활성화했다. 이 시험은 신규 후보 탐색·실제 제품 과제·실 라이브러리의 자동 교체/은퇴나 정시 트리거 검증은 아니다. 관리 동작의 추가/복구 검사는 격리 fixture를 사용했다.

## 관리 대상과 권한

사용자가 요청한 신규·인기 스킬의 정기 검토와 좋은 후보의 추가, 기존 스킬의 개선·대체·삭제를 **이 설치의 역할 스킬 라이브러리**에 적용한다. 공통 시작 목록은 `skills/catalog.json`, 설치별 활성 목록·무결성·이력은 Git에서 제외된 `local/role-skills/registry.json`이다. 예약은 그 로컬 목록과 `local/role-skills/`만 변경한다. 다른 프로젝트·전역 스킬·원래부터 설치된 사용자 스킬·역할 문서·권한·hook·스케줄·모델 설정·Git 커밋·push는 변경하지 않는다.

삭제는 역할 선택에서 제외한 뒤 관리 라이브러리에서 archive로 이동하는 복구 가능한 은퇴다. 임의 사용자 폴더나 이전 버전을 영구 삭제하지 않는다. 수정된 원본·심볼릭 링크·무결성 불일치는 보존하고 자동 변경을 보류한다. 로컬 보관·Git ignore는 보안 저장소가 아니므로 실행 기록은 공개하지 않는다.

## 정기 탐색과 판단

주 1회 새 세션에서 기존 상태·최근 완료 보고서와 지정된 출처를 확인한다. 후보는 공식 공급자 저장소, `skills/catalog.json`의 discovery_sources, GitHub 검색에서 찾는다. 알려진 저장소만 재조회해서 신규 생태계 탐색을 했다고 주장하지 않는다. GitHub 전체의 최신·인기 상승 후보도 검색하되 한 실행의 심층 후보는 최대 3개, 설치/교체는 최대 1개로 제한한다. 처음 본 저장소의 누적 stars를 증가량으로 표현하지 않는다. 비교할 관측이 있을 때만 동일 저장소·기간의 stars 변화, 릴리스·관련 경로 commit, issue 대응·사용 사례를 비교한다.

판단 순서는 역할에 대한 실제 이점 → 기존 스킬과 차이 → 원문·라이선스·지원 버전 → 설치/실행 부작용 → 대표 과제 결과다. 별점·업데이트 날짜 하나로 추가하거나 삭제하지 않는다. 스킬 경로가 그대로면 저장소의 다른 파일 변경은 해당 스킬의 개선이 아니다. 안정적인 문서 스킬은 장기간 변경이 없어도 유지할 수 있다.

| 판단 | 필요한 근거와 조치 |
| --- | --- |
| 유지 | 현재 역할·SDK에 맞고 대표 과제에서 유효함. 억지로 교체하지 않음 |
| 신규/업데이트 시범 | 실제 원문·관련 참조·스크립트·라이선스 확인, SHA 고정, 지원 환경 대조, 격리 과제와 결과 검토 후 pilot 설치 |
| 활성 승격 | 해당 버전/역할/태그에 대한 검증 증거가 있고 기존 대비 이점 또는 필요한 기능이 확인됨 |
| 비활성 | 재현된 오동작·호환성 결함·중복·안전상 문제. 이유·영향받는 역할·대체안 기록 후 선택에서 제외 |
| 은퇴/삭제 | 비활성 후 의존·역할 누락과 대체 결과를 확인. archive로 이동해 복구 경로 보존 |
| 보류 | 조회 실패, 품질 근거 부족, 접근/권한/예산 한계. 이전 성공 관측과 버전 유지 |

전체 프레임워크·자체 위임·상시 hook·바이너리 다운로드·권한 우회·외부 전송을 요구하는 후보는 실행하지 않는다. 현재처럼 특정 참고 문서만 쓸 수 있으면 `reference-only`로 기록하고 제한된 모드 자체를 시험한다. 이런 제한 안에서 쓸 수 없는 후보는 보고만 한다. 브라우저·native 검증은 선택한 환경의 공식 도구를 사용한다.

## 실행과 기록

등록된 요청은 [예약 요청문](../templates/skill-review-automation.md)을 읽는다. `local/role-skills/runs/<UTC시각-임의접미사>/`에 report.md, observations.json, 필요한 candidate.json·evidence.json·시험 산출물을 만들고 마지막에 complete.json을 쓴다. 출처 실패는 partial로 표시하고 마지막 성공 관측을 보존한다. 이전 전체 실패를 성공 기준선으로 바꾸지 않는다. 없음/실패를 0 stars나 활동 없음으로 채우지 않는다.

설치·변경 명령은 [관리 도구](../scripts/skill_catalog.py)로 실행한다. 새 후보 JSON은 catalog의 항목과 같은 형식이며 원본 SHA·역할·태그·실행 모드·라이선스 경로·검토 이유를 포함한다. 자동 신규/교체와 active 승격에는 아래 evidence 파일이 필요하다. 도구는 대상 버전/역할의 일치와 산출물 해시를 검사한다. `true` 값과 해시만으로 실제 품질을 보장하지 않으므로 실행 담당자가 원본·시험·결과를 직접 확인해야 한다. 자동 변경에는 검토한 registry 파일의 SHA-256을 `--expected-registry`로 전달한다. 잠금 안에서 다시 대조하므로 검토 중 생긴 사용자·다른 실행의 변경은 덮어쓰지 않는다. 성공한 변경 뒤에는 결과를 확인하고 새 해시로 다음 결정을 검토한다.

```json
{
  "subject": {"id":"example-skill","repo":"example/skills","path":"skills/example-skill","ref":"0000000000000000000000000000000000000000","roles":["frontend"],"tags":["web"],"mode":"reference-only"},
  "source_review": true,
  "compatibility_review": true,
  "trial_passed": true,
  "artifacts": [{"path":"local/role-skills/runs/example/trial.md","sha256":"실제 SHA-256으로 교체"}]
}
```

```sh
python3 scripts/skill_catalog.py --expected-registry <검토한-registry-SHA256> add --record local/role-skills/runs/<run>/candidate.json --evidence local/role-skills/runs/<run>/evidence.json --installer /path/to/skill-installer/scripts/install-skill-from-github.py
python3 scripts/skill_catalog.py status --id <ID> --state active --reason '<시험 결과>' --evidence local/role-skills/runs/<run>/evidence.json
python3 scripts/skill_catalog.py status --id <ID> --state disabled --reason '<문제와 대체안>'
python3 scripts/skill_catalog.py status --id <ID> --state retired --reason '<의존 확인과 삭제 이유>'
python3 scripts/skill_catalog.py status --id <ID> --state pilot --reason '<복원 후 재검증>'
python3 scripts/skill_catalog.py check
```

초기 seed의 명시적 설치만 원문 확인 후 시험 전 pilot로 배치할 수 있다. 업데이트는 원래 폴더를 덮어쓰지 않고 새 SHA 폴더를 만든다. 이전 spec·무결성·위치는 history에 보존한다. `rollback --id <ID> --reason '<이유>'`로 직전 설치 버전을 복원할 수 있다. 새 버전도 보존하며, 원본을 수정해 무결성을 억지로 맞추지 않는다. 충돌·중단으로 생긴 고립 버전은 보존 후 조사한다.

스킬 추가가 기존 역할의 승인 범위를 넓히지 않는다. 시험 데이터와 격리된 산출물로 확인하고 실제 제품 수정·외부 사이트 변경을 부수 효과로 만들지 않는다. 변경한 내용에 필요한 검사만 수행한다.

## 대표 시험

- 디자이너: 기존 시스템을 지키는 작은 폼 개선과 새 화면 방향 탐색을 구분하는가.
- 프론트엔드: 긴 제목·오류/재시도·좁은 폭에서 지정한 디자인과 동작을 유지하는가.
- 백엔드: API 실패 원인을 근거로 추적하고 권한·계약을 임의로 바꾸지 않는가.
- 검증자: 개발자 보고에 없는 경계 조건을 재현하고, 구현 수정은 담당자에게 돌려보내며 미검증을 통과로 표시하지 않는가.
- 조율자/설계자: 필요한 역할·스킬만 선택하고 원래 AC·계약·산출물을 연결하는가.

초기에는 선택·원문 사용 모드와 비활성/보관/복원 경로를 로컬에서 검증한다. 실제 Web/native 화면과 API를 동반한 시험은 대상 프로젝트에서 수행하며 미실시를 완료로 포함하지 않는다.

## 예약과 환경

적용 대상은 명시한 로컬 Orca 작업 공간이다. 주간 시각·timezone·자동화 ID·CLI와 installer의 실제 경로는 `local/role-skills/registration.json`에 둔다. 비활성 상태에서 수동 시험 후 활성화한다. 결과는 Orca Automations의 실행 이력과 각 실행 세션에서 확인한다. 현재 대화나 외부 메신저로 알림을 보내지는 않는다. 기기·Orca가 오프라인이거나 지정 workspace가 삭제되면 실행되지 않을 수 있고, 다른 checkout으로 자동 이동하지 않는다.

precheck로 자료·설치 무결성을 확인한다. Run now는 Orca 사양상 precheck를 건너뛰므로 요청문에서도 같은 확인을 한다. 실제 권한·모델은 실행 시 확인하고 부모의 성공을 자식 권한으로 간주하지 않는다. 실패를 새 worker·예약·권한 변경으로 우회하지 않는다. 후보·변경 개수 제한은 모델 토큰 상한 강제 기능이 아니다.
