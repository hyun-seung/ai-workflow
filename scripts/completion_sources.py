"""Explicit saved measurement admission, without inferring Stop delivery."""
import hashlib
import os
from pathlib import Path
import stat

import completion_hook as hook
import collect_request_usage as collect
from native_record import inspect_session, identifier
from native_turn_record import load_binding


def require(value):
    if not value:
        raise ValueError('invalid_explicit_completion_source')


def private(value):
    require(isinstance(value, str) and Path(value).is_absolute())
    p = Path(value)
    require(str(p.resolve(strict=True)) == value)
    s = p.stat()
    require(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and
            s.st_nlink == 1 and not s.st_mode & 0o077)
    return p


def describe(cfg, operation, source):
    try:
        require(isinstance(source, dict) and set(source) == {'kind', 'record_file'} and
                source['kind'] == 'explicit_source')
        p = private(source['record_file'])
        _, raw, saved = load_binding(p)
        hook.bounded_json(saved)
        c = operation['plan']['usage_collection']
        require(type(saved.get('schema_version')) is int and saved['schema_version'] == 1)
        context = saved.get('request_context')
        require(isinstance(context, dict) and
                context.get('request_id') == operation['request_id'] and
                context.get('role') == c['role'])
        require(saved['kind'] == 'orca_codex_metadata' and saved['environment'] == 'orca' and
                saved['provider'] == 'codex' and saved['project'] == cfg['project'] and
                operation['plan']['project'] == cfg['project'])
        u = saved['dispatch_usage']; b = saved['binding']
        require(saved['verification']['status'] == 'pass' and u['status'] == 'measured')
        for k in ('task_id', 'dispatch_id'):
            require(b[k] == operation[k] and u[k] == operation[k])
        require(u['actor_id'] == operation['dispatch_id'] and
                u['request_id'] == operation['request_id'] and u['role'] == c['role'])
        sid = saved['session']['id']; identifier(sid)
        require(u['session_id'] == sid)
        session = private(saved['source']['path'])
        hook.verify_saved_measurement(cfg, dict(transcript_path=str(session), session_id=sid), operation, saved)
        require(p.read_bytes() == raw)
        return dict(record_file=str(p), record_sha256=hashlib.sha256(raw).hexdigest(),
                    session_file=str(session), session_id=sid, measurement_key=u['measurement_key'])
    except (KeyError, TypeError, OSError) as error:
        raise ValueError('invalid_explicit_completion_source') from error


def verify(cfg, operation, expected, description, cli, directory):
    require(describe(cfg, operation, dict(kind='explicit_source', record_file=description['record_file'])) == description)
    directory = collect.path(str(directory))
    require(directory.is_dir())
    snapshot = collect.fresh_native(cli, operation, directory, Path(cfg['project']))
    current = inspect_session(cfg['project'], description['session_file'], description['session_id'],
                              operation['task_id'], operation['dispatch_id'], expected, snapshot,
                              operation['request_id'], operation['plan']['usage_collection']['role'])
    require(current['verification']['status'] == 'pass' and current['dispatch_usage']['status'] == 'measured')
    _, _, saved = load_binding(description['record_file'])
    hook.verify_saved_measurement(cfg, dict(transcript_path=description['session_file'],
                                          session_id=description['session_id']), operation, saved, current)
    require(describe(cfg, operation, dict(kind='explicit_source', record_file=description['record_file'])) == description)
    return dict(record_path=description['record_file'], measurement_key=description['measurement_key'],
                provenance=dict(kind='explicit_source', record_sha256=description['record_sha256'],
                                measurement_key=description['measurement_key'], stop_delivery_inferred=False))
