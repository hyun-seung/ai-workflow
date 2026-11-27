"""Finite synthetic guard/Entry integration. No native workers or network calls."""
import copy
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import development_parallel as dp
import development_workflow as dw
import dispatch_guard as dg


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'product'; self.project.mkdir()
        for name in ('a.py', 'b.py'): (self.project / name).write_text('value = 1\n')
        self.policy = dict(version=1, enabled=True, max_dispatches=6, max_fix_rounds=1)
        self.write_policy()
        self.batch = dp.Batch(self.project, 'request-example', 'coordinator-example',
                              self.root / 'batch' / 'batch.json', 'example/coordinator')
        self.units = [dict(id=x, acceptance_ids=['AC' + x], files=[x + '.py'], reads=[], writes=[x + '.py'],
                           resources=[], depends_on=[], plan=None) for x in ('a', 'b')]
        self.args = dict(plan=dict(version=1, acceptance={'ACa': 'First behavior', 'ACb': 'Second behavior'}, units=self.units),
                         native=dict(client='orca', status='available', capacity=2,
                                     source_ref='synthetic-official-capacity', observer='coordinator-example'),
                         max_active_workers=2, reason='Two independent changes with separate reviews')
        self.choice = dict(mode='workflow', reason='Accepted bounded delegation', instruction_ref='explicit-request')
        self.serial = 0

    def write_policy(self):
        (self.project / dw.POLICY).write_text(json.dumps(self.policy))

    def enter(self):
        selection = self.batch.propose(**self.args)
        self.accepted = dict(ref='explicit-project-acceptance', project=str(self.project),
                             sha256=dw.policy_snapshot(self.project)['sha256'], limits=self.policy.copy())
        self.concurrent = dict(ref='existing-authorized-coordinator-selection', selection_sha256=selection['selection_sha256'])
        return self.batch.enter(**self.args, choice=self.choice, policy_acceptance=self.accepted,
                                concurrency_acceptance=self.concurrent, guard_directory=str(self.root / 'guard'))

    def launch(self, unit, session=None):
        granted = self.batch.reserve(unit_id=unit)
        self.assertEqual(granted['launch_permission'], 'submit_once')
        return self.submit(unit, granted, session)

    def submit(self, unit, granted, session=None):
        self.serial += 1
        session = session or f'{unit}-session-{self.serial}'
        g = granted['guard']; operation = g['operation_id']
        ledger = dg.Ledger(g['directory'], self.project, 'coordinator-example')
        p = dict(action='start', project=str(self.project), worktree='current', agent='codex', model=None, effort=None,
                 spec_digest='synthetic', writes=g['writes'], depends_on=g['depends_on'], previous=None, subject='Synthetic',
                 usage_collection=dict(actor_id=g['usage_actor_id'], role=g['usage_role'], task_title='Synthetic assignment'))
        receipt = dict(ok=True, result=dict(taskId='task-' + str(self.serial), dispatchId='dispatch-' + str(self.serial), runId='run-example'))
        with patch.object(dg, 'invoke', return_value=(receipt, 0)) as invoke:
            op = dg.launch(ledger, g['request_id'], operation, p, 'Synthetic assignment',
                           {k: g[k] for k in ('max_active_workers', 'max_dispatches_per_request')}, '/unused')
            invoke.assert_called_once()
        n = dict(client='orca', handle='handle-' + session, provider='example', provider_session_id=session,
                 task_id=op['task_id'], dispatch_id=op['dispatch_id'], guard_directory=g['directory'],
                 guard_operation_id=operation, usage_actor_id=g['usage_actor_id'])
        return self.batch.attach(unit_id=unit, stage_id=granted['stage_id'], execution=n)

    def finish(self, unit, v, *, release=True, outcome='pass', terminal='succeeded'):
        result = dict(outcome=outcome, self_test='pass', snapshot=dw.snapshot(self.project, v['files']),
                      acceptance={k: 'pass' if outcome == 'pass' else 'fail' for k in v['acceptance']}, findings=[])
        receipt = dict(id='result-' + v['stage_id'], binding=v['binding'], stage_id=v['stage_id'], native=v['native'],
                       observer='coordinator-example', source_tool='synthetic-worker-show', terminal=terminal, result=result)
        result = self.batch.complete(unit_id=unit, receipt=receipt)
        if release: self.release(v['native']['guard_operation_id'], terminal)
        return result

    def release(self, operation, outcome='succeeded'):
        # Synthetic exact-exit fixture; no claim that a live process ran or exited.
        ledger = self.batch.ledger(self.batch._read())
        with ledger.lock():
            data = ledger.read(); data['operations'][operation].update(state='released', outcome=outcome)
            ledger.write(data)

    def test_independent_two_cap_two_through_entry_guard_and_aggregate_report(self):
        initial = self.enter()
        self.assertEqual((initial['max_active_workers'], initial['ready']), (2, ['a', 'b']))
        a = self.launch('a'); b = self.launch('b')
        self.assertEqual(self.batch.next()['occupied_count'], 2)
        self.finish('a', a); self.finish('b', b)
        ar = self.launch('a'); br = self.launch('b')
        self.finish('a', ar)
        self.assertFalse(self.batch.report()['workflow_complete'])
        self.finish('b', br)
        report = self.batch.report()
        self.assertTrue(report['workflow_complete'])
        self.assertEqual(report['reserved_dispatches'], 4)
        data = self.batch.ledger(self.batch._read()).read()
        self.assertEqual(set(data['requests']), {'request-example'})
        self.assertEqual(len(data['operations']), 4)
        self.assertTrue(all(o['request_id'] == 'request-example' for o in data['operations'].values()))

    def test_dependencies_wait_for_review_and_genuine_release(self):
        self.units[1]['depends_on'] = ['a']
        self.assertEqual(self.enter()['max_active_workers'], 1)
        with self.assertRaises(ValueError): self.batch.reserve(unit_id='b')
        a = self.launch('a'); self.finish('a', a)
        self.assertEqual(self.batch.next()['ready'], ['a'])
        r = self.launch('a'); self.finish('a', r, release=False)
        self.assertEqual(self.batch.next()['ready'], [])
        self.release(r['native']['guard_operation_id'])
        b = self.batch.reserve(unit_id='b')
        self.assertIn(r['native']['guard_operation_id'], b['guard']['depends_on'])

    def test_overlap_read_write_directory_prefix_and_resources_are_exclusive(self):
        for variant in ('write-write', 'write-read', 'prefix', 'resource'):
            with self.subTest(variant=variant):
                u = copy.deepcopy(self.units)
                if variant == 'write-write': u[1]['writes'].append('a.py')
                if variant == 'write-read': u[1]['reads'].append('a.py')
                if variant == 'prefix':
                    u[0]['writes'].append('shared'); u[1]['reads'].append('shared/child.py')
                if variant == 'resource': u[0]['resources'] = u[1]['resources'] = ['db:example']
                args = dict(self.args, plan=dict(self.args['plan'], units=u))
                self.assertEqual(self.batch.propose(**args)['selection']['max_active_workers'], 1)
        self.units[1]['reads'].append('a.py')
        self.enter(); self.launch('a')
        with self.assertRaisesRegex(ValueError, 'unit_not_ready'): self.batch.reserve(unit_id='b')

    def test_later_independent_frontier_not_initial_ready_count_sets_budget(self):
        (self.project / 'c.py').write_text('value=1')
        self.args['plan']['acceptance']['ACc'] = 'Third behavior'
        self.units.append(dict(self.units[1], id='c', acceptance_ids=['ACc'], files=['c.py'], writes=['c.py'], depends_on=['a']))
        self.units[1]['depends_on'] = ['a']
        v = self.enter()
        self.assertEqual((v['max_active_workers'], v['ready_count']), (2, 1))

    def test_review_budget_is_aggregate_including_corrections(self):
        self.policy['max_dispatches'] = 3; self.write_policy()
        with self.assertRaisesRegex(ValueError, 'insufficient_required_review_budget'): self.batch.propose(**self.args)
        self.policy['max_dispatches'] = 4; self.write_policy(); self.enter()
        a = self.launch('a'); self.finish('a', a)
        ar = self.launch('a'); self.finish('a', ar, outcome='changes_requested')
        v = self.batch.next()
        self.assertFalse(v['budget_sufficient'])
        with self.assertRaises(ValueError): self.batch.reserve(unit_id='a')
        self.assertFalse(self.batch.report()['workflow_complete'])

    def test_unknown_unavailable_and_unsupported_adapter_are_distinct(self):
        for native, expected in [
            (dict(client='orca', status='unknown', capacity=None, source_ref=None, observer=None), 'native_check_required'),
            (dict(self.args['native'], capacity=None), 'native_check_required'),
            (dict(self.args['native'], status='unavailable', capacity=None), 'unavailable'),
            (dict(self.args['native'], client='paseo'), 'unavailable')]:
            self.assertEqual(self.batch.propose(**dict(self.args, native=native))['decision'], expected)
        self.assertFalse(self.batch.path.exists())
        self.assertEqual(self.batch.propose(**dict(self.args, native=dict(self.args['native'], capacity=1)))['selection']['max_active_workers'], 1)

    def test_native_harness_and_user_narrower_limits_bound_selection(self):
        h = dict(delegation_decision_maker='user', limits=dict(max_active_workers=1, max_dispatches_per_request=6))
        with patch.object(dp, 'harness_policy', return_value=h):
            s = self.batch.propose(**self.args)['selection']
            self.assertEqual((s['max_active_workers'], s['decision_maker']), (1, 'user'))
        self.assertEqual(self.batch.propose(**dict(self.args, max_active_workers=1))['selection']['max_active_workers'], 1)

    def test_acceptance_scope_policy_race_and_no_retroactive_adoption(self):
        self.enter(); before = self.batch.path.read_bytes()
        self.units[0]['reads'] = ['b.py']
        with self.assertRaisesRegex(ValueError, 'accepted_batch_scope_changed'):
            self.batch.enter(**self.args, choice=self.choice, policy_acceptance=self.accepted,
                             concurrency_acceptance=self.concurrent, guard_directory=str(self.root / 'guard'))
        self.assertEqual(before, self.batch.path.read_bytes())
        self.policy['max_dispatches'] = 7; self.write_policy()
        with self.assertRaisesRegex(ValueError, 'accepted_policy_changed_before_reservation'): self.batch.reserve(unit_id='a')

    def test_policy_race_between_proposal_and_admission(self):
        proposed = self.batch.propose(**self.args)
        accepted = dict(ref='accepted', project=str(self.project), **proposed['selection']['policy'])
        original = dw.policy_snapshot
        calls = 0
        def race(project):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.policy['max_dispatches'] = 7; self.write_policy()
            return original(project)
        with patch.object(dw, 'policy_snapshot', side_effect=race):
            with self.assertRaisesRegex(ValueError, 'policy_changed_during_admission'):
                self.batch.enter(**self.args, choice=self.choice, policy_acceptance=accepted,
                                 concurrency_acceptance=dict(ref='accepted', selection_sha256=proposed['selection_sha256']),
                                 guard_directory=str(self.root / 'guard'))
        self.assertFalse(self.batch.path.exists())

    def test_restart_uncertainty_keeps_reservation_and_never_reissues_permission(self):
        self.enter(); granted = self.batch.reserve(unit_id='a')
        self.batch = dp.Batch(self.project, 'request-example', 'coordinator-example', self.batch.path, 'example/coordinator')
        self.assertEqual(self.batch.reserve(unit_id='a')['launch_permission'], 'none')
        self.assertEqual(self.batch.next()['occupied_count'], 1)
        b = self.launch('b')
        self.assertEqual(self.batch.next()['occupied_count'], 2)
        ledger = self.batch.ledger(self.batch._read())
        data = ledger.read(); data['operations'][b['native']['guard_operation_id']]['state'] = 'unknown'; ledger.write(data)
        self.assertEqual(self.batch.next()['ready'], [])
        self.assertFalse(self.batch.report()['workflow_complete'])

    def test_guard_replay_and_process_uncertainty_do_not_launch_twice(self):
        self.enter(); g = self.batch.reserve(unit_id='a')['guard']
        ledger = self.batch.ledger(self.batch._read())
        p = dict(writes=g['writes'], depends_on=[], action='start')
        limits = {k: g[k] for k in ('max_active_workers', 'max_dispatches_per_request')}
        with patch.object(dg, 'invoke', side_effect=OSError('synthetic interruption')) as invoke:
            first = dg.launch(ledger, g['request_id'], g['operation_id'], p, 'synthetic', limits, '/unused')
            second = dg.launch(ledger, g['request_id'], g['operation_id'], p, 'synthetic', limits, '/unused')
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual((first['state'], second['replay']), ('unknown', True))
        self.assertEqual(self.batch.next()['occupied_count'], 1)

    def test_missing_stale_nonindependent_reviews_and_cancellation(self):
        self.enter(); a = self.launch('a', 'author-a'); b = self.launch('b', 'author-b')
        self.finish('a', a); self.finish('b', b)
        with self.assertRaisesRegex(ValueError, 'independent_provider_session_required_across_batch'):
            self.launch('a', 'author-b')
        self.assertFalse(self.batch.report()['workflow_complete'])
        self.batch.cancel()
        self.assertFalse(self.batch.report()['workflow_complete'])

    def test_stale_final_snapshots_are_not_whole_request_complete(self):
        self.enter()
        for uid in ('a', 'b'):
            self.finish(uid, self.launch(uid)); self.finish(uid, self.launch(uid))
        self.assertTrue(self.batch.report()['workflow_complete'])
        (self.project / 'a.py').write_text('changed after review')
        self.assertFalse(self.batch.report()['workflow_complete'])
        self.assertEqual(self.batch.report()['unit_reports']['a']['independent_review'], 'stale_snapshot')

    def test_failed_release_blocks_dependency_and_final_completion(self):
        self.units[1]['depends_on'] = ['a']; self.enter()
        self.finish('a', self.launch('a'))
        review = self.launch('a'); self.finish('a', review)
        self.release(review['native']['guard_operation_id'], 'failed')
        self.assertEqual(self.batch.next()['ready'], [])
        self.assertFalse(self.batch.report()['workflow_complete'])
        self.assertTrue(any(x.startswith('unsuccessful_release:') for x in self.batch.report()['omissions']))

    def test_existing_guard_enforces_compiled_read_and_resource_scopes(self):
        self.units[1]['reads'] = ['a.py']
        self.units[0]['resources'] = self.units[1]['resources'] = ['shared-db']
        ledger = dg.Ledger(self.root / 'guard', self.project, 'coordinator-example')
        limits = dict(max_active_workers=2, max_dispatches_per_request=6)
        p = lambda u: dict(action='start', writes=dp.scopes(self.project, u), depends_on=[])
        ledger.reserve('request-example', 'first', p(self.units[0]), limits)
        with self.assertRaisesRegex(ValueError, 'write_scope_owned_by_active_worker'):
            ledger.reserve('request-example', 'second', p(self.units[1]), limits)

    def test_partial_entry_crash_resume_and_choice_reuse(self):
        original = dp.Entry.enter
        calls = 0
        def crash(entry, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError('synthetic crash after first unit admission')
            return original(entry, **kwargs)
        with patch.object(dp.Entry, 'enter', crash):
            with self.assertRaises(OSError): self.enter()
        manifest = self.batch.path.read_bytes()
        m = self.batch._read(); first = self.batch.controller(m, self.units[0]).path.read_bytes()
        v = self.batch.enter(**self.args, choice=self.choice, guard_directory=str(self.root / 'guard'))
        self.assertEqual(v['ready'], ['a', 'b'])
        self.assertEqual(manifest, self.batch.path.read_bytes())
        self.assertEqual(first, self.batch.controller(m, self.units[0]).path.read_bytes())

    def test_duplicate_or_cyclic_units_and_unassigned_acceptance_rejected(self):
        for mutate in (lambda p: p['units'][0].update(depends_on=['b']),
                       lambda p: p['units'][0].update(id='b'),
                       lambda p: p['acceptance'].update(ACmissing='No owner')):
            p = copy.deepcopy(self.args['plan']); p['units'][1]['depends_on'] = ['a']; mutate(p)
            with self.assertRaises(ValueError): self.batch.propose(**dict(self.args, plan=p))

    def test_historical_request_not_adopted_and_limits_not_replanned(self):
        ledger = dg.Ledger(self.root / 'guard', self.project, 'coordinator-example')
        ledger.reserve('request-example', 'historical', dict(action='start', writes=[], depends_on=[]),
                       dict(max_active_workers=1, max_dispatches_per_request=6))
        before = ledger.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'existing_request_cannot_be_migrated_to_batch'): self.enter()
        self.assertEqual(before, ledger.path.read_bytes())

    def test_finished_report_replay_preserves_all_admission_and_execution_bytes(self):
        self.enter()
        for uid in ('a', 'b'):
            self.finish(uid, self.launch(uid)); self.finish(uid, self.launch(uid))
        paths = [self.batch.path, self.batch.ledger(self.batch._read()).path]
        for u in self.units:
            e = self.batch.entry(self.batch._read(), u); paths += [e.path, e.c.path]
        before = {p: p.read_bytes() for p in paths}
        self.assertTrue(self.batch.report()['workflow_complete'])
        self.assertTrue(self.batch.report()['workflow_complete'])
        self.assertEqual(before, {p: p.read_bytes() for p in paths})

    def test_direct_is_stateless_and_legacy_serial_unchanged(self):
        self.assertEqual(self.batch.enter(choice=dict(self.choice, mode='direct'))['decision'], 'direct')
        self.assertFalse(self.batch.path.parent.exists())
        c = dw.Controller(self.project, 'legacy', 'coordinator-example', self.root / 'legacy' / 'state.json', 'example/coordinator')
        v = c.begin(kind='development', direct=False, native_available=True, acceptance={'AC': 'Legacy'}, files=['a.py'])
        raw = c.path.read_bytes()
        self.assertEqual(v['active_concurrency'], 1)
        self.assertEqual(c.next()['active_concurrency'], 1)
        self.assertEqual(raw, c.path.read_bytes())

    def test_plan_v3_frontend_and_visual_requirements_survive_batch(self):
        from test_development_plan import strict_plan, frontend_result
        self.units[0]['plan'] = strict_plan(); self.enter()
        v = self.launch('a')
        result = dict(outcome='pass', self_test='pass', snapshot=dw.snapshot(self.project, v['files']),
                      acceptance={k: 'pass' for k in v['acceptance']}, findings=[], artifact=None, visual_evidence=[])
        frontend_result(v, result)
        receipt = dict(id='frontend-result', binding=v['binding'], stage_id=v['stage_id'], native=v['native'],
                       observer='coordinator-example', source_tool='synthetic-observation', terminal='succeeded', result=result)
        next_view = self.batch.complete(unit_id='a', receipt=receipt)
        self.release(v['native']['guard_operation_id'])
        self.assertEqual((next_view['role'], next_view['frontend_prerequisite']), ('visual_review', 'pass'))
        report = self.batch.report()
        self.assertFalse(report['workflow_complete'])
        self.assertTrue(report['unit_reports']['a']['frontend_quality_active'])
        self.assertNotEqual(report['unit_reports']['a']['visual_quality'], 'pass')
        self.assertNotEqual(report['unit_reports']['a']['independent_review'], 'pass')

    def test_known_design_artifacts_require_declared_scopes(self):
        from test_development_plan import plan as role_plan
        self.units[0]['plan'] = role_plan(pre='fresh')
        with self.assertRaisesRegex(ValueError, 'design_artifact_requires_declared_scope'):
            self.batch.propose(**self.args)

    def test_second_journal_cannot_lose_unsubmitted_unknown_reservations(self):
        self.enter(); self.batch.reserve(unit_id='a')
        second = dp.Batch(self.project, 'request-example', 'coordinator-example',
                          self.root / 'other-batch' / 'batch.json', 'example/coordinator')
        with self.assertRaisesRegex(ValueError, 'logical_request_already_bound_to_batch'):
            second.enter(**self.args, choice=self.choice, policy_acceptance=self.accepted,
                         concurrency_acceptance=self.concurrent, guard_directory=str(self.root / 'guard'))
        self.assertFalse(second.path.exists())
        self.assertEqual(self.batch.next()['occupied_count'], 1)

    def test_cancel_keeps_custody_after_product_scope_becomes_symlink(self):
        self.enter(); self.launch('a')
        (self.project / 'a.py').unlink()
        (self.project / 'a.py').symlink_to(self.project / 'b.py')
        cancelled = self.batch.cancel()
        self.assertEqual(cancelled['a']['phase'], 'cancel_pending')
        self.assertIsNotNone(cancelled['a']['native'])

    def test_cli_restart_reserves_once_with_same_request_and_accepted_cap(self):
        self.enter()
        payload = self.root / 'reserve.json'; payload.write_text(json.dumps(dict(unit_id='a')))
        cmd = [sys.executable, str(Path(dp.__file__)), 'reserve', '--project', str(self.project),
               '--request-id', 'request-example', '--coordinator', 'coordinator-example',
               '--coordinator-actor', 'example/coordinator', '--state', str(self.batch.path), '--input', str(payload)]
        first = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        second = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertEqual((first.returncode, second.returncode), (0, 0), first.stdout + second.stdout)
        a, b = json.loads(first.stdout), json.loads(second.stdout)
        self.assertEqual((a['launch_permission'], b['launch_permission']), ('submit_once', 'none'))
        self.assertEqual(a['stage_id'], b['stage_id'])
        self.assertEqual((a['guard']['request_id'], a['guard']['max_active_workers']), ('request-example', 2))

    def rewrite_child(self, mutate, unit_id='a'):
        """Owned fixture: coherent recovered evidence, never a bad-integrity shortcut."""
        m = self.batch._read(); unit = self.batch._unit(m, unit_id)
        e = self.batch.entry(m, unit)
        state, journal = e.c._read(), e._read()
        mutate(state, journal)
        e.c._save(state); e._save(journal)
        return e

    def test_recovered_public_entry_cannot_replace_original_ac_files_or_role_plan(self):
        from test_development_plan import plan as role_plan
        self.enter(); m = self.batch._read(); e = self.batch.entry(m, self.units[0])
        original = dict(kind='development', choice=self.choice,
                        native={k:self.args['native'][k] for k in ('status','source_ref','observer')},
                        acceptance={'ACa':'First behavior'}, files=['a.py'], policy_acceptance=self.accepted)
        for change in (dict(acceptance={'replacement':'Different criterion'}, files=['b.py']),
                       dict(acceptance={'ACa':'Weaker text under original key'}),
                       dict(files=['b.py']), dict(plan=role_plan(['backend']))):
            with self.subTest(change=change):
                e.path.unlink(); e.c.path.unlink()
                e.enter(**dict(original, **change))  # Public, internally consistent recovery.
                before = {p:p.read_bytes() for p in (e.path,e.c.path,self.batch.path)}
                with self.assertRaisesRegex(ValueError, 'unit_not_ready'):
                    self.batch.reserve(unit_id='a')
                report = self.batch.report()
                self.assertFalse(report['workflow_complete'])
                self.assertIn('a', report['contract_errors'])
                self.assertEqual(before, {p:p.read_bytes() for p in before})

    def test_self_consistent_completed_children_still_require_original_ac_keys_and_text(self):
        self.enter()
        for uid in ('a','b'):
            self.finish(uid,self.launch(uid)); self.finish(uid,self.launch(uid))
        self.assertTrue(self.batch.report()['workflow_complete'])
        for acceptance in ({'replacement':'Weaker criterion'},{'ACa':'Weaker text with the same key'}):
            with self.subTest(acceptance=acceptance):
                def mutate(s,j):
                    s['acceptance']=acceptance.copy(); j['request']['acceptance']=acceptance.copy()
                    for stage in s['stages']: stage['result']['acceptance']={k:'pass' for k in acceptance}
                    for receipt in s['receipts'].values(): receipt['result']['acceptance']={k:'pass' for k in acceptance}
                e=self.rewrite_child(mutate)
                self.assertTrue(e.report()['workflow_complete'])  # Child consistency alone is insufficient.
                before={p:p.read_bytes() for p in (e.path,e.c.path)}
                report=self.batch.report()
                self.assertFalse(report['workflow_complete'])
                self.assertIn('a',report['contract_errors'])
                self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_all_frozen_admission_fields_checked_before_new_permission(self):
        self.enter(); e=self.batch.entry(self.batch._read(),self.units[0])
        original_s,original_j=e.c._read(),e._read()
        def policy_change(s,j):
            s['policy']['max_dispatches']=7
            j['policy_acceptance']['limits']['max_dispatches']=7
            j['policy_observation']['value']['max_dispatches']=7
        def source_change(s,j):
            s['policy_source']['sha256']='f'*64
            j['policy_acceptance']['sha256']='f'*64; j['policy_observation']['sha256']='f'*64
        cases=dict(policy=policy_change, policy_source=source_change,
            acceptance_ref=lambda s,j:j['policy_acceptance'].update(ref='other-acceptance'),
            choice=lambda s,j:j['choice'].update(instruction_ref='other-instruction'),
            native=lambda s,j:j['native'].update(source_ref='other-native-observation'),
            kind=lambda s,j:j['request'].update(kind='small_change'),
            review=lambda s,j:j['request'].update(review_required=False),
            late=lambda s,j:j.update(observation='late_observation'),
            controller_path=lambda s,j:j.update(controller=str(self.root/'other.json')),
            entry_binding=lambda s,j:j['binding'].update(request_id='other-request'),
            controller_binding=lambda s,j:s['binding'].update(request_id='other-request'))
        for field,mutate in cases.items():
            with self.subTest(field=field):
                s,j=copy.deepcopy(original_s),copy.deepcopy(original_j); mutate(s,j)
                e.c._save(s); e._save(j)
                before={p:p.read_bytes() for p in (e.path,e.c.path)}
                with self.assertRaises((ValueError,OSError)): self.batch.reserve(unit_id='a')
                report=self.batch.report()
                self.assertFalse(report['workflow_complete'])
                self.assertEqual(report['ready'],[])
                self.assertIn('a',report['contract_errors'])
                self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_bad_predecessor_contract_never_unblocks_dependent_unit(self):
        self.units[1]['depends_on']=['a']; self.enter()
        self.finish('a',self.launch('a')); self.finish('a',self.launch('a'))
        self.assertEqual(self.batch.next()['ready'],['b'])
        self.rewrite_child(lambda s,j:(s['acceptance'].update(ACa='Substituted'),j['request']['acceptance'].update(ACa='Substituted')))
        self.assertEqual(self.batch.next()['ready'],[])
        with self.assertRaisesRegex(ValueError,'unit_not_ready'): self.batch.reserve(unit_id='b')

    def test_contract_mismatch_retains_unknown_intent_and_active_native_custody(self):
        self.enter(); a=self.launch('a'); b=self.batch.reserve(unit_id='b')
        for uid in ('a','b'):
            self.rewrite_child(lambda s,j:j['native'].update(source_ref='incorrect-restoration'),uid)
        view=self.batch.report()
        self.assertEqual((view['occupied_count'],view['request_occupied_count']),(2,2))
        self.assertEqual(view['units']['a']['native'],a['native'])
        self.assertEqual(view['units']['b']['launch_intent'],b['launch_intent'])
        self.assertEqual(self.batch.reserve(unit_id='b')['launch_permission'],'none')
        cancelled=self.batch.cancel()
        self.assertEqual(cancelled['a']['phase'],'cancel_pending')
        self.assertEqual(cancelled['b']['phase'],'cancel_pending')
        self.assertEqual(cancelled['a']['native'],a['native'])
        self.assertFalse(self.batch.report()['workflow_complete'])

    def test_mismatched_binding_reports_known_ledger_custody_without_new_permission(self):
        self.enter(); a=self.launch('a')
        self.rewrite_child(lambda s,j:s['binding'].update(request_id='wrong-request'))
        report=self.batch.report()
        op=a['native']['guard_operation_id']
        self.assertIn(op,report['request_operations'])
        self.assertEqual(report['request_occupied_count'],1)
        self.assertIn('a',report['unverified_custody'])
        self.assertFalse(report['workflow_complete'])
        self.assertEqual(self.batch.cancel()['a']['phase'],'unverified')

    def test_reentry_does_not_rewrite_mismatched_native_admission(self):
        self.enter(); e=self.rewrite_child(lambda s,j:j['native'].update(source_ref='different-admission'))
        before=e.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'batch_child_contract_mismatch'):
            self.batch.enter(**self.args,choice=self.choice,guard_directory=str(self.root/'guard'))
        self.assertEqual(before,e.path.read_bytes())

    def test_public_entry_recovery_between_ready_check_and_reserve_is_rechecked(self):
        self.enter(); original=dp.UnitController.reserve
        e=self.batch.entry(self.batch._read(),self.units[0])
        def recovered(c,stage_id,intent):
            # Public Entry update occurs after Batch computed ready, before the
            # controller takes its admission locks; no fabricated integrity error.
            e.enter(kind='development',choice=self.choice,
                    native=dict(status='available',source_ref='replacement-observation',observer='coordinator-example'),
                    acceptance={'ACa':'First behavior'},files=['a.py'],policy_acceptance=self.accepted)
            return original(c,stage_id,intent)
        with patch.object(dp.UnitController,'reserve',recovered):
            with self.assertRaisesRegex(ValueError,'batch_child_contract_mismatch'):
                self.batch.reserve(unit_id='a')
        self.assertIsNone(e.c._read()['stages'][-1]['intent'])
        self.assertEqual(e._read()['native']['source_ref'],'replacement-observation')

    def test_partial_entry_without_controller_recovers_only_original_contract(self):
        self.enter(); e=self.batch.entry(self.batch._read(),self.units[0])
        e.c.path.unlink()
        self.assertEqual(self.batch.enter(**self.args,choice=self.choice,guard_directory=str(self.root/'guard'))['ready'],['a','b'])
        e.c.path.unlink()
        j=e._read();j['request']['acceptance']['ACa']='Wrong partial recovery';e._save(j)
        before=e.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'batch_child_contract_mismatch'):
            self.batch.enter(**self.args,choice=self.choice,guard_directory=str(self.root/'guard'))
        self.assertFalse(e.c.path.exists());self.assertEqual(e.path.read_bytes(),before)

    def foreign_reservation(self, *, request='foreign-request', operation='foreign-op', cap=2, writes=None):
        ledger=self.batch.ledger(self.batch._read())
        ledger.reserve(request,operation,dict(action='start',writes=writes or [],depends_on=[]),
                       dict(max_active_workers=cap,max_dispatches_per_request=6))
        return ledger

    def test_completed_request_stays_complete_while_unrelated_request_is_active(self):
        self.enter()
        for uid in ('a','b'):
            self.finish(uid,self.launch(uid)); self.finish(uid,self.launch(uid))
        self.assertTrue(self.batch.report()['workflow_complete'])
        self.foreign_reservation(writes=[str(self.project/'foreign.py')])
        report=self.batch.report()
        self.assertTrue(report['workflow_complete'])
        self.assertEqual((report['occupied_count'],report['request_occupied_count']),(1,0))
        self.assertEqual(report['reserved_dispatches'],4)

    def test_foreign_capacity_strictest_cap_and_scopes_still_limit_scheduling(self):
        self.enter()
        ledger=self.foreign_reservation(writes=[str(self.project/'a.py')])
        view=self.batch.next()
        self.assertEqual(view['ready'],['b'])  # One global slot remains; a's scope is excluded.
        with self.assertRaisesRegex(ValueError,'unit_not_ready'): self.batch.reserve(unit_id='a')
        self.release('foreign-op')
        self.foreign_reservation(request='narrow-request',operation='narrow-op',cap=1)
        self.assertEqual(self.batch.next()['ready'],[])
        with self.assertRaisesRegex(ValueError,'unit_not_ready'): self.batch.reserve(unit_id='b')
        self.assertEqual(self.batch.next()['request_occupied_count'],0)

    def test_own_unowned_rows_block_completion_even_when_released_successfully(self):
        self.enter()
        for uid in ('a','b'):
            self.finish(uid,self.launch(uid)); self.finish(uid,self.launch(uid))
        self.foreign_reservation(request='request-example',operation='unowned-op')
        report=self.batch.report()
        self.assertFalse(report['workflow_complete'])

        self.assertEqual(report['request_occupied_count'],1)
        self.assertIn('unowned_request_operation:unowned-op',report['omissions'])
        self.release('unowned-op')
        report=self.batch.report()
        self.assertEqual(report['request_occupied_count'],0)
        self.assertFalse(report['workflow_complete'])

    def completed_predecessor(self):
        self.units[1]['depends_on'] = ['a']; self.enter()
        self.finish('a', self.launch('a')); self.finish('a', self.launch('a'))
        self.assertEqual(self.batch.next()['ready'], ['b'])
        return self.batch.entry(self.batch._read(), self.units[0])

    def public_child_process(self, action, *, unit_id='a'):
        """Real public API in another process, only owned temporary fixture files.

        replace removes synthetic evidence BEFORE public recovery, as in the
        original verifier reproduction; it is not an arbitrary-edit lock test.
        """
        payload = dict(project=str(self.project), request_id='request-example', coordinator='coordinator-example',
                       state_path=str(self.batch.path), coordinator_actor='example/coordinator')
        code = '''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import development_parallel as dp
b=dp.Batch(**json.loads(sys.stdin.read())); m=json.loads(b.path.read_text()); u=b._unit(m,sys.argv[3]); e=b.entry(m,u)
action=sys.argv[2]
try:
    if action=='report': result=e.report()
    elif action=='cancel': result=e.c.cancel()
    else:
        args=dict(kind='development',choice=m['choice'],
            native={k:m['selection']['native'][k] for k in ('status','source_ref','observer')},
            acceptance={k:m['selection']['plan']['acceptance'][k] for k in u['acceptance_ids']},
            files=u['files'],policy_acceptance=m['policy_acceptance'],plan=u['plan'])
        if action=='replace':
            e.path.unlink();e.c.path.unlink()
            args.update(acceptance={'ACa':'Recovered different criterion'},files=['b.py'])
        elif action=='native': args['native']['source_ref']='different-public-observation'
        result=e.enter(**args)
    print(json.dumps(result))
except (ValueError,OSError) as error:
    print(json.dumps({'error':str(error)}));sys.exit(2)
'''
        run = subprocess.run([sys.executable, '-B', '-c', code, str(Path(dp.__file__).parent), action, unit_id],
                             input=json.dumps(payload), capture_output=True, text=True, timeout=10)
        self.assertIn(run.returncode, (0, 2), run.stderr)
        return run.returncode, json.loads(run.stdout)

    def test_separate_process_predecessor_ac_files_recovery_at_admission_refused(self):
        e = self.completed_predecessor(); original = dp.UnitController.reserve
        target = self.batch.entry(self.batch._read(), self.units[1]).c
        before = target.path.read_bytes(); recovered = []
        def interleave(c, stage_id, intent):
            code, result = self.public_child_process('replace')
            self.assertEqual(code, 0, result); recovered.append(e.c.path.read_bytes())
            return original(c, stage_id, intent)
        with patch.object(dp.UnitController, 'reserve', interleave):
            with self.assertRaisesRegex(ValueError, 'batch_child_contract_mismatch'):
                self.batch.reserve(unit_id='b')
        self.assertEqual(target.path.read_bytes(), before)
        self.assertEqual(e.c.path.read_bytes(), recovered[0])  # Never repairs history.
        self.assertFalse(self.batch.report()['workflow_complete'])

    def test_separate_process_predecessor_native_observation_at_admission_refused(self):
        e = self.completed_predecessor(); original = dp.UnitController.reserve
        state = e.c.path.read_bytes()
        def interleave(c, stage_id, intent):
            code, result = self.public_child_process('native')
            self.assertEqual(code, 0, result)
            return original(c, stage_id, intent)
        with patch.object(dp.UnitController, 'reserve', interleave):
            with self.assertRaisesRegex(ValueError, 'batch_child_contract_mismatch'):
                self.batch.reserve(unit_id='b')
        self.assertEqual(e.c.path.read_bytes(), state)
        self.assertEqual(self.batch.next()['request_occupied_count'], 0)

    def test_predecessor_proof_changes_after_report_refuse_before_durable_intent(self):
        e = self.completed_predecessor(); original = self.batch._predecessor_proofs
        state, journal, source = e.c._read(), e._read(), (self.project/'a.py').read_bytes()
        target = self.batch.entry(self.batch._read(), self.units[1]).c
        before = target.path.read_bytes()
        def missing_intent(s): s['stages'][-1].update(intent=None, native=None)
        def replaced_intent(s):
            last=s['stages'][-1]; last['intent']['guard_operation_id']='replacement-final-operation'
            last['native']['guard_operation_id']='replacement-final-operation'
        variants = dict(cancelled=lambda s:s.update(phase='cancelled'),
                        missing_receipt=lambda s:s['receipts'].clear(),
                        final_intent_missing=missing_intent, final_intent_replaced=replaced_intent)
        for name in (*variants, 'snapshot', 'native_source'):
            with self.subTest(name=name):
                e.c._save(copy.deepcopy(state)); e._save(copy.deepcopy(journal)); (self.project/'a.py').write_bytes(source)
                def interleave(m, u):
                    proof = original(m, u)
                    if name=='snapshot': (self.project/'a.py').write_text('changed after review\n')
                    elif name=='native_source':
                        code, result=self.public_child_process('native'); self.assertEqual(code,0,result)
                    else:
                        changed=e.c._read(); variants[name](changed); e.c._save(changed)
                    return proof
                with patch.object(self.batch, '_predecessor_proofs', interleave):
                    with self.assertRaisesRegex(ValueError, 'batch_predecessor_|batch_child_contract_mismatch'):
                        self.batch.reserve(unit_id='b')
                self.assertEqual(target.path.read_bytes(), before)

    def test_changed_released_operation_between_report_and_save_refused(self):
        e=self.completed_predecessor(); original=self.batch._predecessor_proofs
        operation=e.c._read()['stages'][-1]['intent']['guard_operation_id']
        def interleave(m,u):
            proof=original(m,u); ledger=self.batch.ledger(m)
            with ledger.lock():
                data=ledger.read(); data['operations'][operation]['dispatch_id']='different-exit-identity'; ledger.write(data)
            return proof
        with patch.object(self.batch,'_predecessor_proofs',interleave):
            with self.assertRaisesRegex(ValueError,'batch_predecessor_not_successfully_released'):
                self.batch.reserve(unit_id='b')
        self.assertIsNone(self.batch.entry(self.batch._read(),self.units[1]).c._read()['stages'][-1]['intent'])

    def test_all_admission_locks_held_through_guard_construction_and_save(self):
        e=self.completed_predecessor(); original=self.batch._guard_arguments
        prior={p:p.read_bytes() for p in (e.path,e.c.path)}; observed=[]
        def at_boundary(m,u,stage,dependencies):
            for uid,action in (('a','native'),('a','cancel'),('b','native'),('b','cancel'),('a','report')):
                code,result=self.public_child_process(action,unit_id=uid)
                self.assertEqual((code,result),(2,{'error':'lifecycle_already_running'}))
                observed.append((uid,action))
            # This exact construction occurs under the existing reserve ledger
            # lock, before _save: no duplicate flock and no permission yet.
            with self.assertRaisesRegex(ValueError,'lifecycle_already_running'):
                with self.batch.ledger(m).lock(): pass
            self.assertIsNone(self.batch.entry(m,u).c._read()['stages'][-1]['intent'])
            return original(m,u,stage,dependencies)
        with patch.object(self.batch,'_guard_arguments',at_boundary): grant=self.batch.reserve(unit_id='b')
        self.assertEqual(grant['launch_permission'],'submit_once'); self.assertEqual(len(observed),5)
        self.assertEqual(prior,{p:p.read_bytes() for p in prior})
        self.assertEqual(self.public_child_process('report')[0],0)  # Locks actually released.
        self.assertEqual(self.batch.reserve(unit_id='b')['launch_permission'],'none')

    def test_predecessor_lock_contention_has_bounded_refusal_and_releases_partial_locks(self):
        e=self.completed_predecessor(); original=self.batch._predecessor_proofs
        for path in (e.path.with_suffix('.lock'),e.c.path.with_suffix('.lock')):
            with self.subTest(path=path):
                with dp.locked(path,self.project):
                    with self.assertRaisesRegex(ValueError,'unit_not_ready|lifecycle_already_running'):
                        self.batch.reserve(unit_id='b')
                self.assertEqual(self.batch.next()['ready'],['b'])
        # Contention after optimistic report must unwind earlier acquired locks.
        with ExitStack() as held:
            def interleave(m,u):
                proof=original(m,u);held.enter_context(dp.locked(e.c.path.with_suffix('.lock'),self.project));return proof
            with patch.object(self.batch,'_predecessor_proofs',interleave):
                with self.assertRaisesRegex(ValueError,'lifecycle_already_running'):
                    self.batch.reserve(unit_id='b')
        self.assertEqual(self.public_child_process('report')[0],0)
        self.assertEqual(self.batch.reserve(unit_id='b')['launch_permission'],'submit_once')

    def test_transitive_predecessor_and_deterministic_lock_order(self):
        (self.project/'c.py').write_text('value=1\n')
        self.args['plan']['acceptance']['ACc']='Third behavior'
        self.units[1]['depends_on']=['a']
        self.units.append(dict(self.units[1],id='c',acceptance_ids=['ACc'],files=['c.py'],writes=['c.py'],depends_on=['b']))
        self.enter()
        for uid in ('a','b'):
            self.finish(uid,self.launch(uid));self.finish(uid,self.launch(uid))
        original=dp.locked; paths=[]
        from contextlib import contextmanager
        @contextmanager
        def observe(path,project):
            paths.append(str(path))
            with original(path,project):yield
        m=self.batch._read(); target=self.batch.controller(m,self.units[2])
        # Observe just the locked commit boundary, not advisory report collection.
        proofs=self.batch._predecessor_proofs(m,self.units[2])
        with patch.object(self.batch,'_predecessor_proofs',return_value=proofs), patch.object(dp,'locked',observe):
            grant=target.reserve(target._read()['stages'][-1]['id'],
                dict(client='orca',guard_directory=m['guard_directory'],guard_operation_id='last-op',usage_actor_id='last-actor'))
        entries=[self.batch.entry(m,u) for u in self.units]
        expected=sorted(str(e.path.with_suffix('.lock')) for e in entries)+sorted(str(e.c.path.with_suffix('.lock')) for e in entries[:2])
        self.assertEqual(paths,expected)
        self.assertEqual(grant['guard']['depends_on'],[proofs['b']['state']['stages'][-1]['intent']['guard_operation_id']])
        self.assertEqual(set(proofs),{'a','b'})

    def test_required_dependency_ids_reach_real_guard_cli_and_missing_release_blocks(self):
        e=self.completed_predecessor(); grant=self.batch.reserve(unit_id='b'); g=grant['guard']
        dependency=e.c._read()['stages'][-1]['intent']['guard_operation_id']
        self.assertEqual(g['depends_on'],[dependency])
        ledger=self.batch.ledger(self.batch._read()); prior=ledger.read()
        with ledger.lock():
            data=ledger.read();del data['operations'][dependency];ledger.write(data)
        spec=self.root/'spec.txt';spec.write_text('Synthetic guarded assignment')
        argv=['--project',g['project'],'--directory',g['directory'],'--orca-cli','/unused','start',
              '--request-id',g['request_id'],'--operation-id',g['operation_id'],'--spec-file',str(spec),
              '--max-active-workers',str(g['max_active_workers']),'--max-dispatches-per-request',str(g['max_dispatches_per_request']),
              '--usage-role',g['usage_role'],'--usage-actor-id',g['usage_actor_id'],'--usage-task-title','Synthetic assignment']
        for key in ('writes','depends_on'):
            for value in g[key]:argv += ['--'+key.replace('_','-'),value]
        errors=io.StringIO()
        with patch.dict(os.environ,ORCA_AGENT_SESSION_ID='coordinator-example'), patch.object(dg,'invoke') as invoke:
            with redirect_stderr(errors),redirect_stdout(io.StringIO()):code=dg.main(argv)
        self.assertEqual(code,3);self.assertIn('dependency_not_successfully_released',errors.getvalue());invoke.assert_not_called()
        with ledger.lock():ledger.write(prior)
        running=self.submit('b',grant);self.assertEqual(running['phase'],'running')
        self.assertEqual(self.batch.reserve(unit_id='b')['launch_permission'],'none')

    def test_missing_required_final_intent_never_compiles_empty_dependencies(self):
        e=self.completed_predecessor();s=e.c._read();s['stages'][-1].update(intent=None,native=None);e.c._save(s)
        with self.assertRaisesRegex(ValueError,'batch_predecessor_final_intent_required'):
            self.batch.dependencies(self.batch._read(),self.units[1])
        with self.assertRaisesRegex(ValueError,'unit_not_ready'):
            self.batch.reserve(unit_id='b')

    def test_unchanged_explicit_role_plan_predecessor_passes_existing_review_gates(self):
        from test_development_plan import plan as role_plan
        self.units[0]['plan']=role_plan(['backend']);self.units[1]['depends_on']=['a'];self.enter()
        for _ in range(2):
            view=self.launch('a')
            result=dict(outcome='pass',self_test='pass',snapshot=dw.snapshot(self.project,view['files']),
                        acceptance={k:'pass' for k in view['acceptance']},findings=[],artifact=None,visual_evidence=[])
            receipt=dict(id='result-'+view['stage_id'],binding=view['binding'],stage_id=view['stage_id'],native=view['native'],
                         observer='coordinator-example',source_tool='synthetic-observation',terminal='succeeded',result=result)
            self.batch.complete(unit_id='a',receipt=receipt);self.release(view['native']['guard_operation_id'])
        self.assertTrue(self.batch.next()['unit_reports']['a']['workflow_complete'])
        grant=self.batch.reserve(unit_id='b')
        self.assertEqual(grant['guard']['depends_on'],[view['native']['guard_operation_id']])
        self.assertEqual(self.submit('b',grant)['phase'],'running')

    def test_admitted_dependent_keeps_unknown_native_and_cancel_custody_after_recovery(self):
        self.completed_predecessor();view=self.launch('b');native=view['native']
        ledger=self.batch.ledger(self.batch._read())
        with ledger.lock():
            data=ledger.read();data['operations'][native['guard_operation_id']]['state']='unknown';ledger.write(data)
        code,result=self.public_child_process('replace');self.assertEqual(code,0,result)
        report=self.batch.report()
        self.assertFalse(report['workflow_complete']);self.assertEqual(report['request_occupied_count'],1)
        self.assertEqual(report['units']['b']['native'],native)
        self.assertIn(native['guard_operation_id'],report['request_operations'])
        self.assertEqual(self.batch.reserve(unit_id='b')['launch_permission'],'none')
        cancel=self.batch.cancel()
        self.assertEqual(cancel['b']['phase'],'cancel_pending');self.assertEqual(cancel['b']['native'],native)


if __name__ == '__main__': unittest.main()
