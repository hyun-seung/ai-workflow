import copy
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dispatch_guard as guard
import finish_worker
import lifecycle_io as storage
import request_router
import routing_choices as choices
import routing_dispatch as bridge
from test_dispatch_guard import plan, receipt


class RoutingDispatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.ledger = guard.Ledger(self.root / 'ledger', self.project, 'coordinator_example')
        self.path = self.root / 'choices' / 'request.json'
        self.args = SimpleNamespace(routing_choice_file=self.path, routing_revision=1,
                                    routing_proposal='proposal_example', routing_proposal_version=1,
                                    request_id='request_example', operation_id='operation_example')
        self.limits = dict(max_active_workers=2, max_dispatches_per_request=4)
        self.plan = plan(self.project, model='gpt-6.1-sol', effort='medium')
        self.catalog = request_router.load_catalog(self.project)
        context = dict(project=str(self.project), session_id=self.ledger.coordinator,
                       request_id=self.args.request_id, revision=1, current=dict(model='gpt-6.1-sol', effort='medium'), controls={})
        state, _ = choices.operate(None, 'init', context)
        state, _ = choices.operate(state, 'offer', dict(proposal_id='proposal_example', analysis={
            'recommendation': dict(role='tutor', execution='worker', reason='독립 준비를 명시한 가상 요청')}), lambda: self.catalog)
        self.decision = dict(session_id=self.ledger.coordinator, request_id=self.args.request_id, revision=1,
                             proposal_id='proposal_example', proposal_version=1, choice='accept')
        self.state, _ = choices.operate(state, 'choose', self.decision, lambda: self.catalog)
        storage.save(self.path, self.state, self.project)

    def start(self, **changes):
        current = dict(vars(self.args)); current.update(changes)
        return bridge.launch_selected(SimpleNamespace(**current), self.ledger, self.plan, 'Do the assignment.',
                                      self.limits, 'example', guard.launch)

    def operation(self):
        return self.ledger.read()['operations'][self.args.operation_id]

    def metadata(self):
        profile = dict(model='gpt-6.1-sol', effort='medium')
        return dict(requested=dict(profile, status='observed'), launch_effective=dict(profile, status='observed'),
                    observed_contexts=[dict(values=dict(profile))])

    def test_exact_selection_is_bound_and_only_selected_instructions_are_delivered(self):
        with patch.object(guard, 'invoke', return_value=(receipt(), 0)) as invoke:
            self.assertEqual(self.start()['state'], 'bound')
        passed_plan, text = invoke.call_args.args[1:]
        self.assertEqual(passed_plan['model'], 'gpt-6.1-sol')
        self.assertEqual(passed_plan['effort'], 'medium')
        self.assertTrue(text.startswith('# ai-workflow instructions'))
        self.assertIn('# Selected ai-workflow role: tutor', text)
        self.assertIn(str(choices.ROOT / 'AGENTS.md'), text)
        self.assertIn(str(choices.ROOT / 'principles/privacy.md'), text)
        self.assertIn('Source: ' + str(choices.ROOT / 'roles/tutor.md'), text)
        self.assertIn('Resolve relative references against this source file.', text)
        self.assertIn('Do the assignment.', text)
        expected = (choices.ROOT / 'roles/tutor.md').read_text()
        self.assertIn(expected, text)
        self.assertNotIn((choices.ROOT / 'roles/architect.md').read_text(), text)
        binding = storage.read_json(self.path)['proposals']['proposal_example']['dispatch_binding']
        self.assertEqual(binding['operation_id'], self.args.operation_id)
        self.assertEqual(binding['plan_digest'], storage.digest(passed_plan))

    def test_replay_does_not_launch_or_rewrite_choice(self):
        with patch.object(guard, 'invoke', return_value=(receipt(), 0)) as invoke:
            self.start()
            before = self.path.read_bytes(), self.path.stat().st_mtime_ns
            self.assertTrue(self.start()['replay'])
            invoke.assert_called_once()
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)

    def test_roleless_worker_still_receives_common_instruction_references(self):
        state = copy.deepcopy(self.state)
        state['proposals']['proposal_example']['target']['role'] = None
        storage.save(self.path, state, self.project)
        with patch.object(guard, 'invoke', return_value=(receipt(), 0)) as invoke:
            self.start()
        text = invoke.call_args.args[2]
        self.assertIn(str(choices.ROOT / 'AGENTS.md'), text)
        self.assertIn(str(choices.ROOT / 'principles/privacy.md'), text)
        self.assertNotIn('# Selected ai-workflow role:', text)

    def test_combined_spec_limit_is_checked_before_reservation_or_native_call(self):
        with patch.object(guard, 'invoke') as invoke, self.assertRaisesRegex(ValueError, 'combined_spec_too_large'):
            bridge.launch_selected(self.args, self.ledger, self.plan, 'x' * choices.MAX_BYTES,
                                   self.limits, 'example', guard.launch)
        invoke.assert_not_called()
        self.assertFalse(self.ledger.read()['operations'])
        self.assertNotIn('dispatch_binding', storage.read_json(self.path)['proposals']['proposal_example'])

    def test_one_selection_cannot_start_a_second_operation_or_ledger(self):
        with patch.object(guard, 'invoke', return_value=(receipt(), 0)) as invoke:
            self.start()
            with self.assertRaisesRegex(ValueError, 'another_operation'):
                self.start(operation_id='second_operation')
            self.ledger = guard.Ledger(self.root / 'other-ledger', self.project, self.ledger.coordinator)
            with self.assertRaisesRegex(ValueError, 'another_operation'):
                self.start()
            invoke.assert_called_once()

    def test_changed_spec_after_binding_cannot_reuse_consent(self):
        with patch.object(guard, 'invoke', return_value=(receipt(), 0)) as invoke:
            self.start()
            self.plan['writes'] = [str(self.project / 'different-scope')]
            with self.assertRaisesRegex(ValueError, 'another_operation'):
                self.start()
            invoke.assert_called_once()

    def test_transport_unknown_is_not_retried_under_new_operation(self):
        with patch.object(guard, 'invoke', side_effect=subprocess.TimeoutExpired('example', 1)) as invoke:
            self.assertEqual(self.start()['state'], 'unknown')
            self.assertTrue(self.start()['replay'])
            with self.assertRaises(ValueError):self.start(operation_id='replacement')
            invoke.assert_called_once()

    def test_limit_rejection_does_not_consume_selection(self):
        self.ledger.reserve('other_request', 'busy', plan(self.project), self.limits)
        self.limits['max_active_workers'] = 1
        with patch.object(guard, 'invoke') as invoke, self.assertRaises(guard.LimitReached):
            self.start()
        invoke.assert_not_called()
        self.assertNotIn('dispatch_binding', storage.read_json(self.path)['proposals']['proposal_example'])

    def test_cancelled_or_stale_or_other_scope_selection_never_invokes(self):
        for update in ({'selection': None}, {'session_id': 'other'}, {'request_id': 'other'},
                       {'project': str(self.root)}, {'revision': 2}):
            state = copy.deepcopy(self.state)
            state.update(update)
            if update.get('selection', 'unchanged') is None:
                state['proposals']['proposal_example']['status'] = 'cancelled'
            if update.get('revision') == 2:
                state['proposals']['proposal_example']['status'] = 'expired'; state['selection'] = None
            storage.save(self.path, state, self.project)
            with self.subTest(update=update), patch.object(guard, 'invoke') as invoke, self.assertRaises(ValueError):
                self.start()
            invoke.assert_not_called()

    def test_direct_control_and_delegation_forbid_stop_before_reservation(self):
        for update in ({'mode': 'direct'}, {'delegation': 'forbid'}):
            state = copy.deepcopy(self.state); state['controls'].update(update)
            storage.save(self.path, state, self.project)
            with patch.object(guard, 'invoke') as invoke, self.assertRaisesRegex(ValueError, 'no_longer_valid'):
                self.start()
            invoke.assert_not_called()
        self.assertFalse(self.ledger.read()['operations'])

    def test_current_ai_selection_is_not_silently_converted_to_worker(self):
        state = copy.deepcopy(self.state)
        state['proposals']['proposal_example']['target']['execution'] = 'current'
        storage.save(self.path, state, self.project)
        with patch.object(guard, 'invoke') as invoke, self.assertRaisesRegex(ValueError, 'does_not_authorize_worker'):
            self.start()
        invoke.assert_not_called()

    def test_profile_defaults_missing_or_conflicting_profile_are_not_used(self):
        for update in ({'model': None}, {'effort': None}, {'model': 'gpt-6-astra'}, {'effort': 'high'},
                       {'action': 'retry'}, {'agent': 'claude'},
                       {'worktree': 'new-child'}, {'worktree': 'path:/another/project'},
                       {'usage_collection': {'role': 'architect'}}):
            before = self.plan; self.plan = dict(self.plan, **update)
            with self.subTest(update=update), patch.object(guard, 'invoke') as invoke, self.assertRaises(ValueError):
                self.start()
            invoke.assert_not_called(); self.plan = before

    def test_missing_reference_fields_and_wrong_versions_are_rejected(self):
        for update in ({'routing_proposal_version': 2}, {'routing_revision': True}, {'routing_revision': None},
                       {'routing_proposal': 'other'}, {'routing_proposal_version': 0}):
            with self.subTest(update=update), patch.object(guard, 'invoke') as invoke, self.assertRaises(ValueError):
                self.start(**update)
            invoke.assert_not_called()

    def test_choice_lock_prevents_concurrent_cancellation_during_submission(self):
        with storage.locked(self.path.with_suffix('.lock'), self.project):
            with patch.object(guard, 'invoke') as invoke, self.assertRaisesRegex(ValueError, 'already_running'):
                self.start()
            invoke.assert_not_called()

    def test_native_call_receives_selected_model_and_effort_exactly(self):
        with patch.object(guard.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(receipt()), returncode=0)) as run:
            self.start()
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index('--model') + 1], 'gpt-6.1-sol')
        self.assertEqual(argv[argv.index('--effort') + 1], 'medium')
        self.assertEqual(argv[argv.index('--environment') + 1], 'local')

    def test_cli_accepts_opt_in_reference_without_changing_old_default_path(self):
        spec = self.root / 'task.md'; spec.write_text('Do the assignment.')
        argv = ['--project', str(self.project), '--directory', str(self.ledger.path.parent), '--orca-cli', 'example',
                'start', '--request-id', self.args.request_id, '--operation-id', self.args.operation_id,
                '--spec-file', str(spec), '--model', 'gpt-6.1-sol', '--effort', 'medium',
                '--max-active-workers', '2', '--max-dispatches-per-request', '4',
                '--routing-choice-file', str(self.path), '--routing-revision', '1',
                '--routing-proposal', 'proposal_example', '--routing-proposal-version', '1']
        with patch.dict('os.environ', ORCA_AGENT_SESSION_ID=self.ledger.coordinator), \
                patch.object(guard, 'invoke', return_value=(receipt(), 0)) as invoke, redirect_stdout(io.StringIO()):
            self.assertEqual(guard.main(argv), 0)
            invoke.assert_called_once()

    def test_completion_uses_selected_expectation_and_rejects_conflicting_input(self):
        with patch.object(guard, 'invoke', return_value=(receipt(), 0)):self.start()
        operation = self.operation()
        self.assertEqual(bridge.completion_expectations(operation, {}), dict(model='gpt-6.1-sol', effort='medium'))
        for expected, request, role in [({'model': 'gpt-6-astra'}, None, None), ({}, 'other', None), ({}, None, 'architect')]:
            with self.assertRaises(ValueError):bridge.completion_expectations(operation, expected, request, role)

    def test_all_profile_stages_must_match_and_missing_actual_is_never_filled(self):
        with patch.object(guard, 'invoke', return_value=(receipt(), 0)):self.start()
        operation = self.operation()
        record = self.metadata(); before = copy.deepcopy(record)
        self.assertEqual(bridge.compare_profiles(operation, record)['status'], 'pass')
        self.assertEqual(record, before)
        for stage in ('requested', 'launch_effective', 'observed_contexts'):
            for key in ('model', 'effort'):
                changed = self.metadata()
                row = changed[stage][0]['values'] if stage == 'observed_contexts' else changed[stage]
                row[key] = 'different'
                self.assertEqual(bridge.compare_profiles(operation, changed)['status'], 'fail')
                row[key] = None
                self.assertEqual(bridge.compare_profiles(operation, changed)['status'], 'unverified')
            changed = self.metadata(); changed.pop(stage)
            self.assertEqual(bridge.compare_profiles(operation, changed)['status'], 'unverified')

    def test_completion_cli_checks_binding_before_collection_and_reports_profile_mismatch(self):
        with patch.object(guard, 'invoke', return_value=(receipt(), 0)):self.start()
        metadata = self.root / 'choices' / 'metadata.json'
        record = self.metadata(); record['launch_effective']['model'] = 'different'
        storage.save(metadata, record, self.project)
        result = dict(record_path=str(metadata), replay=False, verification='pass')
        argv = ['--project', str(self.project), '--session-file', 'example', '--session-id', 'session_example',
                '--task-id', 'task_1', '--dispatch-id', 'ctx_1', '--directory', str(self.root / 'completion'),
                '--dispatch-directory', str(self.ledger.path.parent), '--operation-id', self.args.operation_id,
                '--orca-cli', 'example', '--auto-cleanup', 'N']
        output = io.StringIO()
        with patch.dict('os.environ', ORCA_AGENT_SESSION_ID=self.ledger.coordinator), \
                patch.object(finish_worker, 'finish', return_value=result) as finish, \
                patch.object(guard.Ledger, 'refresh', return_value={'state': 'released'}), redirect_stdout(output):
            self.assertEqual(finish_worker.main(argv), 1)
        self.assertEqual(finish.call_args.args[7], dict(model='gpt-6.1-sol', effort='medium'))
        self.assertIn('routing_profile=fail', output.getvalue())
        argv[argv.index('--dispatch-id') + 1] = 'other'
        with patch.dict('os.environ', ORCA_AGENT_SESSION_ID=self.ledger.coordinator), \
                patch.object(finish_worker, 'finish') as finish, redirect_stderr(io.StringIO()):
            self.assertEqual(finish_worker.main(argv), 2)
        finish.assert_not_called()


if __name__ == '__main__':
    unittest.main()
