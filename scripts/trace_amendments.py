"""Append-only observation corrections and logical groups of measured usage.

Physical measurements are immutable. Groups reference them; revisions replace only
the effective view of a typed observation or a group within the same request.
"""
import copy
import hashlib
import json
import re

from dispatch_usage import FIELDS, ROLE_IDS
from trace_observation import (SOURCE as OBSERVATION_SOURCE, read_input, reject_nonfinite,
                               validate_event as observation_event)
from trace_usage import integer

SOURCES = {'revision': 'trace.event-revision.v1', 'usage_group': 'trace.usage-group.v1'}
INPUT_SOURCE = 'trace.amendment-input.v1'
EVENT_FIELDS = {'id', 'request_id', 'kind', 'actor_id', 'time', 'source', 'source_id',
                'completeness', 'detail', 'detail_sha256'}


def uses_contract(row):
    source = row.get('source')
    return row.get('kind') in SOURCES or isinstance(source, str) and any(
        source == s.rsplit('.', 1)[0] or source.startswith(s.rsplit('.', 1)[0] + '.') for s in SOURCES.values())


def uses_input(row):
    source = row.get('source')
    return isinstance(source, str) and (source == 'trace.amendment-input' or source.startswith('trace.amendment-input.'))


def canonical_target(row):
    from trace_store import digest, stamp
    result = copy.deepcopy(row)
    allowed = EVENT_FIELDS | ({'attribution'} if row.get('source') == OBSERVATION_SOURCE else set())
    if set(result) - allowed or allowed - {'detail_sha256'} - set(result):
        raise ValueError('Unsupported correction target fields')
    if result['source'] == OBSERVATION_SOURCE:
        observation_event(result)
    elif result['kind'] == 'usage_group':
        validate_event(result)
    else:
        raise ValueError('Only typed observations and usage groups can be corrected')
    result['time'] = stamp(result['time'])
    result['detail_sha256'] = digest(result['detail'])
    return result


def validate_event(row):
    from trace_store import ident, stamp
    reject_nonfinite(row)
    kind = row.get('kind'); detail = row.get('detail')
    if (not isinstance(kind, str) or kind not in SOURCES or row.get('source') != SOURCES[kind] or
            row.get('source_id') != row.get('id') or row.get('completeness') != 'partial' or
            set(row) - EVENT_FIELDS or not isinstance(detail, dict)):
        raise ValueError('Unsupported amendment kind/source/fields')
    ident(row.get('id')); ident(row.get('request_id')); ident(row.get('actor_id')); stamp(row.get('time'))
    if type(detail.get('version')) is not int or detail['version'] != 1:
        raise ValueError('Unsupported amendment version')
    ident(detail.get('recorded_by_actor_id'))
    if kind == 'usage_group':
        if set(detail) != {'version', 'recorded_by_actor_id', 'task_id', 'title', 'role', 'measurement_event_ids'}:
            raise ValueError('Invalid usage group fields')
        ident(detail['task_id'])
        if not isinstance(detail['role'], str) or detail['role'] not in ROLE_IDS:
            raise ValueError('Invalid usage group role')
        if not isinstance(detail['title'], str) or not 1 <= len(detail['title'].strip()) <= 200:
            raise ValueError('Invalid usage group title')
        members = detail['measurement_event_ids']
        if not isinstance(members, list) or not 1 <= len(members) <= 1000:
            raise ValueError('Invalid usage group members')
        for member in members: ident(member)
        if len(set(members)) != len(members): raise ValueError('Duplicate usage group member')
    else:
        if set(detail) != {'version', 'recorded_by_actor_id', 'target_event_id', 'previous_revision_id',
                           'expected_event_sha256', 'reason', 'replacement'}:
            raise ValueError('Invalid revision fields')
        ident(detail['target_event_id'])
        if detail['previous_revision_id'] is not None: ident(detail['previous_revision_id'])
        if (not isinstance(detail['expected_event_sha256'], str) or
                not re.fullmatch('[0-9a-f]{64}', detail['expected_event_sha256']) or
                not isinstance(detail['reason'], str) or not 1 <= len(detail['reason'].strip()) <= 2000 or
                row['actor_id'] != detail['recorded_by_actor_id'] or not isinstance(detail['replacement'], dict)):
            raise ValueError('Invalid revision authority/hash/reason')
        replacement = canonical_target(detail['replacement'])
        if replacement != detail['replacement']:
            raise ValueError('Revision replacement must be canonical, including detail hash')
        if replacement['id'] != detail['target_event_id'] or replacement['request_id'] != row['request_id']:
            raise ValueError('Revision target/request mismatch')


def project(bundle):
    """Validate chains/references independent of import order and return a view."""
    from trace_store import digest
    events = {e['id']: e for e in bundle['events']}
    actors = {a['id']: a for a in bundle['actors']}
    chains = {}; effective = dict(events)

    def root_actor(actor_id):
        actor = actors.get(actor_id)
        if actor is None or actor.get('parent_id') is not None:
            raise ValueError('Amendment recorder must be a same-request root actor')

    for row in events.values():
        if uses_contract(row):
            validate_event(row); root_actor(row['detail']['recorded_by_actor_id'])
        if row['kind'] == 'revision':
            target = row['detail']['target_event_id']
            chains.setdefault(target, []).append(row)
    histories = {}
    for target, revisions in chains.items():
        if target not in events: raise ValueError('Unknown revision target')
        original = events[target]; current = canonical_target(original)
        children = {}
        for row in revisions:
            previous = row['detail']['previous_revision_id']
            if previous in children: raise ValueError('Revision fork or stale predecessor')
            children[previous] = row
        history = []; previous = None
        while previous in children:
            row = children.pop(previous); detail = row['detail']
            if detail['expected_event_sha256'] != digest(current):
                raise ValueError('Revision expected hash mismatch')
            replacement = detail['replacement']
            for key in ('id', 'request_id', 'kind', 'source', 'source_id', 'completeness'):
                if replacement[key] != original[key]: raise ValueError('Revision changes immutable identity')
            if replacement['actor_id'] not in actors: raise ValueError('Unknown replacement actor')
            if replacement['source'] == OBSERVATION_SOURCE:
                for key, actor in replacement['attribution'].items():
                    if key != 'version' and actor is not None and actor not in actors:
                        raise ValueError('Unknown or cross-request replacement attribution')
            current = replacement; history.append(row); previous = row['id']
        if children or len(history) != len(revisions): raise ValueError('Broken or cyclic revision chain')
        histories[target] = history; effective[target] = current
    groups = []; claimed = set(); tasks = set()
    for row in effective.values():
        if row['kind'] != 'usage_group': continue
        detail = row['detail']; root_actor(detail['recorded_by_actor_id'])
        task = (row['actor_id'], detail['role'], detail['task_id'])
        if task in tasks: raise ValueError('Duplicate logical usage task')
        tasks.add(task); totals = dict.fromkeys(FIELDS, 0)
        for member in detail['measurement_event_ids']:
            usage_event = events.get(member)
            if (not usage_event or usage_event['kind'] != 'usage' or usage_event['actor_id'] != row['actor_id'] or
                    usage_event['detail']['usage']['role'] != detail['role']):
                raise ValueError('Unknown, cross-owner or cross-role usage group member')
            if member in claimed: raise ValueError('Measurement already belongs to another effective group')
            claimed.add(member)
            for key in FIELDS:
                value = usage_event['detail']['usage']['measurements'][key]
                totals[key] = None if totals[key] is None or value is None else integer(totals[key] + value)
        groups.append(dict(event_id=row['id'], actor_id=row['actor_id'], **detail, measurements=totals))
    result = {}
    if histories:
        result.update(effective_events=[effective[e['id']] for e in bundle['events'] if e['kind'] != 'revision'],
                      event_revisions=histories)
    if groups: result['usage_groups'] = groups
    return result


def event_state(store, request_id, event_id):
    from trace_store import digest
    bundle = store.request(request_id)
    if bundle is None: raise ValueError('Unknown request')
    original = next((e for e in bundle['events'] if e['id'] == event_id), None)
    if original is None: raise ValueError('Unknown event')
    canonical_target(original)
    history = bundle.get('event_revisions', {}).get(event_id, [])
    current = history[-1]['detail']['replacement'] if history else original
    return dict(original=original, current=current, expected_event_sha256=digest(current),
                previous_revision_id=history[-1]['id'] if history else None)


def import_amendment(store, filename, kind):
    from trace_store import ident, stamp
    path, raw, payload = read_input(filename)
    if (not isinstance(payload, dict) or set(payload) != {'schema_version', 'id', 'request_id', 'actor_id', 'time', 'recorded_at', 'detail'} or
            type(payload['schema_version']) is not int or payload['schema_version'] != 1):
        raise ValueError('Invalid amendment input')
    ident(payload['id']); ident(payload['request_id'])
    row = dict(id=payload['id'], request_id=payload['request_id'], actor_id=payload['actor_id'],
               kind=kind, time=stamp(payload['time']), source=SOURCES[kind], source_id=payload['id'],
               completeness='partial', detail=payload['detail'])
    validate_event(row)
    evidence = dict(request_id=row['request_id'], source=INPUT_SOURCE, source_id=row['id'],
                    actor_id=row['detail']['recorded_by_actor_id'], time=stamp(payload['recorded_at']),
                    completeness='partial', input_path=str(path), input_sha256=hashlib.sha256(raw).hexdigest())
    store.import_bundle(dict(schema_version=1, requests=[], actors=[], events=[row], collections=[evidence]))
    return dict(event_id=row['id'], kind=kind)


def validate_input(row):
    from trace_observation import validate_input as observation_input
    translated = dict(row); translated['source'] = 'trace.observation-input.v1'
    observation_input(translated)
    if row.get('source') != INPUT_SOURCE: raise ValueError('Unsupported amendment input source/version')


def validate_evidence(store, row):
    validate_input(row)
    found = store.db.execute("SELECT body FROM records WHERE kind='event' AND id=? AND request_id=?",
                             (row['source_id'], row['request_id'])).fetchone()
    if not found: raise ValueError('Amendment input has no event')
    event = json.loads(found[0]); validate_event(event)
    if row['actor_id'] != event['detail']['recorded_by_actor_id']:
        raise ValueError('Amendment input recorder mismatch')
