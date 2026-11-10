'use strict';

const labels = {
  user_request: '사용자 요청', decision: '결정·이유 요약', assignment: '업무 배정',
  instruction: '전달 지시', tool_call: '명령·도구 입력', tool_result: '도구 결과',
  question: '질문', approval_request: '승인 요청 · 결정 미확인',
  approval_resolution: '승인 처리', handoff: '결과 인계', verification: '검증 판정', explanation: '명시된 설명'
};
const show = value => JSON.stringify(value, null, 2) ?? String(value);
const known = value => value === null || value === undefined || value === '' ? '미확인' : value;

function statusText(value) {
  const names = { completed: '완료', finished: '종료', succeeded: '성공', failed: '실패', running: '진행 중', pending: '대기', cancelled: '취소' };
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
  for (const key of ['content', 'summary', 'description', 'reason']) {
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
function roleFor(event, actors) { return actors.get(event.actor_id)?.role || '미확인'; }
function filterEvents(bundle, role, kind) {
  const actors = new Map(bundle.actors.map(actor => [actor.id, actor]));
  return bundle.events.filter(event => (!role || roleFor(event, actors) === role) && (!kind || event.kind === kind));
}
function collectionWarning(request, collections) {
  const counts = new Map();
  for (const collection of collections) counts.set(collection.completeness || 'unknown', (counts.get(collection.completeness || 'unknown') || 0) + 1);
  const parts = [...counts].map(([value, count]) => `${count}개 ${value === 'partial' ? '부분 (partial)' : value === 'unknown' ? '범위 미확인 (unknown)' : value}`);
  return `요청 범위 ${known(request.completeness)} · ${parts.length ? '수집본 ' + parts.join(' / ') : '수집 근거 없음 · 범위 미확인'} · 입력/출력 잘림 여부 미확인`;
}

// The same helpers are exercised by Node's built-in test runner without a DOM.
if (typeof module !== 'undefined') module.exports = { extractBody, displayTime, eventWarnings, filterEvents, statusText, collectionWarning };
if (typeof document !== 'undefined') startViewer();

function startViewer() {
  const $ = id => document.getElementById(id);
  const mobile = matchMedia('(max-width: 768px)');
  let data = null;
  let selectedId = null;
  let selectionVersion = 0;
  let listVersion = 0;
  let listedIds = [];
  let listCount = 0;

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function button(text, action) {
    const element = node('button', text);
    element.type = 'button';
    element.onclick = action;
    return element;
  }
  function disclosure(title, ...children) {
    const element = node('details');
    const content = node('div', undefined, 'details-content');
    content.append(...children);
    element.append(node('summary', title), content);
    return element;
  }
  async function get(url) {
    const response = await fetch(url, { credentials: 'omit' });
    if (!response.ok) {
      const error = new Error('HTTP ' + response.status);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }
  function syncNavigation() {
    $('request-browser').open = !mobile.matches || !selectedId;
    $('browser-label').textContent = `요청 목록 · ${listCount}개${selectedId ? ' · 선택됨' : ''}`;
    for (const element of $('requests').children) {
      const selected = element.dataset.id === selectedId;
      element.setAttribute('aria-current', String(selected));
      element.textContent = element.dataset.title + (selected ? ' · 선택됨' : '');
    }
    $('selection-note').textContent = selectedId && !listedIds.includes(selectedId) ? '선택 요청이 현재 검색 결과에 없음' : '';
  }
  async function list() {
    const version = ++listVersion;
    const query = $('query').value;
    $('list-state').textContent = '요청 목록 불러오는 중…';
    $('list-state').setAttribute('role', 'status');
    $('requests').replaceChildren();
    $('list-actions').replaceChildren();
    try {
      const rows = await get('/api/requests?q=' + encodeURIComponent(query));
      if (version !== listVersion) return;
      listCount = rows.length;
      listedIds = rows.map(row => row.id);
      $('list-state').textContent = rows.length ? `${rows.length}개 표시 · 최대 100개` : query ? '검색 결과가 없습니다.' : '저장된 요청이 없습니다 · 기록을 수입한 뒤 다시 조회하세요.';
      if (!rows.length) $('list-actions').append(button(query ? '검색 초기화' : '다시 조회', () => { $('query').value = ''; list(); }));
      for (const request of rows) {
        const element = button(request.title || request.id, () => selectRequest(request.id));
        element.dataset.id = request.id;
        element.dataset.title = request.title || request.id;
        $('requests').append(element);
      }
      syncNavigation();
    } catch (error) {
      if (version !== listVersion) return;
      $('list-state').setAttribute('role', 'alert');
      $('list-state').textContent = `요청 목록을 읽을 수 없습니다 (${error.status ? 'HTTP ' + error.status : '연결 실패'}).`;
      $('list-actions').append(button('다시 조회', list));
    }
  }
  function renderMetadata(bundle) {
    const actors = node('div');
    for (const actor of bundle.actors) {
      actors.append(
        node('h3', `${actor.role || '역할 미확인'} · ${statusText(actor.status)}`),
        node('p', `환경: ${known(actor.environment)} / provider: ${known(actor.provider)}`),
        node('p', `ID: ${actor.id} · 부모: ${actor.parent_id || '없음·미확인'}`),
        node('p', `요청 모델/effort: ${known(actor.requested?.model)} / ${known(actor.requested?.effort)}`),
        node('p', `관측 모델/effort: ${known(actor.observed?.model)} / ${known(actor.observed?.effort)}`),
        node('p', actor.usage?.status === 'measured' ? '사용량: 측정값 · 합산하지 않음' : '사용량: 미측정 / unavailable'),
        node('pre', show(actor))
      );
    }
    const collections = node('div');
    for (const collection of bundle.collections) collections.append(disclosure(`수집 범위: ${known(collection.completeness)} · 입력/출력 잘림 여부 미확인`, node('pre', show(collection))));
    collections.append(disclosure('요청 원문·출처', node('pre', show(bundle.requests[0]))));
    $('metadata').append(disclosure(`담당자·모델·사용량 (${bundle.actors.length})`, actors), disclosure(`수집 근거 (${bundle.collections.length})`, collections));
  }
  function setOptions(id, values, title, names = {}) {
    const select = $(id);
    select.replaceChildren(node('option', title));
    select.firstChild.value = '';
    for (const value of new Set(values)) {
      const option = node('option', names[value] || value);
      option.value = value;
      select.append(option);
    }
  }
  async function selectRequest(id) {
    const version = ++selectionVersion;
    selectedId = id;
    data = null;
    syncNavigation();
    $('status').setAttribute('role', 'status');
    $('status').className = '';
    $('status').textContent = '기록 불러오는 중…';
    $('main').setAttribute('aria-busy', 'true');
    for (const name of ['summary', 'metadata', 'timeline']) $(name).replaceChildren();
    $('filters').hidden = true;
    try {
      const bundle = await get('/api/request?id=' + encodeURIComponent(id));
      if (version !== selectionVersion) return;
      data = bundle;
      const request = bundle.requests[0];
      const title = node('h2', request.title || request.id);
      title.tabIndex = -1;
      if (request.sample) $('summary').append(node('strong', '가상 예시 — 실제 관측 자료 아님', 'warning'));
      $('summary').append(title, node('p', '상태: ' + statusText(request.status)), node('p', `수입 기록 · 담당 실행 ${bundle.actors.length} · 역할 종류 ${new Set(bundle.actors.map(actor => actor.role || '미확인')).size} · 수집 이벤트 ${bundle.events.length}`, 'counts'), node('p', collectionWarning(request, bundle.collections), 'warning'));
      renderMetadata(bundle);
      const actors = new Map(bundle.actors.map(actor => [actor.id, actor]));
      setOptions('role', [...bundle.actors.map(actor => actor.role || '미확인'), ...bundle.events.map(event => roleFor(event, actors))], '모든 역할');
      setOptions('kind', bundle.events.map(event => event.kind), '모든 이벤트', labels);
      $('context').textContent = request.title || request.id;
      $('filters').hidden = false;
      $('status').textContent = '기록 조회 완료';
      $('status').className = 'quiet';
      render();
      title.focus({ preventScroll: true });
    } catch (error) {
      if (version !== selectionVersion) return;
      $('status').setAttribute('role', 'alert');
      $('status').replaceChildren(node('p', error.status === 404 ? '요청을 찾을 수 없습니다 (HTTP 404).' : `기록을 읽을 수 없습니다 (${error.status ? 'HTTP ' + error.status : '연결 실패'}).`, 'error'), button('동일 요청 다시 조회', () => selectRequest(id)), button('요청 목록으로 이동', () => { $('request-browser').open = true; $('query').focus(); list(); }));
    } finally {
      if (version === selectionVersion) $('main').setAttribute('aria-busy', 'false');
    }
  }
  function resetFilters() { $('role').value = ''; $('kind').value = ''; render(); }
  function renderEvent(event, actors) {
    const item = node('li', undefined, 'event');
    item.dataset.eventId = event.id;
    const header = node('div', undefined, 'event-header');
    header.append(node('span', displayTime(event.time), 'event-time'), node('span', roleFor(event, actors)), node('span', labels[event.kind] || event.kind, 'event-kind'));
    const badges = node('div', undefined, 'badges');
    for (const warning of eventWarnings(event)) badges.append(node('span', warning, 'warning'));
    const body = extractBody(event.detail, event.kind);
    const preview = node('p', body.structured ? '구조화된 기록 · 세부정보 펼치기' : body.text, 'preview' + (body.code ? ' code' : ''));
    const content = node(body.code ? 'pre' : 'div', body.text, body.code ? 'code' : 'body-text');
    const details = disclosure(body.structured ? '세부정보 펼치기' : '미리보기 · 전체 내용 펼치기', content);
    const detail = event.detail;
    if (detail && typeof detail === 'object') {
      for (const key of ['name', 'state', 'exitCode', 'durationMs', 'recorded_at', 'evidence', 'source_extent']) {
        if (Object.hasOwn(detail, key)) details.lastChild.append(node('p', `${key}: ${typeof detail[key] === 'object' ? show(detail[key]) : detail[key]}`));
      }
    }
    // Manual records keep the file hash in detail; retain the existing top-level fallback.
    const originalFileSha256 = detail?.original_file_sha256 ?? event.original_file_sha256;
    details.lastChild.append(disclosure('원문 JSON·출처', node('p', 'detail 정규 JSON SHA256: ' + known(event.detail_sha256)), ...(originalFileSha256 ? [node('p', '원본 파일 SHA256: ' + originalFileSha256)] : []), node('pre', show(event))));
    item.append(header, badges, preview, details);
    return item;
  }
  function render() {
    if (!data) return;
    const events = filterEvents(data, $('role').value, $('kind').value);
    const actors = new Map(data.actors.map(actor => [actor.id, actor]));
    $('event-count').textContent = `표시 ${events.length} / 수집 ${data.events.length}`;
    $('timeline').replaceChildren();
    if (!events.length) {
      const empty = node('li', data.events.length ? '조건에 맞는 이벤트 없음' : '수집된 이벤트 없음 · 미수집 여부는 수집 근거 확인');
      if (data.events.length) empty.append(button('필터 초기화', resetFilters));
      else empty.append(node('p', '위의 수집 근거를 펼쳐 확인하세요.'));
      $('timeline').append(empty);
    }
    for (const event of events) $('timeline').append(renderEvent(event, actors));
    updateSticky();
  }
  function updateSticky() {
    const height = $('filters').getBoundingClientRect().height;
    $('filters').classList.toggle('not-sticky', height > innerHeight * 0.35);
    document.documentElement.style.setProperty('--sticky-height', height + 'px');
  }
  new ResizeObserver(updateSticky).observe($('filters'));
  window.addEventListener('resize', updateSticky);
  mobile.addEventListener('change', syncNavigation);
  $('search').onsubmit = event => { event.preventDefault(); list(); };
  $('role').onchange = $('kind').onchange = render;
  $('reset').onclick = resetFilters;
  list();
}
