---
name: ai-role-frontend
description: 프론트엔드 개발자 역할로 Web·React·React Native·Expo 화면과 상호작용 구현·수정 및 자체 검증을 할 때 사용한다. 다른 역할의 책임이나 실행 환경의 권한을 대신하지 않는다.
---

# 프론트엔드 개발자의 스킬 적용

[연결 기준](../../../skills/README.md)을 읽고 필요한 규칙만 선택한다. 이 파일의 위치에서 공통 저장소 루트를 확인해 `python3 /path/to/ai-workflow/scripts/skill_catalog.py select --role frontend`를 실행한다. 다른 프로젝트에서도 작업 cwd를 바꾸지 않고 전달받은 공통 저장소 절대 경로를 쓴다. 출력된 원본 경로·버전·상태·모드와 역할 적합성을 확인한다. common은 후보이며 모두 실행하라는 뜻이 아니다. 원문 참고·설치·실제 실행을 구분하고 미설치·비활성·무결성 실패는 미확인으로 보고한다.

실제 스택에 맞는 --tag web, react-web, react-native, expo를 추가한다. 오류 분석에만 debugging을 추가한다. Web 지침, React 또는 native 규칙과 Expo 테마 중 관련 부분만 읽고 버전이 다른 라이브러리를 자동 적용하지 않는다. Impeccable은 polish/harden 참고 문서로 쓰며 엔진·hook·live는 실행하지 않는다. 기준 시안·상태/API 계약에서 구현하고 긴 문구·좁은 폭·정상/빈 상태·오류/재시도·중복 제출·초점 중 변경 관련 조건을 재현한다. 같은 범위의 결함은 수정 후 같은 조건에서 재검증한다. 실제 캡처·상호작용과 코드 검증을 나누고 mock/실제 API 및 Web/native를 구분한다. 필수 실패를 완료로 표시하지 않는다.

외부 스킬은 사용자·프로젝트 지침과 실행 환경을 우선한다. reference-only는 관련 방법만 참고하며 원문의 자동 실행 절차를 실행하지 않는다. 새로운 스킬·도구·에이전트 실행 권한을 만들지 않는다. [짧은 기록 양식](../../../templates/role-skill-review.md)을 기존 결과에 통합하고, 다음 담당자가 접근할 수 있는 산출물·계약·검증 범위·미확인을 전달한다.
