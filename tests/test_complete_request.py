"""Focused orchestration tests: native access is always mocked or forbidden."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import complete_request as batch
from lifecycle_io import save, read_json
from trace_store import Store


TERMINALIZE = batch.terminalize


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.output = self.root / 'output'; self.output.mkdir(mode=0o700)
        self.db = self.root / 'trace.sqlite'
        db = Store(self.db); db.close()
        self.m = dict(id='batch-1', request_id='request-1', project=str(self.project),
                      coordinator_id='coordinator-1', operations=[], resources=[dict(directory=str(self.output),
                      resource_id='resource-1', acceptance=str(self.root / 'accept.json'), operation_ids=[])],
                      coordinator_turns=[], retry_pending=False)
        self.cfg = dict(trace_db=str(self.db))
        self.policy = dict(auto_cleanup='Y')
        self.closeout = dict(artifacts=[])
        self.pin = dict(input='fixed')
        self.prepared = lambda *a: (self.m, self.cfg, self.policy, {}, [], self.closeout, self.pin)
        self.calls = []
        patch.object(batch, 'terminalize').start()
        self.addCleanup(patch.stopall)
        patch('subprocess.run', side_effect=AssertionError('native_forbidden')).start()
        patch.object(batch, 'prepare', side_effect=self.prepared).start()
        patch.object(batch.recovery, 'inspect', return_value=dict(receipts=[], receipt_errors=[])).start()
        self.finalize = patch.object(batch.wr, 'finalize', side_effect=lambda *a: self.calls.append(a[-1])).start()
        self.close = patch.object(batch, 'close_request', side_effect=self.close_request).start()

    def close_request(self, store, manifest):
        self.calls.append('preflight' if Path(store.db.execute('PRAGMA database_list').fetchone()[2]) != self.db else 'close')
        return dict(event_id='closeout-1', replay=False)

    def run_batch(self):
        return batch.run(self.root / 'manifest.json', self.output, '/mock/orca')

    def test_y_orders_all_archive_before_preflight_cleanup_and_close(self):
        self.assertEqual(self.run_batch()['status'], 'complete')
        self.assertEqual(self.calls, ['N', 'preflight', 'Y', 'close'])

    def test_n_retains_sources(self):
        self.policy['auto_cleanup'] = 'N'
        self.run_batch()
        self.assertEqual(self.calls, ['N', 'preflight', 'close'])

    def test_invalid_closeout_never_deletes(self):
        self.close.side_effect = ValueError('bad_closeout')
        with self.assertRaisesRegex(ValueError, 'bad_closeout'):
            self.run_batch()
        self.assertEqual(self.calls, ['N'])
        self.assertEqual(read_json(self.output / 'request-1.batch.json')['status'], 'attention_required')

    def test_archive_failure_never_deletes(self):
        self.finalize.side_effect = ValueError('archive_failed')
        with self.assertRaises(ValueError): self.run_batch()
        self.close.assert_not_called()

    def test_cleanup_failure_does_not_close_and_can_resume(self):
        def finalize(*args):
            self.calls.append(args[-1])
            if args[-1] == 'Y': raise ValueError('cleanup_failed')
        self.finalize.side_effect = finalize
        with self.assertRaises(ValueError): self.run_batch()
        self.assertNotIn('close', self.calls)
        self.finalize.side_effect = lambda *a: self.calls.append(a[-1])
        self.assertEqual(self.run_batch()['status'], 'complete')

    def test_unknown_receipt_blocks_cleanup(self):
        batch.recovery.inspect.return_value['receipt_errors'] = [dict(reason='unknown')]
        with self.assertRaisesRegex(ValueError, 'unassociated'): self.run_batch()
        self.finalize.assert_not_called()

    def test_valid_unassociated_event_is_retained_without_authorizing_collection(self):
        warning=dict(receipt_key='f'*64,reason='receipt_not_bound_to_registered_operation')
        batch.recovery.inspect.return_value['receipt_errors']=[warning]
        result=self.run_batch()
        self.assertEqual(result['unassociated_receipts'],1)
        self.assertEqual(read_json(self.output/'request-1.batch.json')['diagnostic']['receipt_errors'],[warning])

    def test_invalid_recorded_coordinator_observation_blocks_archiving(self):
        batch.recovery.inspect.return_value['coordinator_observation_errors']=[dict(reason='bad_binding')]
        with self.assertRaisesRegex(ValueError,'coordinator_observation_attention'):self.run_batch()
        self.finalize.assert_not_called()

    def test_changed_input_after_success_preserves_success_receipt(self):
        self.run_batch(); original = read_json(self.output / 'request-1.batch.json')
        self.pin = dict(input='changed')
        with self.assertRaisesRegex(ValueError, 'binding_changed'): self.run_batch()
        self.assertEqual(read_json(self.output / 'request-1.batch.json'), original)

    def test_lock_blocks_concurrent_handle(self):
        from lifecycle_io import locked
        with locked(self.output / 'request-1.batch.lock', self.project):
            with self.assertRaisesRegex(ValueError, 'already_running'): self.run_batch()

    def test_missing_receipt_blocks_finish_and_delete(self):
        op = dict(plan=dict(usage_collection={}))
        self.prepared = None
        batch.prepare.side_effect = lambda *a: (self.m, self.cfg, self.policy, {'op-1': op}, [], self.closeout, self.pin)
        with self.assertRaisesRegex(ValueError, 'receipt_attention'): self.run_batch()
        self.finalize.assert_not_called()

    def test_real_sqlite_updates_only_selected_request_actors(self):
        db = Store(self.db)
        try:
            base = dict(source='test', source_id='synthetic', completeness='unknown')
            db.import_bundle(dict(schema_version=1, requests=[dict(base, id='request-1'), dict(base, id='other-request')],
                                  actors=[dict(base, id='coordinator-1', request_id='request-1', status='unknown'),
                                          dict(base, id='unselected', request_id='request-1', status='unknown'),
                                          dict(base, id='other-actor', request_id='other-request', status='unknown')], events=[]))
            TERMINALIZE(db, self.m, {})
            self.assertEqual(db.request('request-1')['requests'][0]['status'],'completed')
            self.assertNotIn('status',db.request('other-request')['requests'][0])
            actors = {a['id']: a['status'] for a in db.request('request-1')['actors']}
            self.assertEqual(actors, {'coordinator-1': 'completed', 'unselected': 'unknown'})
            self.assertEqual(db.request('other-request')['actors'][0]['status'], 'unknown')
        finally:
            db.close()

    def operation_fixture(self, status='collected'):
        op = dict(task_id='task-1', dispatch_id='dispatch-1',
                  plan=dict(usage_collection=dict(role='backend-developer', actor_id='actor-1', task_title='task')))
        self.m['operations'] = [dict(operation_id='op-1', expected=dict(model='test', effort='medium', reviewer='test'))]
        batch.prepare.side_effect = lambda *a: (self.m, self.cfg, self.policy, {'op-1': op}, [], self.closeout, self.pin)
        batch.recovery.inspect.return_value['receipts'] = [dict(operation_id='op-1', receipt_key='a'*64, status=status)]
        record = self.root / 'record.json'; save(record, {}, self.project)
        receipt = dict(status='collected', binding=dict(event=dict(transcript_path=str(self.root / 'source.jsonl'),
                                                                 session_id='session-1')), record_path=str(record))
        patch.object(batch.recovery, 'receipt', return_value=receipt).start()
        patch.object(batch.recovery, 'bound').start()
        patch.object(batch.recovery, 'collected').start()
        patch.object(batch.hook, 'verify_saved_measurement').start()
        patch.object(batch, 'import_usage').start()
        return patch.object(batch.fw, 'finish', return_value=dict(verification='pass', dispatch_usage_status='measured',
                                                                 record_path=str(record), replay=False)).start()

    def test_pending_retried_exactly_once(self):
        self.operation_fixture('pending'); self.m['retry_pending'] = True
        with patch.object(batch.recovery, 'retry', return_value=dict(outcome='collected')) as retry:
            self.run_batch()
            retry.assert_called_once_with(self.cfg, 'op-1', 'a'*64)

    def test_pending_without_opt_in_never_calls_finish(self):
        finish = self.operation_fixture('pending')
        with self.assertRaisesRegex(ValueError, 'pending_requires_retry'): self.run_batch()
        finish.assert_not_called(); self.finalize.assert_not_called()

    def test_profile_failure_never_archives(self):
        finish = self.operation_fixture(); finish.return_value['verification'] = 'fail'
        with self.assertRaisesRegex(ValueError, 'profile_or_usage'): self.run_batch()
        self.finalize.assert_not_called()

    def test_changed_source_never_archives(self):
        self.operation_fixture()
        batch.hook.verify_saved_measurement.side_effect = ValueError('hook_measurement_changed')
        with self.assertRaisesRegex(ValueError, 'measurement_changed'): self.run_batch()
        self.finalize.assert_not_called()

    def test_duplicate_keys_and_nan_rejected(self):
        for raw in ('{"id":1,"id":2}', '{"value":NaN}'):
            p = self.root / 'bad.json'; p.write_text(raw)
            with self.assertRaises(ValueError): batch.fingerprint(p)

    def test_path_alias_and_relative_rejected(self):
        with self.assertRaises(ValueError): batch.path('relative')
        with self.assertRaises(ValueError): batch.path(str(self.root / '..' / 'else'))

    def test_success_replay_still_preflights(self):
        self.run_batch(); self.calls.clear()
        journal=self.output/'request-1.batch.json';before=journal.read_bytes()
        self.assertTrue(self.run_batch()['replay'])
        self.assertEqual(self.calls, ['N', 'preflight', 'Y', 'close'])
        self.assertEqual(journal.read_bytes(),before)
        self.assertTrue((self.output/'request-1.batch.replay.json').exists())


if __name__ == '__main__': unittest.main()
