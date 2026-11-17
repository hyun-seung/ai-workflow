# 공식 모범 사례와 ai-workflow 점검

확인일: 2026-11-14. 범위는 이 저장소의 공통 지침·역할·요청 양식·실행 가이드와 단독 Codex 실행기의 입력 구성이다. 영상 원본 재생 검증 대신 제공된 요약을 조사 목록으로 사용하고 공식 원문과 대조했다. 개인 전역 설정, 실제 앱의 지침 로딩, 캐시 적중률·비용 절감은 이번 점검으로 검증하지 않았다.

이 문서는 최초 문서 점검 시점의 기록이다. 이후 진행한 예약 등록·시험 실행과 현재 운영 범위는 [정기 점검 운영](guidance-maintenance.md)에 별도로 기록한다.

## 발견과 조치

| 주제 | 변경 전 확인한 상태 | 판단·조치 |
| --- | --- | --- |
| ① 세션·모델·캐시 | 후속 worker 재사용은 있으나 대화 분리·캐시 기준은 흩어져 있음. `workflow.py`는 매번 ephemeral 단독 실행 | `runtime/session-practices.md` 신설, `runtime/profiles.md` 연결. 연속 세션을 구현했다고 주장하지 않음 |
| ② 하나의 일관된 결과 | 위임 범위는 구분하지만 사용자 대화의 분리 기준은 없음 | 같은 결과는 이어가기, 무관한 목표는 새 대화, 반복 실패는 원인 정리 후 재시작 판단 |
| ③ 짧은 규칙 | `AGENTS.md` 39줄, 개별 역할 문서 17~53줄. 과도한 대문자·생각 주문은 발견하지 못함 | 대규모 축약 불필요. 상시 파일에는 짧은 기준·검증 명령·조건부 링크만 보강 |
| ④ 설정·지식 위치 | 환경·공통·프로젝트 분리는 있음. 제품별 지침 발견 방식 비교는 없음 | `projects/README.md`에 배치·로딩 조건 추가. 불필요한 설정 폴더나 중복 CLAUDE.md 생성은 하지 않음 |
| ⑤ 계획·완료 기준 | 위임 양식의 goal/context/constraints/acceptance는 충족. 작은 직접 요청에도 역할·ID가 필수처럼 읽힘 | 직접 요청용 네 줄 형식과 위임·기록용 계약 분리. 복잡한 작업의 사전 조사·계획 연결 |
| ⑥ 스스로 검증·강제 | 완료 판정·검증 범위·관리 위임 검사 존재. 루트 지침에 실제 테스트 명령은 없음 | 저장소 전용 명령 추가, 반복 검사 금지와 강제 수단의 적용 범위 유지. 전역 hook·필수 CI를 새로 켠 것은 아님 |
| ⑦ 병렬·독립 검토 | 한 담당자 우선·파일 소유·비용·독립 검토 지침 있음 | 동시 변경본의 worktree 격리와 공유 자원·병합 검증을 명시 |
| 지속 갱신 | 공식 출처·확인일·관련 파일을 연결한 정기 점검 절차 없음 | `runtime/guidance-maintenance.md`와 `templates/guidance-review.md` 작성. 예약·자동 수정은 미활성 |

## 영상 요약을 그대로 규칙으로 만들지 않은 항목

- **“Codex에서 권한을 바꾸지 말라”**: 모델·도구·prefix 변경은 캐시를 깨뜨릴 수 있지만, 같은 글에서 권한 변경을 새 메시지로 추가하는 처리도 설명한다. 불필요한 변경은 줄이되 필요한 권한 조정까지 금지하지 않는다. [OpenAI agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/).
- **“구독 1시간 / API 5분”**: Claude Code의 기본값과 요청 종류에 대한 설명이다. TTL 설정·요금제 조건을 함께 확인해야 하며 Codex나 모든 구독 사용량에 같은 할인율을 적용하지 않는다. [Claude 캐시](https://code.claude.com/docs/en/prompt-caching).
- **“Claude는 CLAUDE.md만 읽는다”**: 현재 문서에는 지원 버전·설정에서 `AGENTS.md`도 직접 읽는다고 명시돼 있다. `CLAUDE.md`가 없다는 이유만으로 연결 실패를 단정하지 않는다. 200줄은 CLAUDE.md의 권장 목표이며 모든 도구의 공통 강제 한도가 아니다. [Claude 메모리](https://code.claude.com/docs/en/memory).
- **“최신 모델에는 테스트 지시를 지운다”**: Astra의 불필요한 테스트 반복 유발을 경계하는 조언이다. 프로젝트 검증 명령·완료 기준·필요한 회귀 검사는 유지한다. 같은 글도 명확한 종료 조건과 허용 범위를 권한다. [Astra 프롬프트 점검](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra).
- **“모든 작업을 새 계획 대화·여러 에이전트로”**: 공급자 권고를 강제 단계로 늘리지 않는다. 이미 충분한 맥락과 검증 수단이 있는 작은 작업은 단일 대화에서 처리한다. [Codex 모범 사례](https://learn.chatgpt.com/guides/best-practices), [Claude 모범 사례](https://code.claude.com/docs/en/best-practices).

## 유지한 것과 남은 확인

공개/비공개 구분, 실행 환경 선택, 실제 권한 확인, 완료 기준과 검증 근거, 미측정 사용량 구분은 프로젝트의 필수 경계다. 프롬프트를 짧게 한다는 이유로 제거하지 않았다. `workflow.json`의 모델·effort도 이번 문서 점검만으로 교체하지 않았다.

지침은 목차로 쓰고 실제 지식·검증은 접근 가능한 자료와 도구로 연결한다는 [Harness engineering](https://openai.com/index/harness-engineering/)의 방향과 부합한다. 다만 문서 준수 여부가 실제 작업 품질·사용량 개선을 입증하지는 않는다. 후속 대표 작업에서는 같은 완료 기준 아래 재작업·입력/캐시·시간·미검증을 비교한다. 자동화의 실제 실행·예약·결과 수신 검증은 별도다.

## 이번 변경의 검증

- `python3 scripts/workflow.py check --project .`: 설정과 지침 경로 확인 통과.
- `python3 -m unittest discover -s tests -p 'test_workflow.py' -v`: 9개 통과. 기본 6개 역할의 지침 구성과 대체 CLI를 통한 실행 계약 포함; 실제 모델 호출은 없음.
- 변경 문서의 로컬 링크·앵커와 `git diff --check` 확인. 루트 `AGENTS.md`는 보강 후 45줄.
- 실행 코드는 변경하지 않아 전체 Python 회귀·화면 테스트는 수행하지 않음. 실제 Claude/Codex 지침 로딩·캐시 비용·예약 실행은 미검증.
