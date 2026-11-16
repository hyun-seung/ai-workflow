#!/usr/bin/env python3
"""Read-only filesystem prerequisites for one explicit saved source."""
import argparse
import json
import os
from pathlib import Path
import stat

from completion_hook import bounded_json
from native_record import identifier, unique_object, reject_constant

MAX_METADATA_BYTES = 1024 * 1024


class PrerequisiteError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code
        self.stage = None
        self.target = None


def fingerprint(s):
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_uid, s.st_mode, s.st_nlink


def private_attributes(value, project=None):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError('canonical_path_required')
    p = Path(value)
    if any(v.is_symlink() for v in (p, *p.parents)):
        raise PrerequisiteError('alias')
    if str(p.resolve()) != value:
        raise ValueError('canonical_path_required')
    try:
        s = p.stat()
    except FileNotFoundError:
        raise PrerequisiteError('missing') from None
    except PermissionError:
        raise PrerequisiteError('unreadable') from None
    if not stat.S_ISREG(s.st_mode):
        raise PrerequisiteError('not_regular')
    if s.st_uid != os.getuid():
        raise PrerequisiteError('wrong_owner')
    if s.st_nlink != 1:
        raise PrerequisiteError('hardlink')
    if s.st_mode & 0o077:
        raise PrerequisiteError('public_mode')
    if not s.st_mode & stat.S_IRUSR or not os.access(p, os.R_OK):
        raise PrerequisiteError('unreadable')
    if project is not None and (p.is_relative_to(project) or any((v / '.git').exists() for v in p.parents)):
        raise PrerequisiteError('public_record_location')
    return p, fingerprint(s)


def _preflight(project, record_file, state):
    project = Path(project)
    if not project.is_absolute() or str(project.resolve(strict=True)) != str(project) or not project.is_dir():
        raise ValueError('invalid_project')
    state.update(stage='metadata_attributes', target=dict(kind='metadata'))
    p, before = private_attributes(record_file, project)
    if before[2] > MAX_METADATA_BYTES:
        raise ValueError('metadata_limit')
    state['stage'] = 'metadata_read'
    with os.fdopen(os.open(p, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), 'rb') as stream:
        if fingerprint(os.fstat(stream.fileno())) != before:
            raise PrerequisiteError('metadata_changed')
        raw = stream.read(MAX_METADATA_BYTES + 1)
        after = fingerprint(os.fstat(stream.fileno()))
    if before != after or private_attributes(record_file, project)[1] != before:
        raise PrerequisiteError('metadata_changed')
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError('metadata_limit')
    state['stage'] = 'metadata_validate'
    saved = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
    bounded_json(saved)
    if (not isinstance(saved, dict) or type(saved.get('schema_version')) is not int or
            saved['schema_version'] != 1 or saved.get('kind') != 'orca_codex_metadata' or
            saved.get('project') != str(project) or saved.get('environment') != 'orca' or saved.get('provider') != 'codex'):
        raise ValueError('invalid_metadata_identity')
    sid = saved['session']['id']; identifier(sid)
    state.update(stage='source_attributes', target=dict(kind='session_source', session_id=sid))
    private_attributes(saved['source']['path'])  # Attributes only: never open the source.
    state.update(stage='metadata_recheck', target=dict(kind='metadata', session_id=sid))
    if private_attributes(record_file, project)[1] != before:
        raise PrerequisiteError('metadata_changed')
    return dict(status='ready', ready=True, scope='filesystem_prerequisites_only',
                measurement_validated=False, permission_granted=False,
                target=dict(kind=saved['kind'], session_id=sid), checks=['metadata_private', 'source_private'])


def preflight(project, record_file):
    state = dict(stage='project', target=dict(kind='project'))
    try:
        return _preflight(project, record_file, state)
    except (PrerequisiteError, PermissionError, FileNotFoundError) as error:
        if state['stage'] == 'project':
            raise ValueError('invalid_project') from None
        if not isinstance(error, PrerequisiteError):
            error = PrerequisiteError('unreadable' if isinstance(error, PermissionError) else 'missing')
        error.stage = state['stage']
        error.target = dict(state['target'])
        raise error from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--record-file', required=True)
    args = parser.parse_args(argv)
    try:
        result = preflight(args.project, args.record_file); code = 0
    except PrerequisiteError as error:
        result = dict(status='unmet', ready=False, scope='filesystem_prerequisites_only', checks=[error.code],
                      stage=error.stage, target=error.target); code = 1
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        result = dict(status='invalid', ready=False, checks=['invalid_explicit_input']); code = 2
    print(json.dumps(result)); return code


if __name__ == '__main__':
    raise SystemExit(main())
