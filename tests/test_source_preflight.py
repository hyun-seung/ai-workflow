import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]/'scripts'))
import source_preflight as preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root/'project'; self.project.mkdir()
        self.raw = self.root/'source'; self.write(self.raw, b'NOT JSON: PRIVATE RAW')
        self.record = self.root/'record'
        self.saved = dict(schema_version=1, kind='orca_codex_metadata', environment='orca', provider='codex',
                          project=str(self.project), session=dict(id='synthetic'), source=dict(path=str(self.raw)))
        self.write(self.record, json.dumps(self.saved).encode())

    def write(self, path, raw, mode=0o600):
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), 'wb') as stream:
            stream.write(raw)

    def run_cli(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = preflight.main(['--project', str(self.project), '--record-file', str(self.record)])
        return code, json.loads(output.getvalue())

    def test_ready_reads_only_metadata_and_preserves_files(self):
        before = {p:(p.read_bytes(), p.stat().st_mode) for p in (self.record, self.raw)}
        original = os.open
        def guarded(path, flags, *args, **kwargs):
            self.assertNotEqual(Path(path), self.raw)
            self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC), 0)
            return original(path, flags, *args, **kwargs)
        with patch.object(os, 'open', guarded), patch.object(os, 'chmod', side_effect=AssertionError):
            code, result = self.run_cli()
        self.assertEqual(code, 0); self.assertFalse(result['measurement_validated'])
        self.assertEqual(before, {p:(p.read_bytes(), p.stat().st_mode) for p in before})

    def test_mode_missing_alias_link_and_owner(self):
        for target in ('record', 'raw'):
            p = getattr(self, target); raw = p.read_bytes(); p.unlink(); self.write(p, raw, 0o644)
            self.assertEqual(self.run_cli()[1]['checks'], ['public_mode'])
            p.unlink(); self.write(p, raw)
        with patch.object(preflight.os, 'getuid', return_value=os.getuid()+1):
            self.assertEqual(self.run_cli()[1]['checks'], ['wrong_owner'])
        self.raw.unlink(); self.assertEqual(self.run_cli()[0], 1)
        self.raw.symlink_to(self.record); self.assertEqual(self.run_cli()[1]['checks'], ['alias'])
        self.raw.unlink(); os.link(self.record, self.raw)
        self.assertEqual(self.run_cli()[1]['checks'], ['hardlink'])

    def test_malformed_nonfinite_oversized_identity_and_path(self):
        for raw in (b'{', b'{"x":NaN}', b'{"x":1e999}', b'{"x":1,"x":2}', b' '* (preflight.MAX_METADATA_BYTES+1)):
            self.write(self.record, raw); self.assertEqual(self.run_cli()[0], 2)
        for change in (dict(schema_version=True), dict(session={}), dict(source=dict(path='relative')), dict(kind='wrong')):
            self.write(self.record, json.dumps(dict(self.saved, **change)).encode())
            self.assertEqual(self.run_cli()[0], 2)

    def test_public_record_location(self):
        record = self.project/'record'; self.write(record, json.dumps(self.saved).encode()); self.record = record
        self.assertEqual(self.run_cli()[1]['checks'], ['public_record_location'])

    def test_actual_cli_failure_targets_and_stages(self):
        import subprocess
        for kind in ('metadata', 'session_source'):
            for issue in ('public_mode', 'missing', 'alias', 'unreadable'):
                with self.subTest(kind=kind, issue=issue):
                    case = self.root/(kind+'_'+issue); case.mkdir()
                    source = case/'source'; record = case/'record'
                    self.write(source, b'PRIVATE RAW NOT JSON')
                    saved = dict(self.saved, source=dict(path=str(source)))
                    self.write(record, json.dumps(saved).encode())
                    target = record if kind == 'metadata' else source
                    content = target.read_bytes(); target.unlink()
                    if issue == 'alias':
                        original = case/'original'; self.write(original, content); target.symlink_to(original)
                    elif issue == 'unreadable':
                        descriptor = os.open(target, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0)
                        self.addCleanup(os.close, descriptor)
                        os.write(descriptor, content)
                    elif issue != 'missing':
                        self.write(target, content, 0o644)
                    def snapshot():
                        return {str(p.name): (p.lstat().st_mode, os.pread(descriptor, len(content), 0) if issue == 'unreadable' and p == target else p.read_bytes()) for p in case.iterdir()}
                    before = snapshot()
                    result = subprocess.run([sys.executable, '-B', preflight.__file__, '--project', str(self.project),
                                             '--record-file', str(record)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    value = json.loads(result.stdout)
                    self.assertEqual(value['checks'], [issue])
                    self.assertEqual(value['stage'], 'metadata_attributes' if kind == 'metadata' else 'source_attributes')
                    expected = dict(kind=kind)
                    if kind == 'session_source': expected['session_id'] = 'synthetic'
                    self.assertEqual(value['target'], expected)
                    self.assertNotIn(str(case), result.stdout + result.stderr)
                    self.assertEqual(result.stderr, '')
                    original_open = os.open
                    def guarded(path, flags, *args, **kwargs):
                        self.assertEqual(Path(path), record)
                        self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC), 0)
                        return original_open(path, flags, *args, **kwargs)
                    output = io.StringIO()
                    with patch.object(os, 'open', guarded), patch.object(os, 'chmod', side_effect=AssertionError), patch.object(subprocess, 'Popen', side_effect=AssertionError), contextlib.redirect_stdout(output):
                        code = preflight.main(['--project', str(self.project), '--record-file', str(record)])
                    self.assertEqual(code, 1)
                    self.assertEqual(json.loads(output.getvalue()), value)
                    self.assertEqual(before, snapshot())

    def test_prerequisite_boundary_stages_and_compatibility(self):
        def denied(path, flags, *args, **kwargs):
            if Path(path) == self.record: raise PermissionError('SECRET PRIVATE PATH')
            raise AssertionError('raw source must not open')
        with patch.object(os, 'open', denied), patch.object(os, 'chmod', side_effect=AssertionError):
            code, result = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(result['checks'], ['unreadable'])
        self.assertEqual(result['stage'], 'metadata_read')
        self.assertEqual(result['target'], dict(kind='metadata'))
        self.assertNotIn('SECRET', json.dumps(result))
        from completion_sources import private
        self.raw.unlink()
        with self.assertRaises(ValueError) as caught: private(str(self.raw))
        self.assertEqual(str(caught.exception), 'missing')

    def test_metadata_recheck_retains_only_validated_session(self):
        original = preflight.private_attributes
        calls = 0
        def changed(value, project=None):
            nonlocal calls
            calls += 1
            if calls == 4: raise preflight.PrerequisiteError('metadata_changed')
            return original(value, project)
        with patch.object(preflight, 'private_attributes', changed):
            code, result = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(result['stage'], 'metadata_recheck')
        self.assertEqual(result['target'], dict(kind='metadata', session_id='synthetic'))
