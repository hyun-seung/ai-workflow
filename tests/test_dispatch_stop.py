import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dispatch_guard as guard
from test_dispatch_guard import plan, receipt
from test_lifecycle import snapshot


class StopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.ledger = guard.Ledger(self.root / 'private', self.project, 'coordinator_example')
        self.limits = dict(max_active_workers=1, max_dispatches_per_request=2)
        with patch.object(guard, 'invoke', return_value=(receipt(), 0)):
            guard.launch(self.ledger, 'request_example', 'operation_example', plan(self.project), 'Task', self.limits, 'example')

    def snapshot(self, *, live=False):
        value = snapshot()
        value['result']['dispatch'].update(id='ctx_1', taskId='task_1')
        value['result']['worker'].update(dispatchId='ctx_1', state='cancelled')
        if live:
            value['result']['observation']['status'] = 'running'
        return value

    def test_stop_persists_intent_before_native_call_and_survives_timeout(self):
        def uncertain(*args):
            self.assertEqual(self.ledger.read()['operations']['operation_example']['stop_request']['state'], 'pending')
            raise subprocess.TimeoutExpired('example', 1)
        with patch.object(guard, 'orca', return_value=self.snapshot(live=True)), patch.object(guard, 'invoke_stop', side_effect=uncertain) as stop:
            first = self.ledger.stop('operation_example', 'example')
            self.assertEqual(first['state'], 'bound')
            self.assertEqual(first['stop_request']['state'], 'unknown')
            reopened = guard.Ledger(self.root / 'private', self.project, 'coordinator_example')
            self.assertTrue(reopened.stop('operation_example', 'example')['replay'])
            stop.assert_called_once()
        with self.assertRaises(guard.LimitReached):
            self.ledger.reserve('request_example', 'another', plan(self.project), self.limits)

    def test_crash_after_stop_intent_does_not_repeat_mutation(self):
        data = self.ledger.read()
        data['operations']['operation_example']['stop_request'] = {'state': 'pending', 'requested_at': 'example'}
        self.ledger.write(data)
        with patch.object(guard, 'orca', return_value=self.snapshot(live=True)), patch.object(guard, 'invoke_stop') as stop:
            self.assertTrue(self.ledger.stop('operation_example', 'example')['replay'])
        stop.assert_not_called()

    def test_foreign_identity_and_remote_scope_never_receive_stop(self):
        for mutation in ('dispatch', 'task', 'worker', 'host'):
            value = self.snapshot()
            if mutation == 'dispatch': value['result']['dispatch']['id'] = 'another'
            if mutation == 'task': value['result']['dispatch']['taskId'] = 'another'
            if mutation == 'worker': value['result']['worker']['dispatchId'] = 'another'
            if mutation == 'host': value['result']['dispatch']['hostScope']['kind'] = 'remote'
            with self.subTest(mutation=mutation), patch.object(guard, 'orca', return_value=value), \
                    patch.object(guard, 'invoke_stop') as stop, self.assertRaisesRegex(ValueError, 'identity_mismatch'):
                self.ledger.stop('operation_example', 'example')
            stop.assert_not_called()

    def test_native_refusal_or_even_success_receipt_cannot_prove_exit(self):
        for value, code in (({'ok': False, 'error': {'code': 'keep_open'}}, 1), ({'ok': True, 'result': {}}, 0)):
            data = self.ledger.read(); data['operations']['operation_example'].pop('stop_request', None); self.ledger.write(data)
            with patch.object(guard, 'orca', return_value=self.snapshot(live=True)), patch.object(guard, 'invoke_stop', return_value=(value, code)):
                self.assertEqual(self.ledger.stop('operation_example', 'example')['release'], 'unconfirmed')

    def test_exit_releases_but_does_not_reset_total_budget_or_repeat_stop(self):
        with patch.object(guard, 'orca', return_value=self.snapshot()), patch.object(guard, 'invoke_stop', return_value=({'ok': True, 'result': {}}, 0)) as stop:
            self.assertEqual(self.ledger.stop('operation_example', 'example')['state'], 'released')
            self.ledger.stop('operation_example', 'example')
            stop.assert_called_once()
        self.assertEqual(len(self.ledger.read()['operations']), 1)

    def test_stopped_worker_with_failed_dispatch_releases_without_becoming_success(self):
        # A failed checkpoint can leave a failed Dispatch and a physically stopped
        # worker. Preserve that outcome while recognizing exact exit evidence.
        value = self.snapshot()
        value['result']['dispatch'].update(status='failed', lastFailure='stopped')
        value['result']['worker'].update(state='stopped', stage='process_stopped')
        with patch.object(guard, 'orca', return_value=value) as query, \
                patch.object(guard, 'invoke_stop', return_value=({'ok': True, 'result': {'state': 'stopped'}}, 0)) as stop:
            result = self.ledger.stop('operation_example', 'example')
            self.assertEqual((result['state'], result['outcome']), ('released', 'stopped'))
            self.assertTrue(result['exit_evidence']['exact_worker'])
            count = query.call_count
            self.assertTrue(self.ledger.stop('operation_example', 'example')['replay'])
            self.assertEqual(query.call_count, count)
            stop.assert_called_once()
        self.assertEqual(len(self.ledger.read()['operations']), 1)
        successor = plan(self.project)
        successor['depends_on'] = ['operation_example']
        with self.assertRaisesRegex(guard.LimitReached, 'dependency_not_successfully_released'):
            self.ledger.reserve('request_example', 'successor', successor, self.limits)

    def test_stopped_state_does_not_replace_exit_identity_or_terminal_dispatch_checks(self):
        for change in ('live', 'unverifiable', 'task', 'dispatch', 'worker', 'remote', 'pending',
                       'failed_response', 'stop_stage', 'stop_failure', 'completed_dispatch'):
            value = self.snapshot()
            result = value['result']
            result['dispatch'].update(status='failed', lastFailure='stopped')
            result['worker'].update(state='stopped', stage='process_stopped')
            if change == 'live': result['observation']['status'] = 'running'
            elif change == 'unverifiable': result['observation']['exactWorker'] = False
            elif change == 'task': result['dispatch']['taskId'] = 'task_other'
            elif change == 'dispatch': result['dispatch']['id'] = 'ctx_other'
            elif change == 'worker': result['worker']['dispatchId'] = 'ctx_other'
            elif change == 'remote': result['dispatch']['hostScope']['kind'] = 'remote'
            elif change == 'pending': result['dispatch']['status'] = 'pending'
            elif change == 'failed_response': value['ok'] = False
            elif change == 'stop_stage': result['worker']['stage'] = 'running'
            elif change == 'stop_failure': result['dispatch']['lastFailure'] = None
            elif change == 'completed_dispatch': result['dispatch']['status'] = 'completed'
            with self.subTest(change=change), self.assertRaises(ValueError):
                guard.exited(value, 'task_1', 'ctx_1')

    def test_native_stop_has_exact_dispatch_local_environment_and_no_shell(self):
        result = subprocess.CompletedProcess('example', 1, '{"ok":false,"error":{"code":"stop_unknown"}}', '')
        with patch.object(guard.subprocess, 'run', return_value=result) as run:
            value, code = guard.invoke_stop('example', self.project, 'ctx_example')
        self.assertEqual(code, 1)
        self.assertFalse(value['ok'])
        self.assertEqual(run.call_args.args[0], ['example', 'orchestration', 'worker-stop', '--dispatch', 'ctx_example', '--environment', 'local', '--json'])
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_cancel_proven_not_started_releases_locally_and_preserves_total_budget(self):
        ledger = guard.Ledger(self.root / 'denied', self.project, 'coordinator_example')
        denial = {'ok': False, 'error': {'code': 'runtime_access_denied'}}
        with patch.object(guard, 'invoke', return_value=(denial, 1)):
            operation = guard.launch(ledger, 'request_example', 'denied_operation', plan(self.project), 'Task', self.limits, 'example')
        self.assertEqual(operation['state'], 'not_started')
        with patch.object(guard, 'orca') as native_read, patch.object(guard, 'invoke_stop') as native_stop:
            stdout = io.StringIO()
            with patch.dict(guard.os.environ, ORCA_AGENT_SESSION_ID='coordinator_example'), patch.object(sys, 'stdout', stdout):
                code = guard.main(['--project', str(self.project), '--directory', str(self.root / 'denied'),
                                   '--orca-cli', 'example', 'stop', '--operation-id', 'denied_operation'])
            self.assertEqual(code, 0)
            # The old start denial must not be printed as a new stop failure.
            result = json.loads(stdout.getvalue())
            self.assertEqual(result['state'], 'released')
            self.assertEqual(result['outcome'], 'cancelled')
            self.assertEqual(result['cancellation_evidence']['kind'], 'not_started_permission_denied')
            self.assertNotIn('exit_evidence', result)
            stored = ledger.read()['operations']['denied_operation']
            self.assertEqual(stored['receipt'], denial)
            self.assertNotIn('exit_evidence', stored)
            self.assertTrue(ledger.stop('denied_operation', 'example')['replay'])
        native_read.assert_not_called(); native_stop.assert_not_called()
        # Reopening the ledger must not reopen cancelled work or reset its count.
        reopened = guard.Ledger(self.root / 'denied', self.project, 'coordinator_example')
        with patch.object(guard, 'invoke') as native_start:
            replay = guard.launch(reopened, 'request_example', 'denied_operation', plan(self.project), 'Task', self.limits, 'example')
        self.assertTrue(replay['replay']); native_start.assert_not_called()
        reopened.reserve('request_example', 'second_operation', plan(self.project), self.limits)
        data = reopened.read(); data['operations']['second_operation'].update(state='released', outcome='succeeded'); reopened.write(data)
        with self.assertRaisesRegex(guard.LimitReached, 'max_dispatches'):
            reopened.reserve('request_example', 'third_operation', plan(self.project), self.limits)

    def test_unproven_denial_cannot_free_slot_or_trigger_native_stop(self):
        denial = {'ok': False, 'error': {'code': 'runtime_access_denied'}}
        cases = [
            dict(state='unknown', receipt=denial, returncode=1),
            dict(state='launching', receipt=None, returncode=None),
            dict(state='not_started', receipt=denial, returncode=0),
            dict(state='not_started', receipt=denial, returncode=True),
            dict(state='not_started', receipt=dict(denial, result={}), returncode=1),
            dict(state='not_started', receipt={'ok': True, 'error': denial['error']}, returncode=1),
            dict(state='not_started', receipt=denial, returncode=1, run_id='run_known'),
        ]
        for changes in cases:
            data = self.ledger.read()
            data['operations']['operation_example'].update(dict(task_id=None, dispatch_id=None, run_id=None) | changes)
            self.ledger.write(data)
            with self.subTest(changes=changes), patch.object(guard, 'orca') as native_read, \
                    patch.object(guard, 'invoke_stop') as native_stop, self.assertRaisesRegex(ValueError, 'receipt_inspection'):
                self.ledger.stop('operation_example', 'example')
            native_read.assert_not_called(); native_stop.assert_not_called()
            self.assertNotEqual(self.ledger.read()['operations']['operation_example']['state'], 'released')

    def test_malformed_identity_never_sends_stop(self):
        values = [None, [], {'ok': True, 'result': None}]
        for key in ('dispatch', 'worker'):
            value = self.snapshot(); value['result'][key] = None; values.append(value)
        value = self.snapshot(); value['result']['dispatch']['hostScope'] = []; values.append(value)
        for value in values:
            with self.subTest(value=value), patch.object(guard, 'orca', return_value=value), \
                    patch.object(guard, 'invoke_stop') as stop, self.assertRaisesRegex(ValueError, 'identity_mismatch'):
                self.ledger.stop('operation_example', 'example')
            stop.assert_not_called()

    def test_malformed_exit_after_stop_remains_unconfirmed_without_repeated_stop(self):
        bad_exit = self.snapshot(); bad_exit['result']['observation'] = None
        with patch.object(guard, 'orca', side_effect=[self.snapshot(live=True), bad_exit, bad_exit]), \
                patch.object(guard, 'invoke_stop', return_value=({'ok': True, 'result': {}}, 0)) as stop:
            result = self.ledger.stop('operation_example', 'example')
            self.assertEqual((result['state'], result['release']), ('bound', 'unconfirmed'))
            self.assertTrue(self.ledger.stop('operation_example', 'example')['replay'])
            stop.assert_called_once()
        for field, value in (('dispatch', None), ('worker', []), ('observation', []),
                             ('hostScope', None), ('dispatch_status', []), ('worker_state', {})):
            malformed = self.snapshot()
            if field == 'hostScope': malformed['result']['dispatch']['hostScope'] = value
            elif field == 'dispatch_status': malformed['result']['dispatch']['status'] = value
            elif field == 'worker_state': malformed['result']['worker']['state'] = value
            else: malformed['result'][field] = value
            with self.subTest(field=field), patch.object(guard, 'orca', return_value=malformed), \
                    patch.object(guard, 'invoke_stop') as stop:
                result = self.ledger.stop('operation_example', 'example')
                self.assertEqual((result['state'], result['release']), ('bound', 'unconfirmed'))
            stop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
