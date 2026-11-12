"""Additive, interval-bound model/effort evidence; never overwrite actor fields."""
import copy
import hashlib
import json
from pathlib import Path
import re

from native_record import label
from trace_observation import reject_nonfinite, validate_input as observation_input

SOURCE = 'trace.native-profile.v1'
EVIDENCE = 'trace.native-profile-evidence.v1'


def finite(value):
    try: reject_nonfinite(value)
    except RecursionError: raise ValueError('Profile JSON too deep') from None


def uses_contract(row):
    source = row.get('source')
    return row.get('kind') == 'profile' or isinstance(source, str) and (
        source == 'trace.native-profile' or source.startswith('trace.native-profile.'))


def uses_evidence(row):
    source = row.get('source')
    return isinstance(source, str) and (source == 'trace.native-profile-evidence' or source.startswith('trace.native-profile-evidence.'))


def profile_id(detail):
    from trace_store import digest
    return 'profile:' + digest(detail)


def stage(value, name):
    finite(value)
    if (not isinstance(value, dict) or set(value) != {'model', 'effort', 'status', 'source'} or
            value['status'] not in ('observed', 'unavailable')):
        raise ValueError('Invalid profile stage')
    for key in ('model', 'effort'):
        if value[key] is not None and label(value[key]) is None: raise ValueError('Invalid model/effort')
    if value['status'] == 'unavailable':
        if any(value[k] is not None for k in ('model', 'effort', 'source')): raise ValueError('Unknown stage must stay unknown')
    else:
        source = value['source']
        if (not isinstance(source, dict) or set(source) != {'kind', 'path', 'sha256'} or
                source['kind'] != 'orca.worker-show.launch.' + name or
                not isinstance(source['path'], str) or len(source['path']) > 4096 or not Path(source['path']).is_absolute() or
                not isinstance(source['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', source['sha256'])):
            raise ValueError('Invalid launch profile provenance')


def validate_event(row):
    from trace_store import ident, stamp
    from trace_usage import integer
    finite(row)
    detail = row.get('detail')
    if (row.get('kind') != 'profile' or row.get('source') != SOURCE or row.get('completeness') != 'partial' or
            not isinstance(detail, dict) or set(detail) != {'version', 'usage_event_id', 'measurement_key', 'interval', 'requested', 'effective', 'contexts'} or
            type(detail.get('version')) is not int or detail['version'] != 1):
        raise ValueError('Invalid native profile event')
    ident(row.get('id')); ident(row.get('request_id')); ident(row.get('actor_id')); ident(detail['usage_event_id'])
    if row['id'] != profile_id(detail) or row.get('source_id') != detail['measurement_key']:
        raise ValueError('Profile identity mismatch')
    stage(detail['requested'], 'requested'); stage(detail['effective'], 'effective')
    interval = detail['interval']
    if not isinstance(interval, dict) or not isinstance(detail['contexts'], list) or len(detail['contexts']) > 100000:
        raise ValueError('Invalid profile interval/contexts')
    start, end = integer(interval.get('start_line'), 1), integer(interval.get('end_line'), 1)
    if start >= end: raise ValueError('Invalid profile interval order')
    previous = start
    for context in detail['contexts']:
        if not isinstance(context, dict) or set(context) != {'line', 'time', 'model', 'effort'}:
            raise ValueError('Invalid profile context')
        line = integer(context['line'], 1); stamp(context['time'])
        if not previous < line < end: raise ValueError('Profile context outside native interval')
        previous = line
        for key in ('model', 'effort'):
            if context[key] is not None and label(context[key]) is None: raise ValueError('Invalid observed profile value')


def validate_store(store, row):
    from trace_usage import event_id
    detail = row['detail']
    found = store.db.execute("SELECT body FROM records WHERE kind='event' AND id=?", (detail['usage_event_id'],)).fetchone()
    if not found: raise ValueError('Profile has no usage interval')
    usage_event = json.loads(found[0]); usage = usage_event.get('detail', {}).get('usage', {})
    if (usage_event.get('kind') != 'usage' or usage_event['request_id'] != row['request_id'] or
            usage_event['actor_id'] != row['actor_id'] or detail['usage_event_id'] != event_id(detail['measurement_key']) or
            usage.get('measurement_key') != detail['measurement_key'] or usage.get('interval') != detail['interval'] or
            row['time'] != usage_event['time']):
        raise ValueError('Profile usage/owner/interval mismatch')


def validate_evidence_input(row):
    finite(row)
    if row.get('source') != EVIDENCE: raise ValueError('Unsupported profile evidence source/version')
    translated = dict(row, source='trace.observation-input.v1'); observation_input(translated)


def validate_evidence(store, row):
    validate_evidence_input(row)
    found = store.db.execute("SELECT body FROM records WHERE kind='event' AND id=?", (row['source_id'],)).fetchone()
    if not found: raise ValueError('Profile evidence has no observation')
    event = json.loads(found[0]); validate_event(event)
    if event['request_id'] != row['request_id'] or event['actor_id'] != row['actor_id']:
        raise ValueError('Profile evidence binding mismatch')


def build(record, usage_event, filename, raw):
    """Only the explicitly imported native metadata is read; no path is followed."""
    from trace_store import stamp
    from trace_usage import integer
    if 'observed_contexts' not in record: return None
    usage = usage_event['detail']['usage']
    session = record.get('session'); binding = record.get('binding')
    if (record.get('kind') != 'orca_codex_metadata' or usage['scope'] != 'dispatch' or not isinstance(session, dict) or
            session.get('id') != usage['session_id'] or session.get('source') != 'vscode' or session.get('originator') != 'orca_desktop' or
            not isinstance(binding, dict) or binding.get('task_id') != usage['task_id'] or binding.get('dispatch_id') != usage['dispatch_id']):
        raise ValueError('Unsupported or mismatched profile metadata')
    contexts = record['observed_contexts']; finite(contexts)
    if not isinstance(contexts, list) or len(contexts) > 100000: raise ValueError('Invalid source profile contexts')
    interval = usage['interval']; selected = []
    for context in contexts:
        if not isinstance(context, dict) or not isinstance(context.get('values'), dict): raise ValueError('Invalid source profile context')
        line = integer(context.get('line'), 1)
        if interval['start_line'] < line < interval['end_line']:
            selected.append(dict(line=line, time=stamp(context.get('time')), **{k:context['values'].get(k) for k in ('model', 'effort')}))
    unknown = dict(model=None, effort=None, source=None, status='unavailable')
    detail = dict(version=1, usage_event_id=usage_event['id'], measurement_key=usage['measurement_key'],
                  interval=copy.deepcopy(interval), requested=copy.deepcopy(record.get('requested', unknown)),
                  effective=copy.deepcopy(record.get('launch_effective', unknown)), contexts=selected)
    event = dict(id=profile_id(detail), request_id=usage_event['request_id'], actor_id=usage_event['actor_id'], kind='profile',
                 source=SOURCE, source_id=usage['measurement_key'], time=usage_event['time'], completeness='partial', detail=detail)
    validate_event(event)
    evidence = dict(request_id=event['request_id'], actor_id=event['actor_id'], source=EVIDENCE, source_id=event['id'],
                    time=stamp(record.get('recorded_at')), completeness='partial', input_path=str(filename), input_sha256=hashlib.sha256(raw).hexdigest())
    return event, evidence
