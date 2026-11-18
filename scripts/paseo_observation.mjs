/** Read-only observation of an already connected public Paseo SDK 0.16 client.
 * Canonical timeline rows do not contain stream lifecycle events. Never infer them.
 */
const MAX_ROWS = 200;
const identityKeys = ['serverId', 'agentId', 'workspaceId', 'cwd', 'provider', 'providerSessionId'];
const itemTypes = new Set(['user_message', 'assistant_message', 'reasoning', 'tool_call', 'todo', 'error', 'notification', 'compaction', 'plugin']);
const invalid = () => ({ status: 'invalid', reason: 'invalid_observation', observed_turn_completed: false,
  task_acceptance: null, process_exit: null, usage: null, usage_complete: false });
function requireValue(value) { if (!value) throw new Error('invalid_observation'); }
function record(x) { requireValue(x !== null && typeof x === 'object' && !Array.isArray(x)); return x; }
function str(x) { requireValue(typeof x === 'string' && x.length > 0 && x.length <= 4096 && !/[\u0000-\u001f\u007f]/u.test(x)); return x; }
function seq(x) { requireValue(Number.isSafeInteger(x) && x >= 0); return x; }
// Reject accessors, non-JSON objects, cycles, excessive input, and unsafe numbers before reading.
function bounded(value) {
  let nodes = 0, bytes = 0;
  const active = new Set();
  function visit(x, depth) {
    requireValue(++nodes <= 50000 && depth <= 24);
    if (x === null || typeof x === 'boolean') return;
    if (typeof x === 'string') { bytes += x.length; requireValue(bytes <= 1048576 && x.length <= 262144); return; }
    if (typeof x === 'number') { requireValue(Number.isFinite(x) && Math.abs(x) <= Number.MAX_SAFE_INTEGER); return; }
    requireValue(typeof x === 'object' && !active.has(x));
    requireValue(Array.isArray(x) ? Object.getPrototypeOf(x) === Array.prototype : [Object.prototype, null].includes(Object.getPrototypeOf(x)));
    active.add(x);
    const keys = Reflect.ownKeys(x);
    requireValue(keys.length <= 10000);
    for (const key of keys) {
      requireValue(typeof key === 'string');
      const d = Object.getOwnPropertyDescriptor(x, key);
      requireValue('value' in d);
      visit(d.value, depth + 1);
    }
    active.delete(x);
  }
  visit(value, 0);
}
function identityBindingOf(b) {
  record(b);
  for (const k of identityKeys) str(b[k]);
  if (b.forbiddenIdentities !== undefined) {
    requireValue(Array.isArray(b.forbiddenIdentities) && b.forbiddenIdentities.length <= 200);
    for (const f of b.forbiddenIdentities) {
      record(f); str(f.provider); str(f.providerSessionId);
      requireValue(f.provider !== b.provider || f.providerSessionId !== b.providerSessionId);
    }
  }
  return b;
}
function turnBindingOf(b) { identityBindingOf(b); str(b.turnId); return b; }
function bindingOf(b) {
  turnBindingOf(b); str(b.epoch);
  record(b.cursor); requireValue(b.cursor.epoch === b.epoch); seq(b.cursor.seq);
  if (b.limit !== undefined) requireValue(seq(b.limit) > 0 && b.limit <= MAX_ROWS);
  return b;
}
function snapshot(s, b) {
  record(s);
  for (const k of ['provider', 'cwd', 'workspaceId']) requireValue(str(s[k]) === b[k]);
  requireValue(str(s.id) === b.agentId);
  for (const k of ['model', 'lastUserMessageAt', 'currentModeId', 'title']) requireValue(s[k] === null || typeof s[k] === 'string');
  requireValue(Object.hasOwn(s, 'persistence'));
  if (s.runtimeInfo !== undefined) record(s.runtimeInfo); // optional, never nullable
  record(s.capabilities);
  for (const k of ['supportsStreaming', 'supportsSessionPersistence', 'supportsDynamicModes', 'supportsMcpServers', 'supportsReasoningStream', 'supportsToolInvocations']) requireValue(typeof s.capabilities[k] === 'boolean');
  for (const v of Object.values(s.capabilities)) requireValue(typeof v === 'boolean');
  requireValue(Array.isArray(s.availableModes));
  for (const mode of s.availableModes) {
    record(mode); str(mode.id); requireValue(typeof mode.label === 'string');
    for (const k of ['description', 'icon', 'colorTier']) if (mode[k] !== undefined) requireValue(typeof mode[k] === 'string');
  }
  if (s.labels !== undefined) { record(s.labels); for (const v of Object.values(s.labels)) requireValue(typeof v === 'string'); }
  for (const info of [s.runtimeInfo, s.persistence]) if (info != null) {
    record(info);
    for (const k of ['model', 'thinkingOptionId', 'modeId']) if (info[k] !== undefined) requireValue(info[k] === null || typeof info[k] === 'string');
    if (info.nativeHandle !== undefined) requireValue(typeof info.nativeHandle === 'string');
    if (info.metadata !== undefined) record(info.metadata);
    if (info.extra !== undefined) record(info.extra);
  }
  let session = null;
  for (const info of [s.runtimeInfo, s.persistence]) {
    if (info === undefined || info === null) continue;
    record(info); requireValue(str(info.provider) === b.provider);
    if (info.sessionId === null) { requireValue(info === s.runtimeInfo); continue; }
    const id = str(info.sessionId);
    requireValue(id === b.providerSessionId && (session === null || session === id)); session = id;
  }
  requireValue(session !== null);
  str(s.status); str(s.createdAt); str(s.updatedAt);
  requireValue(Number.isFinite(Date.parse(s.createdAt)) && Number.isFinite(Date.parse(s.updatedAt)));
  requireValue(Date.parse(s.createdAt) <= Date.parse(s.updatedAt));
  requireValue(Array.isArray(s.pendingPermissions));
  for (const permission of s.pendingPermissions) {
    record(permission); str(permission.id); str(permission.provider); requireValue(typeof permission.name === 'string');
    requireValue(['tool', 'plan', 'question', 'mode', 'other'].includes(permission.kind));
  }
  if (s.backgroundWork !== undefined) seq(record(s.backgroundWork).count);
  if (s.activeTurn !== undefined && s.activeTurn !== null) {
    str(record(s.activeTurn).turnId); requireValue(s.activeTurn.startedAt === null || typeof s.activeTurn.startedAt === 'string');
  }
  for (const k of ['providerUnavailable', 'requiresAttention', 'background']) if (s[k] !== undefined) requireValue(typeof s[k] === 'boolean');
  if (s.attentionReason !== undefined && s.attentionReason !== null) requireValue(['finished', 'error', 'permission'].includes(s.attentionReason));
  if (s.archivedAt !== undefined && s.archivedAt !== null) str(s.archivedAt);
  return s;
}
function busy(s) {
  return s.status !== 'idle' || s.pendingPermissions.length > 0 || s.activeTurn != null ||
    s.backgroundWork === undefined || s.backgroundWork.count > 0 || s.providerUnavailable === true ||
    s.archivedAt != null || s.lastError !== undefined || ['error', 'permission'].includes(s.attentionReason);
}
function itemShape(item) {
  if (['user_message', 'assistant_message', 'reasoning'].includes(item.type)) requireValue(typeof item.text === 'string');
  if (item.type === 'error' || item.type === 'notification') requireValue(typeof item.message === 'string');
  if (item.type === 'notification') requireValue(['info', 'warning', 'error'].includes(item.level));
  if (item.type === 'compaction') requireValue(['loading', 'completed'].includes(item.status));
  if (item.type === 'tool_call') {
    str(item.callId); requireValue(typeof item.name === 'string');
    requireValue(['running', 'completed', 'failed', 'canceled'].includes(item.status));
    requireValue(Object.hasOwn(item, 'error') && (item.status === 'failed' ? item.error !== null : item.error === null));
    const d = record(item.detail);
    // Deliberate supported subset of official detail variants; unknown variants refuse.
    const required = { shell: ['command'], read: ['filePath'], edit: ['filePath'], write: ['filePath'], search: ['query'], fetch: ['url'], plan: ['text'], plain_text: [], sub_agent: [], unknown: [] };
    requireValue(Object.hasOwn(required, d.type));
    for (const k of required[d.type]) requireValue(typeof d[k] === 'string');
    if (d.type === 'unknown') requireValue(Object.hasOwn(d, 'input') && Object.hasOwn(d, 'output'));
    // Unsupported optional complex payloads refuse rather than appear schema-validated.
    requireValue(item.agentMessage === undefined && d.icon === undefined);
    for (const k of ['exitCode', 'offset', 'limit', 'numFiles', 'numMatches', 'durationMs', 'durationSeconds', 'code', 'bytes']) if (d[k] !== undefined) requireValue(typeof d[k] === 'number' || (k === 'exitCode' && d[k] === null));
    if (d.toolName !== undefined) requireValue(['search', 'grep', 'glob', 'web_search'].includes(d.toolName));
    if (d.mode !== undefined) requireValue(['content', 'files_with_matches', 'count'].includes(d.mode));
    if (d.truncated !== undefined) requireValue(typeof d.truncated === 'boolean');
    for (const k of ['filePaths', 'annotations']) if (d[k] !== undefined) { requireValue(Array.isArray(d[k])); for (const v of d[k]) requireValue(typeof v === 'string'); }
    if (d.webResults !== undefined) { requireValue(Array.isArray(d.webResults)); for (const v of d.webResults) { record(v); requireValue(typeof v.title === 'string' && typeof v.url === 'string'); } }
    if (d.actions !== undefined) { requireValue(Array.isArray(d.actions)); for (const v of d.actions) { record(v); requireValue(seq(v.index) > 0 && typeof v.toolName === 'string'); if (v.summary !== undefined) requireValue(typeof v.summary === 'string'); } }
    for (const k of ['label', 'text', 'content', 'cwd', 'output', 'oldString', 'newString', 'unifiedDiff', 'prompt', 'result', 'codeText', 'subAgentType', 'description', 'childSessionId', 'log']) if (d[k] !== undefined) requireValue(typeof d[k] === 'string');
  }
  if (item.type === 'todo') {
    requireValue(Array.isArray(item.items));
    for (const todo of item.items) { record(todo); requireValue(typeof todo.text === 'string' && typeof todo.completed === 'boolean'); }
  }
  if (item.type === 'plugin') { str(item.id); str(item.pluginId); str(item.kind); seq(item.version); requireValue(Object.hasOwn(item, 'data')); }
}
function cursor(c, epoch, value) { record(c); requireValue(c.epoch === epoch && seq(c.seq) === value); }
/** Input is {binding,source:{serverId,agentId},before:AgentSnapshotPayload,
 * page:FetchAgentTimelineResponse.payload,after:AgentSnapshotPayload}.
 * Pure normalization is coordinator-observed evidence, not authentication.
 */
export function normalizeObservation(input) {
  try {
    bounded(input); record(input);
    const b = bindingOf(input.binding), source = record(input.source);
    requireValue(source.serverId === b.serverId && source.agentId === b.agentId);
    const before = snapshot(input.before, b), after = snapshot(input.after, b), p = record(input.page);
    const middle = snapshot(p.agent, b);
    requireValue(Date.parse(before.updatedAt) <= Date.parse(middle.updatedAt) && Date.parse(middle.updatedAt) <= Date.parse(after.updatedAt));
    str(p.requestId); requireValue(p.agentId === b.agentId && p.epoch === b.epoch && p.direction === 'after' && p.projection === 'canonical');
    requireValue(p.error === null);
    for (const k of ['reset', 'staleCursor', 'gap', 'hasOlder', 'hasNewer']) requireValue(typeof p[k] === 'boolean');
    requireValue(Array.isArray(p.entries) && p.entries.length <= (b.limit ?? MAX_ROWS));
    const w = record(p.window); seq(w.minSeq); seq(w.maxSeq); seq(w.nextSeq);
    requireValue(w.minSeq <= w.maxSeq && w.maxSeq < Number.MAX_SAFE_INTEGER && w.nextSeq === w.maxSeq + 1);
    let previous = b.cursor.seq;
    for (const row of p.entries) {
      record(row); requireValue(row.provider === b.provider); str(row.timestamp); requireValue(Number.isFinite(Date.parse(row.timestamp)));
      requireValue(seq(row.seqStart) === previous + 1 && seq(row.seqEnd) === row.seqStart);
      requireValue(row.seqStart >= w.minSeq && row.seqEnd <= w.maxSeq);
      requireValue(Array.isArray(row.collapsed) && row.collapsed.length === 0);
      requireValue(Array.isArray(row.sourceSeqRanges) && row.sourceSeqRanges.length === 1);
      const range = record(row.sourceSeqRanges[0]); requireValue(range.startSeq === row.seqStart && range.endSeq === row.seqEnd);
      if (row.turnId !== undefined) str(row.turnId);
      const item = record(row.item); requireValue(itemTypes.has(item.type)); itemShape(item);
      // Unknown/forged stream lifecycle assertions cannot be smuggled into a canonical row.
      if (item.provider !== undefined) requireValue(item.provider === row.provider);
      if (item.turnId !== undefined) requireValue(item.turnId === row.turnId);
      previous = row.seqEnd;
    }
    if (p.entries.length) {
      cursor(p.startCursor, b.epoch, p.entries[0].seqStart); cursor(p.endCursor, b.epoch, previous);
    } else requireValue(p.startCursor === null && p.endCursor === null);
    requireValue(previous <= w.maxSeq && b.cursor.seq <= w.maxSeq);
    const incomplete = p.reset || p.staleCursor || p.gap || p.hasNewer || previous !== w.maxSeq;
    const reason = incomplete ? 'incomplete_timeline' : [before, middle, after].some(busy) ? 'unsettled_snapshot' : 'lifecycle_unavailable';
    return { status: 'pending', reason, identity: Object.fromEntries(identityKeys.map(k => [k, b[k]])),
      turnId: b.turnId, epoch: b.epoch, cursor: { epoch: b.epoch, seq: b.cursor.seq },
      observed_turn_completed: false, task_acceptance: null, process_exit: null, usage: null, usage_complete: false };
  } catch { return invalid(); }
}
/** Snapshot-only query. Does not load a timeline or establish task/turn completion. */
export async function observeStatus(client, binding) {
  try {
    bounded(binding); identityBindingOf(binding);
    const b = JSON.parse(JSON.stringify(binding));
    const handle = client.agents.ref(b.agentId);
    const check = () => {
      requireValue(client.getConnectionState()?.status === 'connected');
      const address = handle.address;
      requireValue(address.serverId === b.serverId && address.agentId === b.agentId);
    };
    check();
    const fetched = await handle.refresh();
    bounded(fetched); check();
    const s = snapshot(record(fetched).agent, b);
    requireValue(['initializing', 'idle', 'running', 'error', 'closed'].includes(s.status));
    return { status: 'observed', reason: 'snapshot_observed',
      identity: Object.fromEntries(identityKeys.map(k => [k, b[k]])),
      agent_status: s.status, active_turn_id: s.activeTurn?.turnId ?? null,
      pending_permissions_count: s.pendingPermissions.length,
      background_work_count: s.backgroundWork?.count ?? null,
      source_continuity_verified: false, admissible_for_controller: false,
      observed_turn_completed: false, task_acceptance: null, process_exit: null,
      usage: null, usage_complete: false };
  } catch { return { ...invalid(), source_continuity_verified: false, admissible_for_controller: false }; }
}
/** Legacy canonical history query; the installed 0.16 server returns projected data. */
export async function observeAgent(client, binding) {
  try {
    bounded(binding); bindingOf(binding);
    // Copy before awaiting so caller mutation cannot retarget an in-flight observation.
    const b = JSON.parse(JSON.stringify(binding));
    const handle = client.agents.ref(b.agentId);
    const checkAddress = () => { const a = handle.address; requireValue(a.serverId === b.serverId && a.agentId === b.agentId); };
    checkAddress();
    const beforeResult = await handle.refresh();
    bounded(beforeResult); snapshot(record(beforeResult).agent, b); checkAddress();
    const before = JSON.parse(JSON.stringify(beforeResult.agent));
    // A live SDK timeline read can hydrate a closed session. Refuse before it.
    requireValue(before.status !== 'closed');
    const page = await handle.timeline.refetch({ direction: 'after', cursor: { ...b.cursor }, projection: 'canonical', limit: b.limit ?? MAX_ROWS });
    bounded(page);
    const pageCopy = JSON.parse(JSON.stringify(page));
    checkAddress();
    const afterResult = await handle.refresh(); checkAddress();
    return normalizeObservation({ binding: b, source: { serverId: b.serverId, agentId: b.agentId }, before, page: pageCopy, after: afterResult?.agent });
  } catch { return invalid(); }
}

const usageKeys = ['inputTokens', 'cachedInputTokens', 'outputTokens', 'totalCostUsd', 'contextWindowMaxTokens', 'contextWindowUsedTokens'];
function eventUsage(value) {
  if (value !== undefined) record(value);
  return Object.fromEntries(usageKeys.map(k => {
    const v = value?.[k];
    if (v !== undefined) requireValue(typeof v === 'number' && Number.isFinite(v) && v >= 0);
    return [k, v === undefined ? null : v];
  }));
}
/** Weaker live-window evidence only. Explicit options: deadlineMs (1..30000),
 * maxCallbacks (1..200). No lifecycle epoch/seq or source continuity attestation.
 * The deadline includes setup, reads, readiness, observation and release waiting.
 */
export async function observeTurnWindow(client, binding, options) {
  return observeWindow(client, binding, options, false);
}
/** Match an explicit request/message ID to the daemon's actual user-message turn. */
export async function observeRequestWindow(client, binding, options) {
  return observeWindow(client, binding, options, true);
}
async function observeWindow(client, binding, options, requestMode) {
  let subscription, registrationAttempted = false, closed = false, armed = false, callbacks = 0, started = false, completed = false;
  let refusal = false, subscriptionId = null, usage = null, cleanup = false, handle, b, matched = false, candidateTurn = null;
  let wake, fail;
  const pair = new Promise((resolve, reject) => { wake = resolve; fail = reject; });
  // Attach rejection handling before synchronous SDK callbacks can fire.
  pair.catch(() => {});
  const result = (status, reason, observed = false) => ({ status, reason, observed_turn_completed: observed,
    source_continuity_verified: false, admissible_for_controller: false, cleanup_confirmed: cleanup,
    task_acceptance: null, process_exit: null, usage: observed ? usage : null, usage_complete: false,
    ...(requestMode ? { request_exclusive: false, usage_attributed_to_request: false } : {}),
    ...(observed ? { identity: Object.fromEntries(identityKeys.map(k => [k, b[k]])), turnId: b.turnId, subscriptionId,
      ...(requestMode ? { requestId: b.requestId, messageId: b.messageId, request_matched: matched } : {}) } : {}) });
  let outcome = result('invalid', 'invalid_observation'), totalEnd = 0, workEnd = 0;
  const now = () => performance.now();
  const wait = async (promise, end) => {
    const guarded = Promise.resolve(promise);
    guarded.catch(() => {});
    requireValue(now() < end);
    let timeout;
    try {
      const timed = new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('deadline')), Math.max(1, end - now())); });
      const value = await Promise.race([guarded, timed]);
      requireValue(now() < end); return value;
    } finally { clearTimeout(timeout); }
  };
  const rejectWindow = () => { refusal = true; fail(new Error('window_refused')); };
  const check = () => {
    requireValue(client.getConnectionState()?.status === 'connected');
    const address = handle.address;
    requireValue(address.serverId === b.serverId && address.agentId === b.agentId);
    if (armed) requireValue(subscription.subscriptionId === subscriptionId);
  };
  const callback = payload => {
    if (closed || refusal) return;
    try {
      requireValue(++callbacks <= options.maxCallbacks && now() < workEnd && armed);
      bounded(payload); record(payload); check();
      requireValue(payload.agentId === b.agentId);
      if (payload.subscriptionId !== undefined) requireValue(str(payload.subscriptionId) === subscriptionId);
      const event = record(payload.event);
      // Session identity is supplied by snapshots, never invented from lifecycle extras.
      for (const owner of [payload, event]) for (const k of ['sessionId', 'providerSessionId']) if (owner[k] !== undefined) requireValue(owner[k] === b.providerSessionId);
      if (payload.serverId !== undefined) requireValue(payload.serverId === b.serverId);
      // No control variant, session restart, permission or failure can be ignored.
      requireValue(['turn_started', 'turn_completed', 'timeline'].includes(event.type));
      requireValue(event.provider === b.provider); str(event.turnId);
      if (!requestMode) requireValue(event.turnId === b.turnId);
      else if (candidateTurn !== null) requireValue(event.turnId === candidateTurn);
      str(payload.timestamp); requireValue(Number.isFinite(Date.parse(payload.timestamp)));
      if (event.type === 'turn_started') {
        requireValue(!started && !completed); started = true; candidateTurn = event.turnId;
      } else if (event.type === 'turn_completed') {
        requireValue(started && !completed && (!requestMode || matched));
        usage = eventUsage(event.usage); completed = true; wake();
      } else {
        requireValue(started && !completed);
        const item = record(event.item); requireValue(itemTypes.has(item.type)); itemShape(item);
        if (requestMode && item.type === 'user_message') {
          requireValue(!matched && str(item.clientMessageId) === b.messageId);
          b.turnId = event.turnId; matched = true;
        }
        requireValue(item.type !== 'error' && !(item.type === 'notification' && item.level === 'error'));
        if (item.provider !== undefined) requireValue(item.provider === event.provider);
        if (item.turnId !== undefined) requireValue(item.turnId === event.turnId);
        // seq/epoch, when supplied, describe only this timeline event, never lifecycle.
        if (payload.seq !== undefined) seq(payload.seq);
        if (payload.epoch !== undefined) str(payload.epoch);
      }
    } catch { rejectWindow(); }
  };
  try {
    bounded(binding);
    if (requestMode) {
      identityBindingOf(binding); str(binding.requestId); str(binding.messageId);
      // Do not accept a guessed turn or history coordinate as request correlation.
      requireValue(binding.turnId === undefined && binding.epoch === undefined && binding.cursor === undefined);
    } else turnBindingOf(binding);
    // Legacy history coordinates may be supplied, but live events never require them.
    if (binding.epoch !== undefined || binding.cursor !== undefined) bindingOf(binding);
    bounded(options); record(options);
    requireValue(seq(options.deadlineMs) > 0 && options.deadlineMs <= 30000);
    requireValue(seq(options.maxCallbacks) > 0 && options.maxCallbacks <= 200);
    // Freeze caller choices across awaits; input mutation cannot retarget the window.
    options = { deadlineMs: options.deadlineMs, maxCallbacks: options.maxCallbacks };
    b = JSON.parse(JSON.stringify(binding));
    totalEnd = now() + options.deadlineMs;
    // Reserve part of the single total deadline for bounded cleanup confirmation.
    workEnd = totalEnd - Math.min(1000, options.deadlineMs / 5);
    handle = client.agents.ref(b.agentId); check();
    const first = await wait(handle.refresh(), workEnd);
    bounded(first); const before = JSON.parse(JSON.stringify(snapshot(record(first).agent, b)));
    requireValue(!busy(before)); check();
    registrationAttempted = true;
    subscription = handle.timeline.subscribe(callback);
    requireValue(subscription && typeof subscription.release === 'function' && subscription.ready && typeof subscription.ready.then === 'function');
    await wait(Promise.race([subscription.ready, pair]), workEnd);
    requireValue(!refusal); check();
    subscriptionId = subscription.subscriptionId;
    requireValue(subscriptionId === null || (typeof subscriptionId === 'string' && str(subscriptionId)));
    armed = true;
    await wait(pair, workEnd); requireValue(!refusal && completed); check();
    // Continue accepting refusal callbacks while the after read/release is outstanding.
    const last = await wait(handle.refresh(), workEnd);
    bounded(last); const after = snapshot(record(last).agent, b);
    requireValue(!busy(after) && Date.parse(before.updatedAt) <= Date.parse(after.updatedAt));
    check(); requireValue(!refusal);
    outcome = result('observed', 'live_window_pair_observed', true);
  } catch {
    outcome = result('pending', refusal ? 'window_refused' : 'window_unverified');
  } finally {
    if (subscription) {
      try {
        // Always request release, even if the work deadline elapsed or ready rejected.
        const release = subscription.release();
        requireValue(release && typeof release.then === 'function');
        await wait(release, totalEnd);
        cleanup = true;
        check();
      } catch {
        cleanup = false;
      }
      closed = true;
      if (!cleanup) outcome = result('pending', 'cleanup_uncertain');
      else if (refusal) outcome = result('pending', 'window_refused');
      else outcome.cleanup_confirmed = true;
    } else { closed = true; if (registrationAttempted) outcome = result('pending', 'cleanup_uncertain'); }
  }
  return outcome;
}
