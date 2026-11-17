import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dispatch_guard as guard
import lifecycle_io as storage
import routing_flow as flow
from test_dispatch_guard import receipt
from test_lifecycle import snapshot


class FlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.path = self.root / 'private' / 'choice.json'
        self.environ = {'ORCA_AGENT_SESSION_ID': 'coordinator_example'}
        self.context = dict(project=str(self.project), session_id='coordinator_example', request_id='request_example',
                            revision=1, current=dict(model='gpt-6.1-sol', effort='medium'), controls={})

    def call(self, action, payload, **kwargs):
        return flow.handle(action, self.path, self.project, payload, environ=self.environ, cli='example', **kwargs)

    def request(self, candidate=None, *, followup=False, context=None, **extra):
        return self.call('request', dict(context=context or self.context, followup=followup,
                                        analysis={'recommendation': candidate}, **extra))

    def propose(self, worker=False):
        return self.request(dict(role='tutor', execution='worker' if worker else 'current', reason='가상 역할 제안'))['selection']

    def choose(self, ref, choice='accept'):
        return self.call('choose', dict(ref, choice=choice))

    def start(self, ref):
        spec = self.root / 'spec.txt'
        spec.write_text('Read-only fictional assignment.'); spec.chmod(0o600)
        payload = dict(selection=ref, directory=str(self.root / 'ledger'), operation_id='operation_example',
                       spec_file=str(spec), limits=dict(max_active_workers=1, max_dispatches_per_request=1))
        with patch.object(flow, 'startup', return_value=dict(environment='orca', connection={'session_identity_confirmed': True})):
            return self.call('start', payload)

    def exited_snapshot(self, *, live=False):
        value = snapshot()
        value['result']['dispatch'].update(id='ctx_1', taskId='task_1')
        value['result']['worker'].update(dispatchId='ctx_1', state='cancelled')
        if live:
            value['result']['observation']['status'] = 'running'
        return value

    def test_direct_and_explicit_control_skip_catalog_and_state_creation(self):
        with patch.object(flow, 'load_catalog', side_effect=AssertionError('unnecessary catalog')):
            self.assertEqual(self.request()['action'], 'direct')
            context = dict(self.context, controls={'mode': 'direct'})
            self.assertEqual(self.request({'unexpected': 'ignored by explicit control'}, context=context)['action'], 'direct')
        self.assertFalse(self.path.exists())

    def test_propose_accept_followup_replay_and_explicit_context_recovery(self):
        ref = self.propose()
        accepted = self.choose(ref)
        self.assertIn('튜터', accepted['guidance']['instructions'][0]['text'])
        self.assertFalse(accepted['runtime_applied'])
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        with patch.object(flow.choices, 'role_context', side_effect=AssertionError('duplicate body')):
            self.assertIsNone(self.choose(ref)['guidance'])
            result = self.call('request', dict(context=self.context, followup=True))
            self.assertEqual(result['action'], 'reuse')
            self.assertNotIn('instructions', result)
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)
        self.assertEqual(self.call('context', {'selection': ref})['role'], 'tutor')

    def test_pending_duplicate_does_not_repeat_full_recommendation(self):
        first = self.propose()
        second = self.request(dict(role='tutor', reason='다른 문구지만 같은 대상'))
        self.assertEqual(second['action'], 'pending')
        self.assertEqual(second['selection'], first)
        self.assertNotIn('routing', second)
        self.assertEqual(storage.read_json(self.path)['sequence'], 1)

    def test_reject_suppression_and_explicit_reconsideration(self):
        ref = self.propose(); self.choose(ref, 'reject')
        candidate = dict(role='tutor', reason='같은 추천')
        self.assertEqual(self.request(candidate)['reason'], 'previous_choice_declined')
        new = self.request(candidate, reconsider=True)
        self.assertEqual(new['action'], 'propose')
        self.assertNotEqual(ref, new['selection'])
        with self.assertRaisesRegex(ValueError, 'terminal'):
            self.choose(ref)

    def test_cancel_current_role_prevents_context_and_followup_reuse(self):
        ref = self.propose(); self.choose(ref); self.choose(ref, 'cancel')
        self.assertEqual(self.call('request', dict(context=self.context, followup=True))['action'], 'needs_analysis')
        with self.assertRaisesRegex(ValueError, 'accepted'):
            self.call('context', {'selection': ref})

    def test_revision_change_expires_selection_and_rejects_stale_approval(self):
        ref = self.propose(); self.choose(ref)
        self.context = dict(self.context, revision=2, controls={'mode': 'direct'})
        self.assertEqual(self.request(context=self.context)['action'], 'direct')
        self.assertIsNone(storage.read_json(self.path)['selection'])
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.choose(ref)

    def test_changed_controls_require_revision_and_new_scope_requires_new_file(self):
        self.propose()
        for changes in ({'controls': {'mode': 'direct'}}, {'request_id': 'another_request'}, {'session_id': 'another_session'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.request(context=dict(self.context, **changes))

    def test_new_analysis_takes_precedence_over_followup_reuse(self):
        ref = self.propose(); self.choose(ref)
        result = self.request(None, followup=True)
        self.assertEqual(result['action'], 'direct')

    def test_missing_input_fresh_flow_creates_no_proposal_and_can_resume(self):
        with patch.object(flow, 'load_catalog', side_effect=AssertionError('catalog read')):
            result = self.call('request', dict(context=self.context, followup=False, analysis={'needs_input': True}))
        self.assertEqual(result['action'], 'needs_input')
        self.assertFalse(self.path.exists())
        self.assertNotIn('selection', result)
        self.assertEqual(self.request(None)['action'], 'direct')
        self.assertFalse(self.path.exists())

    def test_missing_input_skips_cached_role_without_cancelling_consent(self):
        ref = self.propose(); self.choose(ref)
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        with patch.object(flow, 'load_catalog', side_effect=AssertionError('catalog read')), \
                patch.object(flow.choices, 'role_context', side_effect=AssertionError('guidance read')):
            result = self.call('request', dict(context=self.context, followup=True,
                                              analysis={'needs_input': True, 'recommendation': {'role': 'architect'}}))
        self.assertEqual(result['action'], 'needs_input')
        self.assertNotIn('guidance', result)
        self.assertNotIn('selection', result)
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)
        self.assertEqual(self.call('request', dict(context=self.context, followup=True))['action'], 'reuse')

    def test_manual_profile_change_never_creates_executable_choice(self):
        result = self.request(dict(model='gpt-6.1-terra', effort='low', reason='가상 하향 추천'))
        self.assertEqual(result['action'], 'manual')
        self.assertFalse(result['executable'])
        self.assertFalse(self.path.exists())

    def test_worker_capability_requires_exact_environment_and_explicit_profile(self):
        for environ in ({}, {'ORCA_AGENT_SESSION_ID': 'other'}, dict(self.environ, PASEO_AGENT_ID='paseo')):
            self.environ = environ
            self.assertEqual(self.request(dict(execution='worker', reason='가상 위임'))['action'], 'blocked')
        self.environ = {'ORCA_AGENT_SESSION_ID': 'coordinator_example'}
        context = dict(self.context, current={})
        self.assertEqual(self.request(dict(execution='worker', reason='프로필 없음'), context=context)['reason'],
                         'explicit_worker_profile_required')

    def test_choose_rechecks_worker_environment_without_consuming_acceptance(self):
        ref = self.propose(worker=True)
        self.environ = {}
        with self.assertRaisesRegex(ValueError, 'matching_orca'):
            self.choose(ref)
        self.assertEqual(storage.read_json(self.path)['proposals'][ref['proposal_id']]['status'], 'pending')

    def test_start_replay_keeps_native_call_and_request_budget_at_one(self):
        ref = self.propose(worker=True); self.choose(ref)
        with patch.object(guard, 'invoke', return_value=(receipt(), 0)) as invoke:
            self.assertEqual(self.start(ref)['state'], 'bound')
            self.assertTrue(self.start(ref)['replay'])
        invoke.assert_called_once()
        plan, text = invoke.call_args.args[1:]
        self.assertEqual((plan['model'], plan['effort']), ('gpt-6.1-sol', 'medium'))
        self.assertIn('# Selected ai-workflow role: tutor', text)
        # A dispatch binding must not turn a contradictory CLI failure into success.
        with patch.object(flow, 'handle', return_value=flow.output('worker', state='bound', returncode=1)), \
                patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{}'))), patch.object(sys, 'stdout', io.StringIO()):
            code = flow.main(['start', '--project', str(self.project), '--state', str(self.path)])
        self.assertEqual(code, 2)

    def test_cancel_bound_worker_stops_once_and_only_exact_exit_releases(self):
        ref = self.propose(worker=True); self.choose(ref)
        with patch.object(guard, 'invoke', return_value=(receipt(), 0)):
            self.start(ref)
        with patch.object(guard, 'orca', return_value=self.exited_snapshot(live=True)), \
                patch.object(guard, 'invoke_stop', return_value=({'ok': False, 'error': {'code': 'stop_unknown'}}, 1)) as stop:
            cancelled = self.choose(ref, 'cancel')
            self.assertEqual(cancelled['state'], 'bound')
            self.assertEqual(cancelled['release'], 'unconfirmed')
            self.choose(ref, 'cancel')
            stop.assert_called_once()
        with patch.object(guard, 'orca', return_value=self.exited_snapshot()), patch.object(guard, 'invoke_stop') as stop:
            self.assertEqual(self.call('refresh', {'selection': ref})['state'], 'released')
            stop.assert_not_called()
        with self.assertRaises(ValueError), patch.object(guard, 'invoke') as invoke:
            self.start(ref)
        invoke.assert_not_called()

    def test_unknown_start_cancellation_preserves_unresolved_slot_and_selection(self):
        ref = self.propose(worker=True); self.choose(ref)
        with patch.object(guard, 'invoke', side_effect=subprocess.TimeoutExpired('example', 1)):
            self.assertEqual(self.start(ref)['state'], 'unknown')
        with patch.object(guard, 'invoke_stop') as stop:
            result = self.choose(ref, 'cancel')
        self.assertEqual(result['action'], 'cancellation_pending')
        self.assertFalse(result['runtime_stopped'])
        self.assertTrue(result['selection_cancelled'])
        stop.assert_not_called()
        ledger = guard.Ledger(self.root / 'ledger', self.project, 'coordinator_example')
        self.assertEqual(ledger.read()['operations']['operation_example']['state'], 'unknown')

    def test_cancel_denied_start_returns_proof_without_claiming_worker_exit(self):
        ref = self.propose(worker=True); self.choose(ref)
        denied = {'ok': False, 'error': {'code': 'runtime_access_denied'}}
        with patch.object(guard, 'invoke', return_value=(denied, 1)) as native_start:
            self.assertEqual(self.start(ref)['state'], 'not_started')
        native_start.assert_called_once()
        with patch.object(guard, 'orca') as native_read, patch.object(guard, 'invoke_stop') as native_stop:
            cancelled = self.choose(ref, 'cancel')
            self.assertEqual((cancelled['state'], cancelled['outcome']), ('released', 'cancelled'))
            self.assertEqual(cancelled['cancellation_evidence']['kind'], 'not_started_permission_denied')
            self.assertFalse(cancelled['stop_request']['native_call'])
            self.assertIsNone(cancelled['dispatch_id'])
            self.assertTrue(self.choose(ref, 'cancel')['replay'])
        native_read.assert_not_called(); native_stop.assert_not_called()
        state = storage.read_json(self.path)
        self.assertIsNone(state['selection'])
        with patch.object(guard, 'invoke') as native_start, self.assertRaises(ValueError):
            self.start(ref)
        native_start.assert_not_called()

    def test_old_worker_can_be_explicitly_stopped_after_scope_revision(self):
        ref = self.propose(worker=True); self.choose(ref)
        with patch.object(guard, 'invoke', return_value=(receipt(), 0)):
            self.start(ref)
        self.context = dict(self.context, revision=2)
        self.request(None)
        with patch.object(guard, 'orca', return_value=self.exited_snapshot()), \
                patch.object(guard, 'invoke_stop', return_value=({'ok': True, 'result': {}}, 0)) as stop:
            self.assertEqual(self.call('stop', {'selection': ref})['state'], 'released')
        stop.assert_called_once()

    def test_binding_tamper_blocks_native_stop(self):
        ref = self.propose(worker=True); self.choose(ref)
        with patch.object(guard, 'invoke', return_value=(receipt(), 0)):
            self.start(ref)
        ledger = guard.Ledger(self.root / 'ledger', self.project, 'coordinator_example')
        data = ledger.read(); data['operations']['operation_example']['plan']['model'] = 'changed'
        ledger.write(data)
        with patch.object(guard, 'invoke_stop') as stop, self.assertRaisesRegex(ValueError, 'binding_changed'):
            self.call('stop', {'selection': ref})
        stop.assert_not_called()

    def test_native_preflight_requires_confirmed_session_before_submission(self):
        ref = self.propose(worker=True); self.choose(ref)
        spec = self.root / 'spec.txt'; spec.write_text('Task'); spec.chmod(0o600)
        payload = dict(selection=ref, directory=str(self.root / 'ledger'), operation_id='operation_example',
                       spec_file=str(spec), limits={'max_active_workers': 1, 'max_dispatches_per_request': 1})
        with patch.object(flow, 'startup', return_value=dict(environment='orca', connection={'session_identity_confirmed': False})), \
                patch.object(guard, 'invoke') as invoke, self.assertRaisesRegex(ValueError, 'preflight'):
            self.call('start', payload)
        invoke.assert_not_called()

    def test_private_state_cli_and_duplicate_keys(self):
        command = [sys.executable, str(flow.choices.ROOT / 'scripts/routing_flow.py'), '--project', str(self.project),
                   '--state', str(self.path)]
        payload = dict(context=self.context, followup=False, analysis={'recommendation': {'role': 'tutor', 'reason': '가상 설명'}})
        offered = subprocess.run(command + ['request'], input=json.dumps(payload), capture_output=True, text=True)
        self.assertEqual(offered.returncode, 0, offered.stderr)
        ref = json.loads(offered.stdout)['selection']
        accepted = subprocess.run(command + ['choose'], input=json.dumps(dict(ref, choice='accept')), capture_output=True, text=True)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIsNotNone(json.loads(accepted.stdout)['guidance'])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        state_text = self.path.read_text()
        self.assertNotIn('가상 설명', state_text)
        self.assertNotIn('instructions', state_text)
        bad = subprocess.run(command + ['status'], input='{"x":1,"x":2}', capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)


if __name__ == '__main__':
    unittest.main()
