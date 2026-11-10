# 컨텍스트 전달 최적화 실험 001: 파일 참조와 직접 전달

같은 검증 업무에 필요한 자료를 전달하는 두 방식을 비교했다.
A는 지정한 파일 범위를 도구로 읽고, B는 같은 범위를 초기 컨텍스트에 직접 첨부했다.
이번 네 번의 실행에서 B의 평균 총토큰은 A보다 47.13% 적었고, 비캐시 입력은 8.13% 적었다.
이는 해당 비교 실행의 관측값이며, 전체 작업의 순절감이나 금액 절감을 뜻하지 않는다.

## 1. 실험 목적

필요한 정보와 품질 기준을 유지하면서 컨텍스트 전달 방식이 사용량에 미치는 관측 차이를 확인한다.
이 문서에서는 반복 가능한 실험 절차를 **토큰 효율 개선 프로세스**라고 부른다.
자료를 덜 제공하거나 품질 기준을 낮춰 얻은 절약은 개선으로 인정하지 않는다.
이 문서의 실행 수치는 실제 실측 요약이며, 가상 예시 수치가 아니다.
참조 자료인 `examples/run-record.json`은 예제 파일이며, 이번 실험의 실행 기록과 구분한다.

## 2. 비교 조건

| 항목 | 조건 |
| --- | --- |
| 실험일 | 2026-11-10 |
| 요청 모델 / effort | `gpt-6.1-sol` / `medium` |
| 실제 모델 / effort | observed 미확인 |
| 역할 / 접근 | `verifier` / `read-only` |
| Codex CLI | `codex-cli 0.167.0` |
| 세션 | 매회 새 세션으로 실행(실행기에 --ephemeral 지정) |
| 실행 순서 | A1 → B1 → B2 → A2 |
| 제한 | 실행별 timeout 180초, 토큰 상한 아님 |
| 상태 확인 | 미커밋 작업사본 해시와 유효 설정을 전후 확인 |

요청한 모델·effort와 실제 적용된 모델·effort는 동일하다고 단정하지 않는다.
작업사본과 설정 확인만으로 계정·서버 측 조건까지 통제한 것은 아니다.

## 3. 전달 방식과 자료 범위

| 방식 | 자료 전달 | 읽기 지시 |
| --- | --- | --- |
| A: 파일 참조 | 프롬프트에 대상 파일과 범위를 지정 | 해당 범위를 도구로 읽기 |
| B: 직접 전달 | 동일 범위를 `--context-file`로 첨부 | 첨부 자료 재읽기 금지 |

두 방식에 지정한 자료는 다음 세 범위다.

- [`scripts/run_records.py`](../scripts/run_records.py): 1~172행
- [`examples/run-record.json`](../examples/run-record.json): 1~42행
- [`schema/run-record.schema.json`](../schema/run-record.schema.json): 1~103행

세 범위는 실험 당시 각 파일의 전체 내용이었다.
따라서 이번 실험은 필요한 부분만 최소 발췌하는 방식의 효과를 검증하지 않았다.
재현 시에는 파일의 최신 범위와 해시를 다시 확인해야 한다.
A의 실제 읽기 범위 준수 여부는 명령 본문이 없어 `unverified`다.

## 4. 동일한 품질 기준

두 방식 모두 600자 안팎의 동일한 JSON 응답을 요청했다.
각 완료 기준의 근거는 `pass`, `fail`, `unverified`로 구분하도록 했다.
모든 완료 기준이 `pass`일 때만 업무 상태를 `completed`로 인정했다.

| 기준 | 확인 내용 |
| --- | --- |
| AC01 | `total = input + output`; 캐시·추론 토큰을 다시 더하지 않음 |
| AC02 | 미측정인 `null`과 측정된 `0`을 구분 |
| AC03 | 실행 성공과 업무 검증 완료를 구분 |

네 실행 모두 AC01~AC03이 `pass`로 판정되었다.
최종 담당자가 제시된 근거를 수동 대조했으며, 독립 심사자는 참여하지 않았다.
이 품질 판정과 A의 읽기 범위 준수 여부는 별도 검증 항목이다.

## 5. 실행별 실측 결과

토큰 열은 토큰 수, 시간은 밀리초다.
`completed_tool_items`는 완료된 도구 작업 항목 수다.
`cached`와 `uncached`는 각각 입력 중 캐시된 부분과 `input − cached`로 계산한 부분이다. `total = input + output`이며 캐시·추론 세부 토큰을 다시 더하지 않는다.

[공개용 측정 요약 JSON](data/001-context-delivery.json)에 계산 가능한 행별 수치와 관련 코드·지침·설정의 SHA256을 함께 제공한다. 실제 실행 UUID와 개인 경로를 제거한 요약이며 원본 실행 기록은 비공개다.

| 실행 | input | output | total | cached | uncached | ms | completed_tool_items |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A1 | 56,168 | 412 | 56,580 | 38,656 | 17,512 | 20,518 | 3 |
| B1 | 29,571 | 371 | 29,942 | 13,440 | 16,131 | 15,660 | 0 |
| B2 | 29,571 | 400 | 29,971 | 13,440 | 16,131 | 17,063 | 0 |
| A2 | 56,261 | 485 | 56,746 | 38,656 | 17,605 | 21,265 | 3 |

네 실행 모두 실행 상태는 `succeeded`, 측정 상태는 `measured`였다.
모두 `single_turn`으로 기록되었으며, 보고된 `issues`는 없었다.
도구 작업 항목 3개와 0개를 파일 읽기 횟수나 모델 호출 수로 해석하지 않는다.
`model_calls`는 `null`이므로 실제 모델 호출 수는 미측정이다.

## 6. 평균과 상대 차이

상대 차이는 `(B 평균 − A 평균) / A 평균`으로 계산했다.

| 지표 | A 평균 | B 평균 | 상대 차이 |
| --- | ---: | ---: | ---: |
| total | 56,663 | 29,956.5 | −47.13% |
| uncached | 17,558.5 | 16,131 | −8.13% |
| 시간(ms) | 20,891.5 | 16,361.5 | −21.68% |

대응 실행의 총토큰 차이는 B1−A1에서 −26,638이었다.
B2−A2에서는 −26,775였다.
총토큰 감소율과 비캐시 입력 감소율은 서로 다르므로 구분해서 해석해야 한다.

## 7. 관측과 원인 추론

초기 프롬프트는 A가 6,961자, B가 22,905자였다.
B는 초기 문자 수가 더 많았지만, 실행 종료 시 기록된 입력 토큰은 더 적었다.
문자 수는 토큰 수와 같지 않으므로 초기 문자 수만으로 사용량을 예측할 수 없다.
도구 왕복과 중간 문맥이 줄었을 가능성은 원인에 대한 추론이다.
이번 자료만으로 각 요인의 기여도나 인과관계를 확정할 수 없다.

## 8. 사용량 집계 범위

네 비교 실행의 총토큰 합계는 **173,239토큰**이다.
계획·준비에 사용된 **58,854토큰**은 비교 합계와 별도로 기록했다.
대화 조율·검토·보고·공개 문서 작성 비용도 비교 합계에서 제외했다.
현재 대화의 조율·검토·보고 사용량은 미측정이다. 공개 문서 작성에 사용한 별도 역할 실행은 비교 합계에 포함하지 않는다.
따라서 전체 작업의 순절감이나 금액·요금의 47% 절감을 주장하지 않는다.

## 9. 토큰 효율 개선 프로세스

1. **가설 설정:** 바꿀 전달 방식과 예상 효과를 명시한다.
2. **동일 AC 고정:** 품질 기준과 완료 판정 규칙을 두 조건에 동일하게 적용한다.
3. **필요 자료 확정:** 업무에 충분한 자료를 고르고 누락 여부를 확인한다.
4. **manifest 작성:** 파일·범위·해시·요청 설정·제한·측정 항목을 기록한다.
5. **ABBA 배치:** A1/B1/B2/A2 순서를 정하고 매회 새 세션을 사용한다.
6. **dry run:** 입력 전달과 설정을 확인한다. 실제 측정 가능 여부는 실행 후 확인한다.
7. **실행:** 조건을 유지하며 사용량·시간·실행 상태를 수집한다.
8. **품질 확인:** AC 근거를 대조하고 실행 성공과 업무 완료를 분리한다.
9. **지표 해석:** 총토큰·비캐시 입력·시간·별도 비용과 한계를 함께 보고한다.

공통 입력에 A 또는 B의 조건 문구를 이어 붙인다. 아래 파일은 실험 당시 작업 본문을 분리한 것이며 개인 경로나 실행 UUID를 포함하지 않는다.

- [공통 입력](prompts/context-common.txt)
- [A: 파일 참조 입력](prompts/context-A.txt)
- [B: 직접 전달 입력](prompts/context-B.txt)

다음 명령은 저장소 루트에서 실행하는 **dry-run 예시**다. `/path/to/private/runs`와 `/path/to/private/reports`는 저장소와 Git 작업 트리 밖의 비공개 위치로 바꾼다. 현재 설정이 실험의 `gpt-6.1-sol / medium / read-only`와 일치하는지 출력에서 확인한다.

```sh
cat experiments/prompts/context-common.txt experiments/prompts/context-A.txt |
  python3 scripts/workflow.py run \
    --project . --role verifier --task - \
    --task-id CONTEXT-001-A1 --parent-task-id CONTEXT-001 \
    --task-type design-review --timeout-seconds 180 \
    --records-dir /path/to/private/runs \
    --result-ref /path/to/private/reports/A1.md \
    --dry-run
```

```sh
cat experiments/prompts/context-common.txt experiments/prompts/context-B.txt |
  python3 scripts/workflow.py run \
    --project . --role verifier --task - \
    --task-id CONTEXT-001-B1 --parent-task-id CONTEXT-001 \
    --task-type design-review --timeout-seconds 180 \
    --records-dir /path/to/private/runs \
    --result-ref /path/to/private/reports/B1.md \
    --context-file scripts/run_records.py:1:172 \
    --context-file examples/run-record.json:1:42 \
    --context-file schema/run-record.schema.json:1:103 \
    --dry-run
```

실제 비교를 진행할 때는 `--dry-run`을 제거하고 A1 → B1 → B2 → A2 순서로 실행한다. 매번 `--task-id`와 `--result-ref`를 해당 실행에 맞게 바꾸고, 앞 실행의 품질·측정·해시 확인 후 다음 호출로 넘어간다. 실행 실패, 완료 기준 실패·미검증, 측정 누락 또는 기준 변경이 생기면 자동 재시도하지 않고 재계획한다. 각 180초 제한과 4회 제한은 금액·토큰 상한이 아니다.

작업 본문의 `COMPARE-REVIEW-001`은 공통 응답 ID다. CLI의 실행별 작업 ID와 자동 생성된 `run_id`를 별도 보고서에서 연결한다. CLI 메타데이터가 작업 본문에 자동 주입되지는 않는다. `--result-ref`는 참조만 기록하므로 최종 stdout과 판정 근거를 해당 비공개 보고서에 따로 저장해야 한다. 모델 응답의 `usage: unavailable`과 실행기에서 수집한 실제 사용량도 구분한다.

수치 재계산은 모델 호출 없이 할 수 있다.

```sh
python3 - <<'PY'
import json
from pathlib import Path
from statistics import mean

data = json.loads(Path("experiments/data/001-context-delivery.json").read_text())
for metric in ("total", "uncached_input", "duration_ms"):
    a = mean(r[metric] for r in data["rows"] if r["label"].startswith("A"))
    b = mean(r[metric] for r in data["rows"] if r["label"].startswith("B"))
    print(f"{metric}: A={a}, B={b}, relative={(b-a)/a:.2%}")
PY
```

재현할 때는 최신 파일 범위·해시·유효 설정을 확인하고 전후 상태를 기록한다.
당시 기준에는 미커밋 파일이 포함되어 있으므로 Git 커밋만으로 같은 상태를 재현했다고 간주하지 않는다. 공개 JSON의 관련 파일 해시는 내용 대조용이며, 전체 환경을 복원하는 패키지는 아니다. 이후 코드나 지침이 바뀌었다면 새 기준으로 별도 실험을 기록한다.
사용한 실행기의 컨텍스트 범위 수 제한은 8개이며, 원본 파일 각각과 첨부 범위 합계에 64KiB 제한이 있다.
원래 입력의 부족이나 품질 기준 하향으로 토큰을 절약해서는 안 된다.

## 10. 해석의 한계

조건당 2회이므로 결과의 안정성과 일반화 가능성을 충분히 평가하지 못했다.
캐시·전역 설정·계정·서버 모델은 완전히 통제할 수 없었다.
교차 순서는 순서 영향을 살피는 데 도움이 되지만 이러한 변수를 제거하지 못한다.
원문 기록은 비공개이므로 외부 감사와 독립 재검증에 제한이 있다.
모든 파일을 무조건 첨부하는 방식이 유리하다는 결론으로 일반화하지 않는다.
후속 실험에서는 더 다양한 업무·반복 횟수와 범위 준수 근거를 확보해야 한다. 이번 결과는 필요한 자료가 이미 정해진 작은 정적 검토에서 직접 전달을 시험해 볼 근거로 활용한다.
