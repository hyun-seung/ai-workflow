'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { extractBody, displayTime, eventWarnings, filterEvents, collectionWarning, statusText, closeoutLines } = require('../scripts/trace_web/app.js');

test('native bodies preserve literal whitespace and inert HTML', () => {
  const text = '지시\n  <img src=x onerror=alert(1)>\n`command`';
  assert.deepEqual(extractBody({ block: { text }, content: 'other' }, 'instruction'), { text, code: false });
  assert.deepEqual(extractBody({ block: { output: 'a\n b' } }, 'tool_result'), { text: 'a\n b', code: true });
  assert.equal(extractBody({ block: { output: { value: 2 } } }, 'tool_result').text, '{\n  "value": 2\n}');
});
test('tool inputs and manual precedence retain original content', () => {
  assert.equal(extractBody({ input: { cmd: 'echo a\n  echo b', command: 'ignored' } }, 'tool_call').text, 'echo a\n  echo b');
  assert.equal(extractBody({ input: 'literal' }, 'tool_call').text, 'literal');
  assert.equal(extractBody({ content: '', summary: 'summary' }, 'decision').text, '');
  assert.equal(extractBody({ reason: 'why', assigned: 'actor' }, 'assignment').text, 'why');
  assert.equal(extractBody([1, 'a'], 'explanation').text, '[\n  1,\n  "a"\n]');
  assert.equal(extractBody({ evidence: 'source' }, 'verification').structured, true);
});
test('UTC time is explicit and unknown or invalid times are not invented', () => {
  assert.equal(displayTime(null), '시각 미확인');
  assert.equal(displayTime('bad-time'), 'bad-time');
  assert.equal(displayTime('2025-01-02T10:20:30.123+09:00'), '2025-01-02 · UTC · 01:20:30.123');
});
test('warnings preserve partial, retrospective, unresolved decisions and truncation', () => {
  assert.deepEqual(eventWarnings({ completeness: 'partial', detail: { recorded_retrospectively: true, state: 'resolved', block: { text: 'x (truncated)', isError: true } } }), ['사건 범위 partial', '사후 기록', '처리됨(resolved) · 승인/거절 결정 미확인', '도구 오류', '원본 잘림 표식 있음']);
  assert.equal(eventWarnings({ completeness: 'complete', detail: { isError: 'true', truncated: false } }).length, 0);
  assert.match(statusText('completed'), /완료/);
  assert.match(statusText(null), /미확인/);
  assert.match(collectionWarning({ completeness: 'unknown' }, [{ completeness: 'partial' }, { completeness: 'unknown' }]), /1개 부분.*1개 범위 미확인/);
});
test('AND filters preserve API order, unknown actors and reset', () => {
  const bundle = { actors: [{ id: 'a', role: 'backend' }], events: [{ id: 'z', actor_id: 'a', kind: 'instruction' }, { id: 'b', kind: 'instruction' }, { id: 'a', actor_id: 'a', kind: 'tool_call' }] };
  assert.deepEqual(filterEvents(bundle, '백엔드 개발자', 'instruction').map(e => e.id), ['z']);
  assert.deepEqual(filterEvents(bundle, '미확인', '').map(e => e.id), ['b']);
  assert.equal(filterEvents(bundle, '미확인', 'tool_call').length, 0);
  assert.deepEqual(filterEvents(bundle, '', '').map(e => e.id), ['z', 'b', 'a']);
});
test('closeout separates verdict, partial recording, process exit and stale evidence', () => {
  assert.match(closeoutLines(null).join(' '), /미등록.*작업 완료를 뜻하지/);
  const view = { current: true, time: '2026-01-02T00:00:00Z', detail: { status: 'completed', verification: 'pass', summary: '<script>literal</script>', recording_status: 'partial', last_collection_time: null } };
  const lines = closeoutLines(view).join(' ');
  assert.match(lines, /검증: 통과/);
  assert.match(lines, /부분 수집.*전체 수집을 뜻하지/);
  assert.match(lines, /프로세스 종료.*별도로 검증하지/);
  assert.match(lines, /시각 미확인/);
  assert.ok(closeoutLines(view).includes('<script>literal</script>'));
  assert.match(closeoutLines({ ...view, current: false })[0], /판정 재확인 필요/);
});

const { usageFor, usageSummary, tokenText, requestExcerpt, flowGroups, durationText } = require('../scripts/trace_web/app.js');
function measuredActor(id, patch = {}) {
  return { id, request_id: 'request', usage: { status: 'measured', source: 'fixture', scope: 'dispatch', task_attributed: true,
    actor_id: id, request_id: 'request', measurement_key: id,
    measurements: { input_tokens: 100, output_tokens: 30, cached_input_tokens: 80, reasoning_output_tokens: 20, total_tokens: 130 }, ...patch } };
}
test('usage totals require explicit matching dispatch attribution; subsets are not added twice', () => {
  const actors = [measuredActor('coordinator'), measuredActor('worker'), { id: 'unmeasured' }];
  const summary = usageSummary(actors);
  assert.equal(summary.covered, 2); assert.equal(summary.total, 260);
  assert.equal(summary.input, 200); assert.equal(summary.output, 60);
  assert.equal(summary.rows[2].total, null);
  assert.equal(usageFor(measuredActor('other', { actor_id: 'wrong' })).kind, 'reference');
  assert.equal(usageFor(measuredActor('other', { request_id: 'wrong' })).kind, 'reference');
  assert.equal(usageFor(measuredActor('other', { task_attributed: false })).kind, 'reference');
});
test('cumulative and duplicate measurements stay visible as references without task aggregation', () => {
  const cumulative = measuredActor('session', { scope: 'session_cumulative' });
  assert.equal(usageFor(cumulative).total, 130);
  assert.equal(usageSummary([cumulative]).total, null);
  const dup = usageSummary([measuredActor('one', { measurement_key: 'same' }), measuredActor('two', { measurement_key: 'same' })]);
  assert.equal(dup.covered, 0); assert.equal(dup.total, null);
  assert.ok(dup.rows.every(r => /중복/.test(r.label)));
});
test('missing, conflicting and invalid measurements cannot become valid zero or total', () => {
  for (const measurements of [{ input_tokens: 1 }, { input_tokens: -1, output_tokens: 2 },
    { input_tokens: 100, output_tokens: 30, total_tokens: 99 }, { input_tokens: 100, output_tokens: 30, cached_input_tokens: 101 },
    { input_tokens: Number.MAX_SAFE_INTEGER, output_tokens: 1 }]) {
    assert.equal(usageFor(measuredActor('bad', { measurements })).kind, 'unavailable');
  }
  assert.equal(usageSummary([{id:'missing'}]).total, null);
  assert.equal(usageSummary([measuredActor('zero', { measurements: { input_tokens: 0, output_tokens: 0 } })]).total, 0);
  assert.equal(tokenText(null), '—'); assert.equal(tokenText(0), '0');
});
test('request and delegation views only use recorded content and parent relationships', () => {
  const bundle = { requests: [{ title: 'A request' }], actors: [{id:'p'}, {id:'a',parent_id:'p'}, {id:'b',parent_id:'a'}],
    events: [{kind:'user_request',detail:{content:'Original <script>literal</script>'}}, {kind:'decision',detail:{summary:'Recorded plan'}}, {kind:'assignment'}] };
  assert.match(requestExcerpt(bundle).text, /Original <script>/);
  assert.deepEqual(flowGroups(bundle).workers.map(a => a.parent_id), ['p','a']);
  assert.equal(flowGroups(bundle).decisions.length, 1);
  assert.match(requestExcerpt({...bundle,events:[]}).text, /수입되지/);
  assert.equal(durationText({ time:'2026-01-01T00:00:00Z' }), null);
  assert.equal(durationText({execution:{started_at:'2026-01-01T00:00:00Z',finished_at:'2026-01-01T00:01:18Z'}}), '1분 18초');
});
test('clipped native preambles are not presented as task descriptions; raw remains unchanged', () => {
  const {instructionExcerpt} = require('../scripts/trace_web/app.js');
  const event = {kind:'instruction',detail:{block:{text:'You are working inside Orca, a multi-agent IDE.\nsecret-session-metadata\n… (truncated)'}}};
  const before = JSON.stringify(event);
  assert.match(instructionExcerpt(event), /잘렸습니다/);
  assert.doesNotMatch(instructionExcerpt(event), /secret-session/);
  assert.equal(JSON.stringify(event), before);
  assert.equal(instructionExcerpt({kind:'instruction',detail:{content:'Implement the UI'}}), 'Implement the UI');
});
test('canonical roles and fixed colours match workflow and keep task labels separate', () => {
  const { roleCatalog, displayActors } = require('../scripts/trace_web/app.js');
  const workflow = require('../workflow.json');
  assert.deepEqual(Object.keys(roleCatalog).sort(), Object.keys(workflow.roles).sort());
  const bundle = { actors: [{ id:'a',role:'구현·테스트·수정' },{ id:'b',role:'프론트엔드 개발자' }], events: [
    {id:'labels',kind:'assignment',detail:{actor_roles:[{actor_id:'a',role_id:'backend',task_title:'TCP 연결 실험'}]}}] };
  const before = JSON.stringify(bundle), actors = displayActors(bundle);
  assert.equal(actors[0].display_role,'백엔드 개발자');
  assert.equal(actors[0].display_task,'TCP 연결 실험');
  assert.equal(actors[0].role,'구현·테스트·수정');
  assert.equal(actors[1].display_role_id,'frontend');
  assert.deepEqual(displayActors({...bundle,actors:bundle.actors.toReversed()}).map(a=>a.display_role_id),['frontend','backend']);
  assert.equal(roleCatalog.backend.color,'#2e9a77');
  assert.equal(JSON.stringify(bundle),before);
  assert.equal(displayActors({actors:[{id:'x',role:'developer'}],events:[]})[0].display_role,'역할 미분류');
  const conflict = {...bundle,events:[...bundle.events,{id:'conflict',kind:'assignment',detail:{actor_roles:[{actor_id:'a',role_id:'frontend',task_title:'Other'}]}}]};
  assert.equal(displayActors(conflict)[0].display_role_conflict,true);
  assert.equal(displayActors(conflict)[0].display_role_id,null);
});
test('role totals reuse globally deduplicated tasks and preserve missing detail counts', () => {
  const { roleUsage, displayActors } = require('../scripts/trace_web/app.js');
  const bundle={actors:[{...measuredActor('one'),role:'backend'},{...measuredActor('two'),role:'backend'},
    {...measuredActor('three',{measurements:{input_tokens:10,output_tokens:5}}),role:'frontend'},{id:'missing',role:'frontend'}],events:[]};
  const groups=roleUsage(usageSummary(displayActors(bundle)));
  assert.equal(groups[0].total,260);assert.equal(groups[0].cached,160);assert.equal(groups[0].reasoning,40);
  assert.equal(groups[0].uncached,40);assert.equal(groups[1].total,15);assert.equal(groups[1].covered,1);
  assert.equal(groups[1].cached,null);assert.equal(groups[1].reasoning,null);
  const duplicate=roleUsage(usageSummary(displayActors({actors:[{...measuredActor('a',{measurement_key:'same'}),role:'backend'},
    {...measuredActor('b',{measurement_key:'same'}),role:'frontend'}],events:[]})));
  assert.ok(duplicate.every(r=>r.total===null));
});
