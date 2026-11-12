"""Explicit attribution for finalized command and verification observations."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

SOURCE = 'trace.attributed-observation.v1'
INPUT_SOURCE = 'trace.observation-input.v1'
ROLES = ('recorded_by_actor_id', 'executor_actor_id', 'product_owner_actor_id',
         'verification_subject_actor_id', 'verifier_actor_id')
KINDS = {'tool_call', 'tool_result', 'verification'}
MAX_BYTES = 1024 * 1024


def uses_contract(row):
    source = row.get('source')
    return ('attribution' in row or isinstance(source, str) and
            (source == 'trace.attributed-observation' or source.startswith('trace.attributed-observation.')))


def uses_input_contract(row):
    source = row.get('source')
    return isinstance(source, str) and (source == 'trace.observation-input' or source.startswith('trace.observation-input.'))


def reject_nonfinite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Non-finite observation JSON number')
    if isinstance(value, dict):
        for item in value.values(): reject_nonfinite(item)
    elif isinstance(value, (list, tuple)):
        for item in value: reject_nonfinite(item)


def attribution_actor(kind, attribution):
    from trace_store import ident
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError('Unsupported attributed observation kind')
    if (not isinstance(attribution, dict) or set(attribution) != {'version', *ROLES} or
            type(attribution['version']) is not int or attribution['version'] != 1):
        raise ValueError('Invalid observation attribution')
    for role in ROLES:
        if attribution[role] is not None:
            ident(attribution[role])
    ident(attribution['recorded_by_actor_id'])
    if kind in {'tool_call', 'tool_result'}:
        return ident(attribution['executor_actor_id'])
    if kind == 'verification':
        if attribution['executor_actor_id'] is not None:
            raise ValueError('Verification verdict is not a command execution')
        ident(attribution['verification_subject_actor_id'])
        return ident(attribution['verifier_actor_id'])
    raise ValueError('Unsupported attributed observation kind')


def validate_event(row):
    from trace_store import ident
    reject_nonfinite(row)
    actor = attribution_actor(row.get('kind'), row.get('attribution'))
    if (row.get('source') != SOURCE or row.get('source_id') != row.get('id') or
            row.get('completeness') != 'partial' or row.get('actor_id') != actor):
        raise ValueError('Observation executor/verifier binding mismatch')
    ident(row.get('id')); ident(row.get('request_id'))
    detail = row.get('detail')
    if not isinstance(detail, dict):
        raise ValueError('Observation detail must be an object')
    if row['kind'] == 'tool_call':
        if (not isinstance(detail.get('name'), str) or not detail['name'].strip() or
                not isinstance(detail.get('input'), dict) or detail.get('state') != 'completed' or
                type(detail.get('exitCode')) is not int):
            raise ValueError('Attributed command must be finalized with an exit code')
    elif row['kind'] == 'tool_result':
        if (type(detail.get('exitCode')) is not int or
                not all(isinstance(detail.get(k), str) for k in ('stdout', 'stderr'))):
            raise ValueError('Invalid finalized command result')
    elif (not isinstance(detail.get('verdict'), str) or detail['verdict'] not in {'pass', 'fail', 'unverified'} or
          not isinstance(detail.get('summary'), str) or not detail['summary'].strip()):
        raise ValueError('Invalid verification verdict')


def validate_references(store, row):
    for actor in {v for k, v in row['attribution'].items() if k in ROLES and v is not None}:
        if not store.db.execute("SELECT 1 FROM records WHERE kind='actor' AND id=? AND request_id=?",
                                (actor, row['request_id'])).fetchone():
            raise ValueError('Unknown or cross-request observation actor')


def build_event(payload):
    from trace_store import ident, stamp
    fields = {'schema_version', 'id', 'request_id', 'kind', 'time', 'recorded_at', 'attribution', 'detail'}
    if (not isinstance(payload, dict) or set(payload) != fields or
            type(payload['schema_version']) is not int or payload['schema_version'] != 1):
        raise ValueError('Invalid observation input')
    ident(payload['id']); ident(payload['request_id']); stamp(payload['recorded_at'])
    actor = attribution_actor(payload['kind'], payload['attribution'])
    row = dict(id=payload['id'], request_id=payload['request_id'], kind=payload['kind'],
               actor_id=actor, time=stamp(payload['time']), source=SOURCE, source_id=payload['id'],
               completeness='partial', attribution=copy.deepcopy(payload['attribution']), detail=copy.deepcopy(payload['detail']))
    validate_event(row)
    return row


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate observation JSON key')
        result[key] = value
    return result


def invalid_number(_):
    raise ValueError('Non-finite observation JSON number')


def finite_float(value):
    number = float(value)
    reject_nonfinite(number)
    return number


def import_observation(store, filename):
    from trace_store import stamp
    path = Path(filename).resolve(strict=True)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            raise ValueError('Observation input must be a regular file at most 1 MiB')
        raw = stream.read(MAX_BYTES + 1); after = os.fstat(stream.fileno())
    fingerprint = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if len(raw) > MAX_BYTES or fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(path.stat()):
        raise ValueError('Observation input changed or oversized')
    payload = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_number, parse_float=finite_float)
    event = build_event(payload)
    evidence = dict(request_id=event['request_id'], source=INPUT_SOURCE, source_id=event['id'],
                    actor_id=event['attribution']['recorded_by_actor_id'], time=stamp(payload['recorded_at']),
                    completeness='partial', input_path=str(path), input_sha256=hashlib.sha256(raw).hexdigest())
    store.import_bundle(dict(schema_version=1, requests=[], actors=[], events=[event], collections=[evidence]))
    return dict(event_id=event['id'], actor_id=event['actor_id'], recorded_by_actor_id=evidence['actor_id'])


def validate_input(row):
    from trace_store import ident
    reject_nonfinite(row)
    if row.get('source') != INPUT_SOURCE or row.get('completeness') != 'partial':
        raise ValueError('Unsupported observation input source/version')
    ident(row.get('source_id'))
    ident(row.get('request_id')); ident(row.get('actor_id'))
    if (not isinstance(row.get('input_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', row['input_sha256']) or
            not isinstance(row.get('input_path'), str) or len(row['input_path']) > 4096 or
            not Path(row['input_path']).is_absolute()):
        raise ValueError('Invalid observation input provenance')


def validate_evidence(store, row):
    validate_input(row)
    found = store.db.execute("SELECT body FROM records WHERE kind='event' AND id=?", (row['source_id'],)).fetchone()
    if not found:
        raise ValueError('Observation input has no event')
    event = json.loads(found[0])
    if (event.get('source') != SOURCE or event['request_id'] != row['request_id'] or
            event['attribution']['recorded_by_actor_id'] != row.get('actor_id') or
            row.get('completeness') != 'partial'):
        raise ValueError('Observation input binding mismatch')
