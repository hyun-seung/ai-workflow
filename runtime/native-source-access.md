# Orca native 원본·사용량 연결 조사

2026-11-15, Orca 1.4.259와 현재 실행 프로세스의 부모 계보에서 확인한 Codex CLI 0.167.0을 기준으로 조사했다. **공식 데이터 계약은 확인했지만 현재 ai-workflow가 원본 경로·턴별 사용량을 받는 연결은 확보하지 못했다.** 원본 수집 완료나 실제 비용 측정으로 표시하지 않는다.

## 새로 확인한 공식 경로

| 경로 | 확인한 내용 | 현재 목적에 대한 한계 |
| --- | --- | --- |
| Orca 플러그인 `agent.listSessions` | `history:read`로 세션 목록과 메시지/토큰 수 등을 제공 | 문서상 대화·파일 경로를 제공하지 않음. 목록의 totalTokens를 요청별 토큰으로 바꿀 수 없음 |
| Orca 플러그인 `agents.list` | workspace의 agent 상태·세션·담당 작업 정보 제공 | 공개된 반환 계약에서 provider thread 경로·턴별 카운터를 찾지 못함 |
| Codex `thread/read` | 저장된 thread metadata를 읽는 API | 현재 Orca가 소유한 app-server에 호출할 공식 창구가 필요 |
| 설치 버전 `Thread.path` | nullable이며 스키마상 unstable인 디스크 경로 | 파일 존재·현재 세션의 경로를 아직 관측하지 못함. `ephemeral` thread이면 디스크에 남기지 않을 수 있음 |
| 설치 버전 `Thread.id` / `sessionId` | thread 식별자와 같은 session tree가 공유하는 session 식별자를 구분 | 환경 변수의 session ID를 thread ID로 간주하거나 문자열로 변환하지 않음 |
| `thread/tokenUsage/updated` | `threadId`, `turnId`, `tokenUsage` 알림 | 현재 공개된 Orca 조회/플러그인 경로에서 이 원본 알림을 받는 방법은 미확인 |

Orca의 목록·권한·반환 범위는 [공식 plugin host API](https://github.com/stablyai/orca/blob/main/docs/reference/plugin-host-api.md#agentlistsessions)와 설치 CLI의 `orca-plugin-authoring` / `references/host-api.md`에서 확인했다. 목록 API를 쓰려고 플러그인을 설치하거나 history 권한을 추가하지 않았다.

OpenAI의 [`thread/read` 및 이벤트 계약](https://learn.chatgpt.com/docs/app-server#threads)을 확인하고, 실제 Orca가 사용하는 Codex 실행 파일의 `app-server generate-json-schema`로 해당 버전의 정적 스키마를 생성했다. 스키마 생성은 서버·thread·turn을 시작하지 않으며 실제 대화 내용도 읽지 않는다. 설치 스키마에는 `ThreadReadParams.threadId/includeTurns`, `ThreadReadResponse.thread`와 위 필드들이 있다.

토큰 알림의 설치 스키마에는 `total`, `last`, `modelContextWindow`와 입력·출력·캐시·추론·cache-write 등 원본 필드가 있다. 이들을 보존하되 서로 더하거나 context-window 값을 비용으로 바꾸지 않는다. 알림 형식의 존재만으로 특정 요청의 전체 사용량이 측정됐다고 하지 않는다. [공식 토큰 알림 설명](https://learn.chatgpt.com/docs/app-server#turn-events)

## 현재 연결이 막힌 위치

현재 도구의 부모 계보에서 Orca가 실행한 Codex app-server를 확인했지만 별도 `--listen` 인자는 없었다. 공식 계약의 기본 전송은 stdio다. 이 관측만으로 모든 내부 연결 방법의 부재를 단정하지 않으며, **현재 공개된 Orca 창구로 같은 서버에 `thread/read`를 전달할 방법을 찾지 못했다**는 범위로 결론 낸다. [공식 전송 계약](https://learn.chatgpt.com/docs/app-server#protocol)

따라서 저장 thread의 실제 `path`·`ephemeral` 값도 아직 조회하지 못했다. 파일 경로가 없다는 결과나 실제 세션이 ephemeral이라는 결론을 만들어 내지 않는다. 기존 stdio 스트림을 가로채거나 새 app-server의 상태를 현재 Orca 세션 상태로 대신 보고하지 않았다. 세션 폴더 탐색·신뢰 해시 작성·OS/전역 설정 변경도 하지 않았다.

기존 hook 경로는 [프로젝트 정의의 공식 검토·로딩·수신](orca-routing-input.md)이 확보되면 재개할 수 있다. 이번 조사는 같은 probe/worker를 다시 실행한 것이 아니다. 직접 제공받은 정확한 공식 원본 경로가 있다면 기존 `finish` 입력은 그대로 사용할 수 있다.

## 필요한 최소 연결 — 구현·게시 전 제안

다음은 Orca 연동 요청으로 검토할 **초안**이다. 존재하는 명령이나 현재 host API라고 해석하지 않는다. 외부 이슈·메시지로 게시하지 않았다.

우선적으로는 raw 대화나 파일 경로 없이 **명시한 dispatch/turn 한 건의 사용량 근거 내보내기**가 필요하다. Orca가 이미 소유한 provider 연결에서 받은 정보를 읽기 전용으로 전달하면 된다.

- 대상: 정확한 로컬 host·workspace·task/dispatch와 provider thread/turn 연결. 조율자 turn은 명시한 논리 요청 연결을 사용하며 가짜 dispatch를 만들지 않음.
- 신원: Orca ID, provider `threadId`, `sessionId`를 각각 반환하고 서로 변환하지 않음.
- 범위: 시작/종료 상태·관측 구간·누적/증가 카운터의 원래 종류·원본 이벤트 순서를 보존. 부분 수신·재연결·중복·초기 기준값 누락을 명시하며 미측정을 0으로 채우지 않음.
- 실행값: 요청·실행환경 적용·실제 관측 모델/effort를 구분. thread의 현재 설정을 과거 turn의 실제값으로 복제하지 않음.
- 수치: provider 원본 필드를 보존. 기존 수집기의 다섯 토큰 필드로 표현할 수 없는 추가 분류를 조용히 버리지 않고 변환 미지원/부분 측정으로 표시.
- 출처: 수집 host·provider/형식 버전·안정적인 구간 식별자·입력 근거 해시. 대화 본문·인증 정보·다른 세션 제목은 제외.
- 실패: unknown/unsupported/unavailable을 구분하고 다른 서버·계정·세션으로 자동 대체하지 않음. 조회 때문에 agent 시작·resume·turn 시작·입력 전송·설정 변경이 발생하지 않음.

기존 JSONL 수집기를 연결하려면 두 번째 선택으로 **동일 host가 증명한 특정 thread의 경로 metadata**를 받을 수 있다. nullable/unstable 경로임을 보존하고 실제 파일의 프로젝트·native 세션·배정을 다시 대조한다. 명시한 대상 이외의 목록이나 임의 경로 읽기 권한을 기본으로 요구하지 않는다.

이 제안만으로 새 수집 형식을 기존 metadata로 변환하지 않는다. 실제 지원 API·영수증·버전별 예시가 확보된 뒤에 어댑터를 구현하고, 원본 보존·중복 방지·구간 귀속·실제값 검증을 수행해야 한다.

## 다음 단계와 재개 조건

1. Orca의 공식 export/bridge 또는 신뢰된 hook에서 정확한 원본 경로/사용량 근거를 받는다. 현재는 그 연결이 미확인이다.
2. 원본 신원·형식·종료 구간·모델 관측을 검증하고 기존 완료/비교 도구가 받아들일 수 있는지 확인한다. 대화 transcript·목록 totalTokens는 대체 자료가 아니다.
3. 준비된 RP-01 한 쌍의 모델·effort·횟수와 수집 범위를 확정한 뒤 실제 비교를 시작한다. 과거 허용 한도를 초기화하거나 검증용 worker를 먼저 소비하지 않는다.

## 확인한 설치 스키마

아래는 실제 대화가 아닌 Codex 0.167.0의 정적 생성 파일이다. 전체 생성물·프로세스 경로는 비공개 조사 기록에만 보관한다.

| 파일 | SHA-256 |
| --- | --- |
| `ThreadReadParams.json` | `dfe040c6ac71d30795b8be3f3ff232e66f362a37f883b491e5d1ea367f470db4` |
| `ThreadReadResponse.json` | `4b1a0bb5d6896a5ef1c03045e35ca262b21471e07d4e322d874995b6fd31c094` |
| `ThreadTokenUsageUpdatedNotification.json` | `aba4f6c7e4a19b2b842c08ee793b57000c07dafd57b922ad0d8e7c76609108c2` |

수행한 검증은 공식 문서·설치 스키마·현재 프로세스 계보의 대조다. 플러그인 설치, app-server/agent/thread/turn 생성, 실제 원본/토큰 수집, 비용 절감 측정은 수행하지 않았다.
