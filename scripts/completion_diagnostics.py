#!/usr/bin/env python3
"""Bounded local invocation assertions; inspection never collects or replays Stop."""
import argparse
from contextlib import contextmanager
from itertools import islice
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

from lifecycle_io import digest
from run_records import validate_records_dir
from trace_observation import unique, invalid_number

MAX_INVOCATIONS = 128
MAX_RECORDS = 6  # Five first-entry phases and one terminal outcome per invocation.
MAX_RECORD_BYTES = 512
MAX_BYTES = 512 * 1024  # Read-only legacy v1 store.
MAX_EVENT_BYTES = 1024  # Includes the v2 frame's terminating newline.
MAX_EVENTS = MAX_INVOCATIONS * MAX_RECORDS
MAX_APPEND_BYTES = MAX_EVENTS * MAX_EVENT_BYTES
MAX_COMPONENTS = 256
LEGACY_NAME = 'observations.json'
EVENTS_NAME = 'events-v2.jsonl'
PHASES = ('entry', 'payload', 'source', 'registration', 'collection')
OUTCOMES = {'started', 'collected', 'ignored', 'pending', 'error'}
REASONS = {
    'none', 'unknown', 'different_project', 'no_registered_dispatch',
    'unregistered_or_unclosed_turn', 'hook_input_oversized', 'hook_json_too_deep',
    'only_stop_hook_supported', 'invalid_identifier', 'hook_source_path_unavailable',
    'hook_stop_state_unknown', 'malformed_json', 'session_file_limit',
    'session_identity_or_project_mismatch', 'not_supported_orca_worker_source',
    'missing_initial_session_meta', 'session_changed_during_read',
    'hook_ledger_scope_mismatch', 'invalid_hook_ledger_operation',
    'invalid_usage_collection', 'hook_operation_not_confirmed',
    'source_unavailable', 'source_access_denied', 'source_not_regular',
    'source_assignment_rejected', 'source_parser_rejected',
}
CATEGORIES = {'none', 'input', 'source', 'registration', 'collection', 'internal'}
HEX = re.compile(r'[0-9a-f]{64}\Z')
ID = re.compile(r'[0-9a-f]{32}\Z')
SOURCE_REASONS = {'source_unavailable', 'source_access_denied', 'source_not_regular',
                  'source_assignment_rejected', 'source_parser_rejected'}


def checked_private_path(path, project):
    """Lifecycle destination policy without mkdir, including on help/inspection."""
    path = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('diagnostic_path_invalid')
    validate_records_dir(path.parent, [Path(__file__).resolve().parents[1], project])
    if path.exists():
        info = path.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1):
            raise ValueError('diagnostic_file_invalid')
    return path


def destination(config):
    return checked_private_path(Path(config['directory']) / 'invocations' / 'observations.json', config['project'])


@contextmanager
def directory_fd(path, create=False):
    """Pin every component; creation, if requested, uses only its parent's fd."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or len(path.parts) > MAX_COMPONENTS:
        raise ValueError('diagnostic_path_invalid')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            try:
                new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create: raise
                try: os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError: pass  # Revalidate a concurrent winner.
                new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try: os.fsync(fd)  # Persist the new child link, not just its contents.
                except BaseException:
                    os.close(new)
                    raise
            os.close(fd); fd = new
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('diagnostic_directory_invalid')
        yield fd
    finally:
        os.close(fd)


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def private_regular(info, limit):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1 or info.st_size > limit):
        raise ValueError('diagnostic_record_invalid')


def file_state(fd, name):
    try: return os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError: return None


@contextmanager
def writer_lock(fd):
    """Same nonblocking lifecycle policy, anchored to the actual write directory."""
    # The directory inode lock also guards against a replaced lock-file name.
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    lock_fd = None
    try:
        lock_fd = os.open('writer.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                          0o600, dir_fd=fd)
        info = os.fstat(lock_fd)
        private_regular(info, 0)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        linked = file_state(fd, 'writer.lock')
        if linked is None or fingerprint(linked) != fingerprint(info):
            raise ValueError('diagnostic_lock_changed')
        yield
    finally:
        if lock_fd is not None: os.close(lock_fd)
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def event_file(directory, write=False):
    """Open once, then keep the same verified descriptor through read and append."""
    flags = (os.O_RDWR | os.O_APPEND if write else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(EVENTS_NAME, flags, dir_fd=directory)
    except FileNotFoundError:
        if not write:
            yield None
            return
        # No retry/adoption if a name appears before this exclusive creation.
        fd = os.open(EVENTS_NAME, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
    try:
        private_regular(os.fstat(fd), MAX_APPEND_BYTES)
        fcntl.flock(fd, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)


def encode_event(invocation_id, index, record):
    body = dict(version=2, id=invocation_id, index=index, record=record)
    raw = (json.dumps(dict(body, sha256=digest(body)), separators=(',', ':'), allow_nan=False) + '\n').encode()
    if len(raw) > MAX_EVENT_BYTES: raise ValueError('diagnostic_record_invalid')
    return raw


def append_event(fd, raw, expected):
    """One bounded write to the descriptor just read; a short write is not retried."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_EVENT_BYTES or not raw.endswith(b'\n'):
        raise ValueError('diagnostic_record_invalid')
    info = os.fstat(fd)
    private_regular(info, MAX_APPEND_BYTES)
    if fingerprint(info) != expected or info.st_size + len(raw) > MAX_APPEND_BYTES:
        raise ValueError('diagnostic_journal_changed_or_full')
    # No name lookup, promotion, replacement, truncation or error-path deletion.
    # The OS may tear this write. Readers require the entire checksummed frame
    # including its final newline, and reject any partial tail without repair.
    if os.write(fd, raw) != len(raw): raise ValueError('diagnostic_partial_append')
    os.fsync(fd)


def reason_code(error, phase):
    """Classify only known types/exact codes, never extract paths or line numbers."""
    if phase == 'source':
        if isinstance(error, (FileNotFoundError, NotADirectoryError)): return 'source_unavailable'
        if isinstance(error, PermissionError): return 'source_access_denied'
        if isinstance(error, IsADirectoryError): return 'source_not_regular'
        fixed = {
            'session_not_regular_file': 'source_not_regular',
            'unsupported_assignment_header': 'source_assignment_rejected',
            'ambiguous_assignment_header': 'source_assignment_rejected',
            'multiple_session_meta': 'source_parser_rejected',
            'duplicate_json_key': 'source_parser_rejected',
            'nonfinite_json': 'source_parser_rejected',
        }
        if isinstance(error, ValueError) and str(error) in fixed: return fixed[str(error)]
    if isinstance(error, json.JSONDecodeError): return 'malformed_json'
    if isinstance(error, RecursionError) and phase == 'payload': return 'hook_json_too_deep'
    reason = str(error)
    return reason if reason in REASONS - SOURCE_REASONS else 'unknown'


def validate(data):
    if (not isinstance(data, dict) or set(data) != {'version', 'invocations'} or
            type(data['version']) is not int or data['version'] != 1 or
            not isinstance(data['invocations'], list) or len(data['invocations']) > MAX_INVOCATIONS):
        raise ValueError('diagnostic_record_invalid')
    seen = set()
    for invocation in data['invocations']:
        if (not isinstance(invocation, dict) or set(invocation) != {'id', 'records'} or
                not isinstance(invocation['id'], str) or not ID.fullmatch(invocation['id']) or
                invocation['id'] in seen or not isinstance(invocation['records'], list) or
                not 1 <= len(invocation['records']) <= MAX_RECORDS):
            raise ValueError('diagnostic_record_invalid')
        seen.add(invocation['id'])
        phases, reference = set(), None
        for index, record in enumerate(invocation['records']):
            if (not isinstance(record, dict) or
                    set(record) not in ({'phase', 'outcome', 'category', 'reason'},
                                        {'phase', 'outcome', 'category', 'reason', 'event_key'}) or
                    not all(isinstance(record[k], str) for k in ('phase', 'outcome', 'category', 'reason')) or
                    record['phase'] not in PHASES or record['outcome'] not in OUTCOMES or
                    record['category'] not in CATEGORIES or record['reason'] not in REASONS):
                raise ValueError('diagnostic_record_invalid')
            if len(json.dumps(record).encode()) > MAX_RECORD_BYTES:
                raise ValueError('diagnostic_record_invalid')
            if 'event_key' in record:
                key = record['event_key']
                if not isinstance(key, str) or not HEX.fullmatch(key) or (reference and reference != key):
                    raise ValueError('diagnostic_record_invalid')
                reference = key
            if index == 0 and record != dict(phase='entry', outcome='started', category='none', reason='none'):
                raise ValueError('diagnostic_record_invalid')
            if record['outcome'] == 'started':
                if (len(phases) >= len(PHASES) or record['phase'] != PHASES[len(phases)] or record['category'] != 'none' or
                        record['reason'] != 'none' or (len(phases) >= 2 and 'event_key' not in record)):
                    raise ValueError('diagnostic_record_invalid')
                phases.add(record['phase'])
            elif index != len(invocation['records']) - 1 or record['phase'] not in phases:
                raise ValueError('diagnostic_record_invalid')
            elif record['outcome'] in {'collected', 'pending'}:
                reasons = {'none'} if record['outcome'] == 'collected' else {'unregistered_or_unclosed_turn', 'session_changed_during_read'}
                if (record['phase'] != 'collection' or len(phases) != len(PHASES) or record['category'] != 'none' or
                        record['reason'] not in reasons):
                    raise ValueError('diagnostic_record_invalid')
            elif record['outcome'] == 'ignored':
                if ((record['phase'], record['reason']) not in {
                        ('payload', 'different_project'), ('registration', 'no_registered_dispatch'),
                        ('source', 'no_registered_dispatch')} or
                        record['category'] != 'none' or 'event_key' not in record):
                    raise ValueError('diagnostic_record_invalid')
            elif record['category'] != {'payload': 'input', 'source': 'source', 'registration': 'registration',
                                        'collection': 'collection'}.get(record['phase'], 'internal'):
                raise ValueError('diagnostic_record_invalid')
    return data


def inventory(fd):
    limits = {LEGACY_NAME: MAX_BYTES, EVENTS_NAME: MAX_APPEND_BYTES, 'writer.lock': 0}
    with os.scandir(fd) as entries: names = list(islice(entries, 4))
    if len(names) > 3 or any(n.name not in limits for n in names):
        # Old temporary residue and unknown files remain untouched for attention.
        raise ValueError('diagnostic_directory_attention')
    for entry in names:
        info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
        private_regular(info, limits[entry.name])


def read_bytes(fd, limit):
    before = os.fstat(fd)
    private_regular(before, limit)
    os.lseek(fd, 0, os.SEEK_SET)
    with os.fdopen(os.dup(fd), 'rb') as stream: raw = stream.read(limit + 1)
    if len(raw) != before.st_size or fingerprint(before) != fingerprint(os.fstat(fd)):
        raise ValueError('diagnostic_journal_changed')
    return raw, fingerprint(before)


def read_legacy(directory):
    try:
        fd = os.open(LEGACY_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return dict(version=1, invocations=[])
    try:
        raw, _ = read_bytes(fd, MAX_BYTES)
        return validate(json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_number))
    finally:
        os.close(fd)


def reconstruct(legacy, raw):
    """Accept only full, ordered v2 frames; never merge an ID across formats."""
    data = dict(version=1, invocations=list(legacy['invocations']))
    legacy_ids = {item['id'] for item in data['invocations']}
    if not raw: return data
    if len(raw) > MAX_APPEND_BYTES or not raw.endswith(b'\n'):
        raise ValueError('diagnostic_partial_or_oversize_append')
    lines = raw.splitlines(keepends=True)
    if len(lines) > MAX_EVENTS: raise ValueError('diagnostic_capacity')
    items = {}
    for line in lines:
        if len(line) > MAX_EVENT_BYTES or not line.endswith(b'\n'):
            raise ValueError('diagnostic_record_invalid')
        row = json.loads(line, object_pairs_hook=unique, parse_constant=invalid_number)
        if (not isinstance(row, dict) or set(row) != {'version', 'id', 'index', 'record', 'sha256'} or
                type(row['version']) is not int or row['version'] != 2 or
                not isinstance(row['id'], str) or not ID.fullmatch(row['id']) or
                type(row['index']) is not int or not 0 <= row['index'] < MAX_RECORDS or
                not isinstance(row['sha256'], str) or not HEX.fullmatch(row['sha256'])):
            raise ValueError('diagnostic_record_invalid')
        body = {k: row[k] for k in ('version', 'id', 'index', 'record')}
        if row['sha256'] != digest(body) or row['id'] in legacy_ids:
            raise ValueError('diagnostic_conflicting_record')
        item = items.get(row['id'])
        if item is None:
            if row['index'] != 0 or len(data['invocations']) >= MAX_INVOCATIONS:
                raise ValueError('diagnostic_capacity_or_sequence')
            item = dict(id=row['id'], records=[])
            items[row['id']] = item
            data['invocations'].append(item)
        if row['index'] != len(item['records']): raise ValueError('diagnostic_conflicting_record')
        item['records'].append(row['record'])
    return validate(data)


def read_stores(directory, events):
    inventory(directory)
    legacy = read_legacy(directory)
    raw, identity = read_bytes(events, MAX_APPEND_BYTES) if events is not None else (b'', None)
    return reconstruct(legacy, raw), identity, {item['id'] for item in legacy['invocations']}


def read(fd):
    # Read-only, nonblocking locks; inspection creates no lock or data file.
    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    try:
        inventory(fd)
        with event_file(fd) as events: return read_stores(fd, events)[0]
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)


class Observation:
    """Optional best-effort telemetry. No error here changes collection's result."""
    def __init__(self, config):
        self.config = config
        self.id = uuid.uuid4().hex
        self.current = 'entry'
        self.seen = set()
        self.event_key = None
        self.enabled = True
        self.phase('entry')

    def _record(self, outcome, category='none', reason='none'):
        if not self.enabled: return
        try:
            record = dict(phase=self.current, outcome=outcome, category=category, reason=reason)
            if self.event_key is not None: record['event_key'] = self.event_key
            path = destination(self.config)
            with directory_fd(path.parent, create=True) as fd:
                with writer_lock(fd):
                    inventory(fd)
                    with event_file(fd, write=True) as events:
                        data, expected, legacy_ids = read_stores(fd, events)
                        if self.id in legacy_ids: raise ValueError('diagnostic_conflicting_record')
                        item = next((i for i in data['invocations'] if i['id'] == self.id), None)
                        if item is None:
                            if self.current != 'entry' or outcome != 'started' or len(data['invocations']) >= MAX_INVOCATIONS:
                                raise ValueError('diagnostic_capacity_or_missing_entry')
                            item = dict(id=self.id, records=[])
                            data['invocations'].append(item)
                        elif item['records'][-1]['outcome'] != 'started':
                            raise ValueError('diagnostic_already_final')
                        index = len(item['records'])
                        item['records'].append(record)
                        validate(data)
                        append_event(events, encode_event(self.id, index, record), expected)
                        os.fsync(fd)
        except Exception:
            # Never serialize exception text, paths, environment or payload data.
            # A missing final record is incomplete, not a claimed cause of failure.
            self.enabled = False

    def phase(self, phase):
        self.current = phase
        if phase not in self.seen:
            self._record('started')
            self.seen.add(phase)

    def bind_event(self, event):
        self.event_key = digest(dict(session=event['session_id'], turn=event['turn_id']))

    def finish(self, result=None, error=None):
        if error is not None:
            reason = reason_code(error, self.current)
            category = {'payload': 'input', 'source': 'source', 'registration': 'registration',
                        'collection': 'collection'}.get(self.current, 'internal')
            self._record('error', category, reason)
        else:
            reason = result.get('reason', result.get('issue', 'none'))
            self._record(result['status'], 'none', reason if reason in REASONS else 'unknown')
        self.enabled = False


def inspect(config):
    """Read only this config's diagnostic journal, without receipt/DB/source access."""
    result = dict(version=1, status='unknown', root_cause='undetermined', invocations=[],
                  stop_receipt_verified=False, usage_verified=False, worker_exit_verified=False,
                  task_acceptance_evaluated=False)
    try:
        path = destination(config)
        try:
            with directory_fd(path.parent) as fd: data = read(fd)
        except FileNotFoundError:
            return result
        for item in data['invocations']:
            result['invocations'].append(dict(item, state=('incomplete' if item['records'][-1]['outcome'] == 'started' else 'final')))
        if result['invocations']:
            result['status'] = ('attention_required' if any(i['state'] == 'incomplete' or
                                i['records'][-1]['outcome'] in {'error', 'pending'} for i in result['invocations']) else 'observed')
        result['capacity_reached'] = len(data['invocations']) == MAX_INVOCATIONS
    except Exception:
        result.update(status='attention_required', issue='diagnostic_unavailable_or_invalid', invocations=[])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('command', choices=['inspect'])
    args = parser.parse_args(argv)
    try:
        from completion_hook import load_config
        result = inspect(load_config(args.config))
    except Exception:
        print(json.dumps(dict(version=1, status='unknown', issue='config_unavailable_or_invalid',
                              root_cause='undetermined', invocations=[], stop_receipt_verified=False,
                              usage_verified=False, worker_exit_verified=False, task_acceptance_evaluated=False)))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'observed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
