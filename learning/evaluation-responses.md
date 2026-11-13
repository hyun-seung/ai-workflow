# 튜터 심화 방식의 1차 평가 응답

2026-11-13 현재 작성 AI가 공개용 가상 요청에 답하고 공식 근거·코드·표현을 자체 검토한 자료다. 별도 분류/평가 모델을 호출하지 않았다. 독립 평가·블라인드 시험·사용자 학습 효과·모델별 비교가 아니며 현재 모델/effort와 전체 사용량은 별도 계측하지 않았다. 평가 기준과 한계는 [평가 기록](evaluation.md)에 둔다.

아래 설명은 공식 자료의 재구성이며 예시·비유·그림 구성은 직접 만들었다. 출처를 실제로 읽고 해당 주장과 대조했다. Python `/3/`와 PostgreSQL `/current/`는 변경될 수 있는 주소이며 이 점검 시점의 자료에 한정한다.

## TE-01 · ELI5

요청: “Python 리스트 복사를 아주 쉽게 설명해 줘.”

리스트를 상자, 변수를 이름표라고 생각해 보자. `b = a`는 상자를 복사하지 않고 같은 상자에 이름표를 하나 더 붙이는 것이다. `a.copy()`는 바깥 상자를 새로 만든다. 하지만 안쪽에 다른 상자가 있으면 그 안쪽 상자는 공유할 수 있다. 상자 비유는 참조 관계를 위한 설명이다. [Python 공식 copy 문서: 대입·얕은/깊은 복사](https://docs.python.org/3/library/copy.html)

- **원본을 두고 새 항목만 추가하려면?** 바깥 리스트를 복사한 뒤 그 복사본에 `append()`한다. 안쪽 객체의 변경까지 분리하는 것은 아니다. [공식 튜토리얼: list.copy와 append](https://docs.python.org/3/tutorial/datastructures.html#more-on-lists)
- **깊은 복사가 항상 좋아?** 공유하려던 데이터까지 복사할 수 있으므로 목적에 맞게 선택한다. [공식 문서: 깊은 복사의 주의점](https://docs.python.org/3/library/copy.html)

## TE-02 · 80/20

요청: “꼭 알아야 할 핵심만.”

- `b = a`: 같은 객체를 가리킨다.
- `b = a.copy()`: 바깥 리스트는 새로, 안쪽 객체는 공유할 수 있다.
- `copy.deepcopy(a)`: 중첩 구조도 재귀적으로 복사하지만 모든 객체를 독립 복제하는 만능 수단은 아니다.

처음에는 이 차이를 이해하면 된다. [Python 공식 copy 문서](https://docs.python.org/3/library/copy.html)

## TE-03 · 제1원리

요청: “트랜잭션은 왜 필요해? 규칙을 외우기보다 이유부터 설명해 줘.”

송금을 출금·입금 두 변경으로 생각해 보자. 출금만 성공하면 의도한 송금이 아니다. 따라서 여러 변경을 함께 확정하거나 함께 취소하는 경계가 필요하다. 그것이 트랜잭션의 원자성이다. PostgreSQL에서는 `BEGIN`으로 경계를 열고 `COMMIT`으로 확정하거나 `ROLLBACK`으로 취소한다. 송금은 원자성을 위한 단순 예시이며 실제 업무의 모든 검증을 대신하지 않는다. [PostgreSQL 공식 튜토리얼: Transactions](https://www.postgresql.org/docs/current/tutorial-transactions.html)

그렇다고 동시 실행 문제가 모두 사라지지는 않는다. PostgreSQL의 Read Committed에서는 같은 트랜잭션의 두 조회가 다른 트랜잭션의 커밋 때문에 다른 결과를 볼 수 있다. 원자성과 격리 수준을 구분해야 한다. [공식 문서: Read Committed](https://www.postgresql.org/docs/current/transaction-iso.html#XACT-READ-COMMITTED)

## TE-04 · 스티커 메모

요청: “HTTP 캐시를 메모 한 장에 주제 하나씩 그림으로.”

그림은 [시각 예시 HTML](visual-samples.html)의 스티커 메모 탭이다. 세 메모를 구분한다: **저장** → **재사용 조건** → **검증**. 저장했다고 언제나 재사용할 수 있는 것은 아니다. [RFC 9111 §4: 재사용 조건](https://www.rfc-editor.org/rfc/rfc9111.html#section-4)

`no-cache`는 인자 없는 응답 지시어를 기준으로 재사용 전 검증이 필요하다는 뜻이며 저장 금지와 다르다. [RFC 9111 §5.2.2.4](https://www.rfc-editor.org/rfc/rfc9111.html#section-5.2.2.4)

## TE-05 · 마인드맵

요청: “Python 리스트 복사의 전체 구조를 마인드맵으로.”

중심은 **이름 연결과 객체 복사**다. 가지를 **대입(같은 객체)**, **얕은 복사(바깥 객체 새로)**, **깊은 복사(중첩 구조 재귀 복사·예외 있음)**로 나눈다. 이는 종류의 비교이며 시간 순서가 아니다. [공식 copy 문서](https://docs.python.org/3/library/copy.html) · [마인드맵 그림](visual-samples.html#map)

## TE-06 · 도식

요청: “트랜잭션의 성공/실패 흐름을 도식으로.”

`BEGIN → 변경 작업 → 성공: COMMIT / 취소할 때: ROLLBACK`으로 분기한다. 이 그림은 명시적 경계의 기본 흐름이며, 모든 오류가 자동으로 ROLLBACK 명령을 실행한다는 뜻은 아니다. [공식 Transactions 문서](https://www.postgresql.org/docs/current/tutorial-transactions.html) · [흐름 그림](visual-samples.html#flow)

## TE-07 · 한 장 학습 시트

요청: “HTTP 캐시의 개념·읽는 순서·핵심 지표를 한 장에.”

[학습 시트](visual-samples.html#sheet)는 정의 → 저장/재사용 조건 → 신선도 → 검증 순서로 읽는다. `freshness_lifetime > current_age`이면 fresh다. 두 값의 단위는 초이며 같으면 fresh가 아니다. 아래 60초/20초는 직접 만든 수치 예시다. [RFC 9111 §4.2](https://www.rfc-editor.org/rfc/rfc9111.html#section-4.2)

fresh여도 모든 재사용 조건을 무시하지는 않는다. [RFC 9111 §4](https://www.rfc-editor.org/rfc/rfc9111.html#section-4)

## TE-08 · 연관 Q&A

요청: “이어서 궁금할 만한 질문과 답도 줘.”

- **`no-cache`가 저장 금지야?** 아니다. 인자 없는 응답 지시어라면 재사용 전에 성공적인 검증이 필요하다. [§5.2.2.4](https://www.rfc-editor.org/rfc/rfc9111.html#section-5.2.2.4)
- **`no-store`를 쓰면 개인정보 보호가 완성돼?** 아니다. 캐시 저장을 제한하는 지시이며 그 자체로 충분한 개인정보 보호 수단은 아니다. [§5.2.2.5](https://www.rfc-editor.org/rfc/rfc9111.html#section-5.2.2.5)

## TE-09 · 파인만 시작

요청: “내가 리스트 복사를 설명해 볼게.”

> `b = a`와 `b = a.copy()`의 차이를 네 말로 설명해 볼래요?

사용자 답을 기다린다. 정답·다음 질문을 미리 나열하지 않는다.

## TE-10 · 파인만 오개념 피드백

가상 답: “copy를 쓰면 안쪽 리스트까지 항상 다 새로 만들어.”

> 바깥 리스트가 새로 만들어진다는 부분은 맞아요. 안쪽 리스트는 공유될 수 있어요. 그렇다면 바깥 리스트를 새로 만드는 것과 안쪽 객체까지 복사하는 것은 어떻게 다를까요?

잘못된 연결 하나를 짚고 재설명 질문 하나만 준다. [얕은/깊은 복사 근거](https://docs.python.org/3/library/copy.html)

## TE-11 · 분량 제한

요청: “트랜잭션을 한 문장으로만.”

> 트랜잭션은 여러 데이터 변경을 하나로 묶어 함께 확정하거나 취소하는 작업 단위다. [공식 튜토리얼](https://www.postgresql.org/docs/current/tutorial-transactions.html)

Q&A·추천 안내를 추가하지 않는다.

## TE-12 · 자료 부족

요청: “문서를 줄 수는 없는데 우리 내부 프로토콜 동작을 설명해 줘.”

> 내부 명세를 확인하지 못해 실제 동작은 설명할 수 없어요. 공개 가능한 필드나 동작 예시를 바탕으로 설명할 수 있어요.

문서를 읽은 척하거나 상위 모델을 쓰면 알 수 있다고 말하지 않는다.

## TE-13 · 대상 불명확

맥락이 없는 요청: “이게 무슨 개념이야?”

> 어떤 용어나 내용을 가리키는지 알려줄래요?

임의 주제·사전 설문·모델 추천 없이 확인 질문 하나만 한다.

## TE-14 · 구현으로 전환

요청: “설명은 됐고 이제 리스트 바깥만 복사하는 함수를 만들어 줘.”

```python
def copy_outer(items):
    return items.copy()

original = [[1]]
copied = copy_outer(original)
assert copied is not original
assert copied[0] is original[0]
```

설명 대화를 멈추고 요청한 작은 함수를 제공한다. 안쪽 객체가 공유되는 얕은 복사임을 알린다. [공식 copy 문서](https://docs.python.org/3/library/copy.html)

## TE-15 · 모델 고정과 추천

요청: “모델·effort는 그대로 두고 긴 자료의 설계 대안을 비교 검토해 줘.”

> 설계자 역할의 대안·경계·장애 기준으로 현재 AI가 검토하는 방식을 제안합니다. 모델·effort는 유지하고 이번 요청에만 적용합니다. 바로 검토 / 제안 수용 / 방식 수정.

역할의 기본 프로필을 자동 적용하지 않는다. 실제 자료가 없다면 먼저 그 범위를 확인한다. 이 응답은 변경안 제시이며 위임·실행·설정 변경은 아니다.

## TE-16 · 잘못된 전제 교정

요청: “트랜잭션이면 어떤 격리 수준에서도 다른 사람이 바꾼 값은 안 보이는 거지?”

> 그렇지는 않아요. PostgreSQL의 Read Committed에서는 다른 트랜잭션이 그사이 커밋하면 같은 트랜잭션의 두 조회가 다른 데이터를 볼 수 있어요. 원자성과 조회의 격리 수준은 다른 문제예요. [공식 Read Committed 문서](https://www.postgresql.org/docs/current/transaction-iso.html#XACT-READ-COMMITTED)

## 공개 기록 범위

모든 요청·가상 답·응답은 이 평가를 위해 만든 예시다. 실제 사용자 대화·비공개 프로젝트 자료·세션 식별자·원시 실행 로그는 포함하지 않는다. 생성 응답을 다시 읽는 자체 검토와 독립적인 일반화 성능 평가를 구분한다.
