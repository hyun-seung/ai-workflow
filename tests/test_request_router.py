import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import request_router as router


def request(**changes):
    value = dict(version=1, session_id='session_example', request_id='request_example', revision=1,
                 current=dict(role=None, model='gpt-6.1-sol', effort='medium'), controls={})
    value.update(changes)
    return value


def recommend(**changes):
    return dict(recommendation=dict(reason='설계 조건을 비교할 역할 지침이 도움이 됩니다.', **changes))


class RoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = router.load_catalog()

    def route(self, **changes):
        value = router.route(request(**changes), lambda: self.catalog)
        self.assertFalse(value['executable'])
        self.assertFalse(value['runtime_support_verified'])
        self.assertEqual(value['router_model_calls'], 0)
        return value

    def test_direct_and_fixed_controls_bypass_catalog_and_analysis(self):
        with patch.object(router, 'load_catalog', side_effect=AssertionError('catalog read')) as load:
            for controls in ({'mode': 'direct'}, dict(role_locked=True, model_locked=True,
                                                     effort_locked=True, delegation='forbid')):
                result = router.route(request(controls=controls), load)
                self.assertEqual(result['action'], 'direct')
                self.assertEqual(result['message'], '')
            load.assert_not_called()

    def test_unresolved_meaning_requests_current_agent_judgment_without_catalog(self):
        with patch.object(router, 'load_catalog', side_effect=AssertionError('unneeded read')) as load:
            result = router.route(request(), load)
        self.assertEqual(result['action'], 'needs_analysis')
        self.assertEqual(result['message'], '')

    def test_no_benefit_and_unchanged_are_silent_by_default(self):
        for analysis in (dict(recommendation=None), recommend(model='gpt-6.1-sol')):
            result = self.route(analysis=analysis)
            self.assertEqual(result['action'], 'direct')
            self.assertEqual(result['message'], '')

    def test_missing_input_suppresses_candidates_and_previous_reuse_without_catalog(self):
        candidate = recommend(role='architect', model='gpt-6-astra', effort='xhigh', execution='worker')
        for analysis in ({'needs_input': True}, dict(candidate, needs_input=True)):
            with self.subTest(analysis=analysis), patch.object(router, 'load_catalog', side_effect=AssertionError('catalog read')) as load:
                result = router.route(request(analysis=analysis, followup=True, previous=self.previous()), load)
                self.assertEqual((result['action'], result['reason']), ('needs_input', 'required_input_missing'))
                self.assertEqual(result['message'], '')
                self.assertEqual(result['profile']['model'], 'gpt-6.1-sol')
                self.assertIsNone(result['profile']['role'])
                self.assertFalse(result['executable'])
                self.assertEqual(result['router_model_calls'], 0)
                load.assert_not_called()

    def test_missing_input_does_not_override_direct_enable_auto_or_change_locked_profile(self):
        for mode, expected in (('direct', 'direct'), ('auto', 'unsupported')):
            with self.subTest(mode=mode):
                self.assertEqual(self.route(controls={'mode': mode}, analysis={'needs_input': True})['action'], expected)
        fixed = dict(role_locked=True, model_locked=True, effort_locked=True, delegation='forbid')
        with patch.object(router, 'load_catalog', side_effect=AssertionError('catalog read')) as load:
            result = router.route(request(controls=fixed, analysis=dict(recommend(role='architect'), needs_input=True)), load)
        self.assertEqual(result['action'], 'needs_input')
        self.assertEqual(result['profile'], router.profile(request()['current']))
        load.assert_not_called()

    def test_missing_input_flag_is_strict_and_false_keeps_existing_recommendation_contract(self):
        for flag in (None, 0, 1, 'true', []):
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, 'invalid_needs_input'):
                self.route(analysis={'needs_input': flag}, followup=True, previous=self.previous())
        with self.assertRaisesRegex(ValueError, 'recommendation_required'):
            self.route(analysis={'needs_input': False})
        self.assertEqual(self.route(analysis={'needs_input': False, 'recommendation': None})['action'], 'direct')
        self.assertEqual(self.route(analysis=dict(recommend(role='architect'), needs_input=False))['action'], 'propose')

    def test_explicit_always_frequency_gets_a_short_keep_proposal(self):
        result = self.route(controls={'frequency': 'always'}, analysis={'recommendation': None})
        self.assertTrue(result['message'])
        self.assertEqual(result['profile']['model'], 'gpt-6.1-sol')
        result = self.route(controls=dict(frequency='always', role_locked=True, model_locked=True,
                                         effort_locked=True, delegation='forbid'))
        self.assertTrue(result['message'])

    def test_direct_mode_wins_over_always_frequency(self):
        result = self.route(controls={'mode': 'direct', 'frequency': 'always'})
        self.assertEqual(result['message'], '')

    def test_role_default_does_not_silently_change_model_or_effort(self):
        result = self.route(analysis=recommend(role='architect'))
        self.assertEqual(result['action'], 'propose')
        self.assertEqual(result['changes'], ['role'])
        self.assertEqual(result['profile']['model'], 'gpt-6.1-sol')
        self.assertEqual(result['profile']['effort'], 'medium')
        self.assertEqual(result['scope'], 'request')

    def test_combined_recommendation_is_one_message_and_never_execution(self):
        result = self.route(analysis=recommend(role='architect', model='gpt-6-astra',
                                              effort='xhigh', execution='worker'))
        self.assertEqual(result['action'], 'propose')
        self.assertIn('gpt-6-astra', result['message'])
        self.assertIn('xhigh', result['message'])
        self.assertEqual(len(result['changes']), 4)

    def test_locked_dimensions_reject_instead_of_rewriting_reason(self):
        cases = [('role_locked', {'role': 'architect'}),
                 ('model_locked', {'model': 'gpt-6-astra'}),
                 ('effort_locked', {'effort': 'xhigh'})]
        for key, target in cases:
            with self.subTest(key=key):
                result = self.route(controls={key: True}, analysis=recommend(**target))
                self.assertEqual(result['action'], 'revise')
                self.assertEqual(result['violations'], [key])
                self.assertEqual(result['message'], '')

    def test_model_lock_does_not_lock_effort(self):
        result = self.route(controls={'model_locked': True}, analysis=recommend(effort='high'))
        self.assertEqual(result['action'], 'propose')

    def test_forbidden_worker_cannot_be_included_in_proposal(self):
        result = self.route(controls={'delegation': 'forbid'}, analysis=recommend(execution='worker'))
        self.assertEqual(result['action'], 'revise')
        self.assertEqual(result['violations'], ['delegation_forbidden'])

    def test_auto_is_explicitly_unsupported(self):
        self.assertEqual(self.route(controls={'mode': 'auto'})['action'], 'unsupported')

    def previous(self, **changes):
        value = dict(session_id='session_example', request_id='request_example', revision=1,
                     controls={}, selection=dict(role='tutor', model='gpt-6.1-sol',
                                                 effort='medium', execution='current'))
        value.update(changes)
        return value

    def test_same_task_selected_followup_reuses_without_analysis(self):
        result = self.route(followup=True, previous=self.previous())
        self.assertEqual(result['action'], 'reuse')
        self.assertEqual(result['message'], '')
        self.assertEqual(result['profile']['role'], 'tutor')

    def test_other_session_request_revision_and_control_change_never_reuse(self):
        for changes in ({'session_id': 'other'}, {'request_id': 'other'}, {'revision': 2},
                        {'controls': {'model_locked': True}}):
            with self.subTest(changes=changes):
                result = self.route(followup=True, previous=self.previous(**changes))
                self.assertEqual(result['action'], 'needs_analysis')
        self.assertEqual(self.route(previous=self.previous())['action'], 'needs_analysis')

    def test_new_explicit_direct_wins_over_previous_selection(self):
        result = self.route(followup=True, previous=self.previous(), controls={'mode': 'direct'})
        self.assertEqual(result['action'], 'direct')
        self.assertIsNone(result['profile']['role'])

    def test_unapproved_proposal_cannot_be_mistaken_for_previous_selection(self):
        previous = self.previous()
        del previous['selection']
        self.assertEqual(self.route(followup=True, previous=previous)['action'], 'needs_analysis')
        previous['selection'] = {'role': 'tutor'}
        with self.assertRaises(ValueError):
            self.route(followup=True, previous=previous)

    def test_unknown_current_profile_stays_unknown(self):
        result = self.route(current={}, analysis={'recommendation': None})
        self.assertIsNone(result['profile']['model'])
        self.assertIsNone(result['profile']['effort'])

    def test_unknown_roles_models_efforts_and_input_typos_fail(self):
        bad = [request(analysis=recommend(role='archtect')), request(analysis=recommend(model='made-up')),
               request(analysis=recommend(effort='ultra')), request(analysis=recommend(effort=[])),
               request(controls={'model_loked': True}), request(controls={'model_locked': 'yes'}),
               request(version=True), request(revision=True), request(followup='yes'),
               request(analysis={}), request(task='raw task is not part of this contract')]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                router.route(value, lambda: self.catalog)

    def test_route_does_not_mutate_caller_state(self):
        value = request(followup=True, previous=self.previous())
        original = copy.deepcopy(value)
        router.route(value, lambda: self.catalog)
        self.assertEqual(value, original)

    def test_no_subprocess_network_or_model_for_recommendation(self):
        with patch('subprocess.run', side_effect=AssertionError('spawn')), \
             patch('socket.socket', side_effect=AssertionError('network')):
            self.assertEqual(self.route(analysis=recommend(role='architect'))['action'], 'propose')

    def test_catalog_uses_profiles_without_reading_role_documents(self):
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path.suffix == '.md':
                raise AssertionError('detailed instruction read')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'read_text', read):
            catalog = router.load_catalog()
        self.assertEqual(catalog['architect']['model'], 'gpt-6-astra')
        self.assertEqual(len(catalog), 10)


class CommandTests(unittest.TestCase):
    def run_cli(self, raw):
        return subprocess.run([sys.executable, str(ROOT / 'scripts/request_router.py'), 'route'],
                              input=raw, capture_output=True, cwd=ROOT)

    def test_example_and_direct_command(self):
        result = self.run_cli((ROOT / 'examples/request-route.json').read_bytes())
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['action'], 'propose')
        self.assertFalse(output['executable'])
        result = self.run_cli(json.dumps(request(controls={'mode': 'direct'})).encode())
        self.assertEqual(json.loads(result.stdout)['message'], '')

    def test_bounded_strict_json_errors_never_echo_input(self):
        for raw in (b'{"secret":"PRIVATE_VALUE",', b'{"version":1,"version":1}',
                    b'{"version":NaN}', b'[' * 2000, b'x' * (router.MAX_INPUT + 1)):
            result = self.run_cli(raw)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b'')
            self.assertNotIn(b'PRIVATE_VALUE', result.stderr)

    def test_missing_input_cli_returns_success_without_a_proposal(self):
        result = self.run_cli(json.dumps(request(analysis={'needs_input': True})).encode())
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['action'], 'needs_input')
        self.assertEqual(value['message'], '')
        self.assertNotIn('changes', value)


if __name__ == '__main__':
    unittest.main()
