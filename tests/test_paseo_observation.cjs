const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const modulePath = path.resolve(__dirname, '../scripts/paseo_observation.mjs');
const api = import(modulePath);
const stamp = '2026-01-01T00:00:00Z';
function fixture() {
  const binding = { serverId: 'server-fixture', agentId: 'agent-fixture', workspaceId: 'workspace-fixture', cwd: '/fixture/project', provider: 'codex', providerSessionId: 'session-fixture', turnId: 'turn-fixture', epoch: 'epoch-fixture', cursor: { epoch: 'epoch-fixture', seq: 10 } };
  const snapshot = { id: binding.agentId, provider: binding.provider, cwd: binding.cwd, workspaceId: binding.workspaceId, model: null, createdAt: stamp, updatedAt: stamp, lastUserMessageAt: null, status: 'idle', backgroundWork: { count: 0 }, activeTurn: null, capabilities: { supportsStreaming: true, supportsSessionPersistence: true, supportsDynamicModes: false, supportsMcpServers: false, supportsReasoningStream: true, supportsToolInvocations: true }, currentModeId: null, availableModes: [], pendingPermissions: [], persistence: { provider: binding.provider, sessionId: binding.providerSessionId }, runtimeInfo: { provider: binding.provider, sessionId: binding.providerSessionId }, title: null, labels: {} };
  const row = (seq, item) => ({ provider: binding.provider, turnId: binding.turnId, timestamp: stamp, seqStart: seq, seqEnd: seq, sourceSeqRanges: [{ startSeq: seq, endSeq: seq }], collapsed: [], item });
  return { binding, source: { serverId: binding.serverId, agentId: binding.agentId }, before: structuredClone(snapshot), after: structuredClone(snapshot), page: { requestId: 'request-fixture', agentId: binding.agentId, agent: structuredClone(snapshot), direction: 'after', projection: 'canonical', epoch: binding.epoch, reset: false, staleCursor: false, gap: false, window: { minSeq: 1, maxSeq: 12, nextSeq: 13 }, startCursor: { epoch: binding.epoch, seq: 11 }, endCursor: { epoch: binding.epoch, seq: 12 }, hasOlder: true, hasNewer: false, entries: [row(11, { type: 'user_message', text: 'SECRET-PROMPT' }), row(12, { type: 'assistant_message', text: 'SECRET-OUTPUT' })], error: null } };
}
async function normalized(mutator) { const x = fixture(); mutator?.(x); return (await api).normalizeObservation(x); }
test('official canonical rows cannot establish stream lifecycle, acceptance, exit or usage', async () => {
  const r = await normalized(); assert.equal(r.status, 'pending'); assert.equal(r.reason, 'lifecycle_unavailable'); assert.equal(r.observed_turn_completed, false); assert.equal(r.task_acceptance, null); assert.equal(r.process_exit, null); assert.equal(r.usage, null); assert.equal(r.usage_complete, false);
});
for (const key of ['serverId', 'agentId', 'workspaceId', 'cwd', 'provider', 'providerSessionId']) {
  test(`exact binding rejects changed ${key}`, async () => { assert.equal((await normalized(x => x.binding[key] = 'different')).status, 'invalid'); });
}
const bad = {
  'runtime persistence conflict': x => x.before.persistence.sessionId = 'other',
  'runtime provider conflict': x => x.after.runtimeInfo.provider = 'claude',
  'missing both canonical sessions': x => { x.after.runtimeInfo = undefined; x.after.persistence = null; delete x.after.runtimeInfo; },
  'middle snapshot identity mismatch': x => x.page.agent.workspaceId = 'other',
  'missing workspace': x => delete x.before.workspaceId,
  'forbidden provider session pair': x => x.binding.forbiddenIdentities = [{ provider: 'codex', providerSessionId: 'session-fixture' }],
  'epoch mismatch': x => x.page.epoch = 'other',
  'binding cursor epoch mismatch': x => x.binding.cursor.epoch = 'other',
  'end cursor mismatch': x => x.page.endCursor.seq = 11,
  'start cursor epoch mismatch': x => x.page.startCursor.epoch = 'other',
  'historical page before cursor': x => x.binding.cursor.seq = 12,
  'out of order': x => x.page.entries.reverse(),
  'sequence hole': x => x.page.entries[1].seqStart = 14,
  'unsafe sequence': x => x.binding.cursor.seq = Number.MAX_SAFE_INTEGER + 1,
  'boolean sequence': x => x.page.entries[0].seqStart = true,
  'projected page': x => x.page.projection = 'projected',
  'wrong direction': x => x.page.direction = 'tail',
  'error page': x => x.page.error = 'SECRET-ERROR',
  'missing error field': x => delete x.page.error,
  'collapsed canonical row': x => x.page.entries[0].collapsed = ['identity'],
  'range contradicts row': x => x.page.entries[0].sourceSeqRanges[0].endSeq = 12,
  'row provider mismatch': x => x.page.entries[0].provider = 'claude',
  'inner outer turn conflict': x => x.page.entries[0].item.turnId = 'other',
  'inner outer provider conflict': x => x.page.entries[0].item.provider = 'claude',
  'snapshot time reversed': x => x.after.updatedAt = '2025-01-01T00:00:00Z',
  'invalid background count': x => x.after.backgroundWork.count = -1,
  'unlimited query': x => x.binding.limit = 0,
  'oversized query': x => x.binding.limit = 201,
  'fractional query': x => x.binding.limit = 1.5,
  'oversized string': x => x.before.title = 'x'.repeat(262145),
  'cycle': x => x.before.labels.loop = x.before,
  'accessor': x => Object.defineProperty(x.before, 'secret', { get() { throw new Error('SECRET'); }, enumerable: true }),
  'non JSON object': x => x.before.labels.date = new Date(),
  'deep object': x => { let v = x.before.labels; for (let i = 0; i < 30; i++) v = v.next = {}; },
};
for (const [name, mutate] of Object.entries(bad)) test(`invalid: ${name}`, async () => {
  const r = await normalized(mutate); assert.equal(r.status, 'invalid'); assert.equal(r.reason, 'invalid_observation'); assert.equal(JSON.stringify(r).includes('SECRET'), false);
});
for (const [name, mutate] of Object.entries({
  'truncated page': x => x.page.hasNewer = true,
  'stale cursor': x => x.page.staleCursor = true,
  'reset': x => x.page.reset = true,
  'gap': x => x.page.gap = true,
  'pending permission': x => x.after.pendingPermissions = [{ id: 'permission-fixture', provider: 'codex', name: 'fixture', kind: 'tool' }],
  'new active turn': x => x.after.activeTurn = { turnId: 'new-turn', startedAt: stamp },
  'background work': x => x.after.backgroundWork.count = 1,
  'missing background evidence': x => delete x.after.backgroundWork,
  'running before snapshot': x => x.before.status = 'running',
  'failed snapshot': x => x.after.lastError = 'SECRET-FAILURE',
  'permission attention': x => x.page.agent.attentionReason = 'permission',
  'archived snapshot': x => x.after.archivedAt = stamp,
})) test(`pending: ${name}`, async () => { const r = await normalized(mutate); assert.equal(r.status, 'pending'); assert.notEqual(r.reason, 'lifecycle_unavailable'); assert.equal(r.observed_turn_completed, false); });
for (const event of ['turn_started', 'turn_completed', 'turn_failed', 'turn_canceled', 'permission_requested']) {
  test(`canonical schema rejects forged ${event}`, async () => { assert.equal((await normalized(x => x.page.entries[1].item = { type: event, provider: 'codex', turnId: 'turn-fixture' })).status, 'invalid'); });
}
for (const usage of [undefined, {}, { inputTokens: 0 }, { inputTokens: -1 }, { outputTokens: NaN }, { outputTokens: Infinity }, { cachedInputTokens: true }, { totalCostUsd: '1' }]) {
  test(`synthetic completion with usage ${String(usage?.inputTokens)} cannot supply evidence`, async () => {
    const r = await normalized(x => { x.page.entries[1].item = { type: 'turn_completed', provider: 'codex', turnId: 'turn-fixture', ...(usage === undefined ? {} : { usage }) }; }); assert.equal(r.status, 'invalid'); assert.equal(r.usage, null);
  });
}
test('snapshot usage, plugin contents and arbitrary normalized assertions are never used or exposed', async () => {
  const r = await normalized(x => { x.after.lastUsage = { inputTokens: 98765 }; x.before.metadata = { secret: 'SECRET-ENV' }; x.page.entries[1].item = { type: 'plugin', id: 'fixture', pluginId: 'fixture', kind: 'turn_completed', version: 1, data: { type: 'turn_completed', usage: { inputTokens: 777 } } }; x.observed_turn_completed = true; });
  assert.equal(r.usage, null); assert.equal(r.observed_turn_completed, false); assert.doesNotMatch(JSON.stringify(r), /SECRET|98765|777|metadata|lastUsage/);
});
test('empty finite canonical page is pending without historical relabeling', async () => {
  const r = await normalized(x => { x.page.entries = []; x.page.startCursor = null; x.page.endCursor = null; x.page.window.maxSeq = 10; x.page.window.nextSeq = 11; }); assert.equal(r.status, 'pending'); assert.equal(r.observed_turn_completed, false);
});
function fake(x) {
  const calls = []; let n = 0;
  const handle = { address: { ...x.source }, refresh: async () => { calls.push('refresh'); return { agent: n++ ? x.after : x.before, project: null }; }, timeline: { refetch: async options => { calls.push(options); return x.page; } } };
  return { calls, handle, client: { agents: { ref(id) { calls.push(['ref', id]); return handle; } } } };
}
test('wrapper uses public exact ref/address, refresh/page/refresh and bounded canonical options', async () => {
  const x = fixture(), f = fake(x); const r = await (await api).observeAgent(f.client, x.binding);
  assert.equal(r.reason, 'lifecycle_unavailable'); assert.deepEqual(f.calls, [['ref', x.binding.agentId], 'refresh', { direction: 'after', cursor: x.binding.cursor, projection: 'canonical', limit: 200 }, 'refresh']);
});
test('wrapper refuses a foreign server before SDK reads', async () => {
  const x = fixture(), f = fake(x); f.handle.address.serverId = 'foreign'; assert.equal((await (await api).observeAgent(f.client, x.binding)).status, 'invalid'); assert.equal(f.calls.length, 1);
});
test('wrapper refuses a closed session before timeline hydration or another refresh', async () => {
  const x = fixture(); x.before.status = 'closed'; const f = fake(x);
  f.handle.timeline.refetch = async () => { throw new Error('closed timeline must not be read'); };
  const r = await (await api).observeAgent(f.client, x.binding);
  assert.equal(r.status, 'invalid');
  assert.equal(r.observed_turn_completed, false);
  assert.deepEqual(f.calls, [['ref', x.binding.agentId], 'refresh']);
});
test('wrapper catches SDK errors without leaking text', async () => {
  const x = fixture(), f = fake(x); f.handle.timeline.refetch = async () => { throw new Error('SECRET-SDK'); }; const r = await (await api).observeAgent(f.client, x.binding); assert.equal(r.status, 'invalid'); assert.doesNotMatch(JSON.stringify(r), /SECRET/);
});
test('wrapper rejects arbitrary refresh shapes and address drift', async () => {
  const x = fixture(), f = fake(x); f.handle.refresh = async () => x.before; assert.equal((await (await api).observeAgent(f.client, x.binding)).status, 'invalid');
  const g = fake(fixture()); g.handle.timeline.refetch = async () => { g.handle.address.agentId = 'foreign'; return x.page; }; assert.equal((await (await api).observeAgent(g.client, x.binding)).status, 'invalid');
});
test('import has no transports, file writes, timers, SDK dependencies or stdout', () => {
  const script = `for (const k of ['fetch','WebSocket','setTimeout','setInterval']) globalThis[k] = () => { throw Error('side effect'); }; await import(${JSON.stringify(modulePath)});`;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script], { encoding: 'utf8', timeout: 5000 }); assert.equal(result.status, 0); assert.equal(result.stdout, ''); assert.equal(result.stderr, '');
});

// F2 independent review reproductions, expanded across required fields/variants.
for (const [name, change] of Object.entries({
  'arbitrary tool status and missing fields': x => x.page.entries[0].item = { type: 'tool_call', status: 'not-an-sdk-status' },
  'missing required snapshot fields': x => { for (const s of [x.before, x.after, x.page.agent]) for (const k of ['model', 'persistence', 'lastUserMessageAt', 'capabilities', 'currentModeId', 'availableModes', 'title']) delete s[k]; },
  'runtimeInfo forbidden null': x => x.before.runtimeInfo = null,
})) test(`F2 independent regression: ${name}`, async () => assert.equal((await normalized(change)).status, 'invalid'));
for (const key of ['model', 'persistence', 'lastUserMessageAt', 'capabilities', 'currentModeId', 'availableModes', 'title']) test(`required snapshot field: ${key}`, async () => assert.equal((await normalized(x => delete x.after[key])).status, 'invalid'));
for (const [name, change] of Object.entries({
  'unknown status': t => t.status = 'other', 'missing callId': t => delete t.callId,
  'missing name': t => delete t.name, 'missing detail': t => delete t.detail,
  'unknown detail': t => t.detail = { type: 'invented' }, 'missing error': t => delete t.error,
  'failed null error': t => t.status = 'failed', 'running nonnull error': t => t.error = 'SECRET',
  'missing detail discriminator fields': t => t.detail = { type: 'shell' },
})) test(`tool discriminated union: ${name}`, async () => assert.equal((await normalized(x => { const t = { type: 'tool_call', callId: 'tool-fixture', name: 'fixture', status: 'running', detail: { type: 'shell', command: 'SECRET' }, error: null }; change(t); x.page.entries[0].item = t; })).status, 'invalid'));
test('valid official supported tool variants remain pending and private', async () => {
  for (const status of ['running', 'completed', 'failed', 'canceled']) {
    const r = await normalized(x => x.page.entries[0].item = { type: 'tool_call', callId: 'call-fixture', name: 'fixture', status, detail: { type: 'shell', command: 'SECRET' }, error: status === 'failed' ? 'SECRET' : null }); assert.equal(r.status, 'pending'); assert.doesNotMatch(JSON.stringify(r), /SECRET/);
  }
});
function windowFake(settings = {}) {
  const x = fixture(), f = fake(x); let callback; let reads = 0; let released = 0; let ready = false;
  const timers = new Set();
  const emit = event => callback({ agentId: x.binding.agentId, subscriptionId: 'subscription-fixture', timestamp: stamp, event: { provider: 'codex', turnId: x.binding.turnId, ...event } });
  const envelope = payload => callback(payload);
  const sub = Object.assign(() => { throw Error('use release method'); }, { subscriptionId: 'subscription-fixture', ready: settings.hangReady ? new Promise(() => {}) : settings.readyReject ? Promise.reject(new Error('SECRET-READY')) : Promise.resolve(), release: async () => {
    released++; for (const t of timers) clearTimeout(t); timers.clear(); settings.onRelease?.({ emit, f, x });
    if (settings.releaseReject) throw Error('SECRET-RELEASE'); if (settings.hangRelease) await new Promise(() => {});
  } });
  // Production callback is a payload object, not (event, agent) or a wire message.
  f.handle.timeline.subscribe = cb => {
    if (settings.setupThrow) throw Error('SECRET-SETUP'); callback = cb;
    if (settings.early) emit({ type: 'turn_started' });
    sub.ready.then(() => { ready = true; if (settings.hangEvents) return;
      const timer = setTimeout(() => { timers.delete(timer); (settings.events ?? (({ emit }) => { emit({ type: 'turn_started' }); emit({ type: 'turn_completed', ...(settings.usage === undefined ? {} : { usage: settings.usage }) }); }))({ emit, envelope, f, x, sub }); }, 2); timers.add(timer);
    }, () => {});
    return sub;
  };
  f.handle.refresh = async () => { reads++; if (reads > 1) { settings.after?.({ f, x, sub, emit }); if (settings.hangAfter) await new Promise(() => {}); } return { agent: reads === 1 ? x.before : x.after, project: null }; };
  f.client.getConnectionState = () => ({ status: settings.connection?.() ?? 'connected' });
  return { ...f, x, sub, emit, get released() { return released; }, get reads() { return reads; }, get ready() { return ready; } };
}
async function windowResult(settings = {}, options = { deadlineMs: 120, maxCallbacks: 200 }) {
  const f = windowFake(settings); const start = performance.now(); const r = await (await api).observeTurnWindow(f.client, f.x.binding, options); return { r, f, elapsed: performance.now() - start };
}
test('observed pair uses actual subscription ready/release payload with numeric usage only', async () => {
  const { r, f } = await windowResult({ usage: { inputTokens: 3, cachedInputTokens: 1, outputTokens: 0, totalCostUsd: 0.125, contextWindowMaxTokens: 100, contextWindowUsedTokens: 4 } });
  assert.equal(r.status, 'observed'); assert.equal(r.observed_turn_completed, true); assert.equal(r.source_continuity_verified, false); assert.equal(r.admissible_for_controller, false); assert.equal(r.cleanup_confirmed, true); assert.equal(r.task_acceptance, null); assert.equal(r.process_exit, null); assert.equal(r.usage_complete, false); assert.equal(r.usage.inputTokens, 3); assert.equal(r.usage.totalCostUsd, 0.125); assert.equal(f.released, 1); assert.equal(f.reads, 2); assert.equal(f.ready, true); assert.equal(r.epoch, undefined); assert.equal(r.cursor, undefined);
});
test('missing event usage fields remain null; snapshot usage never substituted', async () => {
  const { r } = await windowResult({ after: ({ x }) => x.after.lastUsage = { inputTokens: 999 } }); assert.equal(r.status, 'observed'); assert.deepEqual(Object.values(r.usage), [null, null, null, null, null, null]); assert.doesNotMatch(JSON.stringify(r), /999/);
  const one = await windowResult({ usage: { inputTokens: 0 } }); assert.equal(one.r.usage.inputTokens, 0); assert.equal(one.r.usage.outputTokens, null);
});
for (const value of [-1, NaN, Infinity, true, '2', null]) test(`invalid completed event usage refuses ${String(value)}`, async () => {
  const { r, f } = await windowResult({ usage: { inputTokens: value } }); assert.equal(r.observed_turn_completed, false); assert.equal(r.usage, null); assert.equal(f.released, 1);
});
for (const type of ['turn_failed', 'turn_canceled', 'thread_started', 'permission_requested', 'permission_resolved', 'attention_required', 'subscription_restored', 'replacement', 'error', 'unknown']) test(`window refuses ${type}`, async () => {
  const { r, f } = await windowResult({ events: ({ emit }) => { emit({ type: 'turn_started' }); emit({ type, error: 'SECRET' }); emit({ type: 'turn_completed' }); } }); assert.equal(r.observed_turn_completed, false); assert.equal(f.released, 1); assert.doesNotMatch(JSON.stringify(r), /SECRET/);
});
const eventRefusals = {
  'complete before start': ({ emit }) => emit({ type: 'turn_completed' }),
  'duplicate start': ({ emit }) => { emit({ type: 'turn_started' }); emit({ type: 'turn_started' }); emit({ type: 'turn_completed' }); },
  'duplicate complete': ({ emit }) => { emit({ type: 'turn_started' }); emit({ type: 'turn_completed' }); emit({ type: 'turn_completed' }); },
  'foreign turn': ({ emit }) => { emit({ type: 'turn_started', turnId: 'foreign' }); emit({ type: 'turn_completed' }); },
  'newer turn after completion': ({ emit }) => { emit({ type: 'turn_started' }); emit({ type: 'turn_completed' }); emit({ type: 'turn_started', turnId: 'new-turn' }); },
  'missing turn': ({ envelope, x }) => envelope({ agentId: x.binding.agentId, event: { type: 'turn_started', provider: 'codex' }, timestamp: stamp }),
  'provider conflict': ({ emit }) => emit({ type: 'turn_started', provider: 'claude' }),
  'foreign callback agent': ({ envelope }) => envelope({ agentId: 'foreign', event: { type: 'turn_started', provider: 'codex', turnId: 'turn-fixture' }, timestamp: stamp }),
  'subscription mismatch': ({ envelope, x }) => envelope({ agentId: x.binding.agentId, subscriptionId: 'foreign', event: { type: 'turn_started', provider: 'codex', turnId: x.binding.turnId }, timestamp: stamp }),
  'wire message instead of callback payload': ({ envelope }) => envelope({ type: 'agent_stream', payload: {} }),
  'raw timeline error': ({ emit }) => { emit({ type: 'turn_started' }); emit({ type: 'timeline', item: { type: 'error', message: 'SECRET' } }); emit({ type: 'turn_completed' }); },
  'malformed tool event': ({ emit }) => { emit({ type: 'turn_started' }); emit({ type: 'timeline', item: { type: 'tool_call', status: 'invalid' } }); emit({ type: 'turn_completed' }); },
};
for (const [name, events] of Object.entries(eventRefusals)) test(`window boundary: ${name}`, async () => { const { r, f } = await windowResult({ events }); assert.equal(r.observed_turn_completed, false); assert.equal(f.released, 1); });
for (const [name, settings] of Object.entries({
  'early event before ready': { early: true }, 'ready rejection': { readyReject: true },
  'hanging readiness': { hangReady: true }, 'missing pair timeout': { hangEvents: true },
  'hanging after refresh': { hangAfter: true }, 'hanging release': { hangRelease: true },
  'release rejection': { releaseReject: true }, 'setup throw': { setupThrow: true },
  'after session conflict': { after: ({ x }) => x.after.runtimeInfo.sessionId = 'foreign' },
  'after source retarget': { after: ({ f }) => f.handle.address.serverId = 'foreign' },
  'after active turn': { after: ({ x }) => x.after.activeTurn = { turnId: 'new-turn', startedAt: stamp } },
  'permission during release': { onRelease: ({ emit }) => emit({ type: 'permission_requested' }) },
  'connection disconnected': { connection: () => 'disconnected' },
})) test(`bounded cleanup: ${name}`, async () => {
  const { r, f, elapsed } = await windowResult(settings, { deadlineMs: 50, maxCallbacks: 20 }); assert.equal(r.observed_turn_completed, false); assert.equal(r.usage, null); assert.ok(elapsed < 500, `bounded elapsed ${elapsed}`);
  if (!settings.setupThrow && !settings.connection) assert.equal(f.released, 1);
  if (settings.hangRelease || settings.releaseReject || settings.setupThrow) { assert.equal(r.reason, 'cleanup_uncertain'); assert.equal(r.cleanup_confirmed, false); }
});
test('callback budget includes every callback and fails overflow', async () => {
  const { r, f } = await windowResult({}, { deadlineMs: 120, maxCallbacks: 1 }); assert.equal(r.observed_turn_completed, false); assert.equal(f.released, 1);
});
test('caller event mutation cannot alter stored measured usage', async () => {
  const usage = { inputTokens: 4 };
  const { r } = await windowResult({ events: ({ emit }) => { emit({ type: 'turn_started' }); emit({ type: 'turn_completed', usage }); usage.inputTokens = 900; } }); assert.equal(r.status, 'observed'); assert.equal(r.usage.inputTokens, 4);
});
test('valid timeline events are private and never create lifecycle seq/epoch evidence', async () => {
  const { r } = await windowResult({ events: ({ emit }) => { emit({ type: 'turn_started' }); emit({ type: 'timeline', item: { type: 'assistant_message', text: 'SECRET-PROMPT' } }); emit({ type: 'turn_completed' }); } }); assert.equal(r.status, 'observed'); assert.doesNotMatch(JSON.stringify(r), /SECRET|epoch|seq/);
});
test('window validates explicit finite options before subscription', async () => {
  for (const options of [undefined, {}, { deadlineMs: 30001, maxCallbacks: 2 }, { deadlineMs: 0, maxCallbacks: 2 }, { deadlineMs: 30, maxCallbacks: 201 }, { deadlineMs: 30, maxCallbacks: true }]) { const { r, f } = await windowResult({}, options === undefined ? null : options); assert.equal(r.observed_turn_completed, false); assert.equal(f.released, 0); }
});
test('deadline also bounds a hanging first refresh without acquiring subscription', async () => {
  const f = windowFake(); f.handle.refresh = () => new Promise(() => {});
  const start = performance.now(); const r = await (await api).observeTurnWindow(f.client, f.x.binding, { deadlineMs: 30, maxCallbacks: 2 }); assert.equal(r.observed_turn_completed, false); assert.equal(f.released, 0); assert.ok(performance.now() - start < 500);
});
test('finite callback memory ignores callbacks after first refusal', async () => {
  const { r } = await windowResult({ events: ({ emit }) => { emit({ type: 'turn_failed' }); for (let i = 0; i < 500; i++) emit({ type: 'turn_started' }); } }); assert.equal(r.reason, 'window_refused');
});
test('optional subscription ID absence stays explicit and weak', async () => {
  const { r } = await windowResult({ events: ({ envelope, x, sub }) => {
    // The handle was observable at ready, so a change to null must refuse.
    sub.subscriptionId = null;
    envelope({ agentId: x.binding.agentId, timestamp: stamp, event: { type: 'turn_started', provider: 'codex', turnId: x.binding.turnId } });
  } }); assert.equal(r.observed_turn_completed, false);
});
test('source sample disconnect during callbacks refuses even with cached address', async () => {
  let connected = true;
  const { r } = await windowResult({ connection: () => connected ? 'connected' : 'disconnected', events: ({ emit }) => { emit({ type: 'turn_started' }); connected = false; emit({ type: 'turn_completed' }); connected = true; } }); assert.equal(r.observed_turn_completed, false);
});
test('snapshot session restart/provider mismatch or background work at close refuses', async () => {
  for (const after of [({ x }) => { x.after.runtimeInfo.sessionId = 'restart'; x.after.persistence.sessionId = 'restart'; }, ({ x }) => x.after.provider = 'claude', ({ x }) => x.after.backgroundWork.count = 2, ({ x }) => x.after.runtimeInfo = null]) { const { r, f } = await windowResult({ after }); assert.equal(r.observed_turn_completed, false); assert.equal(f.released, 1); }
});
test('no success from completion delivered before readiness or under foreign subscription', async () => {
  const f = windowFake({ hangReady: true });
  const observation = (await api).observeTurnWindow(f.client, f.x.binding, { deadlineMs: 80, maxCallbacks: 10 });
  await new Promise(resolve => setTimeout(resolve, 2));
  f.emit({ type: 'turn_started' }); f.emit({ type: 'turn_completed' });
  const r = await observation; assert.equal(r.observed_turn_completed, false); assert.equal(f.released, 1);
});
test('invalid required snapshot scalar and detail optional fields refuse', async () => {
  for (const mutate of [x => x.after.model = false, x => x.after.capabilities.supportsStreaming = 'yes', x => x.after.runtimeInfo.model = false, x => x.after.persistence = { provider: 'codex', sessionId: null }, x => x.page.entries[0].item = { type: 'tool_call', callId: 'fixture', name: 'fixture', status: 'running', error: null, detail: { type: 'shell', command: 'SECRET', exitCode: '0' } }]) assert.equal((await normalized(mutate)).status, 'invalid');
});
test('missing completion turn ID cannot be inferred from matching start', async () => {
  const { r } = await windowResult({ events: ({ emit, envelope, x }) => { emit({ type: 'turn_started' }); envelope({ agentId: x.binding.agentId, subscriptionId: 'subscription-fixture', timestamp: stamp, event: { type: 'turn_completed', provider: 'codex' } }); } }); assert.equal(r.observed_turn_completed, false);
});
test('explicit conflicting session assertion refuses rather than overriding snapshot session', async () => {
  const { r } = await windowResult({ events: ({ emit }) => { emit({ type: 'turn_started' }); emit({ type: 'turn_completed', sessionId: 'foreign' }); } }); assert.equal(r.observed_turn_completed, false);
});
test('SDK callback input cycle or excessive size refuses without raw output', async () => {
  for (const events of [({ emit }) => { const item = { type: 'assistant_message', text: 'SECRET' }; item.cycle = item; emit({ type: 'turn_started' }); emit({ type: 'timeline', item }); }, ({ emit }) => { emit({ type: 'turn_started' }); emit({ type: 'timeline', item: { type: 'assistant_message', text: 'x'.repeat(262145) } }); }]) { const { r, f } = await windowResult({ events }); assert.equal(r.observed_turn_completed, false); assert.equal(f.released, 1); assert.doesNotMatch(JSON.stringify(r), /SECRET/); }
});
test('absent observable subscription identity remains null with no continuity certification', async () => {
  let callback;
  const f = windowFake({ hangEvents: true }); f.sub.subscriptionId = null;
  f.handle.timeline.subscribe = handler => { callback = handler; return f.sub; };
  const observation = (await api).observeTurnWindow(f.client, f.x.binding, { deadlineMs: 100, maxCallbacks: 10 });
  await new Promise(resolve => setTimeout(resolve, 2));
  const envelope = { agentId: f.x.binding.agentId, timestamp: stamp };
  callback({ ...envelope, event: { type: 'turn_started', provider: 'codex', turnId: f.x.binding.turnId } });
  callback({ ...envelope, event: { type: 'turn_completed', provider: 'codex', turnId: f.x.binding.turnId } });
  const r = await observation; assert.equal(r.status, 'observed'); assert.equal(r.subscriptionId, null); assert.equal(r.source_continuity_verified, false); assert.equal(f.released, 1);
});
test('ready getter failure still releases obtained public handle', async () => {
  const f = windowFake({ hangEvents: true }); Object.defineProperty(f.sub, 'ready', { get() { throw Error('SECRET-READY-GETTER'); } });
  // Avoid the fake subscription's own scheduling read so the library owns this failure.
  f.handle.timeline.subscribe = () => f.sub;
  const r = await (await api).observeTurnWindow(f.client, f.x.binding, { deadlineMs: 100, maxCallbacks: 10 }); assert.equal(r.observed_turn_completed, false); assert.equal(f.released, 1); assert.doesNotMatch(JSON.stringify(r), /SECRET/);
});
test('public SDK refresh rejection at either stage cannot succeed', async () => {
  for (const stage of [1, 2]) {
    const f = windowFake(); const refresh = f.handle.refresh; let count = 0;
    f.handle.refresh = () => { if (++count === stage) return Promise.reject(Error('SECRET-REFRESH')); return refresh(); };
    const r = await (await api).observeTurnWindow(f.client, f.x.binding, { deadlineMs: 100, maxCallbacks: 10 }); assert.equal(r.observed_turn_completed, false); assert.doesNotMatch(JSON.stringify(r), /SECRET/); assert.equal(f.released, stage === 2 ? 1 : 0);
  }
});
test('malformed release return leaves explicit cleanup uncertainty', async () => {
  const f = windowFake(); const release = f.sub.release; f.sub.release = () => { void release(); return undefined; };
  const r = await (await api).observeTurnWindow(f.client, f.x.binding, { deadlineMs: 100, maxCallbacks: 10 }); assert.equal(r.observed_turn_completed, false); assert.equal(r.reason, 'cleanup_uncertain'); assert.equal(f.released, 1);
});

// State queries and live-window setup must not depend on historical coordinates.
function statusFake(x = fixture()) {
  const f = fake(x);
  Object.defineProperty(f.handle, 'timeline', { get() { throw new Error('history must not be accessed'); } });
  f.client.getConnectionState = () => ({ status: 'connected' });
  const binding = Object.fromEntries(['serverId', 'agentId', 'workspaceId', 'cwd', 'provider', 'providerSessionId'].map(k => [k, x.binding[k]]));
  return { ...f, x, binding };
}
for (const state of ['initializing', 'idle', 'running', 'error', 'closed']) test(`snapshot-only query preserves ${state} without reading history or claiming completion`, async () => {
  const f = statusFake(); f.x.before.status = state;
  f.x.before.lastUsage = { inputTokens: 999 }; f.x.before.title = 'SECRET-STATUS';
  const r = await (await api).observeStatus(f.client, f.binding);
  assert.equal(r.status, 'observed'); assert.equal(r.agent_status, state);
  assert.equal(r.observed_turn_completed, false); assert.equal(r.process_exit, null);
  assert.equal(r.task_acceptance, null); assert.equal(r.usage, null); assert.equal(r.usage_complete, false);
  assert.equal(r.admissible_for_controller, false); assert.equal(r.source_continuity_verified, false);
  assert.doesNotMatch(JSON.stringify(r), /SECRET|999/);
  assert.deepEqual(f.calls, [['ref', f.binding.agentId], 'refresh']);
});
test('snapshot-only query reports active work counts without interpreting them as completion', async () => {
  const f = statusFake(); f.x.before.activeTurn = { turnId: 'actual-active-turn', startedAt: stamp };
  f.x.before.backgroundWork.count = 2;
  const r = await (await api).observeStatus(f.client, f.binding);
  assert.equal(r.active_turn_id, 'actual-active-turn'); assert.equal(r.background_work_count, 2);
  assert.equal(r.observed_turn_completed, false);
});
test('snapshot-only query preserves unknown background work as null', async () => {
  const f = statusFake(); delete f.x.before.backgroundWork;
  assert.equal((await (await api).observeStatus(f.client, f.binding)).background_work_count, null);
});
for (const failure of ['foreign-address', 'disconnected', 'session-conflict', 'unknown-status', 'SDK-rejection']) test(`snapshot-only query refuses ${failure} without exposing input`, async () => {
  const f = statusFake();
  if (failure === 'foreign-address') f.handle.address.serverId = 'foreign';
  if (failure === 'disconnected') f.client.getConnectionState = () => ({ status: 'disconnected' });
  if (failure === 'session-conflict') f.x.before.persistence.sessionId = 'foreign';
  if (failure === 'unknown-status') f.x.before.status = 'SECRET-FAKE-STATUS';
  if (failure === 'SDK-rejection') f.handle.refresh = async () => { throw Error('SECRET-SDK'); };
  const r = await (await api).observeStatus(f.client, f.binding);
  assert.equal(r.status, 'invalid'); assert.equal(r.observed_turn_completed, false);
  assert.doesNotMatch(JSON.stringify(r), /SECRET/);
});
test('snapshot-only query rejects address drift after refresh', async () => {
  const f = statusFake(); f.handle.refresh = async () => { f.handle.address.agentId = 'foreign'; return { agent: f.x.before }; };
  assert.equal((await (await api).observeStatus(f.client, f.binding)).status, 'invalid');
});
test('live window observes a supplied exact turn without epoch, cursor or any history API', async () => {
  const f = windowFake(); delete f.x.binding.epoch; delete f.x.binding.cursor;
  f.handle.timeline.refetch = () => { throw Error('history must not be read'); };
  const r = await (await api).observeTurnWindow(f.client, f.x.binding, { deadlineMs: 200, maxCallbacks: 10 });
  assert.equal(r.observed_turn_completed, true); assert.equal(r.admissible_for_controller, false);
  assert.equal(r.epoch, undefined); assert.equal(r.cursor, undefined); assert.equal(f.released, 1);
});
test('live window still refuses a missing expected turn when no history coordinates are supplied', async () => {
  const f = windowFake(); delete f.x.binding.epoch; delete f.x.binding.cursor; delete f.x.binding.turnId;
  const r = await (await api).observeTurnWindow(f.client, f.x.binding, { deadlineMs: 100, maxCallbacks: 10 });
  assert.equal(r.observed_turn_completed, false); assert.equal(f.reads, 0); assert.equal(f.released, 0);
});

function requestWindowFake(events) {
  const f = windowFake({ events });
  const binding = Object.fromEntries(['serverId', 'agentId', 'workspaceId', 'cwd', 'provider', 'providerSessionId'].map(k => [k, f.x.binding[k]]));
  Object.assign(binding, { requestId: 'request-fixture', messageId: 'submitted-message-fixture' });
  f.handle.timeline.refetch = () => { throw Error('history forbidden'); };
  return { f, binding };
}
const requestEvents = ({ emit }, mutate) => {
  const events = [
    { type: 'turn_started', turnId: 'sdk-issued-turn' },
    { type: 'timeline', turnId: 'sdk-issued-turn', item: { type: 'user_message', clientMessageId: 'submitted-message-fixture', messageId: 'server-message-fixture', text: 'SECRET-REQUEST' } },
    { type: 'turn_completed', turnId: 'sdk-issued-turn', usage: { inputTokens: 0, outputTokens: 7 } },
  ];
  mutate?.(events); for (const event of events) emit(event);
};
test('request window matches exact client message ID to actual daemon turn without history or guessed turn', async () => {
  const { f, binding } = requestWindowFake(args => requestEvents(args));
  const r = await (await api).observeRequestWindow(f.client, binding, { deadlineMs: 200, maxCallbacks: 10 });
  assert.equal(r.status, 'observed'); assert.equal(r.request_matched, true);
  assert.equal(r.requestId, binding.requestId); assert.equal(r.messageId, binding.messageId);
  assert.equal(r.request_exclusive, false); assert.equal(r.usage_attributed_to_request, false);
  assert.equal(r.turnId, 'sdk-issued-turn'); assert.equal(r.usage.inputTokens, 0); assert.equal(r.usage.outputTokens, 7);
  assert.equal(r.source_continuity_verified, false); assert.equal(r.admissible_for_controller, false);
  assert.equal(r.task_acceptance, null); assert.equal(r.process_exit, null); assert.equal(r.usage_complete, false);
  assert.equal(f.released, 1); assert.doesNotMatch(JSON.stringify(r), /SECRET|server-message-fixture/);
});
for (const [name, mutate] of Object.entries({
  'missing client message ID': e => delete e[1].item.clientMessageId,
  'different message ID': e => e[1].item.clientMessageId = 'foreign-message',
  'messageId alone is not client correlation': e => { delete e[1].item.clientMessageId; e[1].item.messageId = 'submitted-message-fixture'; },
  'completion without message row': e => e.splice(1, 1),
  'message before start': e => [e[0], e[1]] = [e[1], e[0]],
  'foreign row turn': e => e[1].turnId = 'foreign-turn',
  'foreign completion turn': e => e[2].turnId = 'foreign-turn',
  'second start': e => e.splice(1, 0, { type: 'turn_started', turnId: 'sdk-issued-turn' }),
  'steered extra user message': e => e.splice(2, 0, { ...e[1], item: { ...e[1].item, clientMessageId: 'another-message' } }),
  'duplicate matched row': e => e.splice(2, 0, structuredClone(e[1])),
  'matching text is insufficient': e => { e[1].item.clientMessageId = 'foreign'; e[1].item.text = 'submitted-message-fixture'; },
  'missing official turn': e => delete e[0].turnId,
})) test(`request correlation refuses ${name}`, async () => {
  const { f, binding } = requestWindowFake(args => requestEvents(args, mutate));
  const r = await (await api).observeRequestWindow(f.client, binding, { deadlineMs: 200, maxCallbacks: 10 });
  assert.equal(r.observed_turn_completed, false); assert.equal(r.request_matched, undefined);
  assert.equal(r.usage, null); assert.equal(f.released, 1); assert.doesNotMatch(JSON.stringify(r), /SECRET/);
});
for (const key of ['requestId', 'messageId']) test(`request binding requires ${key} before SDK reads`, async () => {
  const { f, binding } = requestWindowFake(args => requestEvents(args)); delete binding[key];
  const r = await (await api).observeRequestWindow(f.client, binding, { deadlineMs: 100, maxCallbacks: 10 });
  assert.equal(r.observed_turn_completed, false); assert.equal(f.reads, 0);
});
for (const [key, value] of [['turnId', 'guessed-turn'], ['epoch', 'guessed-epoch'], ['cursor', { epoch: 'guessed', seq: 0 }]]) test(`request binding rejects supplied ${key} as correlation`, async () => {
  const { f, binding } = requestWindowFake(args => requestEvents(args)); binding[key] = value;
  const r = await (await api).observeRequestWindow(f.client, binding, { deadlineMs: 100, maxCallbacks: 10 });
  assert.equal(r.observed_turn_completed, false); assert.equal(f.reads, 0);
});
test('in-flight request binding cannot be changed by caller mutation', async () => {
  let binding;
  const setup = requestWindowFake(args => { binding.messageId = 'changed'; binding.requestId = 'changed'; requestEvents(args); });
  binding = setup.binding;
  const r = await (await api).observeRequestWindow(setup.f.client, binding, { deadlineMs: 200, maxCallbacks: 10 });
  assert.equal(r.request_matched, true); assert.equal(r.messageId, 'submitted-message-fixture'); assert.equal(r.requestId, 'request-fixture');
});
