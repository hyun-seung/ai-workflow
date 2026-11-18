"""Synthetic controller/guard fixtures, not evidence of live native execution."""
import copy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import test_development_workflow as fixtures
from test_development_workflow import execution
import development_entry as de
import development_workflow as dw
import lifecycle_io as io


class DevelopmentEntryTests(fixtures.DevelopmentWorkflowTests):
    def setUp(self):
        super().setUp()
        self.e = de.Entry(self.c, self.root / 'entry' / 'entry.json')
        self.choice = dict(mode='workflow', reason='independent implementation and review', instruction_ref='user-choice-1')
        self.native_check = dict(status='available', source_ref='official-status-1', observer='coordinator-example')
        self.accepted = dict(ref='explicit-user-1', project=str(self.project),
                             sha256=dw.policy_snapshot(self.project)['sha256'], limits=self.p.copy())
        self.entry_args = dict(kind='development', choice=self.choice, native=self.native_check,
                               acceptance=self.ac, files=['app.py'], policy_acceptance=self.accepted)

    def enter(self, **changes):
        return self.e.enter(**dict(self.entry_args, **changes))

    def test_entry_calls_controller_and_restart_preserves_stage(self):
        with patch.object(self.c, 'begin', wraps=self.c.begin) as begin:
            v = self.enter()
            begin.assert_called_once()
        with patch.object(self.c, 'next', wraps=self.c.next) as nxt:
            again = self.enter(policy_acceptance=None)
            nxt.assert_called_once()
        self.assertEqual(v['stage_id'], again['stage_id'])
        self.assertEqual(again['launch_permission'], 'none')
        self.assertEqual(self.e.report()['entry_observation'], 'before_controller')

    def test_policy_presence_is_not_acceptance(self):
        self.assertEqual(self.enter(policy_acceptance=None)['decision'], 'opt_in_required')
        self.assertFalse(self.state.exists())
        self.assertEqual(self.enter()['phase'], 'ready')

    def test_absent_disabled_policy_requires_setup(self):
        (self.project / dw.POLICY).unlink()
        self.assertEqual(self.enter(policy_acceptance=None)['decision'], 'opt_in_required')
        self.assertFalse((self.project / dw.POLICY).exists())
        self.p['enabled'] = False; self.write_policy()
        self.assertEqual(self.enter(policy_acceptance=None)['decision'], 'opt_in_required')

    def test_native_unknown_unavailable_available_distinct(self):
        unknown = dict(status='unknown', source_ref=None, observer=None)
        self.assertEqual(self.enter(native=unknown)['decision'], 'native_check_required')
        self.assertFalse(self.state.exists())
        unavailable = dict(self.native_check, status='unavailable')
        self.assertEqual(self.enter(native=unavailable)['decision'], 'unavailable')
        self.assertEqual(self.enter()['phase'], 'ready')
        v = self.enter(native=unknown)
        self.assertEqual(v['phase'], 'native_check_required')
        self.assertEqual(v['pending_phase'], 'ready')

    def test_direct_override_conflict_is_honest(self):
        self.choice['mode'] = 'direct'
        (self.project / dw.POLICY).write_text('invalid policy should not block explicit direct')
        self.assertTrue(self.enter()['review_conflict'])
        r = self.e.report(self_tests='pass', skills=['backend'], perspectives=['reviewer'])
        self.assertFalse(r['workflow_complete'])
        self.assertEqual(r['execution_mode'], 'solo')
        self.assertEqual(r['independent_review'], 'not_performed')
        self.assertIn('explicit_direct_conflicts_with_required_review', r['omissions'])

    def test_small_conversation_stateless_no_policy_role_read(self):
        (self.project / dw.POLICY).write_text('invalid')
        for kind in ('conversation', 'small_change'):
            with patch.object(dw, 'policy_snapshot', side_effect=AssertionError('no policy read')):
                self.assertEqual(self.enter(kind=kind), {'decision': 'direct'})
        self.assertFalse(self.e.path.exists())

    def test_changed_choice_ac_file_policy_scope_refused_unchanged(self):
        self.enter(); before = self.e.path.read_bytes()
        for change in [dict(choice=dict(self.choice, mode='direct')), dict(acceptance={'AC1': 'different'}),
                       dict(files=['other.py']), dict(policy_acceptance=dict(self.accepted, ref='different'))]:
            with self.subTest(change=change), self.assertRaises(ValueError): self.enter(**change)
            self.assertEqual(before, self.e.path.read_bytes())
        self.p['max_dispatches'] = 4; self.write_policy()
        with self.assertRaisesRegex(ValueError, 'scope_changed'): self.enter()
        self.assertEqual(before, self.e.path.read_bytes())

    def test_report_requires_real_current_review_not_selftests_or_skills(self):
        v = self.enter()
        self.assertFalse(self.e.report(self_tests='pass', perspectives=['reviewer'])['workflow_complete'])
        v = self.run_stage(v, 'dev')
        self.assertEqual(self.e.report()['independent_review'], 'not_performed')
        v = self.run_stage(v, 'review')
        r = self.e.report()
        self.assertTrue(r['workflow_complete'])
        self.assertEqual([x['role'] for x in r['executions']], ['developer', 'reviewer'])
        self.assertEqual(r['self_tests'], 'pass')
        self.assertEqual(r, io.read_json(self.e.path.with_suffix('.report.json')))
        (self.project / 'app.py').write_text('changed')
        r = self.e.report()
        self.assertEqual(r['independent_review'], 'stale_snapshot')
        self.assertFalse(r['workflow_complete'])

    def test_missing_entry_and_late_observation_cannot_backfill_history(self):
        v = self.run_stage(self.begin(), 'dev')
        self.run_stage(v, 'review')
        before = self.state.read_bytes()
        r = self.e.report(self_tests='pass')
        self.assertEqual(r['entry_observation'], 'missing_entry')
        self.assertFalse(self.e.path.exists())
        self.enter()
        r = self.e.report()
        self.assertEqual(r['entry_observation'], 'late_observation')
        self.assertFalse(r['workflow_complete'])
        self.assertEqual(before, self.state.read_bytes())

    def test_foreign_controller_and_journal_bindings_refused(self):
        self.enter()
        other = dw.Controller(self.project, 'foreign', 'coordinator-example', self.state, 'example/coordinator')
        with self.assertRaises(ValueError): de.Entry(other, self.e.path).report()
        other_entry = de.Entry(other, self.root / 'other' / 'entry.json')
        with self.assertRaises(ValueError): other_entry.report()

    def test_forged_same_actor_and_missing_receipt_never_pass_report(self):
        v = self.run_stage(self.enter(), 'dev'); self.run_stage(v, 'review')
        s = self.c._read(); original = copy.deepcopy(s)
        s['receipts'] = {}; self.c._save(s)
        self.assertFalse(self.e.report()['workflow_complete'])
        s = original
        s['stages'][-1]['native']['provider_session_id'] = 'dev'
        with patch.object(self.c, '_guard'):
            self.c._save(s)
            self.assertFalse(self.e.report()['workflow_complete'])

    def test_private_bounded_io_and_no_follow(self):
        self.enter(); self.assertEqual(self.e.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.e.path.parent.stat().st_mode & 0o777, 0o700)
        for value in ['x'*513, '', None]:
            with self.assertRaises(ValueError): self.enter(choice=dict(self.choice, reason=value))
        with self.assertRaises(ValueError): self.enter(files=['../elsewhere'])
        link = self.root / 'link.json'; link.symlink_to(self.e.path)
        with self.assertRaises((ValueError, OSError)): de.Entry(self.c, link).report()
        with self.assertRaises(ValueError): de.Entry(self.c, self.project / 'entry.json').report()
        with self.assertRaises(ValueError): self.enter(native=dict(self.native_check, observer='foreign'))
        with self.assertRaises(TypeError): self.e.report(executions=[execution()])
        self.e.path.chmod(0o644)
        with self.assertRaises(ValueError): self.e.report()

    def test_report_active_failed_cancelled_exhausted_are_not_complete(self):
        v = self.attach(self.enter())
        self.assertFalse(self.e.report()['workflow_complete'])
        self.c.complete(self.receipt(v, self_test='fail'))
        self.assertFalse(self.e.report(self_tests='pass')['workflow_complete'])

    def test_report_original_developer_snapshot_cannot_be_replaced(self):
        v = self.run_stage(self.enter(), 'dev'); self.run_stage(v, 'review')
        s = self.c._read()
        s['stages'][-2]['result']['snapshot'] = '0' * 64
        for receipt in s['receipts'].values():
            if receipt['stage_id'] == s['stages'][-2]['id']:
                receipt['result']['snapshot'] = '0' * 64
        self.c._save(s)
        self.assertFalse(self.e.report()['workflow_complete'])

    def test_pending_restart_does_not_reissue_reserved_launch(self):
        v = self.enter(); intent = self.launch_intent(v)
        self.c.reserve(v['stage_id'], intent)
        self.e = de.Entry(self.restart(), self.e.path)
        v = self.enter(policy_acceptance=None)
        self.assertEqual(v['launch_intent'], intent)
        self.assertEqual(v['launch_permission'], 'none')

    def test_bounded_json_duplicate_and_nonfinite_input(self):
        p = self.root / 'bad.json'
        for raw in ('{"kind":1,"kind":2}', '{"x":NaN}', ' '* (16*1024*1024+1)):
            p.write_text(raw)
            with self.assertRaises(ValueError): io.read_json(p)
        self.assertFalse(self.e.path.exists())

    def test_exact_review_race_refused_before_controller_admission(self):
        decision = dw.decision
        def edit_then_decide(*args, **kwargs):
            self.p['max_dispatches'] = 8
            self.write_policy()
            return decision(*args, **kwargs)
        with patch.object(dw, 'decision', side_effect=edit_then_decide):
            with self.assertRaisesRegex(ValueError, 'accepted_policy_changed_before_admission'):
                self.enter()
        self.assertFalse(self.state.exists())
        self.assertEqual(io.read_json(self.e.path)['policy_acceptance'], self.accepted)
        self.assertEqual(self.accepted['limits']['max_dispatches'], 6)

    def test_expected_acceptance_rechecked_under_original_begin_lock(self):
        locked = dw.locked
        def edit_before_controller_lock(path, project):
            if path == self.c.path.with_suffix('.lock'):
                self.p['max_fix_rounds'] = 1
                self.write_policy()
            return locked(path, project)
        with patch.object(dw, 'locked', side_effect=edit_before_controller_lock):
            with self.assertRaisesRegex(ValueError, 'accepted_policy_changed_before_admission'):
                self.enter()
        self.assertFalse(self.state.exists())

    def test_same_limits_changed_policy_bytes_are_not_new_consent(self):
        decision = dw.decision
        def reformat_then_decide(*args, **kwargs):
            (self.project / dw.POLICY).write_text(json.dumps(self.p, indent=2))
            return decision(*args, **kwargs)
        with patch.object(dw, 'decision', side_effect=reformat_then_decide):
            with self.assertRaisesRegex(ValueError, 'accepted_policy_changed_before_admission'):
                self.enter()
        self.assertFalse(self.state.exists())
        self.assertEqual(io.read_json(self.e.path)['policy_acceptance']['sha256'], self.accepted['sha256'])

    def test_exact_unsupported_forged_native_intent_receipt_report_refused(self):
        v = self.run_stage(self.enter(), 'dev'); self.run_stage(v, 'review')
        original = self.c._read()
        for client in ('paseo', 'codex'):
            s = copy.deepcopy(original)
            for stage in s['stages']:
                n = stage['native']
                n['client'] = client
                for key in ('task_id', 'dispatch_id', 'guard_directory', 'guard_operation_id', 'usage_actor_id'):
                    n[key] = None
                stage['intent'] = dw.intent_for(n)
                for receipt in s['receipts'].values():
                    if receipt['stage_id'] == stage['id']:
                        receipt['native'] = copy.deepcopy(n)
            self.c._save(s)  # Recomputed owner-writable integrity is not semantic validation.
            before = self.state.read_bytes()
            with self.assertRaisesRegex(ValueError, 'unsupported_stored_execution_client'):
                self.e.report()
            self.assertEqual(before, self.state.read_bytes())

    def test_unsupported_state_components_each_fail_closed(self):
        v = self.run_stage(self.enter(), 'dev'); self.run_stage(v, 'review')
        original = self.c._read()
        unsupported = execution('foreign', client='paseo')
        for component in ('intent', 'native', 'receipt'):
            s = copy.deepcopy(original)
            if component == 'intent':
                s['stages'][0]['intent'] = dw.intent_for(unsupported)
            elif component == 'native':
                s['stages'][0]['native'] = unsupported
            else:
                next(iter(s['receipts'].values()))['native'] = unsupported
            self.c._save(s)
            with self.subTest(component=component), self.assertRaisesRegex(ValueError, 'unsupported_stored_execution_client'):
                self.e.report()

    def test_review_status_absent_pending_changes_requested_pass_and_stale(self):
        v = self.enter()
        self.assertEqual(self.e.report(self_tests='pass', skills=['backend'], perspectives=['reviewer'])['independent_review'],
                         'not_performed')
        v = self.run_stage(v, 'dev')
        self.assertEqual(self.e.report()['independent_review'], 'not_performed')  # Merely ready reviewer.
        v = self.attach(v, execution('review'))
        self.assertEqual(self.e.report()['independent_review'], 'pending')
        self.assertFalse(self.e.report()['workflow_complete'])
        v = self.c.complete(self.receipt(v, 'changes_requested'))
        self.assertEqual(self.e.report()['independent_review'], 'changes_requested')
        v = self.run_stage(v, 'dev-fix')
        self.assertEqual(self.e.report()['independent_review'], 'changes_requested')
        v = self.attach(v, execution('review-fix'))
        self.assertEqual(self.e.report()['independent_review'], 'pending')
        self.c.complete(self.receipt(v))
        self.assertTrue(self.e.report()['workflow_complete'])
        self.assertEqual(self.e.report()['independent_review'], 'pass')
        (self.project / 'app.py').write_text('changed after review')
        self.assertEqual(self.e.report()['independent_review'], 'stale_snapshot')

    def test_review_status_failed_and_unverified_are_distinct(self):
        v = self.run_stage(self.enter(), 'dev')
        v = self.attach(v, execution('review'))
        self.c.complete(self.receipt(v, 'fail'))
        self.assertEqual(self.e.report()['independent_review'], 'failed')
        s = self.c._read(); s['receipts'] = {}; self.c._save(s)
        self.assertEqual(self.e.report()['independent_review'], 'unverified')
        self.assertFalse(self.e.report()['workflow_complete'])

    def test_cli_integrated_entry_and_report(self):
        payload = self.root / 'payload.json'; payload.write_text(json.dumps(self.entry_args))
        base = [sys.executable, str(dw.ROOT/'scripts/development_workflow.py'), '--project', str(self.project),
                '--request-id', 'request-example', '--coordinator', 'coordinator-example',
                '--coordinator-actor', 'example/coordinator', '--state', str(self.state), '--entry-state', str(self.e.path)]
        p = subprocess.run(base + ['entry', '--input', str(payload)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout)['phase'], 'ready')
        p = subprocess.run(base + ['report'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(json.loads(p.stdout)['workflow_complete'])
