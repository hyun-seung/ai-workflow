"""Synthetic v2 drafting; parsing/execution occurs only after generation."""
import copy
import json
import os
from contextlib import ExitStack
from unittest.mock import patch
import unittest
import test_draft_request_completion as old
import native_record
import completion_sources
import collect_request_usage


class DraftV2Tests(unittest.TestCase):
    def setUp(self):
        self.base = old.DraftTests('runTest'); self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.f = self.base.fixture
        self.base.data.update(schema_version=2, operation_sources=[dict(operation_id='example', source=dict(kind='stop'))])
        self.base.write()

    def explicit(self):
        f = self.f
        # Fixture construction is a separate step from generation.
        f.source.chmod(0o600)
        snap = f.private/'snapshot.json'; f.save(snap, f.fake_native('mock', 'orchestration', 'worker-show'))
        self.saved = native_record.inspect_session(f.project, f.source, 'session_example', 'task_example', 'ctx_example',
                    f.m['operations'][0]['expected'], snap, 'sample-request', 'backend')
        self.record = f.private/'saved.json'; f.save(self.record, self.saved)
        self.base.data['operation_sources'][0]['source'] = dict(kind='explicit_source', record_file=str(self.record))
        self.base.write(); f.calls.clear()

    def generate(self):
        with ExitStack() as stack:
            for obj, name in ((completion_sources, 'describe'), (native_record, 'read_session'),
                              (native_record, 'inspect_session'), (old.batch, 'prepare'),
                              (collect_request_usage, 'orca')):
                stack.enter_context(patch.object(obj, name, side_effect=AssertionError('generation runtime forbidden')))
            return self.base.build()

    def test_stop_replay_and_prepare(self):
        result = self.generate(); self.assertTrue(result['requires_review']); self.assertFalse(result['execution_authorized'])
        before = self.base.output.stat()
        self.assertTrue(self.generate()['replay'])
        after = self.base.output.stat(); self.assertEqual((before.st_ino,before.st_mtime_ns),(after.st_ino,after.st_mtime_ns))
        old.batch.prepare(self.base.output, self.f.private/'prepare', None, None)
        self.base.data['id']='changed'; self.base.write()
        with self.assertRaises(ValueError): self.generate()
        self.assertEqual(self.base.output.stat().st_mtime_ns, before.st_mtime_ns)

    def test_explicit_unchanged_prepare_and_native_mocked_n(self):
        self.explicit(); f = self.f
        paths = [self.record, f.source, f.db, self.base.input, f.config_path, f.coord, old.Path(f.config['ledger'])]
        before = {p:p.read_bytes() for p in paths}
        self.generate()
        self.assertEqual(before, {p:p.read_bytes() for p in paths}); self.assertEqual(f.calls, [])
        m = json.loads(self.base.output.read_text()); self.assertEqual(m['schema_version'], 2)
        self.assertEqual(m['operations'][0]['source'], self.base.data['operation_sources'][0]['source'])
        old.batch.prepare(self.base.output, f.private/'prepare', None, None)
        f.save(f.manifest, m)
        def native(cli, *args):
            result = f.fake_native(cli, *args)
            if args[:2] == ('orchestration', 'worker-show'):
                result['result']['dispatch']['runId'] = f.operation['run_id']
                result['result']['worker'].pop('runId', None)
            return result
        with patch.object(collect_request_usage, 'orca', native):
            self.assertEqual(f.run_batch('N')['status'], 'complete')
        self.assertTrue(f.work.exists())

    def test_metadata_faults(self):
        self.explicit()
        mutations = [(('schema_version',), True), (('schema_version',), 1.0),
                     (('dispatch_usage','measurements','total_tokens'), True), (('request_context','role'), 'frontend'),
                     (('request_context','request_id'), 'wrong'), (('session','id'), 'wrong'),
                     (('binding','task_id'), 'wrong'), (('dispatch_usage','actor_id'), 'backend'),
                     (('dispatch_usage','measurement_key'), 'bad'), (('provider',), 'other'),
                     (('verification','status'), 'fail'), (('source','path'), 'relative')]
        for keys, value in mutations:
            with self.subTest(keys=keys):
                bad = copy.deepcopy(self.saved); target = bad
                for key in keys[:-1]: target = target[key]
                target[keys[-1]] = value; self.f.save(self.record, bad)
                with self.assertRaises((ValueError, KeyError, TypeError)): self.generate()
                self.assertFalse(self.base.output.exists())
        self.f.save(self.record, self.saved); self.record.chmod(0o644)
        with self.assertRaises(ValueError): self.generate()
        self.record.chmod(0o600); os.link(self.record, self.f.private/'alias')
        with self.assertRaises(ValueError): self.generate()

    def test_selection_and_v1_strict(self):
        row = self.base.data['operation_sources'][0]
        for rows in ([], [row,row], [dict(row, operation_id='unknown')], [dict(row, extra=True)]):
            self.base.data['operation_sources'] = rows; self.base.write()
            with self.assertRaises(ValueError): self.generate()
        self.base.data.update(schema_version=1, operation_sources=[row]); self.base.write()
        self.base.assert_cli_rejection_unchanged()

    def test_path_protection_and_cli(self):
        self.explicit()
        self.saved['source']['path'] = str(self.f.source.parent)
        self.f.save(self.record, self.saved)
        with self.assertRaises(ValueError): self.generate()
        self.saved['source']['path'] = str(self.base.output)
        self.f.save(self.record, self.saved)
        with self.assertRaises(ValueError): self.generate()
        self.base.input.write_text('{bad')
        self.base.assert_cli_rejection_unchanged()

    def test_mixed_selection(self):
        self.explicit()
        self.f.ledger['operations']['second'] = copy.deepcopy(self.f.operation)
        second = self.f.ledger['operations']['second']
        second.update(task_id='task-second', dispatch_id='dispatch-second', run_id='run-second')
        second['receipt'] = dict(ok=True, result=dict(taskId='task-second', dispatchId='dispatch-second', runId='run-second'))
        self.f.write_ledger()
        self.base.data['operation_sources'].append(dict(operation_id='second', source=dict(kind='stop')))
        self.base.write(); self.generate()
        rows = json.loads(self.base.output.read_text())['operations']
        self.assertEqual([r['source']['kind'] for r in rows], ['explicit_source', 'stop'])

    def test_replay_revalidates_and_symlink_rejected(self):
        self.explicit(); self.generate()
        before = (self.base.output.read_bytes(), self.base.output.stat().st_ino, self.base.output.stat().st_mtime_ns)
        self.saved['request_context']['role'] = 'frontend'; self.f.save(self.record, self.saved)
        with self.assertRaises(ValueError): self.generate()
        self.assertEqual(before, (self.base.output.read_bytes(), self.base.output.stat().st_ino, self.base.output.stat().st_mtime_ns))
        self.saved['request_context']['role'] = 'backend'; self.f.save(self.record, self.saved)
        alias = self.f.private/'metadata-link'; alias.symlink_to(self.record)
        self.base.data['operation_sources'][0]['source']['record_file'] = str(alias); self.base.write()
        with self.assertRaises(ValueError): self.generate()
