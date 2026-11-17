#!/usr/bin/env python3
"""Attribute-only source capacity diagnosis; never admits or reads a source."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

from native_record import MAX_BYTES


class DiagnosticError(Exception):
    def __init__(self, code, exit_code=1):
        self.code = code
        self.exit_code = exit_code


def _path(value):
    if (not isinstance(value, str) or not value or '\0' in value
            or not value.startswith('/') or '//' in value or str(Path(value)) != value
            or any(part in ('.', '..') for part in value.split('/'))):
        raise DiagnosticError('invalid_path', 2)
    return Path(value)


def _attributes(path):
    # Walk every component with lstat: resolve() alone would accept aliases.
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise DiagnosticError('path_alias')
    if os.path.realpath(path) != str(path):
        raise DiagnosticError('path_alias')
    return path.lstat()


def _identity(info):
    return tuple(getattr(info, name) for name in (
        'st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
        'st_uid', 'st_mode', 'st_nlink'))


def diagnose(project, session_file):
    """Return (safe JSON object, exit code), without opening either input."""
    try:
        project_path, source_path = _path(project), _path(session_file)
        try:
            project_before = _attributes(project_path)
            if not stat.S_ISDIR(project_before.st_mode):
                raise DiagnosticError('invalid_project', 2)
        except OSError:
            raise DiagnosticError('invalid_project', 2) from None
        before = _attributes(source_path)
        if not stat.S_ISREG(before.st_mode):
            raise DiagnosticError('source_not_regular')
        if before.st_uid != os.getuid():
            raise DiagnosticError('source_owner')
        if before.st_nlink != 1:
            raise DiagnosticError('source_links')
        try:
            after = _attributes(source_path)
            project_after = _attributes(project_path)
            if (_identity(before) != _identity(after)
                    or _identity(project_before) != _identity(project_after)):
                raise DiagnosticError('source_changed')
        except (OSError, DiagnosticError):
            raise DiagnosticError('source_changed') from None
        size = after.st_size
        warning = (MAX_BYTES * 9) // 10
        status = ('over_limit' if size > MAX_BYTES else
                  'near_limit' if size >= warning else 'within_limit')
        return dict(status=status, scope='source_capacity_only', source_bytes=size,
                    max_source_bytes=MAX_BYTES, warning_at_bytes=warning,
                    remaining_bytes=max(0, MAX_BYTES - size),
                    fits_parser_limit=size <= MAX_BYTES,
                    measurement_validated=False, session_identity_verified=False,
                    permission_granted=False), int(size > MAX_BYTES)
    except DiagnosticError as exc:
        return dict(status='error', code=exc.code, scope='source_capacity_only'), exc.exit_code
    except FileNotFoundError:
        return dict(status='error', code='source_missing', scope='source_capacity_only'), 1
    except (OSError, ValueError):
        return dict(status='error', code='source_access', scope='source_capacity_only'), 1


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise DiagnosticError('invalid_arguments', 2)


def main(argv=None):
    parser = SafeParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--project', required=True)
    parser.add_argument('--session-file', required=True)
    try:
        args = parser.parse_args(argv)
        result, code = diagnose(args.project, args.session_file)
    except DiagnosticError as exc:
        result, code = dict(status='error', code=exc.code, scope='source_capacity_only'), exc.exit_code
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return code


if __name__ == '__main__':
    sys.exit(main())
