#!/usr/bin/env python3
"""Draft a review-only binding for one explicitly selected closed coordinator turn."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import stat

from lifecycle_io import private_file
from native_record import _SelectedSource, _source_identity, identifier
from native_turn_record import load_binding, collect_turn
from trace_store import Store

FIELDS = {'schema_version', 'project', 'trace_db', 'request_id', 'actor_id',
          'task_id', 'session_file', 'session_id', 'native_turn_id', 'scope'}


def require(ok):
    if not ok:
        raise ValueError('invalid_draft_input')


def path(value, directory=False):
    require(isinstance(value, str) and 0 < len(value) <= 4096 and '\x00' not in value)
    p = Path(value)
    require(p.is_absolute() and str(p) == value and p.resolve() == p and
            not any(x.is_symlink() for x in (p, *p.parents)))
    if directory:
        require(p.is_dir())
    else:
        s = p.stat()
        require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1)
    return p


def root(data):
    store = Store(data['trace_db'], readonly=True)
    try:
        bundle = store.request(data['request_id'])
        require(bundle is not None)
        actors = [a for a in bundle['actors'] if a['id'] == data['actor_id']]
        require(len(actors) == 1 and actors[0]['request_id'] == data['request_id'] and
                actors[0]['role'] == 'coordinator' and actors[0].get('parent_id') is None)
    finally:
        store.close()


def build(input_file, output_file):
    recipe = path(input_file)
    identity = _source_identity(recipe)
    _, raw_recipe, data = load_binding(recipe)
    require(isinstance(data, dict) and set(data) == FIELDS)
    require(type(data['schema_version']) is int and data['schema_version'] == 1 and
            data['scope'] == 'single_work')
    for key in ('request_id', 'actor_id', 'task_id', 'session_id', 'native_turn_id'):
        identifier(data[key])
    project = path(data['project'], True)
    db = path(data['trace_db']); source = path(data['session_file'])
    private_file(recipe, project); private_file(db, project)
    protected = [recipe, db, source] + [Path(str(p) + suffix) for p in (recipe, db, source)
                                      for suffix in ('-wal', '-shm', '-journal')]
    require(len({recipe, db, source}) == 3)
    require(not any(a == b for a in (recipe, db, source) for b in protected[3:]))
    output = Path(output_file)
    require(isinstance(output_file, str) and output.is_absolute() and str(output) == output_file and
            output.resolve() == output and output.parent.is_dir())
    require(not any(output == p or output.is_relative_to(p) or p.is_relative_to(output) for p in protected))
    private_file(output, project)
    if output.exists(): path(str(output))
    root(data)
    selected = _SelectedSource()
    parsed = selected.read(project, source, data['session_id'])
    starts = [b for b in parsed['boundaries'] if b['kind'] == 'task_started' and b['turn_id'] == data['native_turn_id']]
    # A placeholder allows the existing collector to report missing/open boundaries.
    users = []
    if len(starts) == 1:
        start = starts[0]['line']
        end = next((b['line'] for b in parsed['boundaries'] if b['line'] > start and
                    b['kind'] in ('task_complete', 'turn_aborted')), float('inf'))
        users = [u for u in parsed['user_lines'] if start < u['line'] < end]
    if len(users) > 1000:
        return dict(status='ineligible', issues=['ambiguous_turn_user_messages']), 1
    binding = dict(schema_version=2, role='coordinator', scope='single_work',
                   **{k: data[k] for k in ('request_id', 'actor_id', 'task_id', 'native_turn_id')},
                   messages=[dict(line=u['line'], sha256=u['content_sha256']) for u in users] or
                            [dict(line=1, sha256='0'*64)])
    usage = collect_turn(parsed, binding)
    if usage['status'] != 'measured':
        return dict(status='ineligible', issues=usage['issues'][:20]), 1
    encoded = (json.dumps(binding, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    selected.check()
    require(_source_identity(recipe) == identity and load_binding(recipe)[1] == raw_recipe)
    path(str(db)); private_file(db, project); root(data)
    private_file(output, project)
    selected.check()
    replay = output.exists()
    if replay:
        path(str(output))
        require(load_binding(output)[1] == encoded)
    else:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        except BaseException:
            output.unlink()
            raise
    return dict(status='draft', requires_review=True, execution_authorized=False, replay=replay), 0


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('invalid_cli_arguments')


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('--input', required=True); parser.add_argument('--output', required=True)
    try:
        args = parser.parse_args(argv)
        result, code = build(args.input, args.output)
    except (ValueError, OSError, KeyError, TypeError, RecursionError, UnicodeError, sqlite3.DatabaseError):
        result, code = dict(status='error', error='draft_validation_failed'), 2
    print(json.dumps(result)); return code


if __name__ == '__main__':
    raise SystemExit(main())
