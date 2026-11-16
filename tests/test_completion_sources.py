"""Synthetic component checks for explicit evidence admission."""
import os
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path[:0] = [str(Path(__file__).resolve().parents[1]/'scripts')]
import completion_sources as producer
import collect_request_usage as collect
import native_record
import completion_hook as hook
import complete_request  # Load the exported consumer before fixture path setup.
import test_complete_request_integration as fixture

EXPORT_ROOT = Path(__file__).resolve().parents[1]
assert Path(producer.__file__).resolve() == EXPORT_ROOT/'scripts/completion_sources.py'
assert Path(fixture.batch.__file__).resolve() == EXPORT_ROOT/'scripts/complete_request.py'

class SourceFixture(unittest.TestCase):
    def setUp(self):
        self.f = fixture.IntegratedBatchTests('runTest')
        self.addCleanup(self.f.doCleanups); self.f.setUp()
        f = self.f
        f.source.chmod(0o600)
        Path(f.config['directory']).mkdir(mode=0o700, exist_ok=True)
        self.cfg = hook.load_config(f.config_path)
        self.fault = None
        def native(cli, *args):
            value = f.fake_native(cli, *args)
            if args[:2] == ('orchestration', 'worker-show'):
                value['result']['dispatch']['runId'] = 'wrong' if self.fault == 'run' else f.operation['run_id']
                value['result']['worker'].pop('runId', None)
                if self.fault == 'running': value['result']['observation']['status'] = 'running'
            return value
        self.native = native
        q = patch.object(collect, 'orca', native); q.start(); self.addCleanup(q.stop)
        snap = f.private/'snapshot.json'; f.save(snap, native('mock', 'orchestration', 'worker-show'))
        saved = native_record.inspect_session(f.project, f.source, 'session_example', 'task_example', 'ctx_example', f.m['operations'][0]['expected'], snap, 'sample-request', 'backend')
        self.record = f.private/'saved.json'; f.save(self.record, saved)
        self.source = dict(kind='explicit_source', record_file=str(self.record))
    def describe(self): return producer.describe(self.cfg, self.f.operation, self.source)
    def verify(self, d):
        out = self.f.private/'fresh'; out.mkdir(mode=0o700, exist_ok=True)
        return producer.verify(self.cfg, self.f.operation, self.f.m['operations'][0]['expected'], d, 'mock', out)

class ProducerTests(SourceFixture):
    def test_pure_descriptor_and_fresh_original(self):
        before = self.f.data(); d = self.describe()
        self.assertEqual(set(d), {'record_file','record_sha256','session_file','session_id','measurement_key'})
        self.assertEqual(self.verify(d)['record_path'], str(self.record))
        self.assertEqual(before, self.f.data())
    def test_private_alias_malformed(self):
        for bad in ({}, dict(self.source, extra=1), dict(kind='other', record_file=str(self.record))):
            with self.assertRaises(ValueError): producer.describe(self.cfg,self.f.operation,bad)
        self.record.chmod(0o644)
        with self.assertRaises(ValueError): self.describe()
        self.record.chmod(0o600)
        alias=self.f.private/'alias'; os.link(self.record,alias)
        with self.assertRaises(ValueError): self.describe()
    def test_fresh_native_and_changed_measurement(self):
        d=self.describe()
        for fault in ('run','running'):
            self.fault=fault
            with self.assertRaises(ValueError): self.verify(d)
        self.fault=None
        self.f.rows[4]=fixture.count((101,20,70,5));self.f.write_source()
        with self.assertRaises(ValueError):self.verify(d)


class RequestMetadataRegressionTests(SourceFixture):
    def mutations(self):
        saved = json.loads(self.record.read_text())
        for name, context in (
                ('wrong_request', dict(saved['request_context'], request_id='wrong_request')),
                ('wrong_role', dict(saved['request_context'], role='coordinator')),
                ('null_context', None), ('list_context', []), ('text_context', 'backend'),
                ('empty_context', {}), ('missing_request', dict(role='backend')),
                ('missing_role', dict(request_id='sample-request'))):
            bad = copy.deepcopy(saved); bad['request_context'] = context
            yield name, bad
        bad = copy.deepcopy(saved); del bad['request_context']
        yield 'absent_context', bad
        for version in (2, True, 1.0, '1', None):
            bad = copy.deepcopy(saved); bad['schema_version'] = version
            yield 'version_' + repr(version), bad
        bad = copy.deepcopy(saved); del bad['schema_version']
        yield 'absent_version', bad

    def test_invalid_metadata_rejected_by_describe_verify_and_y(self):
        f = self.f
        original = self.record.read_bytes()
        description = self.describe()
        cases = list(self.mutations())
        f.m['schema_version'] = 2; f.m['operations'][0]['source'] = self.source
        f.save(f.manifest, f.m)
        for name, bad in cases:
            with self.subTest(name=name):
                f.save(self.record, bad)
                calls = len(f.calls)
                with self.assertRaises(ValueError): self.describe()
                with self.assertRaises(ValueError): self.verify(description)
                with self.assertRaises(ValueError): f.run_batch('Y')
                self.assertEqual(len(f.calls), calls)
                self.assertTrue(f.work.exists())
                self.assertFalse(f.archive.exists())
                self.assertFalse(f.output.exists())
            self.record.write_bytes(original)

    def test_valid_schema1_context_allows_explicit_y(self):
        f = self.f; saved = json.loads(self.record.read_text())
        self.assertIs(type(saved['schema_version']), int)
        self.assertEqual(saved['schema_version'], 1)
        self.assertEqual(saved['request_context']['request_id'], f.operation['request_id'])
        self.assertEqual(saved['request_context']['role'], f.operation['plan']['usage_collection']['role'])
        d = self.describe(); self.assertEqual(self.verify(d)['record_path'], str(self.record))
        f.m['schema_version'] = 2; f.m['operations'][0]['source'] = self.source
        f.save(f.manifest, f.m)
        self.assertEqual(f.run_batch('Y')['status'], 'complete')
        self.assertFalse(f.work.exists())

    def test_context_change_after_archive_retains_cleanup_source(self):
        f = self.f
        f.m['schema_version'] = 2; f.m['operations'][0]['source'] = self.source
        f.save(f.manifest, f.m)
        original = fixture.resources.finalize
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[-1] == 'N':
                saved = json.loads(self.record.read_text())
                saved['request_context']['role'] = 'wrong_role'
                f.save(self.record, saved)
            return result
        with patch.object(fixture.resources, 'finalize', side_effect=mutate):
            with self.assertRaises(ValueError): f.run_batch('Y')
        self.assertTrue(f.archive.exists()); self.assertTrue(f.work.exists())
        self.assertEqual(f.data()['requests'][0]['status'], 'running')
