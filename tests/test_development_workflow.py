"""Synthetic normalized native receipts; these tests never execute a worker."""
import copy
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import development_workflow as dw
import lifecycle_io as io


def execution(session='dev', client='orca'):
    return dict(client=client, handle='handle-' + session, provider='example', provider_session_id=session,
                task_id='task-' + session if client == 'orca' else None,
                dispatch_id='dispatch-' + session if client == 'orca' else None,
                guard_directory='/placeholder/guard' if client == 'orca' else None,
                guard_operation_id='op-' + session if client == 'orca' else None,
                usage_actor_id='actor-' + session if client == 'orca' else None)


class DevelopmentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'product'; self.project.mkdir()
        (self.project / 'app.py').write_text('value = 1\n')
        self.state = self.root / 'private' / 'workflow.json'
        self.c = dw.Controller(self.project, 'request-example', 'coordinator-example', self.state, 'example/coordinator')
        self.p = dict(version=1, enabled=True, max_dispatches=6, max_fix_rounds=2)
        self.write_policy()
        self.ac = {'AC1': 'Expected behavior', 'AC2': 'Regression checks'}
        self.args = dict(kind='development', direct=False, native_available=True, acceptance=self.ac, files=['app.py'])
        self.serial = 0

    def write_policy(self):
        (self.project / dw.POLICY).write_text(json.dumps(self.p))

    def begin(self):
        return self.c.begin(**self.args)

    def receipt(self, view, outcome='pass', **changes):
        self.serial += 1
        result = dict(outcome=outcome, self_test='pass' if view['role'] == 'developer' else 'not_applicable',
                      snapshot=dw.snapshot(self.project, ['app.py']), acceptance={k: 'pass' for k in self.ac}, findings=[])
        result.update(changes)
        return dict(id='receipt-' + str(self.serial), binding=view['binding'], stage_id=view['stage_id'],
                    native=view['native'], observer='coordinator-example', source_tool='official-example-status',
                    terminal='succeeded', result=result)

    def prepare(self, view, n=None):
        # Synthetic official launch: reserve first, then record exact guard operation.
        from dispatch_guard import Ledger
        n = copy.deepcopy(n or execution())
        n.update(guard_directory=str(self.root / 'guard'), guard_operation_id='op-' + view['stage_id'])
        granted = self.c.reserve(view['stage_id'], dw.intent_for(n))
        self.assertEqual(granted['launch_permission'], 'submit_once')
        ledger = Ledger(n['guard_directory'], self.project, 'coordinator-example')
        data = ledger.read()
        data['requests']['request-example'] = dict(max_active_workers=1, max_dispatches_per_request=self.p['max_dispatches'])
        data['operations'][n['guard_operation_id']] = dict(request_id='request-example', state='bound',
            task_id=n['task_id'], dispatch_id=n['dispatch_id'], plan=dict(project=str(self.project), model=None,
            effort=None, usage_collection=dict(actor_id=n['usage_actor_id'],
            role='backend' if view['role'] == 'developer' else 'verifier', task_title='Synthetic fixture')))
        ledger.write(data)
        return n

    def attach(self, view, n=None):
        n = self.prepare(view, n)
        return self.c.attach(view['stage_id'], n)

    def run_stage(self, view, session, outcome='pass', **changes):
        view = self.attach(view, execution(session))
        return self.c.complete(self.receipt(view, outcome, **changes))

    def unchanged_failure(self, operation):
        before = self.state.read_bytes()
        with self.assertRaises((ValueError, OSError)):
            operation()
        self.assertEqual(before, self.state.read_bytes())

    def test_unknown_native_is_pending_not_unavailable(self):
        self.assertEqual(dw.decision(self.project, 'development')['decision'], 'native_check_required')
        self.assertEqual(self.c.begin(**dict(self.args, native_available=None))['decision'], 'native_check_required')
        self.assertFalse(self.state.exists())
        v = self.begin(); before = self.state.read_bytes()
        unknown = self.c.next(native_available=None)
        self.assertEqual(unknown['phase'], 'native_check_required')
        self.assertEqual(unknown['stage_id'], v['stage_id'])
        self.assertEqual(before, self.state.read_bytes())
        for invalid in (0, 1, 'unknown'):
            with self.assertRaises(ValueError): dw.decision(self.project, 'development', native_available=invalid)

    def test_expected_policy_optional_strict_and_resume_matches_frozen_scope(self):
        expected = dict(sha256=dw.policy_snapshot(self.project)['sha256'], limits=self.p.copy())
        v = self.c.begin(**self.args, expected_policy=expected)
        before = self.state.read_bytes()
        self.assertEqual(self.c.begin(**self.args, expected_policy=expected)['stage_id'], v['stage_id'])
        self.assertEqual(before, self.state.read_bytes())
        self.unchanged_failure(lambda: self.c.begin(**self.args, expected_policy=dict(expected, sha256='0'*64)))
        self.unchanged_failure(lambda: self.c.begin(**self.args, expected_policy=dict(expected, extra=True)))
        # Existing low-level explicit bool/None and frozen-policy resume remain compatible.
        self.p['max_dispatches'] = 8; self.write_policy()
        self.assertEqual(self.c.begin(**self.args, expected_policy=expected)['stage_id'], v['stage_id'])
        self.assertEqual(self.begin()['stage_id'], v['stage_id'])

    def test_unsupported_actual_execution_and_stored_intent_are_rejected(self):
        v = self.begin(); before = self.state.read_bytes()
        intent = dict(client='paseo', guard_directory=None, guard_operation_id=None, usage_actor_id=None)
        self.assertEqual(self.c.reserve(v['stage_id'], intent)['phase'], 'unavailable')
        self.assertEqual(before, self.state.read_bytes())
        with self.assertRaisesRegex(ValueError, 'unsupported_actual_execution_client'):
            self.c._guard(execution(client='paseo'), 'developer')
        s = self.c._read(); s['stages'][0]['intent'] = intent; self.c._save(s)
        self.unchanged_failure(self.c.next)

    def test_direct_no_policy_role_read_or_state_side_effects(self):
        (self.project / dw.POLICY).write_text('invalid json')
        for kind, direct in [('conversation', False), ('small_change', False), ('development', True)]:
            with patch.object(dw, 'read_json', side_effect=AssertionError('must not read')):
                self.assertEqual(self.c.begin(**dict(self.args, kind=kind, direct=direct)), {'decision': 'direct'})
        self.assertFalse(self.state.parent.exists())

    def test_no_optin_and_unavailable_have_no_state(self):
        (self.project / dw.POLICY).unlink()
        self.assertEqual(self.begin()['decision'], 'opt_in_required')
        self.write_policy()
        self.assertEqual(self.c.begin(**dict(self.args, native_available=False))['decision'], 'unavailable')
        self.assertFalse(self.state.parent.exists())

    def test_policy_is_strict_and_bounded(self):
        for change in [dict(version=True), dict(extra=1), dict(max_dispatches=9), dict(max_fix_rounds=4), dict(enabled=1)]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                dw.policy(dict(self.p, **change))

    def test_pass_requires_independent_review_and_original_ac(self):
        v = self.run_stage(self.begin(), 'dev')
        self.assertEqual((v['phase'], v['role']), ('ready', 'reviewer'))
        v = self.run_stage(v, 'review')
        self.assertEqual(v['phase'], 'completed')
        self.assertFalse(v['usage_complete'])
        self.assertEqual(v['usage_status'], 'unmeasured')
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.state.parent.stat().st_mode), 0o700)
        self.assertEqual(self.begin()['phase'], 'completed')
        self.unchanged_failure(lambda: self.c.attach(v['stage_id'], execution('other')))

    def test_fixes_then_rereview(self):
        v = self.run_stage(self.begin(), 'dev')
        v = self.run_stage(v, 'review', 'changes_requested', acceptance={'AC1': 'fail', 'AC2': 'pass'},
                           findings=[dict(ac_id='AC1', required=True, resolved=False, detail='Fix behavior')])
        self.assertEqual(v['role'], 'developer')
        (self.project / 'app.py').write_text('value = 2\n')
        v = self.run_stage(v, 'dev-fix')
        self.assertEqual(self.run_stage(v, 'review-again')['phase'], 'completed')

    def test_caps(self):
        for dispatches, rounds in [(2, 2), (6, 0)]:
            with self.subTest(dispatches=dispatches, rounds=rounds):
                self.state.unlink(missing_ok=True)
                self.p.update(max_dispatches=dispatches, max_fix_rounds=rounds); self.write_policy()
                v = self.run_stage(self.begin(), 'dev')
                self.assertEqual(self.run_stage(v, 'review', 'changes_requested')['phase'], 'exhausted')

    def test_same_physical_actor_rejected_across_clients(self):
        v = self.run_stage(self.begin(), 'dev')
        n = self.prepare(v, execution('dev'))
        self.unchanged_failure(lambda: self.c.attach(v['stage_id'], n))
        n = dict(n, provider_session_id='coordinator', handle='other-app-handle')
        self.unchanged_failure(lambda: self.c.attach(v['stage_id'], n))
        self.unchanged_failure(lambda: self.c.attach(v['stage_id'], execution('dev', 'paseo')))

    def test_wrong_stale_receipt_and_idempotent_replay(self):
        v = self.attach(self.begin())
        r = self.receipt(v)
        for field, value in [('stage_id', 'old'), ('native', execution('wrong')), ('observer', 'other'), ('terminal', 'running')]:
            bad = copy.deepcopy(r); bad[field] = value
            self.unchanged_failure(lambda: self.c.complete(bad))
        advanced = self.c.complete(r); before = self.state.read_bytes()
        self.assertEqual(self.c.complete(r), advanced)
        self.assertEqual(before, self.state.read_bytes())
        bad = copy.deepcopy(r); bad['result']['outcome'] = 'fail'
        self.unchanged_failure(lambda: self.c.complete(bad))
        r['id'] = 'stale-new-id'
        self.unchanged_failure(lambda: self.c.complete(r))

    def test_developer_self_test_or_terminal_failure_never_reviews(self):
        for mode in ('test', 'terminal'):
            self.state.unlink(missing_ok=True)
            v = self.attach(self.begin())
            r = self.receipt(v)
            if mode == 'test': r['result']['self_test'] = 'fail'
            else: r['terminal'] = 'failed'
            self.assertEqual(self.c.complete(r)['phase'], 'failed')

    def test_reviewer_must_cover_ac_and_resolve_required_findings(self):
        v = self.run_stage(self.begin(), 'dev')
        v = self.attach(v, execution('review'))
        for change in [dict(acceptance={'AC1': 'pass'}), dict(acceptance={'AC1': 'fail', 'AC2': 'pass'}),
                       dict(findings=[dict(ac_id='AC2', required=True, resolved=False, detail='Required fix')])]:
            self.unchanged_failure(lambda: self.c.complete(self.receipt(v, **change)))

    def test_snapshot_changes_before_or_after_review(self):
        v = self.run_stage(self.begin(), 'dev')
        (self.project / 'app.py').write_text('changed')
        self.unchanged_failure(lambda: self.c.reserve(v['stage_id'], dict(client='orca', guard_directory=str(self.root/'guard'),
            guard_operation_id='op-' + v['stage_id'], usage_actor_id='review-actor')))
        (self.project / 'app.py').write_text('value = 1\n')
        v = self.attach(v, execution('review'))
        r = self.receipt(v)
        (self.project / 'app.py').write_text('changed again')
        self.unchanged_failure(lambda: self.c.complete(r))

    def test_cancel_keeps_active_identity_until_terminal(self):
        v = self.attach(self.begin())
        cancelled = self.c.cancel()
        self.assertEqual(cancelled['phase'], 'cancel_pending')
        self.assertEqual(cancelled['native'], v['native'])
        r = self.receipt(v); r['terminal'] = 'running'
        self.unchanged_failure(lambda: self.c.complete(r))
        r['terminal'] = 'stopped'
        self.assertEqual(self.c.complete(r)['phase'], 'cancelled')
        self.assertEqual(self.begin()['phase'], 'cancelled')
        self.unchanged_failure(lambda: self.c.attach(v['stage_id'], execution('replacement')))

    def test_unbound_cancel_requires_reconciliation(self):
        v = self.begin(); n = self.prepare(v); self.c.cancel()
        v = self.c.attach(v['stage_id'], n)
        self.assertEqual(v['phase'], 'cancel_pending')
        self.assertEqual(self.c.complete(self.receipt(v))['phase'], 'cancelled')

    def test_private_state_and_product_boundaries(self):
        for name in ['../outside', '/tmp/outside', '.git/config', '.env', 'secrets.txt', 'key.pem']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                dw.snapshot(self.project, [name])
        (self.project / 'link').symlink_to(self.project / 'app.py')
        with self.assertRaises(ValueError): dw.snapshot(self.project, ['link'])
        inside = dw.Controller(self.project, 'r', 'coordinator-example', self.project / 'state.json', 'example/coordinator')
        with self.assertRaises(ValueError): inside.begin(**self.args)
        target = self.root / 'elsewhere'; target.mkdir()
        (self.root / 'linked').symlink_to(target, target_is_directory=True)
        linked = dw.Controller(self.project, 'r', 'coordinator-example', self.root / 'linked' / 'state.json', 'example/coordinator')
        with self.assertRaises(ValueError): linked.begin(**self.args)
        with self.assertRaises(ValueError): dw.snapshot(self.project, ['app.py'] * 33)
        (self.project / 'large').write_bytes(b'x' * (dw.MAX_BYTES + 1))
        with self.assertRaises(ValueError): dw.snapshot(self.project, ['large'])

    def test_lock_binding_and_invalid_inputs_do_not_advance(self):
        self.begin()
        with io.locked(self.state.with_suffix('.lock'), self.project):
            self.unchanged_failure(self.c.next)
        other = dw.Controller(self.project, 'other', 'coordinator-example', self.state, 'example/coordinator')
        self.unchanged_failure(other.next)
        self.unchanged_failure(lambda: self.c.begin(**dict(self.args, acceptance={'OTHER': 'Changed'})))
        self.unchanged_failure(lambda: self.c.attach(self.c.next()['stage_id'], dict(execution(), extra='invalid')))

    def test_orca_requires_registered_guard_and_usage(self):
        v = self.begin()
        n = dict(execution(client='orca'), task_id='task', dispatch_id='dispatch', guard_directory=str(self.root/'guard'),
                 guard_operation_id='operation', usage_actor_id='actor')
        self.unchanged_failure(lambda: self.c.attach(v['stage_id'], n))

    def test_registered_orca_guard_positive_and_foreign_binding(self):
        v = self.begin()
        from dispatch_guard import Ledger
        directory = self.root / 'guard'
        ledger = Ledger(directory, self.project, 'coordinator-example')
        n = dict(execution(client='orca'), task_id='task-example', dispatch_id='dispatch-example',
                 guard_directory=str(directory), guard_operation_id='operation-example', usage_actor_id='actor-example')
        self.c.reserve(v['stage_id'], dw.intent_for(n))
        data = ledger.read()
        data['requests']['request-example'] = dict(max_active_workers=1, max_dispatches_per_request=6)
        data['operations']['operation-example'] = dict(request_id='request-example', state='bound',
            task_id=n['task_id'], dispatch_id=n['dispatch_id'], plan=dict(project=str(self.project), model=None,
            effort=None, usage_collection=dict(actor_id='actor-example', role='backend', task_title='Implementation')))
        ledger.write(data)
        bad = dict(n, usage_actor_id='other-actor')
        self.unchanged_failure(lambda: self.c.attach(v['stage_id'], bad))
        self.assertEqual(self.c.attach(v['stage_id'], n)['native'], n)

    def test_unavailable_next_preserves_state_and_exact_handle(self):
        v = self.attach(self.begin())
        before = self.state.read_bytes()
        unavailable = self.c.next(native_available=False)
        self.assertEqual(unavailable['phase'], 'unavailable')
        self.assertEqual(unavailable['pending_phase'], 'running')
        self.assertEqual(unavailable['native'], v['native'])
        self.assertEqual(self.state.read_bytes(), before)

    def test_active_cancel_still_works_after_product_replaced_by_symlink(self):
        v = self.attach(self.begin())
        target = self.project / 'app.py'; target.unlink(); target.symlink_to(self.root / 'outside')
        self.assertEqual(self.c.cancel()['native'], v['native'])
        r = dict(id='stop', binding=v['binding'], stage_id=v['stage_id'], native=v['native'],
                 observer='coordinator-example', source_tool='official-status', terminal='stopped',
                 result=dict(outcome='fail', self_test='fail', snapshot=None, acceptance={}, findings=[]))
        self.assertEqual(self.c.complete(r)['phase'], 'cancelled')

    def test_state_schema_unknown_fields_and_duplicate_policy_keys(self):
        self.begin()
        state = io.read_json(self.state)
        state['stages'][0]['unexpected'] = True
        state['integrity'] = io.digest({k: v for k, v in state.items() if k != 'integrity'})
        io.save(self.state, state, self.project)
        self.unchanged_failure(self.c.next)
        (self.project / dw.POLICY).write_text('{"version":1,"enabled":true,"enabled":false,"max_dispatches":2,"max_fix_rounds":0}')
        with self.assertRaises(ValueError): dw.decision(self.project, 'development', native_available=True)

    def restart(self):
        return dw.Controller(self.project, 'request-example', 'coordinator-example', self.state, 'example/coordinator')

    def launch_intent(self, v):
        return dict(client='orca', guard_directory=str(self.root/'guard'),
                    guard_operation_id='operation-' + v['stage_id'], usage_actor_id='actor-example')

    def test_prelaunch_unknown_receipt_restart_and_guard_replay_once(self):
        import dispatch_guard as dg
        from test_dispatch_guard import plan
        v = self.begin(); intent = self.launch_intent(v)
        self.assertIn('reserve', v['native_action'])
        self.assertEqual(v['launch_permission'], 'none')
        grant = self.c.reserve(v['stage_id'], intent)
        self.assertEqual(grant['launch_permission'], 'submit_once')
        self.assertEqual(io.read_json(self.state)['stages'][-1]['intent'], intent)
        ledger = dg.Ledger(intent['guard_directory'], self.project, 'coordinator-example')
        limits = dict(max_active_workers=1, max_dispatches_per_request=6)
        args = (ledger, 'request-example', intent['guard_operation_id'], plan(self.project), 'synthetic', limits, 'fake-cli')
        with patch.object(dg, 'invoke', side_effect=OSError('accepted but receipt lost')) as invoke:
            unknown = dg.launch(*args)
            self.assertEqual(unknown['state'], 'unknown')
            self.c = self.restart(); before = self.state.read_bytes()
            recovered = self.c.next()
            self.assertIn('reconcile/observe', recovered['native_action'])
            self.assertEqual(recovered['launch_intent'], intent)
            self.assertEqual(recovered['launch_permission'], 'none')
            self.assertEqual(self.c.reserve(v['stage_id'], intent)['launch_permission'], 'none')
            replay = dg.launch(*args)
            self.assertTrue(replay['replay'])
            self.assertEqual(invoke.call_count, 1)
            self.assertEqual(before, self.state.read_bytes())
            self.c.cancel(); cancelled = self.restart().next()
            self.assertEqual(cancelled['launch_intent'], intent)
            self.assertEqual(cancelled['launch_permission'], 'none')
            self.assertEqual(self.c.reserve(v['stage_id'], intent)['launch_permission'], 'none')

    def test_lost_reserve_response_never_reissues_submission_permission(self):
        v = self.begin(); intent = self.launch_intent(v)
        with patch.object(self.c, '_view', side_effect=OSError('response lost after durable save')):
            with self.assertRaises(OSError): self.c.reserve(v['stage_id'], intent)
        before = self.state.read_bytes(); self.c = self.restart()
        self.assertEqual(self.c.next()['launch_intent'], intent)
        self.assertEqual(self.c.reserve(v['stage_id'], intent)['launch_permission'], 'none')
        self.assertEqual(before, self.state.read_bytes())

    def test_cli_reserve_is_submit_once_and_restart_is_reconcile_only(self):
        v = self.begin(); intent = self.launch_intent(v)
        payload = self.root/'intent.json'; payload.write_text(json.dumps(dict(stage_id=v['stage_id'], intent=intent)))
        command = [sys.executable, dw.__file__, 'reserve', '--project', str(self.project),
                   '--request-id', 'request-example', '--coordinator', 'coordinator-example',
                   '--coordinator-actor', 'example/coordinator', '--state', str(self.state), '--input', str(payload)]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(json.loads(first.stdout)['launch_permission'], 'submit_once')
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(json.loads(second.stdout)['launch_permission'], 'none')
        self.assertIn('reconcile/observe', json.loads(second.stdout)['native_action'])

    def test_conflicting_reservation_and_wrong_attach_are_atomic(self):
        v = self.begin(); n = self.prepare(v)
        intent = dw.intent_for(n)
        for change in [dict(guard_operation_id='other'), dict(usage_actor_id='different'),
                       dict(guard_directory=str(self.root/'other-guard'))]:
            self.unchanged_failure(lambda: self.c.reserve(v['stage_id'], dict(intent, **change)))
            self.unchanged_failure(lambda: self.c.attach(v['stage_id'], dict(n, **change)))
        self.unchanged_failure(lambda: self.c.attach(v['stage_id'], execution(client='paseo')))
        self.unchanged_failure(lambda: self.c.reserve('wrong-stage', intent))
        self.assertEqual(self.c.attach(v['stage_id'], n)['phase'], 'running')

    def test_attach_without_prelaunch_intent_cannot_retroactively_admit(self):
        v = self.begin()
        self.unchanged_failure(lambda: self.c.attach(v['stage_id'], execution()))

    def test_unsupported_clients_get_no_submission_permission_or_state_change(self):
        v = self.begin(); before = self.state.read_bytes()
        for client in ('paseo', 'codex'):
            response = self.c.reserve(v['stage_id'], dw.intent_for(execution(client=client)))
            self.assertEqual(response['phase'], 'unavailable')
            self.assertEqual(response['launch_permission'], 'none')
            self.assertEqual(self.state.read_bytes(), before)

    def test_preexisting_guard_operation_cannot_be_claimed_after_launch(self):
        from dispatch_guard import Ledger
        v = self.begin(); intent = self.launch_intent(v)
        ledger = Ledger(intent['guard_directory'], self.project, 'coordinator-example')
        data = ledger.read(); data['operations'][intent['guard_operation_id']] = {'state': 'unknown'}; ledger.write(data)
        self.unchanged_failure(lambda: self.c.reserve(v['stage_id'], intent))

    def policy_race(self, mutation):
        original = dw.policy_snapshot; reads = []
        def racing(project):
            result = original(project); reads.append(result)
            if len(reads) == 1: mutation()
            return result
        with patch.object(dw, 'policy_snapshot', side_effect=racing):
            try:
                result = self.begin()
            finally:
                self.assertEqual(len(reads), 2)  # Real snapshot and locked commit re-read.
                self.assertFalse(self.state.exists())
        return result

    def test_policy_true_to_false_race_never_creates_state(self):
        def revoke():
            self.p['enabled'] = False; self.write_policy()
        self.assertEqual(self.policy_race(revoke), {'decision': 'opt_in_required'})

    def test_policy_caps_and_identical_file_replacement_races_reject(self):
        def change_caps():
            self.p['max_dispatches'] = 8; self.write_policy()
        with self.assertRaisesRegex(ValueError, 'policy_changed_during_admission'):
            self.policy_race(change_caps)
        self.p['max_dispatches'] = 6; self.write_policy()
        def replace():
            replacement = self.project / 'replacement.json'; replacement.write_text(json.dumps(self.p))
            replacement.replace(self.project / dw.POLICY)
        with self.assertRaisesRegex(ValueError, 'policy_changed_during_admission'):
            self.policy_race(replace)

    def test_policy_symlink_replacement_in_admission_window_rejects(self):
        def replace():
            replacement = self.root / 'elsewhere-policy.json'; replacement.write_text(json.dumps(self.p))
            target = self.project / dw.POLICY; target.unlink(); target.symlink_to(replacement)
        original = dw.policy_snapshot; calls = []
        def racing(project):
            calls.append(project)
            result = original(project)
            if len(calls) == 1: replace()
            return result
        with patch.object(dw, 'policy_snapshot', side_effect=racing), self.assertRaises(OSError):
            self.begin()
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.state.exists())

    def test_active_policy_caps_frozen_on_restart(self):
        self.begin(); before = self.state.read_bytes()
        self.p.update(max_dispatches=8, max_fix_rounds=3); self.write_policy()
        self.c = self.restart(); self.begin()
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(io.read_json(self.state)['policy']['max_dispatches'], 6)

    def test_restart_fix_context_and_final_review_remain_available(self):
        v = self.run_stage(self.begin(), 'dev')
        finding = dict(ac_id='AC1', required=True, resolved=False, detail='Boundary implementation wrong')
        v = self.run_stage(v, 'review', 'changes_requested', acceptance={'AC1': 'fail', 'AC2': 'pass'}, findings=[finding])
        self.c = self.restart(); v = self.c.next()
        self.assertEqual(v['acceptance'], self.ac)
        self.assertEqual(v['unresolved_required_findings'], [finding])
        self.assertEqual(v['last_review']['result']['outcome'], 'changes_requested')
        self.assertIn('last_review', v['native_action'])
        v = self.run_stage(v, 'fix'); v = self.run_stage(v, 'review-final')
        final = self.restart().next()
        self.assertEqual(final['phase'], 'completed')
        self.assertEqual(final['last_review']['result']['outcome'], 'pass')
        self.assertEqual(final['unresolved_required_findings'], [])

    def test_v1_schema_is_explicitly_rejected_without_migration_or_mutation(self):
        self.begin(); s = io.read_json(self.state)
        s['version'] = 1; s.pop('policy_source')
        for stage in s['stages']: stage.pop('intent')
        io.save(self.state, s, self.project)
        before = self.state.read_bytes()
        with self.assertRaisesRegex(ValueError, 'unsupported_workflow_schema_preserve_original_no_migration'):
            self.restart().next()
        self.assertEqual(before, self.state.read_bytes())

    def test_cli_and_import_have_no_launch_or_filesystem_side_effect(self):
        script = Path(dw.__file__)
        code = 'import sys;sys.path.insert(0, sys.argv[1]);import development_workflow'
        result = subprocess.run([sys.executable, '-c', code, str(script.parent)], cwd=self.project, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.state.parent.exists())
        payload = self.root / 'input.json'; payload.write_text(json.dumps(dict(kind='small_change', direct=False, native_available=False)))
        result = subprocess.run([sys.executable, str(script), 'decision', '--project', str(self.project), '--input', str(payload)], capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout), {'decision': 'direct'})
        self.assertFalse(self.state.parent.exists())

    def test_legacy_backend_instruction_contract_survives_plan_extension(self):
        v = self.begin()
        self.assertEqual(v['version'], 2)
        self.assertIn('backend-developer.md', v['instructions'][0])
        self.assertNotIn('plan', self.c._read())

    def test_quality_contract_does_not_relax_hidden_snapshot_paths(self):
        (self.project/'.hidden').mkdir()
        (self.project/'.hidden'/'screen.txt').write_text('synthetic')
        with self.assertRaises(ValueError):
            dw.paths(self.project, ['.hidden/screen.txt'])


if __name__ == '__main__':
    unittest.main()
