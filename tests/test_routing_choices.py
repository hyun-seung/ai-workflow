import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import routing_choices as choices
import request_router as router


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        self.ctx = dict(project=str(ROOT), session_id='session_example', request_id='request_example', revision=1,
                        current=dict(role=None, model='gpt-6.1-sol', effort='medium'),
                        controls=dict(model_locked=True, effort_locked=True, delegation='forbid'))
        self.catalog = router.load_catalog()
        self.state, _ = self.run_action(None, 'init', self.ctx)

    def run_action(self, state, action, payload):
        return choices.operate(state, action, payload, lambda: self.catalog)

    def offer(self, state=None, pid='proposal_one', **target):
        payload = dict(proposal_id=pid, analysis={'recommendation': dict(role='tutor', reason='명시적인 학습 방식 제안', **target)})
        return self.run_action(state or self.state, 'offer', payload)

    def decision(self, state, choice='accept', pid='proposal_one', **changes):
        payload = dict(session_id='session_example', request_id='request_example', revision=1,
                       proposal_id=pid, proposal_version=state['proposals'][pid]['version'], choice=choice)
        payload.update(changes)
        return self.run_action(state, 'choose', payload)

    def test_accept_and_duplicate_choice_only_record_one_selection(self):
        offered, result = self.offer()
        self.assertEqual(result['routing']['action'], 'propose')
        selected, _ = self.decision(offered)
        self.assertEqual(selected['selection'], 'proposal_one')
        replay, receipt = self.decision(selected)
        self.assertTrue(receipt['replay'])
        self.assertEqual(replay, selected)

    def test_reject_or_cancel_cannot_later_be_accepted(self):
        for choice in ('reject', 'cancel'):
            offered, _ = self.offer()
            ended, _ = self.decision(offered, choice)
            self.assertIsNone(ended['selection'])
            with self.assertRaisesRegex(ValueError, 'terminal'):
                self.decision(ended)

    def test_cancel_accepted_selection_revokes_future_context_not_runtime(self):
        offered, _ = self.offer()
        selected, _ = self.decision(offered)
        cancelled, _ = self.decision(selected, 'cancel')
        self.assertIsNone(cancelled['selection'])
        with self.assertRaisesRegex(ValueError, 'no_accepted'):
            choices.role_context(cancelled, ROOT)

    def test_new_proposal_expires_pending_and_old_choice_fails(self):
        first, _ = self.offer()
        second, _ = self.offer(first, 'proposal_two')
        self.assertEqual(second['proposals']['proposal_one']['status'], 'expired')
        with self.assertRaisesRegex(ValueError, 'terminal'):
            self.decision(second)

    def test_same_id_cannot_mean_a_different_target_or_revision(self):
        first, _ = self.offer()
        replay, receipt = self.offer(first)
        self.assertEqual(replay, first); self.assertTrue(receipt['replay'])
        payload = dict(proposal_id='proposal_one', analysis={'recommendation': {'role': 'architect', 'reason': '설계 검토'}})
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.run_action(first, 'offer', payload)

    def test_wrong_request_session_proposal_version_and_revision_rejected(self):
        first, _ = self.offer()
        for change in ({'session_id': 'other'}, {'request_id': 'other'}, {'revision': 2},
                       {'revision': True}, {'proposal_version': 2}, {'proposal_version': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.decision(first, **change)

    def test_context_change_requires_new_revision_and_expires_selection(self):
        offered, _ = self.offer()
        accepted, _ = self.decision(offered)
        with self.assertRaisesRegex(ValueError, 'new_revision'):
            self.run_action(accepted, 'advance', dict(self.ctx, controls={'mode': 'direct'}))
        advanced, _ = self.run_action(accepted, 'advance', dict(self.ctx, revision=2, controls={'mode': 'direct'}))
        self.assertIsNone(advanced['selection'])
        self.assertEqual(advanced['proposals']['proposal_one']['status'], 'expired')
        with self.assertRaises(ValueError):self.decision(advanced)

    def test_different_project_or_request_needs_a_different_file(self):
        for update in ({'project': '/another/project'}, {'request_id': 'other'}, {'session_id': 'other'}):
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, 'new_state_file'):
                self.run_action(self.state, 'advance', dict(self.ctx, revision=2, **update))

    def test_no_benefit_and_forbidden_changes_do_not_create_proposal(self):
        for recommendation, action in [(None, 'direct'), ({'model': 'gpt-6-astra', 'reason': '상향 제안'}, 'revise')]:
            result, receipt = self.run_action(self.state, 'offer', {'proposal_id': 'proposal_one', 'analysis': {'recommendation': recommendation}})
            self.assertEqual(receipt['routing']['action'], action)
            self.assertEqual(result['sequence'], 0)

    def test_bare_yes_is_not_a_choice_and_no_raw_prompt_can_be_stored(self):
        offered, _ = self.offer()
        with self.assertRaises(ValueError):self.decision(offered, 'yes')
        with self.assertRaises(ValueError):self.run_action(None, 'init', dict(self.ctx, prompt='PRIVATE_RAW'))
        self.assertNotIn('reason', json.dumps(offered))

    def test_only_accepted_role_context_is_loaded_and_no_runtime_calls(self):
        offered, _ = self.offer()
        with self.assertRaises(ValueError):choices.role_context(offered, ROOT)
        selected, _ = self.decision(offered)
        with patch('subprocess.run', side_effect=AssertionError('runtime')), patch('socket.socket', side_effect=AssertionError('network')):
            result = choices.role_context(selected, ROOT)
        self.assertEqual(result['role'], 'tutor')
        self.assertFalse(result['runtime_applied'])
        self.assertEqual(len(result['instructions']), 1)
        self.assertTrue(result['instructions'][0]['path'].endswith('roles/tutor.md'))

    def test_worker_and_model_changes_need_separate_runtime_connection(self):
        ctx = dict(self.ctx, controls={})
        state, _ = self.run_action(None, 'init', ctx)
        for target in ({'execution': 'worker'}, {'model': 'gpt-6-astra'}, {'effort': 'high'}):
            offered, _ = self.offer(state, **target)
            accepted, _ = self.decision(offered)
            with self.assertRaisesRegex(ValueError, 'separate_runtime'):
                choices.role_context(accepted, ROOT)

    def test_history_is_bounded_and_state_input_is_not_mutated(self):
        old = copy.deepcopy(self.state)
        with patch.object(choices, 'MAX_PROPOSALS', 1):
            offered, _ = self.offer()
            with self.assertRaisesRegex(ValueError, 'limit'):
                self.offer(offered, 'proposal_two')
        self.assertEqual(self.state, old)

    def test_corrupt_selection_and_versions_are_rejected(self):
        offered, _ = self.offer()
        for changes in ({'selection': 'proposal_one'}, {'sequence': True}):
            with self.assertRaises(ValueError):choices.validate(dict(offered, **changes))

    def test_orphan_accepted_and_stale_pending_states_are_rejected(self):
        offered, _ = self.offer()
        accepted, _ = self.decision(offered)
        with self.assertRaises(ValueError):
            choices.validate(dict(accepted, selection=None))
        with self.assertRaises(ValueError):
            choices.validate(dict(offered, revision=2))

    def test_context_preparation_rechecks_controls_and_catalog(self):
        offered, _ = self.offer()
        accepted, _ = self.decision(offered)
        changed = copy.deepcopy(accepted)
        changed['controls']['mode'] = 'direct'
        with self.assertRaisesRegex(ValueError, 'no_longer_valid'):
            choices.role_context(changed, ROOT)
        with patch.object(choices, 'load_catalog', return_value={}):
            with self.assertRaisesRegex(ValueError, 'unknown_role'):
                choices.role_context(accepted, ROOT)

    def test_rejecting_new_proposal_preserves_previous_accepted_selection(self):
        offered, _ = self.offer()
        accepted, _ = self.decision(offered)
        pending, _ = self.offer(accepted, 'proposal_two')
        rejected, _ = self.decision(pending, 'reject', 'proposal_two')
        self.assertEqual(rejected['selection'], 'proposal_one')
        self.assertEqual(choices.role_context(rejected, ROOT)['role'], 'tutor')

    def test_cli_state_is_private_and_cannot_be_reused_with_another_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project = root / 'project'; project.mkdir()
            other = root / 'other'; other.mkdir()
            state = root / 'private' / 'choice.json'
            command = [sys.executable, str(ROOT / 'scripts/routing_choices.py'), 'init', '--project', str(project), '--state', str(state)]
            result = subprocess.run(command, input=json.dumps(dict(self.ctx, project=str(project))).encode(), capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)
            self.assertFalse(json.loads(result.stdout)['executable'])
            result = subprocess.run([*command[:2], 'show', '--project', str(other), '--state', str(state)], capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b'')

    def test_followup_reuses_only_accepted_selection_without_loading_instructions(self):
        offered, _ = self.offer()
        accepted, _ = self.decision(offered)
        before = copy.deepcopy(accepted)
        with patch.object(Path, 'open', side_effect=AssertionError('instruction read')), \
                patch('subprocess.run', side_effect=AssertionError('runtime')), \
                patch('socket.socket', side_effect=AssertionError('network')):
            result = choices.reuse_selection(accepted, dict(self.ctx, followup=True), lambda: self.catalog)
        self.assertEqual(result['action'], 'reuse')
        self.assertEqual(result['profile']['role'], 'tutor')
        self.assertEqual(result['message'], '')
        self.assertFalse(result['runtime_applied'])
        self.assertFalse(result['runtime_support_verified'])
        self.assertNotIn('instructions', result)
        self.assertEqual(accepted, before)

    def test_followup_context_changes_skip_catalog_and_never_reuse(self):
        offered, _ = self.offer()
        accepted, _ = self.decision(offered)
        changed = ({'project': '/another/project'}, {'session_id': 'other'}, {'request_id': 'other'},
                   {'revision': 2}, {'current': dict(self.ctx['current'], effort='high')},
                   {'controls': dict(self.ctx['controls'], frequency='always')}, {'followup': False})
        for update in changed:
            with self.subTest(update=update):
                result = choices.reuse_selection(accepted, dict(self.ctx, followup=True) | update,
                                                lambda: self.fail('unneeded catalog read'))
                self.assertEqual(result['action'], 'needs_analysis')
                self.assertEqual(result['profile']['role'], None)

    def test_followup_direct_and_auto_controls_override_previous_choice_without_catalog(self):
        offered, _ = self.offer()
        accepted, _ = self.decision(offered)
        cases = [({'mode': 'direct'}, 'direct'), ({'mode': 'auto'}, 'unsupported'), ({'role_locked': True}, 'direct')]
        for settings, expected in cases:
            payload = dict(self.ctx, followup=True, controls=dict(self.ctx['controls'], **settings))
            result = choices.reuse_selection(accepted, payload, lambda: self.fail('unneeded catalog read'))
            self.assertEqual(result['action'], expected)
            self.assertFalse(result['executable'])

    def test_followup_requires_explicit_boolean_and_complete_context(self):
        for payload in (dict(self.ctx), dict(self.ctx, followup=1), dict(self.ctx, followup='yes'),
                        dict(self.ctx, followup=True, prompt='PRIVATE_RAW')):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                choices.reuse_selection(self.state, payload, lambda: self.catalog)

    def test_followup_pending_rejected_cancelled_and_expired_selections_cannot_reuse(self):
        offered, _ = self.offer()
        rejected, _ = self.decision(offered, 'reject')
        cancelled, _ = self.decision(offered, 'cancel')
        accepted, _ = self.decision(offered)
        expired, _ = self.run_action(accepted, 'advance', dict(self.ctx, revision=2))
        for state in (self.state, offered, rejected, cancelled, expired):
            payload = {key: state[key] for key in choices.CONTEXT_KEYS}
            result = choices.reuse_selection(state, dict(payload, followup=True), lambda: self.fail('catalog read'))
            self.assertEqual(result['action'], 'needs_analysis')
            self.assertEqual(result['reason'], 'no_accepted_selection')

    def test_followup_worker_or_model_change_is_never_reported_applied(self):
        state, _ = self.run_action(None, 'init', dict(self.ctx, controls={}))
        for target in ({'execution': 'worker'}, {'model': 'gpt-6-astra'}, {'effort': 'high'}):
            offered, _ = self.offer(state, **target)
            accepted, _ = self.decision(offered)
            payload = {key: state[key] for key in choices.CONTEXT_KEYS}
            result = choices.reuse_selection(accepted, dict(payload, followup=True), lambda: self.fail('catalog read'))
            self.assertEqual(result['action'], 'needs_analysis')
            self.assertEqual(result['reason'], 'requires_separate_runtime_connection')
            self.assertFalse(result['runtime_applied'])

    def test_followup_rechecks_catalog_before_reusing_selected_role(self):
        offered, _ = self.offer()
        accepted, _ = self.decision(offered)
        with self.assertRaisesRegex(ValueError, 'unknown_role'):
            choices.reuse_selection(accepted, dict(self.ctx, followup=True), lambda: {})

    def test_cli_followup_is_readonly_and_repeatable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project = root / 'project'; project.mkdir()
            state_path = root / 'private' / 'choice.json'
            command = [sys.executable, str(ROOT / 'scripts/routing_choices.py')]
            flags = ['--project', str(project), '--state', str(state_path)]
            ctx = dict(self.ctx, project=str(project))
            actions = [('init', ctx), ('offer', dict(proposal_id='proposal_one', analysis={
                'recommendation': {'role': 'tutor', 'reason': '학습 요청'}})),
                ('choose', dict(session_id=ctx['session_id'], request_id=ctx['request_id'], revision=1,
                                proposal_id='proposal_one', proposal_version=1, choice='accept'))]
            for action, payload in actions:
                result = subprocess.run([*command, action, *flags], input=json.dumps(payload).encode(), capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            before = state_path.read_bytes(), state_path.stat().st_mtime_ns
            outputs = []
            for _ in range(2):
                result = subprocess.run([*command, 'reuse', *flags], input=json.dumps(dict(ctx, followup=True)).encode(), capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                outputs.append(result.stdout)
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(json.loads(outputs[0])['action'], 'reuse')
            self.assertEqual((state_path.read_bytes(), state_path.stat().st_mtime_ns), before)


if __name__ == '__main__':
    unittest.main()
