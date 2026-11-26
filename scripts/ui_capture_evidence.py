"""Explicit, read-only PNG metadata consistency preflight; not authentication."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from trace_closeout import open_artifact

JSON_LIMIT = 1024 * 1024
IMAGE_LIMIT = 32 * 1024 * 1024
TOTAL_LIMIT = 256 * 1024 * 1024
PNG = b'\x89PNG\r\n\x1a\n'
FIELDS = {'expected_receipt_id', 'expected_page_id', 'receipt_path',
          'native_image_path', 'image_path', 'image_sha256'}
IDENTITY = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')


class Invalid(ValueError):
    """A fixed local diagnostic, safe to return to callers."""


def require(condition, code):
    if not condition:
        raise Invalid(code)


def string(value):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= 4096
            and '\x00' not in value, 'invalid_string')
    return value


def canonical_path(value):
    """Only normalize the two macOS system aliases; never resolve user links."""
    path = Path(string(value))
    require(path.is_absolute() and '..' not in path.parts and bool(path.name), 'invalid_path')
    if sys.platform == 'darwin' and path.parts[1] in ('tmp', 'var'):
        path = Path('/private').joinpath(*path.parts[1:])
    return path


def identity(info):
    return tuple(getattr(info, key) for key in IDENTITY)


class Inputs:
    def __init__(self):
        self.total = 0
        self.sources = {}

    def unchanged(self, path, expected):
        fd = open_artifact(path)
        try:
            require(identity(os.fstat(fd)) == expected, 'source_changed')
        finally:
            os.close(fd)

    def read(self, value, limit):
        path = canonical_path(value)
        fd = open_artifact(path)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode), 'not_regular_file')
            require(before.st_size <= limit, 'file_too_large')
            require(self.total + before.st_size <= TOTAL_LIMIT, 'total_too_large')
            data = stream.read(min(limit, TOTAL_LIMIT - self.total) + 1)
            self.total += len(data)
            require(len(data) <= limit and self.total <= TOTAL_LIMIT, 'file_too_large')
            expected = identity(before)
            require(identity(os.fstat(stream.fileno())) == expected and len(data) == before.st_size,
                    'source_changed')
        self.unchanged(path, expected)
        require(path not in self.sources or self.sources[path] == expected, 'source_changed')
        self.sources[path] = expected
        return data

    def verify(self):
        for path, expected in self.sources.items():
            self.unchanged(path, expected)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_key')
        result[key] = value
    return result


def reject_constant(value):
    raise Invalid('nonfinite_json')


def finite_float(value):
    # JSON has no need for floating point metadata here; reject overflow too.
    result = float(value)
    require(result not in (float('inf'), -float('inf')), 'nonfinite_json')
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant,
                      parse_float=finite_float)


def receipt(raw, row):
    wrapper = decode(raw)
    require(isinstance(wrapper, dict) and {'command', 'exit', 'stdout', 'stderr'} <= set(wrapper)
            and not set(wrapper) - {'command', 'time', 'exit', 'stdout', 'stderr'}, 'invalid_wrapper')
    require(type(wrapper['exit']) is int and wrapper['exit'] == 0
            and wrapper['stderr'] == '' and isinstance(wrapper['stdout'], str), 'unsuccessful_wrapper')
    if 'time' in wrapper:
        string(wrapper['time'])
    argv = wrapper['command']
    require(isinstance(argv, list) and len(argv) == 5 and all(isinstance(x, str) for x in argv),
            'invalid_command')
    require(Path(argv[0]).name == 'orca' and argv[1] in ('screenshot', 'full-screenshot')
            and argv[2] == '--page' and argv[4] == '--json', 'invalid_command')
    require(argv[3] == row['expected_page_id'], 'page_mismatch')
    envelope = decode(wrapper['stdout'])
    require(isinstance(envelope, dict) and {'id', 'ok', 'result'} <= set(envelope)
            and not set(envelope) - {'id', 'ok', 'result', '_meta'}, 'invalid_receipt')
    require(envelope['ok'] is True, 'unsuccessful_receipt')
    require(envelope['id'] == row['expected_receipt_id'], 'receipt_id_mismatch')
    result = envelope['result']
    require(isinstance(result, dict) and {'format', 'path'} <= set(result)
            and not set(result) - {'format', 'path', 'expiresAt'}, 'invalid_result')
    require(result['format'] == 'png', 'invalid_format')
    if 'expiresAt' in result:
        string(result['expiresAt'])  # Informational only, never a validity/deletion clock.
    if '_meta' in envelope:
        require(isinstance(envelope['_meta'], dict) and not set(envelope['_meta']) - {'runtimeId'},
                'invalid_metadata')
        if 'runtimeId' in envelope['_meta']:
            string(envelope['_meta']['runtimeId'])
    require(canonical_path(result['path']) == canonical_path(row['native_image_path']), 'native_path_mismatch')


def check_manifest(path, *, expected_image_refs=None):
    """Verify one manifest read, optionally bound to exact current image refs."""
    inputs = Inputs()
    manifest = decode(inputs.read(path, JSON_LIMIT))
    require(isinstance(manifest, dict) and set(manifest) == {'version', 'captures'}
            and type(manifest['version']) is int and manifest['version'] == 1, 'invalid_manifest')
    rows = manifest['captures']
    require(isinstance(rows, list) and 1 <= len(rows) <= 32, 'invalid_captures')
    for row in rows:
        require(isinstance(row, dict) and set(row) == FIELDS, 'invalid_capture')
        for value in row.values():
            string(value)
        require(re.fullmatch('[a-f0-9]{64}', row['image_sha256']) is not None, 'invalid_sha256')
        for key in ('receipt_path', 'native_image_path', 'image_path'):
            canonical_path(row[key])
    if expected_image_refs is not None:
        require(isinstance(expected_image_refs, list) and 1 <= len(expected_image_refs) <= 32,
                'capture_image_refs_required')
        expected = [canonical_path(ref) for ref in expected_image_refs]
        require(len(set(expected)) == len(expected), 'duplicate_capture_image_ref')
        # Only the checked submission path imposes exact, unambiguous coverage.
        for key in ('image_path', 'native_image_path', 'expected_receipt_id'):
            values = [(canonical_path(row[key]) if key.endswith('_path') else row[key])
                      for row in rows]
            require(len(set(values)) == len(values), 'duplicate_capture_coverage')
        require(set(expected) == {canonical_path(row['image_path']) for row in rows},
                'capture_coverage_mismatch')
    diagnostics = []
    for index, row in enumerate(rows):
        try:
            receipt(inputs.read(row['receipt_path'], JSON_LIMIT), row)
            for key in ('native_image_path', 'image_path'):
                data = inputs.read(row[key], IMAGE_LIMIT)
                require(data.startswith(PNG), 'invalid_png_signature')
                require(hashlib.sha256(data).hexdigest() == row['image_sha256'], 'image_hash_mismatch')
            diagnostics.append({'index': index, 'status': 'pass', 'code': 'metadata_consistent'})
        except Invalid as error:
            diagnostics.append({'index': index, 'status': 'fail', 'code': str(error)})
        except (OSError, ValueError, TypeError, RecursionError, OverflowError):
            # Codes are fixed locally; never echo a receipt or OS exception containing paths.
            diagnostics.append({'index': index, 'status': 'fail', 'code': 'capture_invalid_or_unavailable'})
    inputs.verify()
    return {'status': 'pass' if all(x['status'] == 'pass' for x in diagnostics) else 'fail',
            'scope': 'metadata_consistency_only', 'captures': diagnostics}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('invalid_arguments')


def main(argv=None):
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('--manifest', required=True)
        args = parser.parse_args(argv)
        result = check_manifest(args.manifest)
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        result = {'status': 'invalid', 'scope': 'metadata_consistency_only',
                  'code': 'invalid_or_unavailable_input'}
    print(json.dumps(result, allow_nan=False))
    return 0 if result['status'] == 'pass' else 1


if __name__ == '__main__':
    sys.exit(main())
