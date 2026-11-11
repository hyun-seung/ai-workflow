"""Append measured native usage observations without replacing source actors."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from dispatch_usage import FIELDS, ROLE_IDS

MAX_SAFE = 2**53 - 1
SOURCE = 'codex.token_count.cumulative_delta.v1'


def event_id(key):
    return 'usage:' + hashlib.sha256(key.encode()).hexdigest()


def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_SAFE:
        raise ValueError('Invalid usage integer')
    return value


def counters(value):
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError('Invalid usage counters')
    for key, number in value.items():
        if key in ('cached_input_tokens', 'reasoning_output_tokens') and number is None:
            continue
        integer(number)
    if value['total_tokens'] != value['input_tokens'] + value['output_tokens']:
        raise ValueError('Usage total mismatch')
    for detail, primary in [('cached_input_tokens', 'input_tokens'), ('reasoning_output_tokens', 'output_tokens')]:
        if value[detail] is not None and value[detail] > value[primary]:
            raise ValueError('Usage subset mismatch')


def validate_usage(usage):
    from trace_store import ident, stamp
    expected = {'version', 'status', 'scope', 'task_attributed', 'request_id', 'role', 'task_id',
                'dispatch_id', 'actor_id', 'session_id', 'source', 'measurement_key', 'measurements',
                'issues', 'interval', 'baseline', 'final', 'increments', 'request_binding'}
    if not isinstance(usage, dict) or set(usage) != expected:
        raise ValueError('Unsupported dispatch usage record')
    u = usage
    if (type(u['version']) is not int or u['version'] != 1 or u['status'] != 'measured' or
            u['scope'] != 'dispatch' or u['task_attributed'] is not True or u['issues'] != [] or
            u['source'] != SOURCE or u['request_binding'] != 'explicit' or
            not isinstance(u['role'], str) or u['role'] not in ROLE_IDS):
        raise ValueError('Usage must be measured and explicitly attributed')
    for key in ('request_id', 'task_id', 'dispatch_id', 'actor_id', 'session_id'):
        ident(u[key])
    if u['actor_id'] != u['dispatch_id']:
        raise ValueError('Native usage actor must match dispatch')
    interval = u['interval']
    if not isinstance(interval, dict) or set(interval) != {'native_turn_id', 'start_line', 'assignment_line', 'end_line', 'started_at', 'finished_at'}:
        raise ValueError('Invalid usage interval')
    ident(interval['native_turn_id'])
    if u['measurement_key'] != 'codex.session:' + u['session_id'] + ':turn:' + interval['native_turn_id']:
        raise ValueError('Usage key does not match physical interval')
    for key in ('start_line', 'assignment_line', 'end_line'):
        integer(interval[key], 1)
    if not interval['start_line'] < interval['assignment_line'] < interval['end_line']:
        raise ValueError('Invalid usage interval order')
    for key in ('started_at', 'finished_at'): stamp(interval[key])
    if interval['started_at'] and interval['finished_at'] and stamp(interval['started_at']) > stamp(interval['finished_at']):
        raise ValueError('Invalid usage time order')
    counters(u['measurements'])
    for key in ('baseline', 'final'):
        snapshot = u[key]
        if not isinstance(snapshot, dict) or set(snapshot) != ({'line', 'time', 'values', 'method'} if key == 'baseline' else {'line', 'time', 'values'}):
            raise ValueError('Invalid usage counter snapshot')
        counters(snapshot['values']); stamp(snapshot['time'])
    baseline, final = u['baseline'], u['final']
    if baseline['method'] == 'zero_reconciled_with_first_usage':
        if baseline['line'] is not None or any(v != 0 for v in baseline['values'].values()):
            raise ValueError('Invalid initial usage baseline')
    elif baseline['method'] == 'prior_turn_counter':
        if integer(baseline['line'], 1) >= interval['start_line']:
            raise ValueError('Invalid prior usage baseline')
    else:
        raise ValueError('Unsupported usage baseline')
    if not interval['assignment_line'] < integer(final['line'], 1) < interval['end_line']:
        raise ValueError('Invalid final usage line')
    for key in FIELDS:
        a, b, measured = baseline['values'][key], final['values'][key], u['measurements'][key]
        if measured is not None and (a is None or b is None or b - a != measured):
            raise ValueError('Usage does not match counter endpoints')
    if not isinstance(u['increments'], list) or len(u['increments']) > 100000:
        raise ValueError('Invalid usage increments')
    previous = interval['assignment_line']; totals = dict.fromkeys(FIELDS, 0)
    for increment in u['increments']:
        if not isinstance(increment, dict) or set(increment) != {'line', 'time', 'values'}:
            raise ValueError('Invalid usage increment')
        line = integer(increment['line'], 1)
        if not previous < line <= final['line']:
            raise ValueError('Invalid usage increment order')
        previous = line; stamp(increment['time']); counters(increment['values'])
        if increment['values']['total_tokens'] == 0:
            raise ValueError('Duplicate or empty usage increment')
        for key in FIELDS:
            value = increment['values'][key]
            totals[key] = None if totals[key] is None or value is None else totals[key] + value
    for key in FIELDS:
        if u['measurements'][key] is not None and totals[key] != u['measurements'][key]:
            raise ValueError('Usage does not match recorded increments')


def validate_event(row):
    from trace_store import ident, stamp
    detail = row.get('detail')
    if not isinstance(detail, dict) or set(detail) != {'version', 'task_title', 'usage'} or type(detail['version']) is not int or detail['version'] != 1:
        raise ValueError('Invalid usage event')
    if not isinstance(detail['task_title'], str) or not 1 <= len(detail['task_title'].strip()) <= 200:
        raise ValueError('Invalid usage task title')
    u = detail['usage']; validate_usage(u); ident(row.get('actor_id'))
    if (row['id'] != event_id(u['measurement_key']) or row['request_id'] != u['request_id'] or
            row['source'] != 'trace.native-usage' or row['source_id'] != u['measurement_key'] or
            row['time'] != stamp(u['interval']['finished_at'])):
        raise ValueError('Usage event binding mismatch')


def attributed(usage):
    return (isinstance(usage, dict) and usage.get('status') == 'measured' and
            usage.get('scope') == 'dispatch' and usage.get('task_attributed') is True)


def physical_span(usage):
    key, session = usage.get('measurement_key'), usage.get('session_id')
    if isinstance(key, str) and key.startswith('codex.session:') and ':turn:' in key:
        if session is None:
            encoded = key[len('codex.session:'):]
            if encoded.count(':turn:') != 1:
                raise ValueError('Ambiguous legacy physical session needs reconciliation')
            session = encoded.split(':turn:', 1)[0]
        elif not isinstance(session, str) or not key.startswith('codex.session:' + session + ':turn:'):
            raise ValueError('Conflicting legacy physical session metadata')
    if session is None:
        return None, None
    if not isinstance(session, str) or not session:
        raise ValueError('Invalid legacy physical session')
    interval = usage.get('interval')
    if not isinstance(interval, dict):
        return session, None
    start, end = interval.get('start_line'), interval.get('end_line')
    if type(start) is not int or type(end) is not int or not 1 <= start < end <= MAX_SAFE:
        return session, None
    return session, (start, end)


def reject_physical_conflict(a, b):
    if isinstance(a.get('measurement_key'), str) and a['measurement_key'] == b.get('measurement_key'):
        raise ValueError('Physical usage key already belongs to another actor')
    if isinstance(a.get('dispatch_id'), str) and a['dispatch_id'] == b.get('dispatch_id'):
        raise ValueError('Dispatch already has a different usage observation')
    sa, ia = physical_span(a); sb, ib = physical_span(b)
    if sa is not None and sa == sb:
        if ia is None or ib is None:
            raise ValueError('Legacy physical interval needs explicit reconciliation')
        if max(ia[0], ib[0]) <= min(ia[1], ib[1]):
            raise ValueError('Usage intervals overlap in the same session')


def validate_owner_legacy(legacy, usage):
    if not attributed(legacy):
        return
    if legacy.get('measurement_key') != usage['measurement_key'] or legacy.get('measurements') != usage['measurements']:
        raise ValueError('Legacy attributed actor usage needs explicit reconciliation')
    session, interval = physical_span(legacy)
    if session is not None and (session != usage['session_id'] or interval is not None and
            interval != (usage['interval']['start_line'], usage['interval']['end_line'])):
        raise ValueError('Legacy physical interval needs explicit reconciliation')


def validate_legacy_actor(store, row):
    """Check legacy imports in either order, including different keys/owners."""
    legacy = row.get('usage', {})
    if not attributed(legacy):
        return
    physical_span(legacy)
    for (body,) in store.db.execute("SELECT body FROM records WHERE kind='event' AND json_extract(body,'$.kind')='usage'"):
        event = json.loads(body); usage = event['detail']['usage']
        if event['actor_id'] == row['id']:
            validate_owner_legacy(legacy, usage)
        else:
            reject_physical_conflict(legacy, usage)
    for (body,) in store.db.execute("SELECT body FROM records WHERE kind='actor' AND id!=?", (row['id'],)):
        other = json.loads(body).get('usage', {})
        if attributed(other):
            reject_physical_conflict(legacy, other)


def validate_store(store, row):
    """Run inside the import transaction, including generic bundle/JSONL restores."""
    u = row['detail']['usage']
    others = store.db.execute("SELECT body FROM records WHERE kind='event' AND "
                             "json_extract(body,'$.kind')='usage' AND id!=?", (row['id'],)).fetchall()
    for (body,) in others:
        other = json.loads(body)['detail']['usage']
        reject_physical_conflict(other, u)
    actor = json.loads(store.db.execute("SELECT body FROM records WHERE kind='actor' AND id=?", (row['actor_id'],)).fetchone()[0])
    legacy = actor.get('usage', {})
    validate_owner_legacy(legacy, u)
    for (body,) in store.db.execute("SELECT body FROM records WHERE kind='actor' AND id!=?", (row['actor_id'],)):
        other_actor = json.loads(body); prior = other_actor.get('usage', {})
        if attributed(prior):
            reject_physical_conflict(prior, u)


def validate_evidence(store, row):
    key = row.get('measurement_key')
    if not isinstance(key, str) or row.get('source_id') != key:
        raise ValueError('Invalid usage evidence key')
    found = store.db.execute("SELECT body FROM records WHERE kind='event' AND id=?", (event_id(key),)).fetchone()
    if not found:
        raise ValueError('Usage evidence has no observation')
    observation = json.loads(found[0])
    if observation['request_id'] != row['request_id'] or observation['actor_id'] != row.get('actor_id'):
        raise ValueError('Usage evidence binding mismatch')
    if not isinstance(row.get('input_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', row['input_sha256']):
        raise ValueError('Invalid usage input hash')
    if not isinstance(row.get('input_path'), str) or len(row['input_path']) > 4096:
        raise ValueError('Invalid usage input path')


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('Duplicate usage JSON key')
        result[key] = value
    return result


def invalid_number(_):
    raise ValueError('Invalid usage JSON number')


def import_usage(store, filename, actor_id=None, task_title=None):
    from trace_store import ident, stamp
    path = Path(filename).resolve(strict=True)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode): raise ValueError('Usage input must be a regular file')
        raw = stream.read(8 * 1024 * 1024 + 1); after = os.fstat(stream.fileno())
    fingerprint = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if len(raw) > 8 * 1024 * 1024 or fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(path.stat()):
        raise ValueError('Usage input oversized or changed while reading')
    record = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_number)
    if not isinstance(record, dict): raise ValueError('Invalid usage input')
    u = record.get('dispatch_usage'); validate_usage(u)
    owner = ident(actor_id if actor_id is not None else u['actor_id'])
    detail = dict(version=1, task_title=task_title if task_title is not None else u['task_id'], usage=copy.deepcopy(u))
    event = dict(id=event_id(u['measurement_key']), request_id=u['request_id'], actor_id=owner, kind='usage',
                 source='trace.native-usage', source_id=u['measurement_key'], time=stamp(u['interval']['finished_at']),
                 completeness='partial', detail=detail)
    # Only explicit input is opened. Native source paths are preserved as references, never followed.
    source = record.get('source', {}); origin = {}
    if not isinstance(source, dict): raise ValueError('Invalid usage source metadata')
    for key in ('path', 'sha256'):
        if key in source:
            if not isinstance(source[key], str) or len(source[key]) > 4096: raise ValueError('Invalid usage source metadata')
            if key == 'sha256' and not re.fullmatch('[0-9a-f]{64}', source[key]): raise ValueError('Invalid usage source hash')
            origin[key] = source[key]
    evidence = dict(request_id=u['request_id'], source='trace.native-usage-evidence', source_id=u['measurement_key'],
                    time=stamp(record.get('recorded_at')), completeness='partial', actor_id=owner,
                    measurement_key=u['measurement_key'], input_path=str(path),
                    input_sha256=hashlib.sha256(raw).hexdigest(), native_source=origin,
                    actor_mapping='explicit' if actor_id is not None else 'native_dispatch_actor')
    store.import_bundle(dict(schema_version=1, requests=[], actors=[], events=[event], collections=[evidence]))
    return dict(event_id=event['id'], request_id=u['request_id'], actor_id=owner,
                measurement_key=u['measurement_key'])
