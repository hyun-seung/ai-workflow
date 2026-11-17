"""Synthetic, bounded tests for attribute-only diagnosis."""
import builtins
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import source_capacity as capacity

class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        self.source = self.project / 'synthetic.jsonl'
        self.source.write_bytes(b'not JSON')
        self.source.chmod(0o644)

    def run_cli(self, *args):
        result = subprocess.run([sys.executable, '-B', str(Path(capacity.__file__)), *args],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.stderr, '')
        self.assertLess(len(result.stdout), 600)
        self.assertNotIn(str(self.project), result.stdout)
        return json.loads(result.stdout), result.returncode

    def test_cli_boundaries_sparse(self):
        warning = capacity.MAX_BYTES * 9 // 10
        for size, status, code in [(0, 'within_limit', 0), (warning-1, 'within_limit', 0),
                                   (warning, 'near_limit', 0), (capacity.MAX_BYTES, 'near_limit', 0),
                                   (capacity.MAX_BYTES+1, 'over_limit', 1)]:
            with self.subTest(size=size):
                with self.source.open('wb') as stream:
                    stream.truncate(size)  # sparse: no large materialized payload
                result, exit_code = self.run_cli('--project', str(self.project), '--session-file', str(self.source))
                self.assertEqual((result['status'], exit_code), (status, code))
                self.assertEqual(result['source_bytes'], size)
                self.assertEqual(result['remaining_bytes'], max(0, capacity.MAX_BYTES-size))
                self.assertEqual(result['fits_parser_limit'], size <= capacity.MAX_BYTES)
                for key in ['measurement_validated', 'session_identity_verified', 'permission_granted']:
                    self.assertIs(result[key], False)

    def test_no_actions_or_mutations(self):
        db = self.project / 'synthetic.db'
        db.write_bytes(b'fixture')
        before = [(p.read_bytes(), p.stat()) for p in (self.source, db)]
        with patch.object(builtins, 'open', side_effect=AssertionError('open')), \
             patch.object(Path, 'open', side_effect=AssertionError('path open')), \
             patch.object(os, 'open', side_effect=AssertionError('descriptor')), \
             patch.object(os, 'chmod', side_effect=AssertionError('chmod')), \
             patch.object(hashlib, 'sha256', side_effect=AssertionError('hash')), \
             patch.object(subprocess, 'run', side_effect=AssertionError('native')):
            result, code = capacity.diagnose(str(self.project), str(self.source))
        self.assertEqual(code, 0)
        self.assertEqual(result['scope'], 'source_capacity_only')
        self.assertNotIn('private', result)
        self.assertNotIn('admitted', result)
        for p, (content, info) in zip((self.source, db), before):
            self.assertEqual(p.read_bytes(), content)
            self.assertEqual(capacity._identity(p.stat()), capacity._identity(info))

    def test_malformed_cli(self):
        for args in [[], ['--help'], ['--unknown', '/secret/path'],
                     ['--project'], ['--project', str(self.project), '--session-file', 'relative']]:
            result, code = self.run_cli(*args)
            self.assertEqual(code, 2)
            self.assertEqual(result['status'], 'error')

    def test_path_errors(self):
        for value in ['relative', str(self.project)+'/../x', str(self.project)+'//x', '/x/./y', '/x/']:
            self.assertEqual(capacity.diagnose(str(self.project), value)[1], 2)
        self.assertEqual(capacity.diagnose(str(self.source), str(self.source))[1], 2)
        self.assertEqual(capacity.diagnose(str(self.project), str(self.project/'absent'))[0]['code'], 'source_missing')
        self.assertEqual(capacity.diagnose(str(self.project), str(self.project))[0]['code'], 'source_not_regular')
        alias = self.project/'alias'
        alias.symlink_to(self.project, target_is_directory=True)
        self.assertEqual(capacity.diagnose(str(self.project), str(alias/self.source.name))[0]['code'], 'path_alias')
        alias.unlink()
        os.link(self.source, alias)
        self.assertEqual(capacity.diagnose(str(self.project), str(self.source))[0]['code'], 'source_links')

    def test_second_check_disappearance_and_alias(self):
        original = capacity._attributes
        for failure in [FileNotFoundError('secret'), capacity.DiagnosticError('path_alias')]:
            calls = 0
            def inspect(path):
                nonlocal calls
                if path == self.source:
                    calls += 1
                    if calls == 2:
                        raise failure
                return original(path)
            with patch.object(capacity, '_attributes', side_effect=inspect):
                result, code = capacity.diagnose(str(self.project), str(self.source))
                self.assertEqual((result['code'], code), ('source_changed', 1))
                self.assertNotIn('source_bytes', result)

    def test_owner_access_mutation_and_replacement(self):
        original = capacity._attributes
        for field, value in [('st_uid', os.getuid()+1), ('st_ino', 999999), ('st_size', 1),
                             ('st_mtime_ns', 1), ('st_ctime_ns', 1), ('st_mode', stat.S_IFIFO), ('st_nlink', 2)]:
            calls = 0
            def changed(path):
                nonlocal calls
                info = original(path)
                if path == self.source:
                    calls += 1
                    if calls == 2 or field == 'st_uid':
                        fields = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
                        fields[field] = value
                        return SimpleNamespace(**fields)
                return info
            with self.subTest(field=field), patch.object(capacity, '_attributes', side_effect=changed):
                result, code = capacity.diagnose(str(self.project), str(self.source))
                self.assertEqual(code, 1)
                self.assertEqual(result['code'], 'source_owner' if field == 'st_uid' else 'source_changed')
                self.assertNotIn('source_bytes', result)
        def denied(path):
            if path == self.source:
                raise PermissionError('secret path')
            return original(path)
        with patch.object(capacity, '_attributes', side_effect=denied):
            self.assertEqual(capacity.diagnose(str(self.project), str(self.source))[0]['code'], 'source_access')


if __name__ == '__main__':
    unittest.main()
