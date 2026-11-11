"""Explicit, private execution trace imports. No agent execution or inferred reasoning."""
from __future__ import annotations
import hashlib
import math
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from datetime import datetime, timezone
from run_records import validate_records_dir
from trace_closeout import closeout_view, validate_detail

MAX_BYTES = 8 * 1024 * 1024
KINDS = {'user_request', 'decision', 'assignment', 'instruction', 'tool_call', 'tool_result',
         'question', 'approval_request', 'approval_resolution', 'handoff', 'verification', 'explanation', 'completion'}
FORBIDDEN = {'analysis', 'reasoning', 'thinking', 'chain_of_thought'}
ROLE_IDS = {'coordinator', 'architect', 'designer', 'backend', 'frontend', 'verifier'}


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if k.lower() not in FORBIDDEN}
    if isinstance(value, list):
        return [clean(v) for v in value if not isinstance(v, dict) or v.get('type', '').lower() not in FORBIDDEN]
    if isinstance(value, str) and re.search(r'<\s*(analysis|reasoning|thinking)\b', value, re.I):
        return '[내부 추론 블록 제외]'
    return value


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}', value):
        raise ValueError('ID must be 1–200 letters/digits/_.:-')
    return value


def stamp(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('time must be ISO 8601 with timezone or null')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('time needs timezone')
    return dt.astimezone(timezone.utc).isoformat()


def private_db(path, create=False):
    path = Path(os.path.abspath(Path(path).expanduser()))
    # Reject symlinks before resolve; never chmod existing user files or directories.
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink trace paths are not supported')
    parent = validate_records_dir(path.parent, [Path(__file__).resolve().parents[1]])
    if create:
        missing = []
        p = parent
        while not p.exists():
            missing.append(p)
            p = p.parent
        for p in reversed(missing):
            p.mkdir(mode=0o700)
        validate_records_dir(parent, [])
    for p in (path, Path(str(path)+'-wal'), Path(str(path)+'-shm'), Path(str(path)+'-journal')):
        if p.exists() or p.is_symlink():
            s = p.lstat()
            if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) & 0o177:
                raise ValueError('DB and sidecars must be owned regular files mode 0600')
    if not path.exists():
        if not create:
            raise ValueError('DB does not exist')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    return path


class Store:
    def __init__(self, path, readonly=False):
        self.path = private_db(path, create=not readonly)
        self.db = sqlite3.connect(self.path.as_uri() + ('?mode=ro' if readonly else '?mode=rw'), uri=True)
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if version not in (0, 1) or (version == 0 and tables):
            self.db.close()
            raise ValueError('Not a supported trace database')
        if readonly and version != 1:
            self.db.close()
            raise ValueError('Trace database is not initialized')
        if not readonly:
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS records(kind TEXT, id TEXT, request_id TEXT, body TEXT,
                PRIMARY KEY(kind,id));
            CREATE INDEX IF NOT EXISTS by_request ON records(request_id,kind);
            CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY, request_id TEXT, body TEXT);
            PRAGMA user_version=1;
            ''')
        self.db.execute('PRAGMA query_only=' + ('ON' if readonly else 'OFF'))

    def close(self):
        self.db.close()

    def import_bundle(self, bundle):
        if bundle.get('schema_version') != 1:
            raise ValueError('Unsupported schema_version')
        bundle = clean(bundle)
        rows = []
        for kind, plural in [('request','requests'), ('actor','actors'), ('event','events')]:
            if not isinstance(bundle.get(plural), list):
                raise ValueError('Missing list: '+plural)
            for row in bundle[plural]:
                row = dict(row)
                ident(row.get('id'))
                rid = row['id'] if kind == 'request' else ident(row.get('request_id'))
                for key in ('source', 'source_id', 'completeness'):
                    if not isinstance(row.get(key), str) or not row[key]:
                        raise ValueError('Missing '+key)
                row['time'] = stamp(row.get('time'))
                if kind == 'actor':
                    for key in ('parent_id',):
                        if row.get(key) is not None: ident(row[key])
                    for key in ('role', 'environment', 'provider'):
                        row.setdefault(key, None)
                        if row[key] is not None and not isinstance(row[key], str):
                            raise ValueError('Actor metadata must be text or null')
                    for key in ('requested', 'observed'):
                        row.setdefault(key, {'model': None, 'effort': None})
                        if not isinstance(row[key], dict): raise ValueError('Invalid model metadata')
                        for field in ('model', 'effort'):
                            row[key].setdefault(field, None)
                            if row[key][field] is not None and not isinstance(row[key][field], str):
                                raise ValueError('Model/effort must be text or null')
                    usage = row.get('usage')
                    if not isinstance(usage, dict) or not all(usage.get(k) for k in ('source','scope')) or not isinstance(usage.get('measurements'), dict) or not usage['measurements']:
                        row['usage'] = {'status':'unavailable', 'source':None, 'scope':None}
                    else:
                        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in usage['measurements'].values()):
                            raise ValueError('Invalid measured usage')
                        row['usage']['status'] = 'measured'
                if kind == 'event':
                    if row.get('kind') == 'tool_call' and not isinstance(row.get('detail'), dict):
                        raise ValueError('Tool call detail must be an object')
                    if row.get('kind') not in KINDS: raise ValueError('Unsupported event kind')
                    if row.get('actor_id') is not None: ident(row['actor_id'])
                    if 'detail' not in row: raise ValueError('Event detail missing')
                    if row['kind'] == 'assignment' and isinstance(row['detail'], dict) and 'actor_roles' in row['detail']:
                        labels = row['detail']['actor_roles']
                        if not isinstance(labels, list) or not 1 <= len(labels) <= 200:
                            raise ValueError('Invalid actor role labels')
                        seen = set()
                        for label in labels:
                            if not isinstance(label, dict) or label.get('role_id') not in ROLE_IDS:
                                raise ValueError('Invalid workflow role ID')
                            aid = ident(label.get('actor_id'))
                            if aid in seen or not isinstance(label.get('task_title'), str) or not 1 <= len(label['task_title'].strip()) <= 200:
                                raise ValueError('Invalid actor role label')
                            seen.add(aid)
                    if row['kind'] == 'completion':
                        if row['source'] != 'trace.closeout' or row['time'] is None or not row.get('actor_id'):
                            raise ValueError('Invalid closeout event source/time/coordinator')
                        validate_detail(row['detail'])
                    row['detail_sha256'] = digest(row['detail'])
                rows.append((kind, row['id'], rid, row))
        with self.db:
            for kind, key, rid, row in rows:
                old = self.db.execute('SELECT body FROM records WHERE kind=? AND id=?', (kind,key)).fetchone()
                if old:
                    previous = json.loads(old[0])
                    if previous == row: continue
                    # Only explicit lifecycle status fields may evolve. Content conflicts roll back.
                    mutable = {'status'} if kind in ('request','actor') else ({'detail', 'detail_sha256'} if row.get('kind') == 'tool_call' else set())
                    if {k:v for k,v in previous.items() if k not in mutable} != {k:v for k,v in row.items() if k not in mutable}:
                        raise ValueError('Conflicting ID: '+key)
                    if kind == 'event':
                        a,b = dict(previous['detail']),dict(row['detail'])
                        for field in ('state','exitCode','durationMs'): a.pop(field,None); b.pop(field,None)
                        if a != b: raise ValueError('Conflicting tool input: '+key)
                        if previous['detail'].get('state') in ('completed','resolved','failed') and row['detail'].get('state') not in ('completed','resolved','failed'):
                            continue
                    elif previous.get('status') in ('completed','finished','failed','succeeded','cancelled') and row.get('status') in ('running','pending','unknown'):
                        continue
                self.db.execute('INSERT INTO records VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE '
                                'SET request_id=excluded.request_id, body=excluded.body', (kind,key,rid,encoded(row)))
            for kind,key,rid,row in rows:
                if not self.db.execute("SELECT 1 FROM records WHERE kind='request' AND id=?",(rid,)).fetchone():
                    raise ValueError('Unknown request '+rid)
                ref = row.get('actor_id') if kind == 'event' else row.get('parent_id') if kind == 'actor' else None
                if ref and not self.db.execute("SELECT 1 FROM records WHERE kind='actor' AND id=? AND request_id=?",(ref,rid)).fetchone():
                    raise ValueError('Unknown actor/parent '+ref)
                if kind == 'event' and row.get('kind') == 'assignment' and isinstance(row.get('detail'), dict):
                    for label in row['detail'].get('actor_roles', []):
                        if not self.db.execute("SELECT 1 FROM records WHERE kind='actor' AND id=? AND request_id=?", (label['actor_id'], rid)).fetchone():
                            raise ValueError('Unknown actor role reference')
                if kind == 'event' and row.get('kind') == 'completion':
                    coordinator = json.loads(self.db.execute(
                        "SELECT body FROM records WHERE kind='actor' AND id=? AND request_id=?",
                        (row['actor_id'], rid)).fetchone()[0])
                    if coordinator.get('parent_id') is not None:
                        raise ValueError('Closeout coordinator must be a root actor')
                    for assignment in row['detail']['assignments']:
                        if assignment['actor_id'] == row['actor_id']:
                            raise ValueError('Closeout coordinator cannot also be an assignment')
                        if not self.db.execute("SELECT 1 FROM records WHERE kind='actor' AND id=? AND request_id=?",(assignment['actor_id'],rid)).fetchone():
                            raise ValueError('Unknown closeout actor')
            for collection in bundle.get('collections', []):
                rid = ident(collection['request_id'])
                if not all(isinstance(collection.get(k), str) and collection[k] for k in ('source', 'source_id', 'completeness')):
                    raise ValueError('Invalid collection metadata')
                stamp(collection.get('time'))
                if not self.db.execute("SELECT 1 FROM records WHERE kind='request' AND id=?", (rid,)).fetchone():
                    raise ValueError('Unknown collection request')
                key = digest(collection)
                self.db.execute('INSERT OR IGNORE INTO collections VALUES(?,?,?)', (key,ident(collection['request_id']),encoded(collection)))
        return {'records_received':len(rows)}

    def requests(self, q='', limit=100):
        if len(q)>200 or not 1<=limit<=200: raise ValueError('Invalid search/limit')
        # Literal substring search, no SQL interpolation or wildcard semantics.
        # Unknown receipt times are not invented from snapshot activity. Keep
        # them after known times, newest local registration first within ties.
        return [json.loads(x[0]) for x in self.db.execute(
            "SELECT body FROM records WHERE kind='request' AND instr(lower(body),lower(?))>0 "
            "ORDER BY json_extract(body,'$.time') IS NULL, "
            "julianday(json_extract(body,'$.time')) DESC, rowid DESC LIMIT ?", (q,limit))]

    def request(self, rid):
        ident(rid)
        records = self.db.execute('SELECT kind,body FROM records WHERE request_id=?',(rid,)).fetchall()
        result = {'requests':[], 'actors':[], 'events':[], 'collections':[]}
        for kind,body in records: result[{'request':'requests','actor':'actors','event':'events'}[kind]].append(json.loads(body))
        if not result['requests']: return None
        result['events'].sort(key=lambda e:(e['time'] is None,e['time'] or '',e['id']))
        result['collections'] = [json.loads(r[0]) for r in self.db.execute('SELECT body FROM collections WHERE request_id=? ORDER BY rowid',(rid,))]
        result['closeout'] = closeout_view(result)
        return result


def load_file(path):
    with open(path, 'rb') as stream: data = stream.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES: raise ValueError('Input exceeds 8 MiB')
    return data.decode('utf-8')


def load_bundle(path):
    text = load_file(path)
    if str(path).endswith('.jsonl'):
        out = {'schema_version':1,'requests':[],'actors':[],'events':[], 'collections':[]}
        for line in text.splitlines():
            if not line.strip(): continue
            obj = json.loads(line)
            if obj.get('schema_version') != 1 or obj.get('type') not in ('request','actor','event','collection'):
                raise ValueError('Invalid JSONL record')
            out[{'request':'requests','actor':'actors','event':'events','collection':'collections'}[obj['type']]].append(obj['record'])
        return out
    return json.loads(text)


def orca_bundle(snapshot, request_id, title, role=None, parent_id=None):
    """Only explicit worker-read snapshots. No native CLI calls or workflow defaults."""
    if snapshot.get('ok') is not True: raise ValueError('Unsuccessful Orca snapshot')
    r = snapshot['result']; transcript = r['transcript']; aid = ident(r['dispatchId'])
    ident(request_id)
    source = 'orca.worker-read'
    base = {'source':source, 'source_id':aid, 'time':None, 'completeness':'unknown'}
    # The CLI supplies this request identity; it is not a fact observed in a worker.
    # Every dispatch for the same explicit request must produce identical metadata.
    request = {'id':request_id, 'title':title, 'status':'unknown',
               'source':'trace.explicit-request', 'source_id':request_id,
               'time':None, 'completeness':'unknown'}
    actor = dict(base, id=aid, request_id=request_id, parent_id=parent_id, role=role,
                 environment='orca', provider=r.get('provider'), status=r.get('status',{}).get('worker','unknown'))
    events = []
    omitted = 0
    for m in transcript['messages']:
        if m.get('channel') in FORBIDDEN or m.get('role') in FORBIDDEN:
            omitted += len(m.get('blocks', [])); continue
        mid = m['id']
        timestamp = m.get('timestamp')
        when = datetime.fromtimestamp(timestamp/1000,timezone.utc).isoformat() if type(timestamp) in (int,float) else None
        for i,b in enumerate(m['blocks']):
            typ = b.get('type')
            if typ not in ('text','tool-call','tool-result'):
                omitted += 1; continue
            kind = {'text':'explanation','tool-call':'tool_call','tool-result':'tool_result'}[typ]
            if typ == 'text' and m.get('role') == 'user': kind = 'instruction'
            if typ == 'text' and m.get('role') == 'system' and 'Run a command?' in b.get('text',''): kind = 'approval_request'
            events.append(dict(base,id='orca:'+hashlib.sha256((aid+':'+mid+':'+str(i)).encode()).hexdigest(),
                request_id=request_id, actor_id=aid, time=when, kind=kind,
                source_id=mid+':'+str(i), detail={'message_role':m.get('role'), 'message_source':m.get('source'),
                  'block':clean(b)} if typ != 'tool-call' else clean(b)))
    collection = {'request_id':request_id, 'source':source, 'source_id':aid,
        'time':None, 'completeness':'partial' if transcript.get('limited') else 'unknown',
        'sourceIdentity':r.get('sourceIdentity'), 'native_source':r.get('source'),
        'cursor':r.get('cursor'), 'nextCursor':transcript.get('nextCursor'), 'limited':transcript.get('limited'),
        'returnedMessageCount':transcript.get('returnedMessageCount'), 'warnings':r.get('warnings'),
        'fallbackReason':r.get('fallbackReason'), 'omitted_blocks':omitted,
        'input_output_extent':'unknown; native text may be truncated', 'status':r.get('status')}
    return {'schema_version':1,'requests':[request],'actors':[actor],'events':events,'collections':[collection]}
