import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import settle_worker as sw
from lifecycle_io import digest, locked
import test_completion_hook as fixtures
from test_lifecycle import snapshot


class SettleTests(unittest.TestCase):
    # Reuse the real bounded source/Stop/SQLite fixture, not its inherited tests.
    save = fixtures.CompletionHookTests.save
    write_source = fixtures.CompletionHookTests.write_source
    write_ledger = fixtures.CompletionHookTests.write_ledger
    run_hook = fixtures.CompletionHookTests.run_hook
    data = fixtures.CompletionHookTests.data

    def setUp(self):
        fixtures.CompletionHookTests.setUp(self)
        self.operation['plan'].update(previous=None)
        self.write_ledger()
        self.stop = self.run_hook()
        self.key = digest(dict(session='session_example', turn='turn_one'))
        self.output = self.private / 'settled'
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, ORCA_AGENT_SESSION_ID='coordinator_example').start()

    def native(self, live=False, **changes):
        value = snapshot()
        value['result']['dispatch']['runId'] = 'run_example'
        value['result']['worker']['stage'] = 'settled'
        value['result']['observation'].update(agentWait=None)
        if live: value['result']['observation']['status'] = 'alive'
        for section, fields in changes.items(): value['result'][section].update(fields)
        return value

    def settle(self, release=False):
        return sw.settle(self.config, 'example', self.key, self.output, 'unused', release)

    def state(self):
        return json.loads((self.output / 'example.settle.json').read_text())

    def ledger_state(self):
        return json.loads(Path(self.config['ledger']).read_text())['operations']['example']['state']

    def test_exact_exit_uses_one_query_releases_slot_without_accepting_or_db_write(self):
        before = self.data()
        with patch.object(sw, 'orca', return_value=self.native()) as call:
            result = self.settle()
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result['status'], 'exited')
        self.assertFalse(result['task_acceptance_evaluated'])
        self.assertEqual(self.ledger_state(), 'released')
        self.assertEqual(self.data(), before)
        self.assertEqual([p['name'] for p in result['attempt']['phases']], ['verify_saved_stop', 'observe_worker', 'record_exact_exit'])

    def test_live_without_explicit_release_preserves_slot(self):
        with patch.object(sw, 'orca', return_value=self.native(live=True)) as call:
            self.assertEqual(self.settle()['status'], 'awaiting_exit')
        self.assertEqual(call.call_count, 1)
        self.assertEqual(self.ledger_state(), 'bound')

    def test_release_then_fresh_exact_exit_uses_only_two_shows_and_one_release(self):
        before = self.data()
        # A short explicit sequence also proves even release_pending isn't exit evidence.
        with patch.object(sw, 'orca', side_effect=[self.native(live=True),
                          {'ok': True, 'result': {'status': 'release_pending'}}, self.native()]) as call:
            result = self.settle(True)
        self.assertEqual([c.args[2] for c in call.call_args_list], ['worker-show', 'worker-release', 'worker-show'])
        self.assertIsNotNone(self.state()['release_intent'])
        self.assertEqual(result['status'], 'exited')
        self.assertEqual(self.data(), before)

    def test_pending_and_retained_never_prove_exit_or_repeat_release(self):
        for outcome in ('release_pending', 'retained', 'unknown_new_shape'):
            with self.subTest(outcome=outcome):
                self.output = self.private / outcome
                with patch.object(sw, 'orca', side_effect=[self.native(live=True),
                       {'ok': True, 'result': {'status': outcome}}, self.native(live=True)]):
                    self.assertEqual(self.settle(True)['status'], 'awaiting_exit_after_release')
                with patch.object(sw, 'orca', return_value=self.native(live=True)) as call:
                    self.assertEqual(self.settle(True)['status'], 'release_already_requested')
                self.assertEqual(call.call_count, 1)
                self.assertEqual(self.ledger_state(), 'bound')

    def test_timeout_after_intent_never_resends_and_later_observed_exit_can_release(self):
        with patch.object(sw, 'orca', side_effect=[self.native(live=True), subprocess.TimeoutExpired('orca', 30)]):
            with self.assertRaises(subprocess.TimeoutExpired): self.settle(True)
        self.assertIsNotNone(self.state()['release_intent'])
        with patch.object(sw, 'orca', return_value=self.native(live=True)) as call:
            self.assertEqual(self.settle(True)['status'], 'release_already_requested')
        self.assertEqual(call.call_count, 1)
        with patch.object(sw, 'orca', return_value=self.native()) as call:
            self.assertEqual(self.settle(True)['status'], 'exited')
        self.assertEqual(call.call_count, 1)

    def test_wrong_task_run_remote_or_unproven_live_cannot_release(self):
        for changes in (dict(dispatch=dict(taskId='wrong')), dict(dispatch=dict(runId='wrong')),
                        dict(dispatch=dict(hostScope=dict(kind='remote'))),
                        dict(observation=dict(exactWorker=False)), dict(observation=dict(agentWait=dict(reason='prompt'))),
                        dict(worker=dict(state='working')), dict(worker=dict(stage='running'))):
            with self.subTest(changes=changes), patch.object(sw, 'orca', return_value=self.native(live=True, **changes)) as call:
                try: self.settle(True)
                except ValueError: pass
                self.assertEqual(call.call_count, 1)
                self.assertEqual(self.ledger_state(), 'bound')

    def test_absent_wait_stays_unknown_and_official_retention_never_frees_slot(self):
        value = self.native(live=True); value['result']['observation'].pop('agentWait')
        with patch.object(sw, 'orca', side_effect=[value, {'ok': True, 'result': {'status': 'retained'}}, value]) as call:
            result = self.settle(True)
        self.assertEqual(result['status'], 'awaiting_exit_after_release')
        self.assertEqual(result['attempt']['agent_wait_observation'], 'unavailable')
        self.assertEqual(call.call_count, 3)
        self.assertEqual(self.ledger_state(), 'bound')

    def test_changed_source_counters_or_missing_source_blocks_mutation(self):
        original = self.source.read_bytes()
        self.rows[4]['payload']['info']['total_token_usage']['input_tokens'] += 1
        self.write_source()
        with patch.object(sw, 'orca', return_value=self.native(live=True)) as call:
            with self.assertRaises(ValueError): self.settle(True)
        self.assertEqual(call.call_count, 1)
        self.source.write_bytes(original); self.source.unlink()
        with patch.object(sw, 'orca', return_value=self.native(live=True)) as call:
            with self.assertRaises(OSError): self.settle(True)
        self.assertEqual(call.call_count, 1)

    def test_pending_stop_or_db_missing_observation_blocks_before_native(self):
        receipt_path = Path(self.config['directory']) / (self.key + '.receipt.json')
        row = json.loads(receipt_path.read_text()); row['status'] = 'pending'; self.save(receipt_path, row)
        with patch.object(sw, 'orca') as call:
            with self.assertRaisesRegex(ValueError, 'not_collected'): self.settle(True)
        call.assert_not_called()
        row['status'] = 'collected'; self.save(receipt_path, row)
        self.store.db.execute("DELETE FROM records WHERE id=?", (row['event_id'],))
        self.store.db.commit()
        with patch.object(sw, 'orca') as call:
            with self.assertRaisesRegex(ValueError, 'usage_missing'): self.settle(True)
        call.assert_not_called()

    def test_success_replay_is_historical_no_native_or_source_access(self):
        with patch.object(sw, 'orca', return_value=self.native()): self.settle()
        before = (self.output / 'example.settle.json').read_bytes()
        self.source.unlink()
        with patch.object(sw, 'orca') as call:
            result = self.settle(True)
        call.assert_not_called()
        self.assertTrue(result['replay']); self.assertFalse(result['fresh_observation'])
        self.assertEqual((self.output / 'example.settle.json').read_bytes(), before)

    def test_saved_input_cannot_rebind_or_forge_completed_shape(self):
        with patch.object(sw, 'orca', return_value=self.native(live=True)): self.settle()
        p = self.output / 'example.settle.json'; original = json.loads(p.read_text())
        for mutate in (lambda d: d['binding'].update(receipt_key='f' * 64),
                       lambda d: d.update(release_intent=True), lambda d: d.update(completed=[]),
                       lambda d: d.update(attempts={})):
            d = copy.deepcopy(original); mutate(d); self.save(p, d)
            with patch.object(sw, 'orca') as call:
                with self.assertRaises(ValueError): self.settle(True)
            call.assert_not_called()

    def test_active_followup_prevents_old_dispatch_release(self):
        follow = copy.deepcopy(self.operation); follow['plan']['previous'] = 'example'
        follow.update(dispatch_id='ctx_follow', state='bound')
        self.operation['state'] = 'released'; self.ledger['operations']['follow'] = follow; self.write_ledger()
        with patch.object(sw, 'orca') as call:
            self.assertEqual(self.settle(True)['status'], 'lineage_active')
        call.assert_not_called()

    def test_failed_worker_exit_keeps_failed_outcome(self):
        with patch.object(sw, 'orca', return_value=self.native(dispatch=dict(status='failed'), worker=dict(state='failed'))):
            self.assertEqual(self.settle()['status'], 'exited')
        self.assertEqual(json.loads(Path(self.config['ledger']).read_text())['operations']['example']['outcome'], 'failed')

    def test_coordinator_and_shared_lock_fail_without_native_mutation(self):
        with patch.dict(os.environ, ORCA_AGENT_SESSION_ID='someone_else'), patch.object(sw, 'orca') as call:
            with self.assertRaisesRegex(ValueError, 'coordinator_mismatch'): self.settle(True)
        call.assert_not_called()
        with locked(Path(self.config['ledger']).with_suffix('.lock'), self.project), patch.object(sw, 'orca') as call:
            with self.assertRaisesRegex(ValueError, 'already_running'): self.settle(True)
        call.assert_not_called()

    def test_malformed_cli_is_short_json_error(self):
        self.config_path.write_text('[]')
        with patch('sys.stdout') as stdout:
            self.assertEqual(sw.main(['--config', str(self.config_path), '--operation-id', 'example',
                                     '--receipt-key', self.key, '--directory', str(self.output)]), 2)
        self.assertNotIn('Traceback', str(stdout.write.call_args_list))


if __name__ == '__main__': unittest.main()
