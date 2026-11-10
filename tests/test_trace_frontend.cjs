'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { extractBody, displayTime, eventWarnings, filterEvents, collectionWarning, statusText } = require('../scripts/trace_web/app.js');

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
  const bundle = { actors: [{ id: 'a', role: 'author' }], events: [{ id: 'z', actor_id: 'a', kind: 'instruction' }, { id: 'b', kind: 'instruction' }, { id: 'a', actor_id: 'a', kind: 'tool_call' }] };
  assert.deepEqual(filterEvents(bundle, 'author', 'instruction').map(e => e.id), ['z']);
  assert.deepEqual(filterEvents(bundle, '미확인', '').map(e => e.id), ['b']);
  assert.equal(filterEvents(bundle, '미확인', 'tool_call').length, 0);
  assert.deepEqual(filterEvents(bundle, '', '').map(e => e.id), ['z', 'b', 'a']);
});
