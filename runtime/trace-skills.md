# 역할별 스킬 기록과 관리 상태

역할 문서·적용 스킬의 연결은 [skills/README.md](../skills/README.md), 실제 사용 관측은 이 문서의 명시 기록으로 구분한다. 문서 연결·설치·선택만으로 실제 읽기·작업 적용·도구 실행을 자동 감지하지 않는다. 과거 기록에 없던 스킬을 역할이나 설치 목록에서 추정하지 않는다.

## 요청·담당자에 연결하는 사용 기록

기존 bundle/JSONL 수입에 `kind: skill_use`, `source: trace.skill-use.v1` 이벤트를 추가한다. 같은 ID의 내용은 불변이다. `source_id`는 이벤트 ID와 같고 `request_id`, `actor_id`, timezone이 있는 `time`, `completeness: partial`이 필요하다. 아래는 가상 detail이며 실제 출처·커밋·근거로 바꾸어야 한다.

```json
{
  "version": 1,
  "skill": {
    "id": "example-skill",
    "source_url": "https://github.com/example/skills",
    "source_ref": "0000000000000000000000000000000000000000",
    "mode": "reference-only"
  },
  "role": "frontend",
  "stage": "applied",
  "purpose": "긴 텍스트와 오류 복구 규칙을 화면에 적용",
  "result": {"status": "partial", "summary": "Web만 확인; native는 미검증"},
  "evidence_event_ids": ["example-verification"]
}
```

- `stage`: `selected` 선택, `read` 문서 참고, `applied` 작업 적용, `executed` 도구 실행. 읽은 지침을 적용하는 것과 스크립트/엔진 실행을 구분한다. reference-only는 executed를 허용하지 않는다.
- `result.status`: `unverified`, `pass`, `fail`, `partial`. 선택/참고만으로 통과를 기록할 수 없다. 미검증 외의 결과에는 applied/executed와 같은 요청의 tool_result/verification 근거가 필요하다. 자기 자신이나 다른 사용 기록은 근거가 될 수 없다.
- `source_ref`는 40/64자리 고정 커밋이다. URL은 자격 증명·query·fragment 없는 HTTPS 출처다. 수입 검사는 원격 커밋의 실재나 실행 사실을 인증하지 않는다.
- `role`은 기록 당시 수행한 공통 역할이다. 실행자는 actor_id로 보존하고 여러 역할 관점을 별도 에이전트처럼 만들지 않는다. 독립 검증자의 근거를 인용할 수 있어 근거 actor는 달라도 되며 화면에서 원래 담당자를 확인할 수 있다.

역할 카드를 펼치면 단계·판정·목적·원본 버전·연결된 근거를 볼 수 있다. `통과 기록`은 명시 판정의 표시이며 스킬 전체의 품질 인증이나 설치 상태의 active 승격이 아니다. 미수입은 `스킬 사용 미기록`이며 미사용으로 단정하지 않는다. 가상 [skill-flow.json](../tests/fixtures/traces/skill-flow.json)은 실제 업무 자료가 아니다.

## 읽기 전용 라이브러리 연결

```sh
python3 scripts/trace_viewer.py --db /private/path/traces/workflow.sqlite3 serve --port 8765 --skills-root /path/to/ai-workflow
```

명시한 공통 저장소의 catalog·registry를 조회한다. `/api/skills`는 등록 상태·역할·고정 버전·전체 파일 무결성 결과와 최근 변경 이력을 제공한다. 라이브러리 절대 경로·원본 파일별 해시 목록은 반환하지 않는다. 운영자가 명시한 경로의 보고서·이력 문구는 로컬 비공개 자료이며 공개용 데이터로 재사용하지 않는다. root 인자를 생략하면 미연결로 표시한다. HTTP query로 경로를 바꾸거나 스킬을 실행·설치·변경하는 기능은 없다. 기존 same-origin/loopback 경계를 유지한다.

최근 정기 검토는 `local/role-skills/runs/<run>/review-summary.json`을 읽는다. 모든 연결된 폴더의 명시 검토 시각으로 최신을 선택하며 최신 실패를 이전 성공으로 대체하지 않는다. 잘못된 보고서가 있으면 누락 경고를 표시하고 전체 최신 검토라고 단정하지 않는다. 구조화 요약이 없는 과거 Markdown을 성공으로 추정하지 않는다. `trials/`는 주간 검토 기준선에 포함하지 않는다.

요약은 `schema_version: 1`, 폴더명과 같은 `run_id`, timezone이 있는 `reviewed_at`, `status: complete|partial|failed`, `summary`, `candidates`를 갖는다. 후보는 최대20개이며 `id`, `decision: add|update|hold|disabled|retired|error`, 고정 `source_ref` 또는 null, `reason`을 갖는다. 검토 판단은 실제 설치 변경과 다르므로 등록 상태·변경 이력과 함께 확인한다. 파일은128KiB 이하여야 한다.

```sh
python3 scripts/trace_skills.py local/role-skills/runs/<run>/review-summary.json
```

이 명령은 형식을 확인할 뿐 실제 점검을 수행하거나 라이브러리를 바꾸지 않는다. 예약 절차는 [스킬 유지관리](skill-maintenance.md)를 따른다. 실제 토큰은 별도 [사용량 계약](trace-usage.md)을 따르며 스킬별로 임의 배분하지 않는다.

## 실제 운영 연결을 확인할 때

1. 선택한 환경의 공식 도구로 기존 예약 실행을 먼저 확인한다. 실행 중이면 중복 실행하지 않는다. 예약 시각과 실제 시작 시각을 각각 보존하고 지연 실행을 정시 성공으로 기록하지 않는다. 지연 원인은 근거가 있을 때만 설명한다.
2. 도구의 실행 완료, 실행 폴더의 `complete.json`, 유효한 `review-summary.json`, 실제 화면 반영을 따로 확인한다. 도구에서 completed여도 보고서의 partial/failed를 유지한다. 요약이 없으면 기존 보고서에서 성공 요약을 만들어 넣지 않는다.
3. 가상 DB와 별도의 비공개 DB에 실제 요청·담당자·수행 근거를 연결한다. 원래 요청 시각·모델·토큰이 미확인이면 그대로 둔다. 관측자가 실행 상태를 조회한 명령과 예약 담당자가 수행한 행동을 같은 실행자의 기록으로 합치지 않는다. 필요하면 [실행자 관측 계약](trace-observations.md)을 사용한다.
4. 실제로 선택·참고·적용·실행한 스킬만 고정 버전과 근거로 기록한다. 작업 중 부분 확인 기록은 덮어쓰지 않고 보존한다. 이후 [마감 판정](trace-closeout.md)은 그 시점의 확인 범위·남은 제한을 별도로 설명한다.
5. 같은 요청이 실제 화면에서 조회되고 스킬 단계·근거·정기 검토 판정이 원본과 일치하는지 확인한다. 연결 정합성의 검증 통과와 신규 스킬 전체의 품질·native·다른 역할 검증을 구분한다. 조회 화면과 파일 해시는 실제 실행을 자동 감지하거나 전체 수집을 보장하지 않는다.
