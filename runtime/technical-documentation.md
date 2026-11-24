# 기술 문서 변경 절차

[역할](../roles/technical-writer.md)과 [설정 양식](../templates/documentation-project.json)을 프로젝트에 연결한다. Markdown·Obsidian은 선택이며 Git·외부 서비스 없이 사용할 수 있다. 공통 원칙은 재사용하고 실제 문서 경로·담당자·환경은 프로젝트가 정한다.

1. 필요·독자·완료 기준을 확인한다. 관련 소스/설계/문서/검증 기록을 지정 범위에서 조사하고 검색 경계와 미확인을 기록한다.
2. 코드의 현재 구현, 설계 의도, 관측된 동작, 사람이 확정한 정책, 미결을 구분한다. 출처 우선순위는 조사 순서이며 모순을 자동 해결하는 규칙이 아니다. 근거가 다르면 사람에게 선택할 쟁점을 제안한다.
3. [제안](../templates/document-change-proposal.md)에 버전·무엇/왜/어디·구체 diff·정확한 대상 snapshot·설정 hash를 묶는다. [검토](../templates/document-review-result.md)는 승인/수정 필요/거절/보류를 명시한다. 내용 의미·정책 변경은 담당 사람의 명시 결정이 필요하다.
4. 승인 범위만 반영한다. 제안 내용/버전, 대상 파일/내용, 설정 변경은 기존 결론을 무효화한다. 작은 오탈자·링크 수정은 프로젝트의 유효한 명시 권한 안에서 직접 처리하며 매번 질문하지 않는다. 도우미의 minor 경로도 정확한 제안에 결합된 검토 기록이 필요하고, 기존 권한 원문을 evidence에 연결한다. 기존 포괄 권한을 새 의미 변경 승인으로 확장하지 않는다.
5. 링크·형식·충돌·반영 결과·남은 결정을 확인한다. 자체 확인, 자동 lint, 실제 별도 검토, 사람 정책 승인, 미검증을 따로 보고한다.

## 로컬 도우미

`python3 scripts/document_change.py --help`. [가상 시험](../examples/technical-documentation/README.md)에서 실행한다. 프로젝트·입력·설정 경로는 symlink 별칭이 없는 실제 경로를 사용한다. 기존 UTF-8 일반 파일 1~32개, 합계 2MiB 이내의 전체 교체만 지원한다. 신규 문서는 별도 승인된 생성 절차를 사용한다. 원본 snapshot의 경로 제한(숨김·민감 이름 제외)을 재사용한다. config와 request/review JSON은 중복 키를 거부한다.

- prepare: `--project PROJECT --config CONFIG --input REQUEST`로 제안과 sha256을 stdout에 출력한다. REQUEST는 version(양의 정수), author, kind(minor/meaning), rationale, changes(상대 파일명: 새 전체 내용)이다. 결과의 proposal 객체만 별도 JSON으로 저장한다. stdout을 기존 문서 파일로 redirect하지 않는다.
- 검토자는 diff·출처·대상을 실제 확인하고 별도 review 배열을 작성한다. 각 항목은 role(independent/policy), reviewer, conclusion, version, proposal_sha256, evidence, synthetic(false)이다. 도우미는 승인을 생성하지 않는다. 의미 변경은 independent와 policy 둘 다 필요하며 작성자는 독립 검토자가 될 수 없다. 독립성의 실제 수행과 사람 정책 권한은 운영자가 확인한다.
- check/apply: `--project PROJECT --config CONFIG --input PROPOSAL --reviews REVIEWS --actor APPLIER`. 설정 allowlist, 루트, symlink, 제안/diff/대상/설정 일치, 명시 결론을 검사한다. 미승인은 쓰기 전에 거부한다. apply는 기존 백업 디렉터리에 모든 원본을 독점 생성한 뒤 파일별 임시 파일 교체를 수행한다. 출력 complete=false 또는 종료 2이면 중단하고 applied/backups를 확인한다. 자동 rollback이나 무조건 재시도하지 않고 현재 상태에서 새 제안을 만든다. 원본 모드는 보존하지 않고 교체 문서는 0600이다.

### 검토 JSON 예시

도우미는 `conclusion`이 정확히 `"approved"`인 기록만 승인으로 판정한다. 다른 결론이거나 필수 검토가 빠지면 반영을 거부한다. 다음은 의미 변경에 필요한 두 역할을 보여주는 **합성 시험용 형식 예시**이며 실제 검토·사람 승인 기록이 아니다. placeholder는 실제 제안과 일치하지 않으므로 그대로 사용할 수 없다.

```json
[
  {
    "role": "independent",
    "reviewer": "document-reviewer",
    "conclusion": "approved",
    "version": 1,
    "proposal_sha256": "<검토한 proposal 객체의 SHA256>",
    "evidence": "SYNTHETIC: 가상 시험에서 문서와 diff를 확인한 기록",
    "synthetic": true
  },
  {
    "role": "policy",
    "reviewer": "policy-owner",
    "conclusion": "approved",
    "version": 1,
    "proposal_sha256": "<검토한 proposal 객체의 SHA256>",
    "evidence": "SYNTHETIC: 가상 시험용 정책 검토 기록",
    "synthetic": true
  }
]
```

합성 기록은 `synthetic_trial=true`인 가상 시험에서만 사용한다. 실제 사용에서는 각 검토자가 실제 검토 후 설정에 허용된 자신의 식별자, 정확한 제안 버전·digest와 실제 증거를 기록하고 `synthetic=false`로 표시한다. 예시를 복사해 false로 바꾸는 것은 실제 검토나 사람 승인을 대신하지 않는다. `minor`는 독립 검토 역할의 기록만 필요하지만 유효한 기존 권한의 근거와 정확한 제안 결합은 유지한다.

추천(역할 지침), 프로그램 검사(이 도우미), 실제 앱의 권한 강제는 별개다. review JSON은 로컬 신뢰 자료이며 서명·신원 인증·보안 승인을 제공하지 않는다. 해시도 편집기 전체 잠금이 아니다. 단일 작성자가 다른 편집을 멈춘 상태에서 보수적으로 순차 사용한다. no-follow와 적용 직전 재확인은 경합 위험을 줄이지만 전역 잠금·원자적 다중 파일 트랜잭션·악의적인 동시 경로 변경 방어를 보장하지 않는다. 중간 실패에서는 일부 반영될 수 있다.

## 자동 검사 범위

`requirements-docs.txt`의 markdown-it-py 3.0.0 / mdurl 0.1.2를 격리 설치하고 PYTHONPATH에 지정한다. 예:

```sh
python3 -m pip install --target /tmp/docs-deps -r requirements-docs.txt
PYTHONPATH=/tmp/docs-deps python3 examples/technical-documentation/trial.py
PYTHONPATH=/tmp/docs-deps python3 -m unittest discover -s tests -p 'test_document*.py' -v
```

CommonMark 토큰에서 인라인·참조 링크와 이미지 목적지를 읽어 로컬 기존 일반 파일 존재를 검사한다. fenced/indented 코드블록과 inline code 안의 링크 예제는 제외한다. 괄호·이스케이프·percent encoding 목적지는 파서와 URL decoding으로 처리한 뒤 동일한 루트·symlink 검사를 적용한다. 이미지 내용·렌더링은 검사하지 않는다. CommonMark는 미닫힘 fence를 코드블록으로 허용하며 문법 lint 전체를 제공하지 않는다. 마지막 개행·행 끝 공백은 별도 검사한다.

errors는 누락 파일·위험 경로·공백 오류로 check/apply를 거부한다. skipped는 wiki, Mermaid, HTML, 원격/절대 URL, anchor, query 등 검사하지 않은 범위다. anchor가 있는 링크는 파일 부분도 검증했다고 주장하지 않는다. 파서 dependency가 없으면 unavailable 오류로 종료 2이며 제한된 regex로 조용히 대체하지 않는다. 내부 링크를 네트워크로 가져오거나 문서 코드를 실행하지 않는다. 로컬 존재 확인은 내용 정확성·시각 렌더링·원격 접근성 증거가 아니다.

## 독자가 작업을 마칠 수 있는지 확인

문서의 독자 목표와 관측 가능한 완료 결과를 먼저 정하고, 결과·행동을 앞에 쓴다. 정확한 용어와 필요한 조건을 남기되 독자가 수행·선택하는 데 필요 없는 구현 설명은 줄인다. 기존 문서의 목적·길이·배치를 읽고 실제 빈틈만 최소 수정한다. [출처와 채택 범위](../skills/technical-writing-sources.md)를 참고한다.

새 절차나 중요한 설명 변경에는 영향에 따라 [독자 시험 양식](../templates/document-reader-review.md)을 사용한다. 독자에게 최종 문서, 그 안에서 허용한 전제, 구체 작업·질문만 제공한다. 작성자의 정답·의심 결함·구현 소스·해결 힌트는 전달하지 않는다. 독자가 실제 답과 수행 결과·막힌 단계·빠진 정보를 기록한 뒤 작성자가 목표와 비교한다. 추가 자료가 필요했다면 문서만으로 완료한 것으로 표시하지 않는다. 자체 읽기는 자체 확인, 실제 별도 독자의 수행만 독립 독자 시험으로 기록한다. 독자 이해 시험은 구현 안전성 검토나 사람 정책 승인을 대신하지 않는다. 모든 작업에 별도 에이전트를 요구하지 않으며, 기존 프로젝트 권한 내 오탈자·링크 수정은 이유를 남기고 생략할 수 있다.

## 근거 변경에 따른 재검토 후보

[manifest 양식](../templates/document-evidence.json)의 project를 실제 절대 루트로, roots/files를 명시 허용 범위로, claim ID와 affected documents 및 source SHA256을 실제 조사한 값으로 채운다. 각 source kind는 code/design/verified/human/pending 중 하나다. kind는 출처의 성격이며 사실·승인 인증이 아니다. pending에는 미결 기록 파일을 연결할 수 있다. 파일당 전체 내용 hash를 사용하며 민감한 소스 원문은 manifest에 복사하지 않는다.

`python3 scripts/document_evidence.py --project PROJECT --manifest MANIFEST`는 명시된 파일만 읽고 changed/missing 소스와 영향을 받는 문서의 재검토 후보를 반환한다. 종료 0은 기록과 동일, 1은 drift, 2는 입력/경계/IO 오류다. 로컬 경로 이탈·symlink는 거부한다. Git·서비스·전수 검색·자동 진실 판정은 없다. 도우미는 advisory이며 check/apply에 자동 결합되지 않는다. 근거가 바뀌면 작업자는 관련 문서와 의존 제안·검토를 재검토하고, manifest도 갱신했다면 그 hash를 제안 rationale 또는 검토 evidence에 기록해 새 버전·digest로 다시 결합한다. 기존 review JSON은 manifest 변경만으로 프로그램상 자동 무효화되지 않으므로 이전 결론을 재사용하지 않는 운영 책임이 남는다.

백업 실패 시 `backups`는 write/flush/fsync/close가 끝난 원본 백업, `incomplete_backups`는 생성했지만 완성하지 못한 파일 경로다. 후자는 복구 원본으로 사용하지 않는다. complete=false이면 applied의 파일만 교체됐을 수 있다. 안전한 백업과 현재 문서를 비교하고 필요한 복구를 검토한 뒤 현재 상태에서 새 제안을 만든다. 자동 삭제·rollback·재시도는 없다.

legacy 실행기와 native 개발 컨트롤러에 technical-writer 단계를 추가하지 않는다. 역할/스킬/카탈로그/수동 전달로 발견하며 workflow.json 기본값은 유지한다.

## 선택형 lychee / Vale 로컬 검사

`python3 scripts/document_checks.py --project PROJECT --config CONFIG`는 별도 advisory 도우미다. [설정 양식](../templates/document-checks.json)은 두 도구 모두 기본 disabled다. 기존 prepare/check/apply와 evidence는 이 도우미를 호출하지 않는다. 도구가 없거나 실패해도 핵심 승인·백업 흐름을 끄지 않는다. runner는 설치·다운로드·sync·자동 수정·서버 실행을 하지 않는다.

프로젝트 JSON의 정확한 필드는 version(1), project(실제 절대 경로), roots(프로젝트 상대 디렉터리 배열), files(기존 UTF-8 .md 입력 배열), targets(lychee가 검사할 명시적 로컬 링크 목적지 배열), tools다. targets는 기존 일반 파일 또는 아직 없는 파일 경로다. 누락 목적지를 검사하려면 targets에 명시한다. 모든 파일·목적지는 roots 안에 있어야 한다. 배열은 각각 1~32개이며 중복·숨김 경로·`..`·절대 상대파일명·symlink(루트의 모든 상위 경로 포함)는 거부한다. config/문서/목적지 파일당 2MiB, 입력 합계 및 목적지 합계 각각 2MiB를 제한한다. JSON 중복·추가 키도 거부한다. 예제 경로를 실제 승인된 프로젝트 경로로 바꾸고 canonical 경로를 사용한다.

각 도구는 enabled(boolean), executable(정확한 절대 경로), sha256(64자리 소문자 hex), version을 갖는다. enabled일 때만 실행 신뢰를 선언하며 PATH 자동 선택은 없다. 이번에 검증한 프로토콜은 `lychee 0.24.2`, `vale version 3.27.0`으로 한정한다. 다른 버전은 별도 호환성 시험 후 어댑터를 갱신한다. 프로젝트 밖의 바이너리는 운영자가 직접 출처·checksum·라이선스를 확인한 신뢰 파일만 지정한다. 버전·hash는 실행 전후 확인한다. 버전 출력만으로 바이너리를 신뢰하지 않는다.

lychee에는 CommonMark 파서가 추출한 허용 링크를 고정 이름의 임시 Markdown으로 재구성해 전달한다. 허용 목적지의 현재 존재를 빈 임시 파일/누락 상태로 재현하므로 원문·실제 목적지 내용은 lychee에 전달하지 않는다. 원래 문서 렌더링·lychee의 원문 추출 능력을 시험하는 방식은 아니다. mandatory `--offline`, 명시적 생성 TOML, cache=false, mail/verbatim=false, 고정 인수만 사용한다. 임시 cwd/HOME/XDG와 상속하지 않는 최소 환경으로 ambient lychee.toml·preprocess·토큰·proxy·설정 환경변수의 영향을 줄인다. 임의 TOML·명령·추가 flag는 지원하지 않는다. 디렉터리·glob·HTTP·email 입력은 전달하지 않는다. 원격/절대 URL·anchor·query·HTML·wiki·Mermaid·루트 이탈·미허용 목적지는 skipped에 기록하며 지원하는 로컬 파일 존재만 검사한다. 정상 결과의 pass도 이 부분 범위에만 해당한다. 지원 링크가 없으면 not_run이며 원격 링크를 성공으로 세지 않는다. 여러 문서가 같은 목적지를 가리키면 한 번만 검사한다.

Vale에는 files의 임시 사본과 직접 생성한 `.vale.ini`/규칙만 전달한다. `--no-global`로 전역 설정을 끄고 Project 스타일만 사용한다. terms는 disallowed/preferred 쌍의 배열이며 이 첫 시범은 각 항목을 2~32자의 완성형 한글 literal로 제한한다. 자유 YAML·regex·Packages·외부 rule pack·script·plugin·converter·vale sync는 지원하지 않는다. 메시지에 원문을 담지 않고 출력은 문서명·행·rule ID만 반환한다. 선호어는 프로젝트 매핑이며 자동 대체하지 않는다. 한국어 정답·보편적인 스타일/문법 품질을 판정하지 않는다.

실제 Vale 3.27.0에서 가상 금지어 `구름단추`는 warning 1개, 선호어 `하늘단추`는 0개였다. `구름단추`의 fenced/inline code 예제와 `왕구름단추`, `구름단추는`은 0개였다. `nonword: true`와 Unicode 문자/숫자/underscore 경계로 독립된 literal만 찾는다. 이는 이 fixture에서 코드 오탐이 없었다는 근거이며 모든 Markdown dialect에 대한 보장은 아니다. 한국어 조사 결합·활용형·공백 변형·분해 Unicode는 놓칠 수 있다.

도구별 status는 disabled / unavailable(명시 바이너리 또는 파서 부재) / not_run(지원 입력 없음 또는 설정 실패 전) / error(경계·hash·버전·실행·timeout·출력 오류) / findings / pass다. lychee는 실제 JSON의 errors와 exit 0/2를 함께 확인한다. Vale warning은 실제로 exit 0이므로 JSON alert를 읽어 findings로 판정한다. 알 수 없는 종료값·비정상 JSON·불완전 lychee 집계는 error다. CLI 종료값은 error/unavailable/설정 오류 2, findings 1, 그 외 0이며, 0은 disabled/not_run도 포함하므로 반드시 tool status와 skipped를 읽는다. subprocess는 shell 없이 실행하며 호출당 기본 10초, stdout+stderr 합계 2MiB로 제한하고 timeout 시 프로세스 그룹을 종료한다. 원시 stderr와 원문 Match는 보고서에 복사하지 않는다.

보고서 version/config SHA256/inputs/targets(누락은 null)를 실제 검토한 문서 및 목적지와 대조한다. 끝에 재확인하여 변경이 발견되면 error로 바꾼다. 이 snapshot은 악의적인 동시 변경에 대한 전역 잠금이 아니며, 실행 중 다른 편집을 멈춘 순차 사용이 전제다. 신뢰된 로컬 실행 파일 자체는 실행 경계다. wrapper는 OS sandbox가 아니며 악성 바이너리의 파일 접근·프로세스·네트워크를 통제한다고 주장하지 않는다. 해시 확인 사이의 공격적인 교체도 완전히 막지 않는다.

선택형 보고서는 기존 승인 제안 snapshot에 자동 결합되지 않는다. 운영자가 보고서 버전·설정·입력 identity를 제안/검토 evidence에 직접 연결하고 변경 시 다시 검사한다. 검사 통과는 코드·앱·편집기·보안 승인이나 실제 독립 검토·사람 정책 승인이 아니다. 재현 시험은 [checker-trial](../examples/technical-documentation/README.md)을 참고한다.
