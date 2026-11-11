"""Explicit request closeout over existing trace records; no runtime calls."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

TERMINAL = {'completed', 'finished', 'succeeded', 'failed', 'cancelled'}
OUTCOMES = {'completed', 'needs_followup', 'cancelled'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def trace_digest(bundle):
    # Export order and derived presentation fields do not change source identity.
    return sha({key: sorted((row for row in bundle[key]
                            if key != 'events' or row.get('kind') != 'completion'), key=canonical)
                for key in ('requests', 'actors', 'events', 'collections')})


def text(value, limit=16000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('Invalid closeout text')
    return value


def validate_detail(detail):
    from trace_store import ident
    if not isinstance(detail, dict) or type(detail.get('version')) is not int or detail['version'] != 1:
        raise ValueError('Invalid closeout version')
    if detail.get('status') not in OUTCOMES or detail.get('verification') not in {'pass', 'fail', 'unverified'}:
        raise ValueError('Invalid closeout outcome')
    if detail['status'] == 'completed' and detail['verification'] != 'pass':
        raise ValueError('Completed closeout requires explicit verification pass')
    text(detail.get('summary'))
    for key in ('trace_sha256', 'manifest_sha256'):
        if not isinstance(detail.get(key), str) or not re.fullmatch('[a-f0-9]{64}', detail[key]):
            raise ValueError('Invalid closeout digest')
    assignments = detail.get('assignments')
    if not isinstance(assignments, list) or len(assignments) > 200:
        raise ValueError('Invalid closeout assignments')
    if any(not isinstance(row, dict) or set(row) - {'actor_id', 'previous_actor_id'} for row in assignments):
        raise ValueError('Invalid closeout assignment')
    ids = [ident(row['actor_id']) for row in assignments]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate closeout actor')
    edges = {}
    for row in assignments:
        previous = row.get('previous_actor_id')
        if previous is not None and (previous not in ids or previous == row['actor_id']):
            raise ValueError('Unknown closeout predecessor')
        edges[row['actor_id']] = previous
    for actor in ids:
        seen = set()
        while actor is not None:
            if actor in seen:
                raise ValueError('Cyclic closeout assignments')
            seen.add(actor)
            actor = edges[actor]
    artifacts = detail.get('artifacts')
    if not isinstance(artifacts, list) or len(artifacts) > 32:
        raise ValueError('Invalid closeout artifacts')
    for row in artifacts:
        if not isinstance(row, dict) or row.get('kind') not in {'result', 'verification', 'other'}:
            raise ValueError('Invalid artifact kind')
        text(row.get('label'), 200); text(row.get('path'), 4096)
        if not isinstance(row.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', row['sha256']):
            raise ValueError('Invalid artifact hash')
        if type(row.get('bytes')) is not int or row['bytes'] < 0:
            raise ValueError('Invalid artifact size')
    if detail['status'] == 'completed' and not {'result', 'verification'} <= {r['kind'] for r in artifacts}:
        raise ValueError('Completed closeout needs result and verification artifacts')
    if not isinstance(detail.get('limitations'), list) or len(detail['limitations']) > 32:
        raise ValueError('Invalid closeout limitations')
    for value in detail['limitations']: text(value, 2000)


def closeout_view(bundle):
    records = [row for row in bundle['events'] if row.get('kind') == 'completion']
    if not records:
        return None
    latest = max(records, key=lambda row: (row['time'], row['id']))
    return {'event_id': latest['id'], 'time': latest['time'], 'detail': latest['detail'],
            'current': latest['detail']['trace_sha256'] == trace_digest(bundle)}


def open_artifact(path):
    """Open each component relative to its parent fd, never following a link."""
    if not path.is_absolute() or '..' in path.parts or not path.name:
        raise ValueError('Artifact must be an absolute path without parent traversal')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, flags, dir_fd=parent)
            os.close(parent)
            parent = child
        return os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)


def artifact(row):
    if not isinstance(row, dict) or set(row) - {'kind', 'label', 'path', 'sha256'}:
        raise ValueError('Invalid artifact input')
    path = Path(text(row.get('path'), 4096))
    fd = open_artifact(path)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 256 * 1024 * 1024:
            raise ValueError('Artifact must be a regular file at most 256 MiB')
        digest = hashlib.sha256()
        total = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            total += len(chunk)
            if total > 256 * 1024 * 1024:
                raise ValueError('Artifact grew beyond size limit')
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('Artifact changed while reading')
        # Rewalk without links: a renamed parent must not leave a stale path
        # paired with a hash from a formerly reachable file.
        current = open_artifact(path)
        try:
            found = os.fstat(current)
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            if any(getattr(found, key) != getattr(after, key) for key in fields):
                raise ValueError('Artifact path changed while reading')
        finally:
            os.close(current)
    result = dict(row, path=str(path), sha256=digest.hexdigest(), bytes=after.st_size)
    if 'sha256' in row and row['sha256'] != result['sha256']:
        raise ValueError('Artifact digest mismatch')
    return result


def close_request(store, manifest):
    from trace_store import ident, stamp
    allowed = {'schema_version', 'id', 'request_id', 'coordinator_id', 'time', 'status',
               'summary', 'verification', 'assignments', 'artifacts', 'limitations'}
    if not isinstance(manifest, dict) or set(manifest) != allowed or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1:
        raise ValueError('Invalid closeout manifest')
    rid = ident(manifest['request_id']); cid = ident(manifest['coordinator_id'])
    event_id = 'closeout:' + sha([rid, ident(manifest['id'])])
    when = stamp(manifest['time'])
    if when is None:
        raise ValueError('Closeout requires explicit recording time')
    assignments = manifest['assignments']
    if not isinstance(assignments, list) or any(not isinstance(r, dict) or set(r) - {'actor_id', 'previous_actor_id'} for r in assignments):
        raise ValueError('Invalid closeout assignment input')
    if not isinstance(manifest['artifacts'], list) or len(manifest['artifacts']) > 32:
        raise ValueError('Invalid closeout artifact input')
    detail = {key: manifest[key] for key in ('status', 'summary', 'verification', 'assignments', 'limitations')}
    detail.update(version=1, manifest_sha256=sha(manifest), artifacts=[artifact(row) for row in manifest['artifacts']],
                  trace_sha256='0' * 64, recorded_retrospectively=True, process_exit='not_evaluated')
    validate_detail(detail)
    store.db.execute('BEGIN IMMEDIATE')
    try:
        bundle = store.request(rid)
        if bundle is None:
            raise ValueError('Import the request and actors before closeout')
        actors = {a['id']: a for a in bundle['actors']}
        if cid not in actors or actors[cid].get('parent_id') is not None:
            raise ValueError('Closeout requires an existing root coordinator')
        if {r['actor_id'] for r in assignments} != set(actors) - {cid}:
            raise ValueError('Closeout must cover every imported worker exactly once')
        if manifest['status'] == 'completed' and any(a.get('status') not in TERMINAL for a in actors.values()):
            raise ValueError('Completed closeout has unfinished actor reports')
        detail['trace_sha256'] = trace_digest(bundle)
        detail['recording_status'] = 'partial' if any(c.get('completeness') == 'partial' for c in bundle['collections']) else 'unknown'
        detail['last_collection_time'] = max((stamp(c['time']) for c in bundle['collections'] if c.get('time')), default=None)
        detail['reported_terminal_actors'] = sum(a.get('status') in TERMINAL for a in actors.values())
        detail['covered_actors'] = len(actors)
        event = dict(id=event_id, request_id=rid, actor_id=cid, kind='completion', time=when,
                     source='trace.closeout', source_id=manifest['id'], completeness='partial', detail=detail)
        previous = next((e for e in bundle['events'] if e['id'] == event_id), None)
        if previous:
            if {k: v for k, v in previous.items() if k != 'detail_sha256'} != event:
                raise ValueError('Closeout changed; use a new revision after reviewing the new evidence')
            store.db.commit()
            return {'event_id': event_id, 'replay': True}
        latest = closeout_view(bundle)
        if latest and when <= latest['time']:
            raise ValueError('A new closeout must have a later recording time')
        store.import_bundle({'schema_version': 1, 'requests': [], 'actors': [], 'events': [event]})
        store.db.commit()
        return {'event_id': event_id, 'replay': False}
    except BaseException:
        store.db.rollback()
        raise
