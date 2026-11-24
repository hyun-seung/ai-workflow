# 가상 기술 문서 시험

README·사용 가이드·ADR의 변경 제안부터 검토·검사·반영·실패 복구까지 로컬 임시 파일로 확인한다. 실제 프로젝트·계정·서비스·Git은 사용하지 않는다. 모든 승인 레코드는 SYNTHETIC이며 synthetic_trial=true인 임시 설정에서만 사용한다. 실제 독립 검토나 사람 정책 승인으로 재사용하지 않는다.

## 실행 준비

로컬 도우미에는 Python 3.10 이상과 pip가 필요하다. 의존 패키지의 최소 Python 버전과 도우미의 실행 기준은 다르다. 이 후속 시험은 macOS Python 3.14.3에서 수행했으며 다른 Python 버전은 미검증이다. 저장소 루트에서 아래 명령을 실행한다. dependency는 선택한 scratch 디렉터리에만 설치하며 시스템 Python에는 설치하지 않는다. 기존 scratch 경로에 다른 패키지가 있다면 새 전용 경로를 고른다.

```sh
python3 -m pip install --target /tmp/docs-deps -r requirements-docs.txt
PYTHONPATH=/tmp/docs-deps python3 examples/technical-documentation/trial.py
PYTHONPATH=/tmp/docs-deps python3 -m unittest discover -s tests -p 'test_document*.py' -v
```

parser가 없으면 check/apply는 명확히 실패한다. trial은 임시 디렉터리를 만들고 결과 JSON을 출력한 뒤 파일을 정리한다. 출력의 임시 백업 경로도 실행 종료 후 없어지므로 실제 복구 백업으로 사용하지 않는다.

## 순서와 결과 읽기

1. trial은 기존 UTF-8 문서 세 파일과 백업 디렉터리를 만든다. 가상 코드·시험은 삭제 204, 설계는 200과 본문을 의도한다. 제안은 README에 탐색 링크, 가이드에 근거별 관측, ADR에 불일치와 사람의 선택 대기를 기록한다. 최종 계약·코드·설계를 자동 변경하지 않는다.
2. prepare는 버전·작성자·분류·이유·전체 변경 내용을 받고 정확한 프로젝트·설정 hash·대상 snapshot·diff를 결합한다. 실제 CLI 입력 필드는 [절차](../../runtime/technical-documentation.md)의 로컬 도우미 항목을 따른다. 출력 wrapper의 proposal만 PROPOSAL JSON에 저장하고 문서 원본에 redirect하지 않는다.
3. 실제 사용에서는 검토자가 문서·diff·출처를 읽고 별도의 REVIEWS 배열에 명시 결론을 기록한다. 의미 변경은 독립 검토와 사람 정책 결정이 필요하다. trial은 미승인 apply 거부를 먼저 확인한 뒤 오직 가상 레코드로 check→apply를 진행한다.
4. 출력 check의 errors/skipped/mode와 result의 complete/applied/backups/incomplete_backups를 확인한다. CommonMark 링크·이미지의 로컬 존재만 검사한다. wiki·Mermaid·원격·anchor·HTML·query는 skipped이며 렌더링과 내용 정확성은 별도다.
5. trial은 기록한 소스를 바꾸고 drift의 재검토 후보가 가이드·ADR인지 확인한다. source hash가 바뀌면 문서와 의존 제안·검토를 재검토한다. helper는 advisory로 승인·진실을 판정하지 않는다. 이미 반영한 제안의 재사용도 거부한다.
6. trial은 합성 fsync 백업 실패를 주입한다. 실패 시 문서가 그대로이고 failed_backup.incomplete_backups에 불완전 파일이 보고되는지 확인한다. 현재 상태를 다시 읽고 새 버전·가상 검토를 만들어 recovery.complete=true를 확인한다. 실제 부분 반영 시에는 applied와 완성 backups를 대조해 복구 내용을 검토한다. 불완전 백업은 복구에 쓰지 않으며 자동 rollback·무조건 재시도하지 않는다.

개별 CLI 단계는 `python3 scripts/document_change.py prepare|check|apply --help`와 절차의 필드 설명을 따른다. 증거 drift CLI는 `python3 scripts/document_evidence.py --project PROJECT --manifest MANIFEST`다. template의 루트·파일·hash placeholder를 실제 승인된 로컬 범위로 바꾸고 source 원문은 외부로 보내지 않는다.

## 독자 시험과 제한

[독자 작업 입력](reader-task.md)을 실제 별도 독자에게 최종 문서와 함께 제공하고 관측 결과를 [양식](../../templates/document-reader-review.md)에 남길 수 있다. 작성자의 기대 답·구현 힌트를 전달하지 않는다. 자체 읽기와 실제 독립 독자 수행을 구분한다. 작은 오탈자·링크 변경은 기존 프로젝트 권한 내에서 이유를 기록하고 독자 시험을 생략할 수 있다.

trial이 자동 확인하는 것은 합성 레코드의 로컬 처리·백업·drift·복구다. 실제 별도 독자 시험, 사람 승인, 앱 렌더링, 원격·anchor 검사, 전수 영향 탐색은 수행하지 않는다. 신규 문서 생성 엔진과 native technical-writer 단계는 지원 범위에 추가하지 않았다. lychee/Vale는 아래 별도 선택형 시험으로 분리한다.

전체 저장소 검사에는 같은 PYTHONPATH로 `python3 -m unittest discover -s tests -v`, 설정에는 `python3 scripts/workflow.py check --project .`를 쓴다. skill-creator의 `scripts/quick_validate.py .agents/skills/ai-role-technical-writer`는 PyYAML이 필요하며 frontmatter 검사와 실제 행동 검증은 별개다.


## 선택형 검사 시험

```sh
PYTHONPATH=/tmp/docs-deps python3 examples/technical-documentation/checker-trial.py
PYTHONPATH=/tmp/docs-deps python3 examples/technical-documentation/checker-trial.py --tools /absolute/path/to/trusted-tools.json
```

첫 명령은 disabled 상태를 재현한다. 두 번째 명령의 JSON은 [설정 양식](../../templates/document-checks.json)의 tools 객체 자체이며, 직접 검증한 정확한 바이너리 경로·SHA256·version·enabled와 프로젝트 terms를 넣는다. 도구를 자동 검색·다운로드·설치하지 않는다. 실제 프로젝트에서 사용할 때에는 양식 전체의 project/roots/files/targets도 명시하고 `scripts/document_checks.py --project PROJECT --config CONFIG`로 실행한다. 실행 경계와 상태 의미는 [절차](../../runtime/technical-documentation.md#선택형-lychee--vale-로컬-검사)를 따른다.

고정 macOS arm64 lychee 0.24.2 / Vale 3.27.0의 실제 시험 결과: good는 두 도구 pass, broken_remote_korean은 둘 다 findings(로컬 누락 링크 1개, 한국어 금지어 1개)였다. 원격 링크는 skipped다. code_and_boundary는 lychee not_run과 Vale pass(코드 예제·합성어·조사 결합에서 alert 0), remote_only는 lychee not_run이다. Vale pass는 지정 용어에 대해서만 유효하다. 조사 결합을 놓치는 제한도 시험에 드러난다.

이 trial은 임시 가상 문서·설정만 만들고 종료 때 정리한다. 상태·입력 hash 출력의 경로는 임시 경로다. unittest의 합성 실행 파일은 실제 바이너리 검증 근거가 아니다. 실제 native 실행을 하지 못한 환경에서는 unavailable/미검증으로 남기고 fixture 성공으로 대체하지 않는다.
