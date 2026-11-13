'use strict';

const labels = {
  user_request: '사용자 요청', decision: '결정·이유 요약', assignment: '업무 배정',
  instruction: '전달 지시', tool_call: '명령·도구 입력', tool_result: '도구 결과',
  question: '질문', approval_request: '승인 요청 · 결정 미확인',
  approval_resolution: '승인 처리', handoff: '결과 인계', verification: '검증 판정', explanation: '명시된 설명', completion: '요청 마감', usage: '토큰 측정', usage_group: '논리 작업 묶음', revision: '기록 정정', profile: '실행 프로필 관측'
};
const show = value => JSON.stringify(value, null, 2) ?? String(value);
const known = value => value === null || value === undefined || value === '' ? '미확인' : value;
// IDs match workflow.json and roles/README.md. Colours follow roles, never row order.
const roleCatalog = {
  coordinator: { label: '조율자', color: '#8861ce', background: '#f5f0fc' },
  architect: { label: '설계자', color: '#4278cf', background: '#edf4ff' },
  designer: { label: '디자이너', color: '#b55b8e', background: '#fdf0f7' },
  backend: { label: '백엔드 개발자', color: '#2e9a77', background: '#edf8f3' },
  frontend: { label: '프론트엔드 개발자', color: '#cf7534', background: '#fff5eb' },
  verifier: { label: '검증자', color: '#a47817', background: '#fff8e6' }
};
function roleKey(value) {
  return Object.hasOwn(roleCatalog, value) ? value : Object.keys(roleCatalog).find(key => roleCatalog[key].label === value) || null;
}
function displayActors(bundle) {
  // Explicit additive labels preserve old source roles; conflicting labels stay unresolved.
  const bindings = bundle.events.filter(e => e.kind === 'assignment').flatMap(e =>
    Array.isArray(e.detail?.actor_roles) ? e.detail.actor_roles.map(row => ({ ...row, event_id: e.id })) : []);
  return bundle.actors.map(actor => {
    const matches = bindings.filter(row => row.actor_id === actor.id);
    const keys = new Set(matches.map(row => row.role_id));
    const selected = matches.length === 1 ? matches[0] : null;
    const key = matches.length ? keys.size === 1 ? roleKey(matches[0].role_id) : null : roleKey(actor.role_id ?? actor.role);
    const title = selected?.task_title || actor.task_title || (!roleKey(actor.role) ? actor.role : null) || '수입된 배정';
    return { ...actor, display_role_id: key, display_role: roleCatalog[key]?.label || '역할 미분류',
      display_task: title, display_basis: matches.map(row => row.event_id), display_role_conflict: keys.size > 1 };
  });
}

function statusText(value) {
  const names = { completed: '완료', finished: '종료', succeeded: '성공', failed: '실패', running: '진행 중', pending: '대기', cancelled: '취소', needs_followup: '후속 필요' };
  return names[value] ? `${names[value]} (${value})` : `${known(value)} · 뜻 미확인`;
}
function extractBody(detail, kind) {
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) {
    return { text: typeof detail === 'string' ? detail : show(detail), code: false };
  }
  if (typeof detail.block?.text === 'string') return { text: detail.block.text, code: false };
  if (detail.block && Object.hasOwn(detail.block, 'output')) {
    return { text: typeof detail.block.output === 'string' ? detail.block.output : show(detail.block.output), code: true };
  }
  if (kind === 'tool_call') {
    const input = detail.input;
    const command = typeof input?.cmd === 'string' ? input.cmd : input?.command;
    return { text: typeof command === 'string' ? command : typeof input === 'string' ? input : show(input ?? detail), code: true };
  }
  for (const key of ['content', 'summary', 'description', 'reason', 'text']) {
    if (typeof detail[key] === 'string') return { text: detail[key], code: false };
  }
  return { text: show(detail), code: true, structured: true };
}
function displayTime(value) {
  if (!value) return '시각 미확인';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const iso = date.toISOString();
  return `${iso.slice(0, 10)} · UTC · ${iso.slice(11, 23)}`;
}
function eventWarnings(event) {
  const detail = event.detail;
  const warnings = [];
  if (event.completeness !== 'complete') warnings.push(`사건 범위 ${known(event.completeness)}`);
  if (detail?.recorded_retrospectively === true) warnings.push('사후 기록');
  if (detail?.state === 'resolved' || detail?.block?.state === 'resolved') warnings.push('처리됨(resolved) · 승인/거절 결정 미확인');
  if (detail?.block?.isError === true || detail?.isError === true) warnings.push('도구 오류');
  function truncated(value) {
    if (typeof value === 'string') return /\(truncated\)|\[truncated\]|\bclipped\b/i.test(value);
    if (!value || typeof value !== 'object') return false;
    return Object.entries(value).some(([key, item]) => (/truncat|clipped/i.test(key) && item === true) || truncated(item));
  }
  if (truncated(detail)) warnings.push('원본 잘림 표식 있음');
  return warnings;
}
function roleFor(event, actors) { const actor = actors.get(event.actor_id); return event.kind === 'usage' ? roleCatalog[event.detail?.usage?.role]?.label || '역할 미분류' : actor?.display_role || actor?.role || '미확인'; }
function filterEvents(bundle, role, kind) {
  const actors = new Map(displayActors(bundle).map(actor => [actor.id, actor]));
  return bundle.events.filter(event => (!role || roleFor(event, actors) === role) && (!kind || event.kind === kind));
}
function collectionWarning(request, collections) {
  const counts = new Map();
  for (const collection of collections) counts.set(collection.completeness || 'unknown', (counts.get(collection.completeness || 'unknown') || 0) + 1);
  const parts = [...counts].map(([value, count]) => `${count}개 ${value === 'partial' ? '부분 (partial)' : value === 'unknown' ? '범위 미확인 (unknown)' : value}`);
  return `요청 범위 ${known(request.completeness)} · ${parts.length ? '수집본 ' + parts.join(' / ') : '수집 근거 없음 · 범위 미확인'} · 입력/출력 잘림 여부 미확인`;
}
function closeoutLines(closeout) {
  if (!closeout) return ['최종 결과 미등록 · 조회 완료는 작업 완료를 뜻하지 않습니다.'];
  const d = closeout.detail;
  const verification = { pass: '통과', fail: '실패', unverified: '미검증' }[d.verification] || '미확인';
  return [closeout.current ? '등록된 최종 결과' : '마감 이후 기록 변경 · 판정 재확인 필요',
    `마감 판정: ${statusText(d.status)} · 검증: ${verification}`, d.summary,
    `수집 범위: ${d.recording_status === 'partial' ? '부분 수집' : '범위 미확인'} · 전체 수집을 뜻하지 않습니다.`,
    '프로세스 종료 여부는 이 마감 기록에서 별도로 검증하지 않습니다.',
    `마감 기록: ${displayTime(closeout.time)} · 마지막 수집: ${displayTime(d.last_collection_time)}`];
}

// Only explicitly attributed, non-cumulative measurements are eligible for totals.
function profileFor(bundle, actor) {
  const owner = actor.owner_actor_id || actor.id;
  const usage = bundle.events.filter(e => e.kind === 'usage' && e.actor_id === owner &&
    (!actor.observation_id || e.id === actor.observation_id));
  const usageIds = new Set(usage.map(e => e.id));
  const evidence = bundle.events.filter(e => e.kind === 'profile' && e.source === 'trace.native-profile.v1' &&
    e.actor_id === owner && e.detail?.version === 1 && usageIds.has(e.detail?.usage_event_id));
  const stages = { requested: new Map(), effective: new Map(), actual: new Map() };
  const covered = new Set();
  function add(map, id, value) { if (!map.has(id)) map.set(id, []); map.get(id).push(value); }
  for (const e of evidence) {
    const d = e.detail;
    for (const name of ['requested', 'effective']) add(stages[name], d.usage_event_id, d[name]);
    for (const context of d.contexts) {
      add(stages.actual, d.measurement_key + ':' + context.line, context); covered.add(d.usage_event_id);
    }
  }
  function field(map, name, missing, stageName) {
    let partial = missing, conflict = false, unspecified = false;
    const all = new Set();
    for (const rows of map.values()) {
      const selected = stageName === 'actual' ? rows : rows.filter(r => r?.status === 'observed');
      const nativeNull = stageName !== 'actual' && selected.some(r => r[name] == null);
      const values = new Set(selected.map(r => r?.[name]).filter(v => typeof v === 'string' && v));
      if (!values.size && !nativeNull) partial = true;
      if (values.size > 1 || nativeNull && values.size) conflict = true;
      unspecified ||= nativeNull;
      for (const v of values) all.add(v);
    }
    const values = [...all].sort();
    const unset = stageName === 'requested' ? 'native 기본값 요청' : '적용값 미제공';
    const state = conflict ? 'conflict' : values.length > 1 || unspecified && values.length ? 'varies' :
      !values.length && !unspecified ? 'unavailable' : partial ? 'partial' : unspecified ? 'unspecified' : 'known';
    const text = state === 'unavailable' ? '미확인' : [...values, ...(unspecified ? [unset] : [])].join(' / ') +
      (state === 'conflict' ? ' · 근거 상충' : state === 'varies' ? ' · 구간별 상이' : state === 'partial' ? ' · 일부 미확인' : '');
    return { values, state, text };
  }
  const result = { evidence, covered: covered.size, intervals: usage.length };
  for (const name of ['requested', 'effective', 'actual']) {
    const missing = usage.some(e => name === 'actual' ? !covered.has(e.id) : !stages[name].has(e.id));
    result[name] = Object.fromEntries(['model', 'effort'].map(key => [key, field(stages[name], key, missing, name)]));
  }
  // Actor registration describes the actor, not every subsequent dispatch.
  if (!actor.observation_id) {
    for (const key of ['model', 'effort']) if (result.requested[key].state === 'unavailable' && actor.requested?.[key]) {
      result.requested[key] = { values: [actor.requested[key]], state: 'registered', text: actor.requested[key] + ' · 등록값' };
    }
  }
  result.warnings = [];
  for (const name of ['requested', 'effective']) for (const key of ['model', 'effort']) {
    const a = result[name][key], b = result.actual[key];
    if (['known', 'registered'].includes(a.state) && b.state === 'known' && a.values[0] !== b.values[0]) {
      result.warnings.push((name === 'requested' ? '요청' : '실행환경 적용') + ' ' + key + '와 실제 관측값이 다릅니다.');
    }
  }
  return result;
}
function profileLine(stage) { return '모델 ' + stage.model.text + ' · effort ' + stage.effort.text; }
function effectiveBundle(bundle) {
  return bundle.effective_events ? { ...bundle, original_events: bundle.events, events: bundle.effective_events } : bundle;
}

function usageFor(actor) {
  const u = actor.usage || {}, m = u.measurements || u.tokens || {};
  const valid = value => Number.isSafeInteger(value) && value >= 0;
  const input = m.input_tokens, output = m.output_tokens, total = input + output;
  if (!['measured', 'partial'].includes(u.status) || !valid(input) || !valid(output) || !valid(total) ||
      (m.total_tokens != null && m.total_tokens !== total) ||
      (m.cached_input_tokens != null && (!valid(m.cached_input_tokens) || m.cached_input_tokens > input)) ||
      (m.reasoning_output_tokens != null && (!valid(m.reasoning_output_tokens) || m.reasoning_output_tokens > output))) {
    return { kind: 'unavailable', label: '배정별 미측정', input: null, output: null, total: null };
  }
  const attributed = u.status === 'measured' && (u.scope === 'dispatch' ||
    u.scope === 'native_turn' && [2, 3].includes(u.version) && actor.observation_id && u.role === 'coordinator' && u.dispatch_id === null) && u.task_attributed === true &&
    u.actor_id === (actor.observation_id ? actor.native_actor_id : actor.id) && u.request_id === actor.request_id && typeof u.source === 'string' && u.source &&
    typeof u.measurement_key === 'string' && u.measurement_key.trim();
  return { kind: attributed ? 'attributed' : 'reference', label: attributed ? u.scope === 'native_turn' ? '조율자 턴 측정' : '배정별 측정' : u.scope === 'session_cumulative' ? '세션 누적 · 참고값' : '배정 귀속 미확인',
    input, output, total, cached: m.cached_input_tokens ?? null, reasoning: m.reasoning_output_tokens ?? null,
    uncached: m.cached_input_tokens == null ? null : input - m.cached_input_tokens,
    ordinaryOutput: m.reasoning_output_tokens == null ? null : output - m.reasoning_output_tokens,
    key: attributed ? u.measurement_key.startsWith('codex.session:') ? u.measurement_key : u.source + ':' + u.measurement_key : null };
}
function usageUnits(bundle, actors = displayActors(bundle)) {
  const observations = bundle.events.filter(event => event.kind === 'usage');
  return actors.flatMap(actor => {
    const own = observations.filter(event => event.actor_id === actor.id);
    if (!own.length) return [{ ...actor, owner_actor_id: actor.id }];
    return own.map(event => {
      const u = event.detail.usage;
      return { ...actor, id: event.id, owner_actor_id: actor.id, observation_id: event.id,
        native_actor_id: u.actor_id, task_id: u.task_id, dispatch_id: u.dispatch_id,
        display_role_id: u.role, display_role: roleCatalog[u.role]?.label || '역할 미분류',
        display_task: event.detail.task_title, usage: u, usage_event: event };
    });
  });
}
function actorUsage(summary, actor) {
  const rows = summary.rows.filter(row => (row.actor.owner_actor_id || row.actor.id) === actor.id);
  if (rows.length === 1 && !rows[0].actor.observation_id) return rows[0];
  const measured = rows.filter(row => row.kind === 'attributed');
  if (!measured.length) return usageFor(actor);
  const result = { kind: 'attributed', label: `측정된 ${measured.length}개 배정 합계` };
  for (const field of ['input', 'output', 'total', 'cached', 'reasoning', 'uncached', 'ordinaryOutput']) {
    const sum = measured.reduce((n, row) => n + row[field], 0);
    result[field] = measured.every(row => row[field] != null) && Number.isSafeInteger(sum) ? sum : null;
  }
  if (result.total == null) result.kind = 'unavailable';
  return result;
}
function roleUsage(summary) {
  const groups = new Map();
  for (const row of summary.rows) {
    const key = row.actor.display_role_id || 'unclassified';
    if (!groups.has(key)) groups.set(key, { key, label: roleCatalog[key]?.label || '역할 미분류', rows: [] });
    groups.get(key).rows.push(row);
  }
  for (const group of groups.values()) {
    const measured = group.rows.filter(row => row.kind === 'attributed');
    group.covered = measured.length;
    for (const field of ['input', 'output', 'total', 'cached', 'reasoning', 'uncached', 'ordinaryOutput']) {
      const sum = measured.reduce((n, row) => n + row[field], 0);
      group[field] = measured.length && measured.every(row => row[field] != null) && Number.isSafeInteger(sum) ? sum : null;
    }
  }
  return [...groups.values()];
}
function usageSummary(actors) {
  const rows = actors.map(actor => ({ actor, ...usageFor(actor) }));
  const counts = new Map();
  for (const row of rows) if (row.key) counts.set(row.key, (counts.get(row.key) || 0) + 1);
  for (const row of rows) if (row.key && counts.get(row.key) > 1) { row.kind = 'reference'; row.label = '중복 측정 · 집계 제외'; }
  const measured = rows.filter(row => row.kind === 'attributed');
  const input = measured.reduce((n, row) => n + row.input, 0), output = measured.reduce((n, row) => n + row.output, 0);
  const safe = [input, output, input + output].every(Number.isSafeInteger);
  return { rows, covered: measured.length, total: measured.length && safe ? input + output : null,
    input: measured.length && safe ? input : null, output: measured.length && safe ? output : null };
}
function tokenText(value) { return value == null ? '—' : value.toLocaleString('ko-KR'); }
function brief(text, limit = 155) {
  const clean = String(text ?? '').replace(/^#+\s*/gm, '').replace(/\s+/g, ' ').trim();
  return clean.length > limit ? clean.slice(0, limit) + '…' : clean;
}
function instructionExcerpt(event) {
  if (!event) return '전달 지시가 수입되지 않았습니다.';
  const body = extractBody(event.detail, event.kind);
  if (/^You are working inside Orca,/.test(body.text) && /\(truncated\)|\[truncated\]/i.test(body.text)) {
    return '수신 지시가 도구 안내 부분에서 잘렸습니다. 조율자가 보존한 업무 지시와 수신 원문을 펼쳐 확인하세요.';
  }
  return body.structured ? '구조화된 지시입니다. 원문을 펼쳐 확인하세요.' : brief(body.text);
}
function requestExcerpt(bundle) {
  const request = bundle.requests[0];
  if (typeof request.summary === 'string' && request.summary.trim()) return { label: '요청 요약', text: request.summary };
  const event = bundle.events.find(e => e.kind === 'user_request');
  if (!event) return { label: '요청 내용', text: '요청 원문이 수입되지 않았습니다.' };
  const body = extractBody(event.detail, event.kind);
  return { label: '보존된 요청 발췌', text: body.structured ? '구조화된 요청 기록입니다. 펼쳐서 내용을 확인하세요.' : brief(body.text, 200) };
}
function flowGroups(bundle) {
  return { roots: bundle.actors.filter(a => !a.parent_id), workers: bundle.actors.filter(a => a.parent_id),
    decisions: bundle.events.filter(e => e.kind === 'decision'), assignments: bundle.events.filter(e => e.kind === 'assignment') };
}
// Only explicit, timezone-bearing usage intervals position measurements.
function instant(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const day = Number(value.slice(8, 10)), month = Number(value.slice(5, 7)), year = Number(value.slice(0, 4));
  if (month < 1 || month > 12 || day < 1 || day > new Date(Date.UTC(year, month, 0)).getUTCDate()) return null;
  if (Number(value.slice(11, 13)) > 23 || Number(value.slice(14, 16)) > 59 || Number(value.slice(17, 19)) > 59) return null;
  const time = Date.parse(value); return Number.isFinite(time) ? time : null;
}
function compareInstants(a, b) {
  const first = instant(a), second = instant(b);
  if (first === null || second === null) return null;
  if (first !== second) return Math.sign(first - second);
  const remainder = value => (value.match(/\.(\d+)/)?.[1] || '').slice(3);
  const x = remainder(a), y = remainder(b), size = Math.max(x.length, y.length);
  return x.padEnd(size, '0').localeCompare(y.padEnd(size, '0'));
}
function segmentLabel(row, index) {
  return `구간 ${index + 1} · ${row.actor.display_task || '제목 미확인'} · ${tokenText(row.kind === 'attributed' ? row.total : null)} tokens`;
}
function measurementInterval(row) {
  const interval = row.actor.usage_event?.detail?.usage?.interval;
  const start = instant(interval?.started_at), end = instant(interval?.finished_at);
  const reason = start === null || end === null ? '시작·종료 시각 미확인 (timezone 포함 필요)' : compareInstants(interval.finished_at, interval.started_at) < 0 ? '종료가 시작보다 앞선 기록' : null;
  return { start, end, valid: !reason, point: !reason && start === end, reason };
}
function logicalTasks(bundle, measured) {
  const claimed = new Set(), tasks = [];
  for (const group of bundle.usage_groups || []) {
    const rows = measured.rows.filter(row => group.measurement_event_ids?.includes(row.actor.observation_id) &&
      row.actor.owner_actor_id === group.actor_id && row.actor.display_role_id === group.role && !claimed.has(row.actor.id));
    if (!rows.length) continue;
    rows.forEach(row => claimed.add(row.actor.id));
    tasks.push({ id: group.event_id, title: group.title, role: group.role, rows, group });
  }
  for (const row of measured.rows) if (!claimed.has(row.actor.id)) tasks.push({ id: row.actor.id,
    title: row.actor.display_task || '수입된 배정', role: row.actor.display_role_id, rows: [row], group: null });
  return tasks;
}
function measurementTimeline(tasks) {
  const rows = tasks.flatMap(task => task.rows.map(row => ({ task, row, ...measurementInterval(row) })));
  const valid = rows.filter(row => row.valid);
  return { rows, start: valid.length ? Math.min(...valid.map(row => row.start)) : null,
    end: valid.length ? Math.max(...valid.map(row => row.end)) : null };
}
function durationText(request) {
  const a = request.execution?.started_at, b = request.execution?.finished_at;
  const order = compareInstants(b, a);
  if (order === null || order < 0) return null;
  const seconds = Math.round((Date.parse(b) - Date.parse(a)) / 1000);
  return seconds < 60 ? `${seconds}초` : `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
}
if (typeof module !== 'undefined') module.exports = { compareInstants, segmentLabel, instant, measurementInterval, logicalTasks, measurementTimeline, profileFor, profileLine, effectiveBundle, extractBody, displayTime, eventWarnings, filterEvents, statusText, collectionWarning, closeoutLines, usageFor, usageSummary, tokenText, brief, instructionExcerpt, requestExcerpt, flowGroups, durationText, roleCatalog, roleKey, displayActors, roleUsage, usageUnits, actorUsage };
if (typeof document !== 'undefined') startViewer();

function startViewer() {
  const $ = id => document.getElementById(id), mobile = matchMedia('(max-width: 768px)');
  let data = null, selectedId = null, selectionVersion = 0, listVersion = 0, listCount = 0;
  function node(tag, text, className) {
    const e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    if (className) e.className = className;
    return e;
  }
  function button(text, action) { const e = node('button', text); e.type = 'button'; e.onclick = action; return e; }
  function disclosure(title, ...children) {
    const d = node('details'), body = node('div', undefined, 'details-content');
    body.append(...children); d.append(node('summary', title), body); return d;
  }
  function lazyDisclosure(title, build, className = '') {
    const d = disclosure(title); d.className = className;
    d.addEventListener('toggle', () => { if (d.open && !d.dataset.loaded) { build(d.lastChild); d.dataset.loaded = 'true'; } });
    return d;
  }
  function pill(status) {
    const names = { completed: '완료', finished: '종료', succeeded: '완료', failed: '실패', running: '진행 중', pending: '대기', cancelled: '취소', needs_followup: '후속 필요' };
    return node('span', names[status] || '상태 미확인', 'status-pill ' + (['completed', 'succeeded', 'finished'].includes(status) ? 'good' : ['failed', 'needs_followup'].includes(status) ? 'bad' : ''));
  }
  async function get(url) {
    const response = await fetch(url, { credentials: 'omit' });
    if (!response.ok) { const error = new Error('HTTP ' + response.status); error.status = response.status; throw error; }
    return response.json();
  }
  function syncNavigation() {
    $('request-browser').open = !mobile.matches || !selectedId;
    $('browser-label').textContent = `내 요청 · ${listCount}개${selectedId ? ' · 목록 펼치기' : ''}`;
    for (const e of $('requests').children) e.setAttribute('aria-current', String(e.dataset.id === selectedId));
  }
  async function list(refresh = false) {
    const version = ++listVersion;
    $('list-state').textContent = '목록 불러오는 중…'; $('list-state').setAttribute('role', 'status');
    $('list-actions').replaceChildren();
    try {
      const rows = await get('/api/requests?limit=100');
      if (version !== listVersion) return;
      listCount = rows.length;
      $('requests').replaceChildren();
      $('list-state').textContent = rows.length ? `${rows.length}개 요청 · 최대 100개` : '저장된 요청이 없습니다.';
      for (const request of rows) {
        const e = button('', () => selectRequest(request.id)); e.className = 'request-item'; e.dataset.id = request.id;
        e.append(node('strong', request.title || request.id), node('small', request.time ? displayTime(request.time).split(' · UTC')[0] : '접수 시각 미확인'));
        $('requests').append(e);
      }
      syncNavigation();
      if (rows.length && (!selectedId || !rows.some(r => r.id === selectedId))) await selectRequest(rows[0].id, false);
      else if (rows.length && refresh) await selectRequest(selectedId, false);
      else if (!rows.length) { ++selectionVersion; selectedId = null; clearRequest(); $('main').setAttribute('aria-busy', 'false'); $('status').className = ''; $('status').setAttribute('role', 'status'); $('status').textContent = '아직 기록이 없습니다. 요청 기록을 수입한 뒤 새로고침해 주세요.'; syncNavigation(); }
    } catch (error) {
      if (version !== listVersion) return;
      $('list-state').setAttribute('role', 'alert'); $('list-state').textContent = '목록을 읽을 수 없습니다.';
      $('list-actions').append(button('목록 다시 조회', () => list(refresh)));
    }
  }
  function clearRequest() {
    data = null;
    for (const name of ['summary', 'metrics', 'workflow', 'usage', 'metadata', 'timeline']) $(name).replaceChildren();
    $('all-records').hidden = true; $('all-records').open = false;
  }
  function metric(label, value, note) {
    const e = node('article', undefined, 'metric');
    e.append(node('p', label, 'metric-label'), node('strong', value, 'metric-value'), node('p', note, 'metric-note'));
    return e;
  }
  function renderEvent(event, actors, preview = true) {
    const item = node('li', undefined, 'event'); item.dataset.eventId = event.id;
    const header = node('div', undefined, 'event-header');
    header.append(node('span', displayTime(event.time), 'event-time'), node('span', roleFor(event, actors)), node('span', labels[event.kind] || event.kind, 'event-kind'));
    const body = extractBody(event.detail, event.kind), badges = node('div', undefined, 'badges');
    for (const warning of eventWarnings(event)) badges.append(node('span', warning, 'warning'));
    const history = data?.event_revisions?.[event.id] || [];
    if (history.length) badges.append(node('span', '정정됨', 'warning'));
    item.append(header, badges);
    if (history.length) item.append(lazyDisclosure('정정 이력·최초 기록 펼치기', target => {
      const original = data.original_events.find(e => e.id === event.id);
      target.append(node('pre', show({ original, revisions: history })));
    }));
    if (preview) item.append(node('p', body.structured ? '구조화된 기록' : brief(body.text, 200), 'preview'));
    item.append(lazyDisclosure(event.kind === 'tool_call' ? '실제 명령 펼치기' : '내용·원문 펼치기', target => {
      target.append(node(body.code ? 'pre' : 'div', body.text, body.code ? 'code' : 'body-text'));
      const hash = event.detail?.original_file_sha256 ?? event.original_file_sha256;
      target.append(lazyDisclosure('원문 JSON·출처·해시', raw => {
        raw.append(node('p', '정규 JSON SHA256: ' + known(event.detail_sha256), 'muted'));
        if (hash) raw.append(node('p', '원본 파일 SHA256: ' + hash, 'muted'));
        raw.append(node('pre', show(event)));
      }));
    }, 'plain-disclosure'));
    return item;
  }
  function eventList(events, actors) {
    const list = node('ol'); list.className = 'event-list';
    list.style.listStyle = 'none'; list.style.padding = '0';
    for (const e of events) list.append(renderEvent(e, actors));
    return list;
  }
  function evidenceDetails(title, events, actors) {
    return lazyDisclosure(`${title} (${events.length})`, body => {
      body.append(events.length ? eventList(events, actors) : node('p', '해당 기록이 수입되지 않았습니다.', 'muted'));
    }, 'plain-disclosure');
  }
  function renderSummary(bundle, actors) {
    const request = bundle.requests[0], excerpt = requestExcerpt(bundle), title = node('h2', request.title || request.id);
    title.tabIndex = -1;
    const row = node('div', undefined, 'title-row');
    row.append(title, pill(bundle.closeout?.current ? bundle.closeout.detail.status : request.status));
    $('summary').append(node('p', 'REQUEST / ' + request.id, 'eyebrow'), row, node('p', excerpt.label + ' · ' + excerpt.text, 'request-copy'));
    if (request.sample) $('summary').append(node('strong', '가상 예시 · 실제 업무 자료 아님', 'warning'));
    const foot = node('div', undefined, 'summary-foot');
    foot.append(evidenceDetails('요청 원문·보존 기록 펼치기', bundle.events.filter(e => e.kind === 'user_request'), actors));
    const partial = bundle.collections.some(c => c.completeness !== 'complete') || request.completeness !== 'complete';
    foot.append(node('span', partial ? '일부 기록 · 원문 전체 여부는 출처 확인' : '수입된 기록 기준', partial ? 'warning' : 'muted'));
    $('summary').append(foot);
    if (bundle.closeout && !bundle.closeout.current) $('summary').append(node('p', '마감 이후 기록이 변경되었습니다. 판정을 다시 확인해야 합니다.', 'warning'));
    return title;
  }
  function paint(e, role) { const color = roleCatalog[role] || { color: '#718096', background: '#f2f4f7' }; e.style.setProperty('--role-color', color.color); e.style.setProperty('--role-bg', color.background); }
  function agentCard(actor, index, bundle, actors, usage) {
    const profile = profileFor(bundle, actor);
    const own = bundle.events.filter(e => e.actor_id === actor.id), instructions = own.filter(e => e.kind === 'instruction');
    const decisions = own.filter(e => e.kind === 'decision'), plans = decisions.filter(e => !e.detail?.limits?.length);
    const selected = actor.parent_id ? instructions[0] : (plans[0] || decisions[0]);
    const text = selected ? extractBody(selected.detail, selected.kind) : null;
    const d = lazyDisclosure('', body => {
      body.append(profileDetails(profile, actor), node('p', usage.label, 'muted'));
      const grid = node('div', undefined, 'mini-grid');
      for (const [label, value] of [['입력', usage.input], ['출력', usage.output], ['합계', usage.total]]) {
        const cell = node('div', label, 'mini-stat'); cell.append(node('strong', tokenText(value))); grid.append(cell);
      }
      body.append(grid);
      body.append(tokenDetails(usage));
      if (usage.kind !== 'attributed') body.append(node('p', '참고 측정값은 이 배정의 토큰으로 확정하거나 요청 합계에 더하지 않습니다.', 'muted'));
      if (decisions.length) body.append(evidenceDetails('조율자가 남긴 계획·이유', decisions, actors));
      body.append(evidenceDetails(actor.parent_id ? '전달받은 지시' : '하위 에이전트에 내린 지시', instructions, actors));
      const commands = own.filter(e => ['tool_call', 'tool_result'].includes(e.kind));
      body.append(evidenceDetails('실제 명령·도구 결과', commands, actors));
      const output = own.filter(e => ['explanation', 'handoff', 'verification'].includes(e.kind));
      body.append(evidenceDetails('작업 설명·반환 결과', output, actors));
      if (own.some(e => e.kind === 'usage')) body.append(evidenceDetails('배정별 측정 근거', own.filter(e => e.kind === 'usage'), actors));
      body.append(lazyDisclosure('모델·사용량 원문과 출처', target => {
        target.append(node('pre', show(bundle.actors.find(a => a.id === actor.id))));
        target.append(node('p', '표시 역할 근거: ' + (actor.display_basis.join(', ') || '원본 역할'), 'muted'));
      }, 'plain-disclosure'));
      body.append(node('p', '수입된 실행별 기록입니다. 같은 세션의 초기·후속 내용이 함께 포함될 수 있습니다.', 'notice'));
    }, 'work-card');
    d.dataset.actorId = actor.id; d.dataset.roleId = actor.display_role_id || 'unclassified'; paint(d, actor.display_role_id);
    const head = d.firstChild, top = node('div', undefined, 'card-top');
    top.append(node('span', actor.display_role, 'card-role'), pill(actor.status));
    const parent = actors.get(actor.parent_id), predecessor = bundle.closeout?.detail.assignments.find(a => a.actor_id === actor.id)?.previous_actor_id;
    head.append(top, node('p', actor.display_task, 'card-task'), node('p', parent ? `${parent.display_role} → ${actor.display_role}` : '최상위 실행 · 조율 기록 확인', 'card-path'));
    if (actor.display_role_conflict) head.append(node('p', '역할 지정이 서로 충돌합니다. 원문 근거를 확인하세요.', 'warning'));
    if (predecessor) head.append(node('p', '후속 배정 · ' + (actors.get(predecessor)?.display_role || predecessor), 'muted'));
    head.append(node('p', actor.parent_id ? instructionExcerpt(selected) : text && !text.structured ? brief(text.text) : '명시된 계획 기록이 없습니다.', 'card-copy'));
    const bottom = node('div', undefined, 'card-bottom');
    bottom.append(node('strong', usage.kind === 'attributed' ? `${tokenText(usage.total)} tokens` : '토큰 · 배정별 미확인'));
    head.append(bottom, node('p', '요청: ' + profileLine(profile.requested), 'profile-line muted'),
      node('p', '실제 관측: ' + profileLine(profile.actual), 'profile-line'), node('p', '지시·사용량·명령 펼치기 ↗', 'card-open'));
    return d;
  }
  function profileDetails(profile, actor) {
    return lazyDisclosure('모델·effort의 요청·적용·관측 근거', body => {
      for (const [name, title] of [['requested', '요청'], ['effective', '실행환경 적용'], ['actual', '실제 관측']]) {
        body.append(node('p', title + ': ' + profileLine(profile[name])));
      }
      for (const warning of profile.warnings) body.append(node('p', warning, 'warning'));
      body.append(node('p', `원본 턴 관측 ${profile.covered}/${profile.intervals}개 측정 구간. 실행환경 적용값과 provider의 실제 관측값은 구분합니다.`, 'notice'));
      if (actor.observed?.model || actor.observed?.effort) body.append(node('p', '원래 수입값 · 이 표시의 실제 관측 근거로 사용하지 않음: ' + show(actor.observed), 'muted'));
      body.append(lazyDisclosure('프로필 원문·출처·입력 해시', raw => raw.append(node('pre', show({
        events: profile.evidence, evidence: data.collections.filter(c => c.source === 'trace.native-profile-evidence.v1' && profile.evidence.some(e => e.id === c.source_id))
      })))));
    }, 'plain-disclosure');
  }
  function renderFlow(bundle, actors, measured) {
    const section = $('workflow'); section.className = 'section-panel';
    const heading = node('div', undefined, 'section-heading'), headingText = node('div');
    headingText.append(node('h3', '어떻게 나누어 진행했나요?'), node('p', '카드를 펼치면 전달 지시와 실제 명령을 볼 수 있습니다.'));
    heading.append(headingText); section.append(heading);
    const legend = node('div', undefined, 'legend');
    for (const key of new Set([...actors.values()].map(a => a.display_role_id))) {
      const e = node('span', roleCatalog[key]?.label || '역할 미분류'); paint(e, key); legend.append(e);
    }
    section.append(legend);
    const groups = flowGroups({ ...bundle, actors: [...actors.values()] }), board = node('div', undefined, 'flow-board');
    function phase(number, name) { const e = node('div', undefined, 'phase'), h = node('div', undefined, 'phase-title'); h.append(node('b', number), node('span', name)); e.append(h); return e; }
    const planning = phase('01', '조율 · 계획'), delegation = phase('02', '담당 업무'), result = phase('03', '결과 · 검증');
    for (const a of groups.roots) planning.append(agentCard(a, bundle.actors.indexOf(a), bundle, actors, actorUsage(measured, a)));
    if (!groups.roots.length) planning.append(node('p', '조율자 연결 기록이 없습니다.', 'empty'));
    planning.append(evidenceDetails('전체 배정 근거', groups.assignments, actors));
    planning.append(evidenceDetails('하위 에이전트에 내린 지시', bundle.events.filter(e => e.kind === 'instruction' && groups.roots.some(a => a.id === e.actor_id)), actors));
    const cards = node('div', undefined, 'assignment-cards');
    for (const a of groups.workers) cards.append(agentCard(a, bundle.actors.indexOf(a), bundle, actors, actorUsage(measured, a)));
    if (!groups.workers.length) cards.append(node('p', '연결된 하위 실행이 없습니다.', 'empty'));
    delegation.append(cards);
    const closeout = bundle.closeout, box = node('div', undefined, 'result-box' + (!closeout?.current ? ' unknown-result' : ''));
    const verdicts = bundle.events.filter(e => e.kind === 'verification');
    box.append(node('h4', closeout ? closeout.current ? '등록된 최종 결과' : '판정 재확인 필요' : '검증·결과 기록'));
    box.append(node('p', closeout ? brief(closeout.detail.summary, 240) : verdicts.length ? '반환된 검증 기록이 있습니다. 최종 마감 판정은 아직 등록되지 않았습니다.' : '최종 결과가 아직 수입되지 않았습니다.'));
    if (closeout) {
      box.append(pill(closeout.detail.status));
      box.append(lazyDisclosure('결과 파일·판정·제한 사항', target => {
        for (const line of closeoutLines(closeout)) target.append(node('p', line));
        for (const file of closeout.detail.artifacts) target.append(node('p', file.label + ': ' + file.path));
        for (const line of closeout.detail.limitations) target.append(node('p', line));
        target.append(lazyDisclosure('마감 원문·파일 해시', raw => raw.append(node('pre', show(closeout))), 'plain-disclosure'));
      }, 'plain-disclosure'));
    }
    box.append(evidenceDetails('검증 근거', verdicts, actors), evidenceDetails('결과 인계', bundle.events.filter(e => e.kind === 'handoff'), actors));
    result.append(box);
    board.append(planning, node('span', '→', 'flow-arrow'), delegation, node('span', '→', 'flow-arrow'), result);
    section.append(board, node('p', '단계별 보기 · 카드 위치와 길이는 실행 시간이나 병렬 실행을 뜻하지 않습니다.', 'notice'));
  }
  function tokenDetails(row) {
    return lazyDisclosure('토큰 세부 항목 · 파일 읽기 등', body => {
      const grid = node('dl', undefined, 'token-breakdown');
      for (const [name, value] of [['입력 전체', row.input], ['캐시 입력 (입력에 포함)', row.cached], ['캐시 외 입력', row.uncached],
        ['출력 전체', row.output], ['추론 출력 (출력에 포함)', row.reasoning], ['추론 외 출력', row.ordinaryOutput], ['총 토큰 (입력 + 출력)', row.total]]) {
        grid.append(node('dt', name), node('dd', tokenText(value)));
      }
      grid.append(node('dt', '파일 읽기·검색 토큰'), node('dd', '도구별 귀속 미수집'), node('dt', '파일 수정·명령 실행 토큰'), node('dd', '도구별 귀속 미수집'));
      body.append(grid, node('p', '캐시 입력·추론 출력은 전체 값에 포함됩니다. 다시 더하지 않습니다. 파일 읽기 결과 등이 입력에 포함될 수 있지만, 이 기록에는 도구별 토큰 배분 근거가 없습니다.', 'notice'));
      if (row.kind === 'reference') body.append(node('p', row.label + ' · 요청/역할/작업 합계 제외', 'warning'));
    }, 'plain-disclosure');
  }
  function renderTasks(bundle, measured, section) {
    const tasks = logicalTasks(bundle, measured), timeline = measurementTimeline(tasks);
    const actors = new Map(displayActors(bundle).map(actor => [actor.id, actor]));
    section.append(node('h3', '작업 묶음과 측정 시간 흐름'), node('p', '명시 묶음만 연결합니다. 토큰 합계는 물리 측정을 한 번만 합산합니다.', 'notice'));
    const chart = node('div', undefined, 'measurement-chart');
    chart.tabIndex = 0; chart.setAttribute('role', 'region'); chart.setAttribute('aria-label', '측정 시간 흐름 · 내부 가로 스크롤 가능');
    const plot = node('div', undefined, 'measurement-plot');
    if (timeline.start !== null) plot.append(node('p', `${displayTime(new Date(timeline.start).toISOString())} → ${displayTime(new Date(timeline.end).toISOString())}`, 'muted'));
    else plot.append(node('p', '위치를 표시할 유효 측정 시각이 없습니다.', 'empty'));
    const taskList = node('div', undefined, 'logical-tasks');
    tasks.forEach((task, index) => {
      const detail = lazyDisclosure('', body => {
        if (task.group) {
          const event = bundle.events.find(e => e.id === task.group.event_id);
          if (event) body.append(eventList([event], actors));
          body.append(disclosure('현재 묶음 구성·합계 원문', node('pre', show(task.group))));
        }
        for (const [memberIndex, row] of task.rows.entries()) {
          const member = node('article', undefined, 'task-usage'), interval = measurementInterval(row);
          member.id = `logical-task-${index}-member-${memberIndex}`; member.tabIndex = -1;
          member.append(node('h4', segmentLabel(row, memberIndex)),
            node('p', `${row.label} · 입력 ${tokenText(row.input)} · 출력 ${tokenText(row.output)}`, 'muted'),
            node('p', interval.valid ? `${displayTime(row.actor.usage.interval.started_at)} → ${displayTime(row.actor.usage.interval.finished_at)}${interval.point ? ' · 같은 밀리초로 표시' : ''}` : interval.reason, 'muted'),
            tokenDetails(row), profileDetails(profileFor(bundle, row.actor), row.actor));
          if (interval.valid) member.append(node('p', `원본 구간: ${row.actor.usage.interval.started_at} → ${row.actor.usage.interval.finished_at}`, 'body-text'));
          const events = bundle.events.filter(e => e.actor_id === (row.actor.owner_actor_id || row.actor.id) && ['instruction', 'assignment', 'tool_call', 'tool_result', 'handoff'].includes(e.kind));
          member.append(evidenceDetails('담당자 지시·명령·결과 (해당 구간 귀속 미확인)', events, actors));
          if (row.actor.usage_event) member.append(eventList([row.actor.usage_event], actors));
          body.append(member);
        }
      }, 'logical-task');
      detail.id = `logical-task-${index}`; paint(detail, task.role);
      const m = task.group?.measurements;
      const total = m ? m.total_tokens : task.rows[0].kind === 'attributed' ? task.rows[0].total : null;
      const intervals = task.rows.map(measurementInterval), valid = intervals.filter(i => i.valid).length;
      detail.firstChild.append(node('span', roleCatalog[task.role]?.label || '역할 미분류', 'card-role'),
        node('strong', task.title || '제목 미확인', 'logical-title'),
        node('span', `${tokenText(total)} tokens · ${task.rows.length}개 ${task.group ? '측정 구간 · 명시 묶음' : task.rows[0].actor.observation_id ? '물리 측정' : '미측정 담당자'}`, 'muted'),
        node('small', `시각 확인 ${valid} · 미확인 ${intervals.length - valid} · 상세 펼치기`));
      if (bundle.event_revisions?.[task.id]?.length) detail.firstChild.append(node('span', '정정됨 · 이력은 상세에서 확인', 'warning'));
      taskList.append(detail);
      const validRows = timeline.rows.filter(r => r.task === task && r.valid);
      if (!validRows.length) return;
      const lane = node('div', undefined, 'measurement-lane'); paint(lane, task.role);
      lane.append(node('p', `${roleCatalog[task.role]?.label || '역할 미분류'} · ${task.title}`, 'lane-label'));
      // A full-width labelled control keeps short/point intervals reachable without stretching time.
      for (const segment of validRows) {
        const memberIndex = task.rows.indexOf(segment.row), memberId = `logical-task-${index}-member-${memberIndex}`;
        const label = segmentLabel(segment.row, memberIndex);
        const control = button('', () => {
          const enterMember = () => {
            const member = document.getElementById(memberId);
            if (!member) return;
            taskList.querySelectorAll('.selected-member').forEach(e => e.classList.remove('selected-member'));
            member.classList.add('selected-member'); member.scrollIntoView({ block: 'start' }); member.focus();
          };
          if (!detail.dataset.loaded) detail.addEventListener('toggle', enterMember, { once: true });
          detail.open = true; enterMember();
        });
        control.className = 'measurement-link'; control.setAttribute('aria-controls', memberId);
        control.addEventListener('focus', () => control.classList.add('focused-segment'));
        control.addEventListener('blur', () => control.classList.remove('focused-segment'));
        control.append(node('span', label, 'segment-label'));
        const track = node('span', undefined, 'measurement-track');
        const bar = node('span', segment.point ? '◆' : '', 'measurement-segment' + (segment.point ? ' point' : ''));
        bar.setAttribute('aria-hidden', 'true');
        const span = timeline.end - timeline.start;
        bar.style.left = `${span ? (segment.start - timeline.start) / span * 100 : 0}%`;
        bar.style.width = `${span ? (segment.end - segment.start) / span * 100 : 0}%`;
        const interval = segment.row.actor.usage.interval;
        const precision = segment.point ? '같은 밀리초로 표시 · 밀리초 미만 차이는 원문 확인' : `${(segment.end - segment.start) / 1000}초 (밀리초 정밀도)`;
        control.setAttribute('aria-label', `${label} · ${roleCatalog[task.role]?.label || '역할 미분류'} · 원본 ${interval.started_at} → ${interval.finished_at} · ${precision} · 이 멤버 상세 열기`);
        control.append(node('span', precision, 'segment-precision')); track.append(bar); control.append(track); lane.append(control);
      }
      plot.append(lane);
    });
    chart.append(plot); section.append(chart,
      node('p', '막대는 기록된 측정 구간이며 공백을 메우지 않습니다. 겹침은 기록의 겹침일 뿐 병렬 스케줄링이나 요청 전체 처리시간을 확정하지 않습니다. 구간 라벨이나 막대를 클릭하거나 Tab·Enter로 해당 멤버 상세를 엽니다. 차트 위치·길이는 밀리초 정밀도입니다.', 'notice'),
      node('p', `시각 미확인 영역: ${timeline.rows.filter(row => !row.valid).length}개 · 아래 작업 상세에 보존합니다. 같은 밀리초로 표시되는 구간은 ◆이며 실제 동일 시각으로 단정하지 않습니다. 길이를 늘리지 않으며 세부 시각은 원문에서 확인합니다.`, 'notice'), taskList);
    if (!tasks.length) section.append(node('p', '수입된 작업이나 담당자가 없습니다.', 'empty'));
  }
  function renderUsage(bundle, measured) {
    const section = $('usage'); section.className = 'section-panel';
    const heading = node('div', undefined, 'section-heading'), h = node('div');
    h.append(node('h3', '토큰 사용량'), node('p', '요청 전체 → 역할별 → 작업별 → 세부 항목')); heading.append(h); section.append(heading);
    const total = node('div', undefined, 'request-token-total');
    total.append(node('span', '이 요청의 측정된 총 토큰'), node('strong', tokenText(measured.total)),
      node('small', `입력 ${tokenText(measured.input)} · 출력 ${tokenText(measured.output)} · 측정 ${measured.covered}/${measured.rows.length}개 작업`));
    section.append(total); renderTasks(bundle, measured, section); section.append(node('h4', '역할별 사용량'));
    const roles = node('div', undefined, 'role-usage-grid');
    for (const group of roleUsage(measured)) {
      const card = lazyDisclosure('', body => {
        body.append(node('p', '측정 구간이 연결된 배정은 각각 표시합니다. 미연결 담당자의 추가 작업은 누락되어 있을 수 있습니다.', 'notice'));
        for (const r of group.rows) {
          const task = node('article', undefined, 'task-usage'); task.dataset.actorId = r.actor.id;
          const profile = profileFor(bundle, r.actor);
          task.append(node('h4', r.actor.display_task), node('p', '실제 관측: ' + profileLine(profile.actual), 'profile-line'));
          task.append(profileDetails(profile, r.actor));
          const grid = node('div', undefined, 'mini-grid');
          for (const [label, key] of [['입력', 'input'], ['출력', 'output'], ['합계', 'total']]) {
            const cell = node('div', label, 'mini-stat'); cell.append(node('strong', tokenText(r.kind === 'attributed' ? r[key] : null))); grid.append(cell);
          }
          task.append(grid, node('p', r.label, 'muted'), tokenDetails(r));
          if (r.actor.observation_id) task.append(lazyDisclosure('이 작업의 측정 원문·출처', target => {
            target.append(node('p', `작업 ${r.actor.task_id} · dispatch ${r.actor.dispatch_id}`, 'muted'));
            target.append(node('pre', show({ event: r.actor.usage_event,
              evidence: bundle.collections.filter(c => c.source === 'trace.native-usage-evidence' && c.measurement_key === r.actor.usage.measurement_key) })));
          }, 'plain-disclosure'));
          if (r.kind !== 'attributed') task.append(node('p', '배정별 수치 미연결 · 세션 참고값이 있으면 세부 항목에서 확인합니다.', 'notice'));
          body.append(task);
        }
      }, 'role-usage');
      paint(card, group.key); card.dataset.roleId = group.key;
      card.firstChild.append(node('span', group.label, 'card-role'), node('strong', tokenText(group.total), 'role-token-total'),
        node('small', `입력 ${tokenText(group.input)} · 출력 ${tokenText(group.output)}`),
        node('small', `${group.rows.length}개 작업 · 측정 ${group.covered}/${group.rows.length} · 작업별 펼치기`));
      roles.append(card);
    }
    section.append(roles);
    const note = measured.total == null ? '배정별 수치가 아직 연결되지 않았습니다. 미측정은 0이 아니며 세션 누적값은 요청 합계에 더하지 않습니다.' : `측정된 합계 ${tokenText(measured.total)} tokens · 표시된 작업 ${measured.covered}/${measured.rows.length}개 측정. 조율·검증·추가 작업의 전체 수집을 뜻하지 않습니다.`;
    section.append(node('p', note, 'usage-note'));
  }
  function renderMetadata(bundle) {
    $('metadata').append(lazyDisclosure('기록 출처·수집 범위 확인', body => {
      body.append(node('p', collectionWarning(bundle.requests[0], bundle.collections), 'warning'));
      body.append(node('p', '관측 모델과 요청 모델은 구분합니다. 알려지지 않은 값이나 판단은 만들어 채우지 않습니다.', 'notice'));
      body.append(node('pre', show({ requests: bundle.requests, collections: bundle.collections })));
    }));
  }
  function setOptions(id, values, title, names = {}) {
    const select = $(id); select.replaceChildren(node('option', title)); select.firstChild.value = '';
    for (const value of new Set(values)) { const e = node('option', names[value] || value); e.value = value; select.append(e); }
  }
  async function selectRequest(id, focus = true) {
    const version = ++selectionVersion; selectedId = id; clearRequest(); syncNavigation();
    $('status').className = ''; $('status').setAttribute('role', 'status'); $('status').textContent = '기록 불러오는 중…'; $('main').setAttribute('aria-busy', 'true');
    try {
      const received = await get('/api/request?id=' + encodeURIComponent(id));
      const bundle = effectiveBundle(received);
      if (version !== selectionVersion) return;
      data = bundle;
      const displayed = displayActors(bundle), actors = new Map(displayed.map(a => [a.id, a])), units = usageUnits(bundle, displayed), measured = usageSummary(units), title = renderSummary(bundle, actors);
      const duration = durationText(bundle.requests[0]);
      $('metrics').append(metric('참여한 담당 실행', bundle.actors.length + '개', new Set(displayed.map(a => a.display_role)).size + '개 역할 · 수입된 배정 기준'),
        metric('측정된 요청 토큰', tokenText(measured.total), `배정별 측정 ${measured.covered}/${measured.rows.length} · 입력 ${tokenText(measured.input)} / 출력 ${tokenText(measured.output)}`),
        metric('전체 실행 시간', duration || '미확인', duration ? '명시된 시작 → 종료 기준' : '시작·종료 구간 미수집'),
        metric('수집된 실행 기록', bundle.events.length + '개', '명령·지시·검증을 펼쳐 확인'));
      renderFlow(bundle, actors, measured); renderUsage(bundle, measured); renderMetadata(bundle);
      setOptions('role', [...displayed.map(a => a.display_role), ...bundle.events.map(e => roleFor(e, actors))], '모든 역할');
      setOptions('kind', bundle.events.map(e => e.kind), '모든 이벤트', labels);
      $('all-records').hidden = false; $('records-label').textContent = `실제 명령과 전체 기록 (${bundle.events.length}) 펼치기`;
      $('status').textContent = '기록 조회 완료'; $('status').className = 'quiet';
      if (focus) title.focus({ preventScroll: true });
    } catch (error) {
      if (version !== selectionVersion) return;
      $('status').className = ''; $('status').setAttribute('role', 'alert');
      $('status').replaceChildren(node('p', error.status === 404 ? '요청을 찾을 수 없습니다 (HTTP 404).' : '기록을 불러오지 못했습니다. 연결을 확인한 뒤 다시 시도해 주세요.', 'error'), button('다시 조회', () => selectRequest(id)));
    } finally { if (version === selectionVersion) $('main').setAttribute('aria-busy', 'false'); }
  }
  function renderTimeline() {
    if (!data || !$('all-records').open) return;
    const events = filterEvents(data, $('role').value, $('kind').value), actors = new Map(displayActors(data).map(a => [a.id, a]));
    $('event-count').textContent = `${events.length} / ${data.events.length}개`; $('timeline').replaceChildren();
    if (!events.length) $('timeline').append(node('li', '조건에 맞는 기록이 없습니다.', 'empty'));
    for (const e of events) $('timeline').append(renderEvent(e, actors, false));
  }
  $('all-records').addEventListener('toggle', renderTimeline);
  $('role').onchange = $('kind').onchange = renderTimeline;
  $('reset').onclick = () => { $('role').value = ''; $('kind').value = ''; renderTimeline(); };
  $('refresh').onclick = () => list(true);
  mobile.addEventListener('change', syncNavigation);
  list();
}
