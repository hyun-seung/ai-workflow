"""Opt-in, offline, allowlisted feedback summaries. Never transfers raw traces."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from dispatch_usage import ROLE_IDS
from run_records import validate_records_dir
from trace_store import Store, encoded, ident, private_db
from trace_usage import counters, integer, validate_event

MAX_BYTES = 1024 * 1024
APPS = ('orca', 'paseo', 'codex', 'other', 'unknown')
RESULTS = ('passed', 'failed', 'unknown')
REWORK = ('none', 'requirements', 'implementation', 'verification', 'environment', 'unknown')
HEX = re.compile(r'[0-9a-f]{64}')


def token(key, domain, value):
    return hmac.new(key, encoded([domain, value]).encode(), hashlib.sha256).hexdigest()


def private_file(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink paths are unsupported')
    validate_records_dir(path.parent, [Path(__file__).resolve().parents[1]])
    if not path.parent.exists():
        raise ValueError('Create a private output directory first (mode 0700)')
    return path


def read_private(path, limit=MAX_BYTES):
    path = private_file(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) & 0o177 or info.st_size > limit):
            raise ValueError('Input must be an owned regular file mode 0600 within size limit')
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Input exceeds size limit')
    return data


def write_private(path, data):
    path = private_file(path)
    if len(data) > MAX_BYTES:
        raise ValueError('Output exceeds 1 MiB')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink()
        raise


def read_key(path):
    key = read_private(path, 32)
    if len(key) != 32:
        raise ValueError('Key must contain exactly 32 random bytes')
    return key


def make_bundle(data, key, commit, app='unknown', result='unknown', rework='unknown', *, project_id):
    """Projection only; no free-text field or source identifier is copied."""
    if not data or len(data['requests']) != 1:
        raise ValueError('One existing request is required')
    ident(project_id)
    rid = data['requests'][0]['id']
    actors = []
    for a in data['actors']:
        observed = a.get('observed') or {}
        model = observed.get('model')
        effort = observed.get('effort')
        actors.append(dict(id=token(key, 'actor', [project_id, a['id']]),
                           role=a.get('role') if a.get('role') in ROLE_IDS else 'unknown',
                           model=token(key, 'model', model) if isinstance(model, str) and model else None,
                           effort=effort if effort in ('minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra') else None))
    observations = []
    for e in data['events']:
        if e.get('kind') != 'usage':
            continue
        validate_event(e)
        u = e['detail']['usage']
        if u['request_id'] != rid:
            raise ValueError('Usage request mismatch')
        observations.append(dict(id=token(key, 'measurement', u['measurement_key']),
                                 actor=token(key, 'actor', [project_id, e['actor_id']]),
                                 session=token(key, 'session', u['session_id']),
                                 start=u['interval']['start_line'], end=u['interval']['end_line'],
                                 source='codex.token_count.cumulative_delta.v1',
                                 tokens=u['measurements']))
    payload = dict(version=1, namespace=token(key, 'namespace', 'feedback-v1'),
                   project=token(key, 'project', project_id),
                   request=token(key, 'request', [project_id, rid]), workflow_commit=commit, app=app,
                   declared=dict(verification=result, rework=rework),
                   full_usage_complete=False, evidence='local-validation-summary-only',
                   actors=sorted(actors, key=lambda a: a['id']),
                   observations=sorted(observations, key=lambda o: o['id']))
    bundle = dict(payload=payload, sha256=hashlib.sha256(encoded(payload).encode()).hexdigest())
    validate_bundle(bundle)
    return bundle


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError('Unsupported feedback fields')


def fingerprint(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError('Invalid fingerprint')


def validate_bundle(bundle):
    exact(bundle, 'payload sha256')
    p = bundle['payload']
    exact(p, 'version namespace project request workflow_commit app declared full_usage_complete evidence actors observations')
    if type(p['version']) is not int or p['version'] != 1:
        raise ValueError('Unsupported feedback version')
    for value in (p['namespace'], p['project'], p['request'], bundle['sha256']):
        fingerprint(value)
    if not isinstance(p['workflow_commit'], str) or not re.fullmatch('[0-9a-f]{40}', p['workflow_commit']):
        raise ValueError('Workflow commit must be a full Git SHA1')
    if p['app'] not in APPS or p['full_usage_complete'] is not False or p['evidence'] != 'local-validation-summary-only':
        raise ValueError('Unsupported feedback provenance')
    exact(p['declared'], 'verification rework')
    if p['declared']['verification'] not in RESULTS or p['declared']['rework'] not in REWORK:
        raise ValueError('Invalid declared result')
    for name in ('actors', 'observations'):
        if not isinstance(p[name], list) or len(p[name]) > 1000:
            raise ValueError('Feedback list exceeds limit')
    actor_ids = set()
    for a in p['actors']:
        exact(a, 'id role model effort'); fingerprint(a['id'])
        if a['id'] in actor_ids or a['role'] not in ROLE_IDS | {'unknown'}:
            raise ValueError('Invalid or repeated actor')
        actor_ids.add(a['id'])
        if a['model'] is not None:
            fingerprint(a['model'])
        if a['effort'] is not None and a['effort'] not in ('minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
            raise ValueError('Invalid observed effort')
    seen = set(); spans = []
    for o in p['observations']:
        exact(o, 'id actor session start end source tokens')
        for key in ('id', 'actor', 'session'):
            fingerprint(o[key])
        integer(o['start'], 1); integer(o['end'], 1); counters(o['tokens'])
        if (o['id'] in seen or o['actor'] not in actor_ids or o['start'] >= o['end'] or
                o['source'] != 'codex.token_count.cumulative_delta.v1'):
            raise ValueError('Invalid observation binding')
        for old in spans:
            reject_overlap(old, o)
        seen.add(o['id']); spans.append(o)
    if hashlib.sha256(encoded(p).encode()).hexdigest() != bundle['sha256']:
        raise ValueError('Feedback digest mismatch')
    return p


def reject_overlap(a, b):
    if a['id'] == b['id'] or (a['session'] == b['session'] and
                             max(a['start'], b['start']) <= min(a['end'], b['end'])):
        raise ValueError('Duplicate or overlapping physical measurement')


def load_feedback(path):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError('Repeated JSON field')
            result[k] = v
        return result
    data = json.loads(read_private(path), object_pairs_hook=unique)
    validate_bundle(data)
    return data


def open_feedback(path, readonly=False):
    path = private_db(path, create=not readonly)
    db = sqlite3.connect(path.as_uri() + ('?mode=ro' if readonly else '?mode=rw'), uri=True)
    try:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if (version, tables) not in ((0, set()), (101, {'feedback'})) or readonly and version != 101:
            raise ValueError('Use a separate feedback DB, never the original trace DB')
        if not readonly and version == 0:
            db.execute('CREATE TABLE IF NOT EXISTS feedback(request TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('PRAGMA user_version=101'); db.commit()
        return db
    except BaseException:
        db.close()
        raise


def merge(path, bundle):
    # Validate before creating or opening a destination.
    p = validate_bundle(bundle)
    db = open_feedback(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            duplicate = False
            for (body,) in db.execute('SELECT body FROM feedback'):
                old = validate_bundle(json.loads(body))
                if old['namespace'] != p['namespace']:
                    raise ValueError('Different exchange keys require separate feedback DBs')
                if old['request'] == p['request']:
                    if encoded(old) != encoded(p):
                        raise ValueError('Request changed; preserve and review both files')
                    duplicate = True
                    continue
                for a in old['observations']:
                    for b in p['observations']:
                        reject_overlap(a, b)
            if not duplicate:
                db.execute('INSERT INTO feedback VALUES (?, ?)', (p['request'], encoded(bundle)))
        return {'imported': not duplicate, 'duplicate': duplicate}
    finally:
        db.close()


def summary(path):
    db = open_feedback(path, readonly=True)
    try:
        payloads = [validate_bundle(json.loads(row[0])) for row in db.execute('SELECT body FROM feedback ORDER BY request')]
        observations = [o for p in payloads for o in p['observations']]
        groups = {}
        for p in payloads:
            for a in p['actors']:
                key = (a['role'], a['model'], a['effort'])
                group = groups.setdefault(key, dict(role=a['role'], model=a['model'], effort=a['effort'],
                                                    actors=0, actors_with_measurements=0, measured_total_tokens=None))
                measured = [o for o in p['observations'] if o['actor'] == a['id']]
                group['actors'] += 1
                group['actors_with_measurements'] += bool(measured)
                if measured:
                    group['measured_total_tokens'] = (group['measured_total_tokens'] or 0) + sum(o['tokens']['total_tokens'] for o in measured)
        return dict(requests=len(payloads), actors=sum(len(p['actors']) for p in payloads),
                    actors_with_measurements=sum(len({o['actor'] for o in p['observations']}) for p in payloads),
                    observations=len(observations),
                    measured_total_tokens=sum(o['tokens']['total_tokens'] for o in observations) if observations else None,
                    full_usage_complete=False, evidence='local-validation-summary-only',
                    profiles=sorted(groups.values(), key=encoded),
                    results=[dict(request=p['request'], project=p['project'], workflow_commit=p['workflow_commit'], app=p['app'],
                                  declared=p['declared']) for p in payloads])
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init-key'); init.add_argument('--out', required=True)
    export = sub.add_parser('export')
    for arg in ('db', 'request-id', 'project-id', 'key', 'workflow-commit', 'out'):
        export.add_argument('--' + arg, required=True)
    export.add_argument('--app', choices=APPS, default='unknown')
    export.add_argument('--verification', choices=RESULTS, default='unknown')
    export.add_argument('--rework', choices=REWORK, default='unknown')
    imp = sub.add_parser('import'); imp.add_argument('--db', required=True); imp.add_argument('--file', required=True)
    show = sub.add_parser('summary'); show.add_argument('--db', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init-key':
            write_private(args.out, os.urandom(32)); result = {'created': True}
        elif args.command == 'export':
            key = read_key(args.key)
            store = Store(args.db, readonly=True)
            try:
                bundle = make_bundle(store.request(args.request_id), key, args.workflow_commit,
                                     args.app, args.verification, args.rework, project_id=args.project_id)
            finally:
                store.close()
            write_private(args.out, (encoded(bundle) + '\n').encode())
            result = {'exported': True, 'sha256': bundle['sha256'], 'full_usage_complete': False}
        elif args.command == 'import':
            result = merge(args.db, load_feedback(args.file))
        else:
            result = summary(args.db)
        print(encoded(result)); return 0
    except (ValueError, OSError, sqlite3.Error, TypeError, KeyError, OverflowError, RecursionError):
        # Never echo paths, raw identifiers, keys or source text on malformed input.
        print('Feedback exchange failed: check input, private paths and conflicts.', file=__import__('sys').stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
