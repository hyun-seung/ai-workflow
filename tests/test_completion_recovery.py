import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import completion_hook as hook
import completion_recovery as recovery
from lifecycle_io import digest, locked
import test_completion_hook as hook_tests
from test_dispatch_usage import boundary, count
from test_native_record import assigned, context


class RecoveryTests(unittest.TestCase):
    setUp = hook_tests.CompletionHookTests.setUp
    save = hook_tests.CompletionHookTests.save
    write_source = hook_tests.CompletionHookTests.write_source
    write_ledger = hook_tests.CompletionHookTests.write_ledger
    run_hook = hook_tests.CompletionHookTests.run_hook
    data = hook_tests.CompletionHookTests.data

    def key(self):
        return digest(dict(session=self.payload['session_id'], turn=self.payload['turn_id']))

    def status(self, **kwargs): return recovery.inspect(hook.load_config(self.config_path), **kwargs)
    def retry(self): return recovery.retry(hook.load_config(self.config_path), 'example', self.key())
    def receipts(self): return list(Path(self.config['directory']).glob('*.receipt.json'))
    def usage(self): return [e for e in self.data()['events'] if e['kind'] == 'usage']
    def cli(self, *args):
        return subprocess.run([sys.executable, str(Path(recovery.__file__)), '--config', str(self.config_path), *args],
                              capture_output=True, text=True, timeout=10)

    def test_status_reports_missing_without_reading_source_or_mutating_database(self):
        before = self.data()
        with patch.object(hook, 'read_session', side_effect=AssertionError('source must not be read')):
            status = self.status()
        self.assertEqual(status['counts']['missing'], 1)
        self.assertEqual(status['operations'][0]['reason'], 'no_received_stop_receipt')
        self.assertFalse(status['full_usage_complete']); self.assertEqual(before, self.data())
        self.assertEqual(self.receipts(), [])

    def test_collected_status_verifies_saved_record_and_database_without_source(self):
        self.run_hook(); self.source.unlink(); before = self.data()
        self.assertEqual(self.status()['counts']['collected'], 1)
        self.assertEqual(self.retry()['outcome'], 'error')
        self.assertEqual(self.status()['receipts'][0]['last_recovery']['outcome'], 'error')
        self.assertEqual(before, self.data())

    def test_restart_retries_saved_pending_in_new_process_without_duplicate_tokens(self):
        final = self.rows.pop(); self.write_source(); self.run_hook()
        self.assertEqual(self.status()['counts']['pending'], 1)
        self.rows.append(final); self.write_source()
        for _ in range(2):
            result = self.cli('retry', '--operation-id', 'example', '--receipt-key', self.key())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.usage()), 1)
        self.assertEqual(self.usage()[0]['detail']['usage']['measurements']['total_tokens'], 120)
        self.assertEqual(self.status()['counts']['collected'], 1)
        self.assertFalse(json.loads(result.stdout)['worker_exit_verified'])

    def test_import_failure_reason_and_later_retry_preserve_intent_and_actor(self):
        before = self.data()['actors']
        with patch.object(hook, 'append_usage', side_effect=ValueError('simulated_import_failure')):
            with self.assertRaises(ValueError): self.run_hook()
            result = self.retry()
        self.assertEqual(result['outcome'], 'error')
        self.assertEqual(result['issue'], 'simulated_import_failure')
        status = self.status()
        self.assertEqual(status['receipts'][0]['reason'], 'saved_import_intent')
        self.assertEqual(status['receipts'][0]['last_recovery']['issue'], 'simulated_import_failure')
        self.assertEqual(self.retry()['outcome'], 'collected')
        self.assertEqual(self.data()['actors'], before); self.assertEqual(len(self.usage()), 1)

    def test_database_restore_is_detected_and_replay_restores_one_event(self):
        self.run_hook()
        with self.store.db:
            self.store.db.execute("DELETE FROM records WHERE kind='event' AND json_extract(body,'$.kind')='usage'")
        self.assertEqual(self.status()['receipts'][0]['reason'], 'usage_missing_from_database')
        self.assertEqual(self.retry()['outcome'], 'collected')
        self.assertEqual(len(self.usage()), 1); self.assertEqual(self.status()['counts']['collected'], 1)

    def test_changed_native_counters_refuse_collected_replay_before_and_after_db_restore(self):
        self.run_hook(); before = self.data(); receipt_before = self.receipts()[0].read_bytes()
        info = self.rows[4]['payload']['info']
        for name in ('total_token_usage', 'last_token_usage'):
            info[name]['input_tokens'] += 100; info[name]['total_tokens'] += 100
        self.write_source()
        self.assertEqual(self.retry()['issue'], 'hook_measurement_changed')
        self.assertEqual(before, self.data()); self.assertEqual(self.receipts()[0].read_bytes(), receipt_before)
        with self.store.db:
            self.store.db.execute("DELETE FROM records WHERE kind='event' AND json_extract(body,'$.kind')='usage'")
        self.assertEqual(self.retry()['outcome'], 'error'); self.assertEqual(self.usage(), [])

    def test_profile_change_is_rejected_but_future_assigned_turn_append_is_allowed(self):
        self.run_hook(); original = copy.deepcopy(self.rows); before = self.data()
        self.rows[2]['payload']['effort'] = 'high'; self.write_source()
        self.assertEqual(self.retry()['issue'], 'hook_source_context_or_binding_changed')
        self.rows = original + [boundary('task_started', 'turn_two'), context(effort='high'), assigned('task_two','ctx_two'),
                                count((300, 50, 200, 10), (200, 30, 130, 5)), boundary('task_complete','turn_two')]
        self.write_source(); self.assertEqual(self.retry()['outcome'], 'collected')
        self.assertEqual(before, self.data())

    def test_process_death_before_and_after_db_commit_recovers_in_fresh_process(self):
        for phase in ('before', 'after'):
            with self.subTest(phase=phase):
                code = '''import os,sys
sys.path.insert(0,sys.argv[1])
import completion_hook as h
real=h.append_usage
def crash(*args):
 if sys.argv[3]=='after': real(*args)
 os._exit(77)
h.append_usage=crash
import json
h.collect(h.load_config(sys.argv[2]),json.loads(sys.argv[4]),attempts=1,delay=0)
'''
                # Reset to the pre-import state for the second crash point.
                if phase == 'after':
                    self.store.db.execute("DELETE FROM records WHERE kind='event' AND json_extract(body,'$.kind') IN ('usage','profile')")
                    self.store.db.execute('DELETE FROM collections'); self.store.db.commit()
                    for p in Path(self.config['directory']).glob('*.json'): p.unlink()
                    audit = recovery.audit_path(self.config, self.key())
                    if audit.exists(): audit.unlink()
                dead = subprocess.run([sys.executable, '-c', code, str(Path(hook.__file__).parent), str(self.config_path), phase,
                                       json.dumps(self.payload)], capture_output=True, text=True, timeout=10)
                self.assertEqual(dead.returncode, 77, dead.stderr)
                pending = json.loads(self.receipts()[0].read_text()); self.assertEqual(pending['status'], 'pending')
                self.assertIn('snapshot', pending)
                if phase == 'after': self.assertEqual(len(self.usage()), 1)
                result = self.cli('retry', '--operation-id', 'example', '--receipt-key', self.key())
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(len(self.usage()), 1)
                self.assertEqual(self.status()['counts']['collected'], 1)

    def test_two_requests_in_reused_session_keep_independent_intervals_and_replay(self):
        first = self.run_hook()
        bundle = self.data(); request = dict(bundle['requests'][0], id='request-two')
        actor = dict(bundle['actors'][0], id='actor-two', request_id='request-two', role='backend', parent_id=None)
        self.store.import_bundle(dict(schema_version=1, requests=[request], actors=[actor], events=[]))
        op = copy.deepcopy(self.operation)
        op.update(task_id='task_two', dispatch_id='ctx_two', request_id='request-two')
        op['plan']['usage_collection']['actor_id'] = 'actor-two'
        self.ledger['operations']['second'] = op; self.write_ledger()
        self.assertEqual(self.status()['counts']['missing'], 1)
        self.rows += [boundary('task_started', 'turn_two'), context(), assigned('task_two', 'ctx_two'),
                      count((300, 50, 200, 10), (200, 30, 130, 5)), boundary('task_complete', 'turn_two')]
        self.write_source(); self.payload['turn_id'] = 'turn_two'; self.run_hook()
        self.payload['turn_id'] = 'turn_one'; self.run_hook()
        self.assertEqual(first['event_id'], self.usage()[0]['id'])
        other = self.store.request('request-two'); u = next(e for e in other['events'] if e['kind'] == 'usage')
        self.assertEqual(u['detail']['usage']['measurements']['total_tokens'], 230)
        self.assertEqual(self.status()['counts']['collected'], 2)
        self.assertEqual(self.status(request_id='request-two')['expected_operations'], 1)

    def test_aborted_or_unclosed_turn_is_not_promoted_to_zero_or_collected(self):
        self.rows[-1] = boundary('turn_aborted'); self.write_source(); self.run_hook()
        result = self.cli('retry', '--operation-id', 'example', '--receipt-key', self.key())
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.status()['counts']['pending'], 1); self.assertEqual(self.usage(), [])

    def test_concurrent_sessions_keep_separate_receipts_and_commit_each_usage_once(self):
        op = copy.deepcopy(self.operation); op.update(task_id='task_two', dispatch_id='ctx_two')
        self.ledger['operations']['second'] = op; self.write_ledger()
        rows = copy.deepcopy(self.rows); rows[0]['payload']['id'] = 'session_two'
        rows[3] = assigned('task_two', 'ctx_two')
        source = self.root/'session-two.jsonl'; source.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        payloads = [self.payload, dict(self.payload, session_id='session_two', transcript_path=str(source))]
        code = 'import json,sys;sys.path.insert(0,sys.argv[1]);import completion_hook as h;print(json.dumps(h.collect(h.load_config(sys.argv[2]),json.loads(sys.argv[3]),attempts=1,delay=0)))'
        processes = [subprocess.Popen([sys.executable,'-c',code,str(Path(hook.__file__).parent),str(self.config_path),json.dumps(p)],
                                      stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for p in payloads]
        try:
            for process in processes:
                stdout,stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode,0,stderr); self.assertEqual(json.loads(stdout)['status'],'collected')
        finally:
            for process in processes:
                if process.poll() is None: process.kill(); process.wait(timeout=5)
        self.assertEqual(len(self.usage()),2); self.assertEqual(len(self.receipts()),2)
        self.assertEqual(self.status()['counts']['collected'],2)
        self.assertEqual(sum(e['detail']['usage']['measurements']['total_tokens'] for e in self.usage()),240)

    def test_binding_change_rejects_retry_before_reading_source_or_writing_audit(self):
        self.rows.pop(); self.write_source(); self.run_hook()
        self.operation['plan']['usage_collection']['actor_id'] = 'another'; self.write_ledger()
        with patch.object(hook, 'collect', side_effect=AssertionError('must not collect')):
            with self.assertRaisesRegex(ValueError, 'hook_operation_binding_changed'): self.retry()
        self.assertEqual(self.status()['counts']['needs_attention'], 1)
        self.assertFalse((Path(self.config['directory'])/'recovery').exists())

    def test_tampered_metadata_intent_or_database_never_reports_collected(self):
        row = self.run_hook(); path = Path(row['record_path']); raw = path.read_bytes()
        path.write_text('{}'); self.assertEqual(self.status()['counts']['needs_attention'], 1); path.write_bytes(raw)
        original = self.usage()[0]; bad = copy.deepcopy(original); bad['actor_id'] = 'sample-coordinator'
        self.store.db.execute("UPDATE records SET body=? WHERE id=?", (json.dumps(bad), original['id'])); self.store.db.commit()
        self.assertEqual(self.status()['receipts'][0]['reason'], 'usage_database_binding_or_value_mismatch')

    def test_pending_snapshot_tamper_is_visible_without_opening_source(self):
        with patch.object(hook, 'append_usage', side_effect=ValueError('import_failure')):
            with self.assertRaises(ValueError): self.run_hook()
        path = self.receipts()[0]; row = json.loads(path.read_text()); row['snapshot']['recorded_at'] = 'changed'; self.save(path,row)
        self.assertEqual(self.status()['counts']['needs_attention'], 1)

    def test_malformed_saved_metadata_is_reported_even_with_a_matching_digest(self):
        row = self.run_hook(); self.save(Path(row['record_path']), [])
        path = self.receipts()[0]; saved = json.loads(path.read_text()); saved['record_sha256'] = digest([]); self.save(path,saved)
        result = self.cli('status')
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertEqual(json.loads(result.stdout)['receipts'][0]['reason'],'invalid_saved_metadata')
        self.assertNotIn('Traceback',result.stderr)

    def test_invalid_key_or_unbound_receipt_does_not_trigger_collection(self):
        self.rows.pop(); self.write_source(); self.run_hook()
        with self.assertRaisesRegex(ValueError, 'invalid_receipt_key'):
            recovery.retry(self.config, 'example', '../outside')
        path = self.receipts()[0]; row = json.loads(path.read_text()); row.pop('operation_id'); row.pop('operation_binding'); self.save(path,row)
        self.assertEqual(len(self.status()['receipt_errors']), 1)
        with self.assertRaises(ValueError): self.retry()

    def test_launching_and_non_opt_in_operations_are_not_counted_as_collected(self):
        self.operation.update(state='launching', task_id=None, dispatch_id=None); self.write_ledger()
        self.assertEqual(self.status()['counts']['not_ready'], 1)
        self.operation['plan'].pop('usage_collection'); self.write_ledger()
        self.assertEqual(self.status()['status'], 'no_registered_operations')

    def test_recovery_lock_and_interrupted_audit_cannot_create_duplicate_import(self):
        self.rows.pop(); self.write_source(); self.run_hook(); key = self.key()
        path = recovery.audit_path(self.config,key)
        with locked(path.with_suffix('.lock'), self.project):
            with self.assertRaisesRegex(ValueError, 'lifecycle_already_running'): self.retry()
        final = boundary('task_complete'); self.rows.append(final); self.write_source()
        row = recovery.receipt(self.config,key)
        self.save(path,dict(version=1,key=key,binding_sha256=recovery.audit_binding(row),attempts=1,outcome='running',time=None))
        self.assertEqual(self.status()['receipts'][0]['last_recovery']['outcome'],'running')
        self.assertEqual(self.retry()['outcome'],'collected'); self.assertEqual(len(self.usage()),1)

    def test_cli_errors_are_json_and_status_does_not_mask_bad_receipts(self):
        self.run_hook(); path = self.receipts()[0]; path.write_text('['*2000+'0'+']'*2000)
        result = self.cli('status')
        self.assertEqual(result.returncode,1,result.stderr); self.assertTrue(json.loads(result.stdout)['receipt_errors'])
        self.assertNotIn('Traceback',result.stderr)
        self.operation['plan']['usage_collection']['role']='invalid'; self.write_ledger()
        result = self.cli('status'); self.assertEqual(result.returncode,2); self.assertEqual(json.loads(result.stdout)['status'],'error')


if __name__ == '__main__': unittest.main()
