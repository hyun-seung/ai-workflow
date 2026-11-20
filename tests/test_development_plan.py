"""Synthetic plan boundaries, not live frontend or visual quality evidence."""
import copy
import hashlib
import unittest
from unittest.mock import patch
import test_development_workflow as f
import development_workflow as dw
import development_entry as de
from development_plan import validate_plan


def plan(roles=None, pre='none', human=False, visual=False):
    return dict(version=1, implementation=roles or ['frontend'], pre_design=pre,
                human_selection=human, visual_review=visual, artifact='design.txt' if pre != 'none' else None,
                reuse=None, selection_ref='explicit-role-choice')


class PlanTests(unittest.TestCase):
    setUp = f.DevelopmentWorkflowTests.setUp
    write_policy = f.DevelopmentWorkflowTests.write_policy
    unchanged_failure = f.DevelopmentWorkflowTests.unchanged_failure

    def artifact(self):
        (self.project / 'design.txt').write_text('proposal one')
        return dict(path='design.txt', version='v1', sha256=hashlib.sha256(b'proposal one').hexdigest(), selection_ref='selected-proposal-one')

    def begin(self, p=None):
        return self.c.begin(**self.args, plan=p or plan())

    def launch(self, v, session=None, wrong_role=None):
        from dispatch_guard import Ledger
        n = f.execution(session or v['stage_id'])
        n.update(guard_directory=str(self.root / 'guard'), guard_operation_id='op-'+v['stage_id'])
        self.assertEqual(self.c.reserve(v['stage_id'], dw.intent_for(n))['launch_permission'], 'submit_once')
        ledger = Ledger(n['guard_directory'], self.project, 'coordinator-example')
        data = ledger.read()
        data['requests']['request-example'] = dict(max_active_workers=1, max_dispatches_per_request=self.p['max_dispatches'])
        data['operations'][n['guard_operation_id']] = dict(request_id='request-example', state='bound', task_id=n['task_id'], dispatch_id=n['dispatch_id'],
            plan=dict(project=str(self.project), model=None, effort=None, usage_collection=dict(actor_id=n['usage_actor_id'], role=wrong_role or v['usage_role'], task_title='Synthetic plan')))
        ledger.write(data)
        return self.c.attach(v['stage_id'], n)

    def finish(self, v, outcome='pass', evidence=True):
        self.serial += 1
        snap = dw.snapshot(self.project, ['app.py'])
        r = dict(outcome=outcome, self_test='pass' if v['role'] in ('frontend', 'backend') else 'not_applicable',
                 snapshot=snap, acceptance={k:'pass' for k in self.ac}, findings=[],
                 artifact=self.artifact() if v['role'] == 'pre_design' else None,
                 visual_evidence=[dict(kind='image', subject='implementation', provenance='implementation_capture',
                     implementation_stage_id=[x['id'] for x in self.c._read()['stages'] if x['role'] in ('frontend', 'backend')][-1],
                     source_ref='synthetic-implementation-capture', snapshot=snap, observer='coordinator-example')] if v['role'] == 'visual_review' and evidence else [])
        receipt = dict(id='r'+str(self.serial), binding=self.c.binding, stage_id=v['stage_id'], native=v['native'], observer='coordinator-example', source_tool='synthetic-official', terminal='succeeded', result=r)
        return self.c.complete(receipt)

    def advance(self, v, **kwargs):
        return self.finish(self.launch(v), **kwargs)

    def entry(self, p):
        self.e = de.Entry(self.c, self.root/'entry'/'entry.json')
        self.entry_args = dict(kind='development', choice=dict(mode='workflow', reason='required reviews', instruction_ref='user-choice'),
            native=dict(status='available', source_ref='official-synthetic', observer='coordinator-example'), acceptance=self.ac,
            files=['app.py'], plan=p, policy_acceptance=dict(ref='user-policy', project=str(self.project), sha256=dw.policy_snapshot(self.project)['sha256'], limits=self.p.copy()))
        return self.e.enter(**self.entry_args)

    def decision(self, v):
        return dict(plan_hash=v['plan_hash'], artifact=v['proposal'], proposal_stage_id=self.c._read()['stages'][-2]['id'],
                    decision='approved', instruction_ref='explicit-human-selection-message', observer='coordinator-example')

    def test_roles_and_distinct_mixed_duties(self):
        for roles in (['backend'], ['frontend'], ['frontend','backend'], ['backend','frontend']):
            with self.subTest(roles=roles):
                self.state.unlink(missing_ok=True)
                v = self.begin(plan(roles))
                for role in roles:
                    self.assertEqual(v['role'], role)
                    self.assertEqual(v['usage_role'], role)
                    self.assertIn(role+'-developer.md', v['instructions'][0])
                    v = self.advance(v)
                self.assertEqual(self.advance(v)['phase'], 'completed')

    def test_human_pending_exact_choice_before_reserve(self):
        v = self.advance(self.begin(plan(pre='fresh', human=True)))
        self.assertEqual(v['gates']['statuses']['design_selection'], 'pending')
        self.unchanged_failure(lambda:self.c.reserve(v['stage_id'], dw.intent_for(f.execution())))
        d = self.decision(v)
        for change in (dict(decision='proceed'), dict(plan_hash='0'*64), dict(observer='agent'), dict(proposal_stage_id='fake')):
            self.unchanged_failure(lambda:self.c.select_design(dict(d, **change)))
        self.c.select_design(d)
        self.assertEqual(self.advance(self.advance(v))['phase'], 'completed')

    def test_changed_design_blocks_approved_implementation(self):
        v = self.advance(self.begin(plan(pre='fresh', human=True)))
        self.c.select_design(self.decision(v))
        (self.project/'design.txt').write_text('changed proposal')
        self.unchanged_failure(lambda:self.c.reserve(v['stage_id'], dw.intent_for(f.execution())))

    def test_reuse_requires_exact_artifact_and_selection(self):
        p = plan(pre='reuse'); p['reuse'] = self.artifact()
        v = self.begin(p)
        self.assertEqual(v['role'], 'frontend')
        self.assertEqual(self.advance(self.advance(v))['phase'], 'completed')
        self.state.unlink()
        (self.project/'design.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'artifact_changed'): self.begin(p)
        p['reuse'] = None
        with self.assertRaises(ValueError): self.begin(p)

    def test_visual_evidence_distinct_from_functional_and_tests(self):
        v = self.entry(plan(visual=True))
        v = self.advance(v)
        self.assertEqual(v['role'], 'visual_review')
        v = self.advance(v, evidence=False)
        self.assertFalse(self.e.report(self_tests='pass')['workflow_complete'])
        self.assertEqual(self.e.report()['visual_review'], 'unverified')
        self.assertEqual(v['role'], 'frontend')

    def test_report_complete_and_stale_source(self):
        v = self.entry(plan(visual=True))
        v = self.advance(self.advance(self.advance(v)))
        self.assertEqual(v['phase'], 'completed')
        r = self.e.report(perspectives=['designer'])
        self.assertTrue(r['workflow_complete'])
        self.assertEqual([x['role'] for x in r['executions']], ['frontend','visual_review','reviewer'])
        (self.project/'app.py').write_text('changed source')
        r = self.e.report()
        self.assertEqual(r['visual_review'], 'stale')
        self.assertEqual(r['independent_review'], 'stale')
        self.assertFalse(r['workflow_complete'])

    def test_wrong_role_and_same_actor_rejected(self):
        v = self.begin()
        with self.assertRaisesRegex(ValueError, 'guard_usage'): self.launch(v, wrong_role='backend')
        self.state.unlink()
        v = self.finish(self.launch(self.begin(), session='same'))
        with self.assertRaisesRegex(ValueError, 'independent'): self.launch(v, session='same')

    def test_total_budget_before_launch_and_fix_round_shared(self):
        self.p['max_dispatches'] = 2; self.write_policy()
        with self.assertRaisesRegex(ValueError, 'total_dispatch_cap'): self.begin(plan(['frontend','backend']))
        self.assertFalse(self.state.exists())
        self.p.update(max_dispatches=6, max_fix_rounds=1); self.write_policy()
        v = self.advance(self.begin(plan(visual=True)))
        v = self.advance(v, outcome='changes_requested')
        self.assertEqual(self.c._read()['round'], 1)
        v = self.advance(self.advance(v))
        self.assertEqual(v['role'], 'reviewer')
        self.assertEqual(self.advance(v, outcome='fail')['phase'], 'exhausted')

    def test_resume_no_new_grant_or_changed_plan(self):
        v = self.entry(plan())
        self.assertEqual(self.e.enter(**self.entry_args)['stage_id'], v['stage_id'])
        n = f.execution(); n.update(guard_directory=str(self.root/'guard'), guard_operation_id='only-operation')
        self.c.reserve(v['stage_id'], dw.intent_for(n))
        self.assertEqual(self.c.reserve(v['stage_id'], dw.intent_for(n))['launch_permission'], 'none')
        with self.assertRaises(ValueError): self.e.enter(**dict(self.entry_args, plan=plan(['backend'])))
        self.assertEqual(self.c.next()['stage_id'], v['stage_id'])

    def test_tampered_completed_gate_is_rejected(self):
        self.begin()
        s = self.c._read(); s['phase'] = 'completed'; self.c._save(s)
        with self.assertRaisesRegex(ValueError, 'gates_bypassed'): self.c.next()

    def test_native_and_optin_direct_preserved(self):
        p = plan()
        v = self.entry(p)
        self.assertEqual(self.c.next(native_available=None)['phase'], 'native_check_required')
        self.assertEqual(self.c.next(native_available=False)['phase'], 'unavailable')
        self.assertEqual(self.c.begin(**dict(self.args, direct=True), plan=p), {'decision':'direct'})
        self.assertEqual(self.c.begin(**dict(self.args, kind='small_change'), plan=p), {'decision':'direct'})

    def test_legacy_v2_never_migrates(self):
        self.c.begin(**self.args)
        original = self.state.read_bytes()
        with self.assertRaises(ValueError): self.begin()
        self.assertEqual(original, self.state.read_bytes())
        self.assertEqual(self.c._read()['version'], 2)


    def test_design_failure_consumes_same_fix_cap_and_reselects(self):
        self.p.update(max_dispatches=8, max_fix_rounds=1); self.write_policy()
        v = self.advance(self.begin(plan(pre='fresh', human=True)), outcome='changes_requested')
        self.assertEqual(v['role'], 'pre_design')
        self.assertEqual(self.c._read()['round'], 1)
        v = self.advance(v)
        self.c.select_design(self.decision(v))
        self.assertEqual(self.advance(self.advance(v))['phase'], 'completed')

    def test_reuse_human_selection_still_required(self):
        p = plan(pre='reuse', human=True); p['reuse'] = self.artifact()
        v = self.begin(p)
        self.unchanged_failure(lambda:self.c.reserve(v['stage_id'], dw.intent_for(f.execution())))
        d = dict(plan_hash=v['plan_hash'], artifact=p['reuse'], proposal_stage_id='reuse', decision='approved', instruction_ref='human-exact-reuse', observer='coordinator-example')
        self.c.select_design(d)
        self.assertEqual(self.advance(self.advance(v))['phase'], 'completed')

    def test_approval_mutation_during_assertion_rejected(self):
        v = self.advance(self.begin(plan(pre='fresh', human=True)))
        original = self.c._artifact
        calls = []
        def mutate(artifact):
            calls.append(1)
            if len(calls) == 2: (self.project/'design.txt').write_text('mutated')
            return original(artifact)
        with patch.object(self.c, '_artifact', side_effect=mutate):
            self.unchanged_failure(lambda:self.c.select_design(self.decision(v)))

    def test_reuse_mutation_during_admission_rejected(self):
        p = plan(pre='reuse'); p['reuse'] = self.artifact()
        original = self.c._artifact
        calls = []
        def mutate(artifact):
            calls.append(1)
            if len(calls) == 2: (self.project/'design.txt').write_text('mutated')
            return original(artifact)
        with patch.object(self.c, '_artifact', side_effect=mutate), self.assertRaises(ValueError): self.begin(p)
        self.assertFalse(self.state.exists())

    def test_plan_entry_unaccepted_unknown_and_unavailable(self):
        self.entry(plan())
        self.state.unlink(); self.e.path.unlink()
        self.assertEqual(self.e.enter(**dict(self.entry_args, policy_acceptance=None))['decision'], 'opt_in_required')
        self.assertFalse(self.state.exists())
        unknown = dict(status='unknown', source_ref=None, observer=None)
        self.assertEqual(self.e.enter(**dict(self.entry_args, native=unknown))['decision'], 'native_check_required')
        self.assertEqual(self.e.enter(**dict(self.entry_args, native=dict(self.entry_args['native'], status='unavailable')))['decision'], 'unavailable')

    def test_report_missing_and_late_entry_remains_incomplete(self):
        v = self.begin()
        e = de.Entry(self.c, self.root/'entry'/'entry.json')
        self.assertEqual(e.report()['entry_observation'], 'missing_entry')
        self.entry(plan())
        self.advance(self.advance(v))
        self.assertFalse(self.e.report()['workflow_complete'])
        self.assertEqual(self.e.report()['entry_observation'], 'late_observation')

    def test_coherent_role_tamper_rejected_by_actual_guard(self):
        v = self.entry(plan())
        self.advance(self.advance(v))
        s = self.c._read()
        s['plan']['implementation'] = ['backend']; s['plan_hash'] = dw.digest(s['plan'])
        s['stages'][0]['role'] = 'backend'; self.c._save(s)
        # Journal's independently accepted plan also prevents this state rewrite.
        with self.assertRaisesRegex(ValueError, 'plan_mismatch'): self.e.report()

    def test_receipt_replay_and_snapshot_tamper(self):
        v = self.entry(plan())
        self.advance(self.advance(v))
        s = self.c._read(); receipt = list(s['receipts'].values())[-1]
        before = self.state.read_bytes()
        self.assertEqual(self.c.complete(receipt)['phase'], 'completed')
        self.assertEqual(before, self.state.read_bytes())
        s['stages'][-1]['snapshot'] = '0'*64; self.c._save(s)
        with self.assertRaisesRegex(ValueError, 'snapshot_mismatch'): self.e.report()

    def visual_receipt(self, view, evidence):
        self.serial += 1
        snap = dw.snapshot(self.project, ['app.py'])
        return dict(id='visual-boundary-'+str(self.serial), binding=self.c.binding, stage_id=view['stage_id'],
                    native=copy.deepcopy(view['native']), observer='coordinator-example', source_tool='synthetic-official-observation',
                    terminal='succeeded', result=dict(outcome='pass', self_test='not_applicable', snapshot=snap,
                        acceptance={k:'pass' for k in self.ac}, findings=[], artifact=None, visual_evidence=evidence))

    def current_capture(self, kind='image'):
        state = self.c._read()
        implemented = [x for x in state['stages'] if x['role'] in ('frontend', 'backend')][-1]
        return dict(kind=kind, subject='implementation',
                    provenance='implementation_capture' if kind == 'image' else 'real_ui_observation',
                    implementation_stage_id=implemented['id'], source_ref='synthetic-current-implemented-ui',
                    snapshot=dw.snapshot(self.project, ['app.py']), observer='coordinator-example')

    def test_r1_generated_mockup_only_cannot_pass_visual_gate(self):
        v = self.launch(self.advance(self.entry(plan(visual=True))))
        reference = dict(kind='image', subject='design_reference', provenance='generated_mockup',
                         implementation_stage_id=None, source_ref='generated-preimplementation-mockup-only-no-actual-ui',
                         snapshot=dw.snapshot(self.project, ['app.py']), observer='coordinator-example')
        v = self.c.complete(self.visual_receipt(v, [reference]))
        report = self.e.report()
        self.assertEqual(report['visual_review'], 'unverified')
        self.assertEqual(report['gates']['visual_review'], 'unverified')
        self.assertFalse(report['workflow_complete'])
        self.assertEqual(v['role'], 'frontend')
        self.assertEqual(self.c._read()['round'], 1)

    def test_r1_legacy_image_without_explicit_subject_is_rejected(self):
        v = self.launch(self.advance(self.entry(plan(visual=True))))
        old = dict(kind='image', source_ref='generated-preimplementation-mockup-only-no-actual-ui',
                   snapshot=dw.snapshot(self.project, ['app.py']), observer='coordinator-example')
        self.unchanged_failure(lambda:self.c.complete(self.visual_receipt(v, [old])))

    def test_r1_provenance_and_evidence_schema_are_strict_and_bounded(self):
        v = self.launch(self.advance(self.entry(plan(visual=True))))
        capture = self.current_capture()
        for change in (dict(subject='unknown'), dict(provenance='generated_mockup'), dict(kind='real_ui'),
                       dict(implementation_stage_id=None), dict(observer='worker'), dict(snapshot='0'*64),
                       dict(source_ref='x'*4097), dict(extra=True)):
            with self.subTest(change=change):
                self.unchanged_failure(lambda:self.c.complete(self.visual_receipt(v, [dict(capture, **change)])))
        self.unchanged_failure(lambda:self.c.complete(self.visual_receipt(v, [capture]*33)))
        self.assertFalse(self.e.report()['workflow_complete'])

    def test_r1_capture_must_reference_latest_mixed_implementation(self):
        v = self.entry(plan(['frontend', 'backend'], visual=True))
        first_id = v['stage_id']
        v = self.launch(self.advance(self.advance(v)))
        capture = self.current_capture()
        capture['implementation_stage_id'] = first_id
        self.c.complete(self.visual_receipt(v, [capture]))
        self.assertEqual(self.e.report()['visual_review'], 'unverified')
        self.assertFalse(self.e.report()['workflow_complete'])

    def test_r1_valid_current_capture_and_real_ui_observation_pass(self):
        for kind in ('image', 'real_ui'):
            with self.subTest(kind=kind):
                self.state.unlink(missing_ok=True)
                if hasattr(self, 'e'): self.e.path.unlink(missing_ok=True)
                v = self.launch(self.advance(self.advance(self.entry(plan(['backend','frontend'], visual=True)))))
                v = self.c.complete(self.visual_receipt(v, [self.current_capture(kind)]))
                self.advance(v)
                self.assertTrue(self.e.report()['workflow_complete'])
                self.assertEqual(self.e.report()['visual_review'], 'pass')

    def test_r1_mixed_visual_failure_cannot_extend_total_cap(self):
        self.p.update(max_dispatches=4, max_fix_rounds=1); self.write_policy()
        v = self.launch(self.advance(self.advance(self.entry(plan(['frontend','backend'], visual=True)))))
        v = self.c.complete(self.visual_receipt(v, []))
        self.assertEqual(v['phase'], 'exhausted')
        self.assertEqual(len(self.c._read()['stages']), 3)
        self.assertFalse(self.e.report()['workflow_complete'])

    def test_r1_actual_capture_stales_after_source_edit(self):
        v = self.launch(self.advance(self.entry(plan(visual=True))))
        v = self.c.complete(self.visual_receipt(v, [self.current_capture()]))
        self.advance(v)
        (self.project/'app.py').write_text('source changed after capture')
        report = self.e.report()
        self.assertEqual(report['visual_review'], 'stale')
        self.assertEqual(report['independent_review'], 'stale')
        self.assertFalse(report['workflow_complete'])

    def coherent_actor_rewrite(self, state, review_role, provider_session):
        stage = [x for x in state['stages'] if x['role'] == review_role][-1]
        stage['native']['provider_session_id'] = provider_session
        for receipt in state['receipts'].values():
            if receipt['stage_id'] == stage['id']:
                receipt['native'] = copy.deepcopy(stage['native'])
        self.c._save(state)  # Recompute integrity; malformed semantic identity stays detectable.
        return stage['id']

    def r3_baseline(self, pre='fresh', human=True, visual=True):
        self.state.unlink(missing_ok=True)
        if hasattr(self, 'e'): self.e.path.unlink(missing_ok=True)
        p = plan(pre=pre, human=human, visual=visual)
        if pre == 'reuse': p['reuse'] = self.artifact()
        v = self.entry(p)
        if pre == 'fresh': v = self.advance(v)
        if human:
            decision = self.decision(v) if pre == 'fresh' else dict(
                plan_hash=v['plan_hash'], artifact=v['proposal'], proposal_stage_id='reuse',
                decision='approved', instruction_ref='explicit-reused-design-choice', observer='coordinator-example')
            v = self.c.select_design(decision)
        return v

    def test_r3_required_artifact_change_stales_visual(self):
        for pre in ('fresh', 'reuse'):
            for human in (True, False):
                with self.subTest(pre=pre, human=human):
                    v = self.r3_baseline(pre, human)
                    for _ in range(3): v = self.advance(v)
                    self.assertEqual(v['phase'], 'completed')
                    self.assertTrue(self.e.report()['workflow_complete'])
                    original = self.state.read_bytes()
                    entry = self.e.path.read_bytes()
                    receipts = copy.deepcopy(self.c._read()['receipts'])
                    (self.project/'design.txt').write_text('new-design-baseline')
                    for _ in range(2):
                        report = self.e.report(); view = self.c.next()
                        self.assertFalse(report['workflow_complete'])
                        self.assertFalse(view['gates']['complete'])
                        self.assertEqual(report['gates'], view['gates']['statuses'])
                        self.assertEqual(report['gates']['design_selection'], 'stale')
                        self.assertEqual(report['human_selection'], 'stale' if human else 'not_performed')
                        self.assertEqual(report['visual_review'], 'stale',
                                         'Required visual review must become stale when its design baseline changes')
                        self.assertEqual(report['independent_review'], 'pass')
                        self.assertEqual(self.c._read()['receipts'], receipts)
                        self.assertEqual(self.state.read_bytes(), original)
                        self.assertEqual(self.e.path.read_bytes(), entry)
                    self.assertEqual([s['result']['outcome'] for s in self.c._read()['stages']],
                                     ['pass'] * (4 if pre == 'fresh' else 3))

    def test_r3_unchanged_baseline_remains_pass(self):
        for pre in ('fresh', 'reuse'):
            with self.subTest(pre=pre):
                v = self.r3_baseline(pre)
                for _ in range(3): v = self.advance(v)
                original = self.state.read_bytes()
                for _ in range(2):
                    report = self.e.report(); view = self.c.next()
                    self.assertTrue(report['workflow_complete'])
                    self.assertEqual(report['gates'], view['gates']['statuses'])
                    self.assertEqual(report['visual_review'], 'pass')
                    self.assertEqual(self.state.read_bytes(), original)

    def test_r3_design_stale_preserves_nonpass_visual_statuses(self):
        for pre in ('fresh', 'reuse'):
            for status in ('absent', 'not_performed', 'pending', 'failed', 'unverified'):
                with self.subTest(pre=pre, status=status):
                    v = self.r3_baseline(pre, visual=status != 'absent')
                    v = self.advance(v)
                    if status == 'absent': v = self.advance(v)
                    elif status == 'pending': v = self.launch(v)
                    elif status in ('failed', 'unverified'):
                        # A correction may queue pre-design; the observed visual failure remains history.
                        v = self.advance(v, outcome='fail' if status == 'failed' else 'pass', evidence=False)
                    before = self.e.report()['visual_review']
                    self.assertEqual(before, 'not_performed' if status == 'absent' else status)
                    original = self.state.read_bytes()
                    (self.project/'design.txt').write_text('changed baseline')
                    report = self.e.report(); view = self.c.next()
                    self.assertEqual(report['visual_review'], before)
                    self.assertEqual(report['gates'], view['gates']['statuses'])
                    self.assertFalse(report['workflow_complete'])
                    self.assertEqual(self.state.read_bytes(), original)

    def test_r3_unselected_design_never_claims_visual_performed(self):
        v = self.advance(self.entry(plan(pre='fresh', human=True, visual=True)))
        for changed in (False, True):
            if changed: (self.project/'design.txt').write_text('unselected mutation')
            original = self.state.read_bytes()
            report = self.e.report(); view = self.c.next()
            self.assertEqual(report['human_selection'], 'stale' if changed else 'pending')
            self.assertEqual(report['visual_review'], 'not_performed')
            self.assertNotIn('visual_review', view['gates']['statuses'])
            self.assertFalse(report['workflow_complete'])
            self.assertEqual(self.state.read_bytes(), original)

    def test_r3_collision_overrides_design_stale(self):
        for role in ('visual_review', 'reviewer'):
            with self.subTest(role=role):
                v = self.r3_baseline()
                for _ in range(3): v = self.advance(v)
                state = self.c._read()
                sid = self.coherent_actor_rewrite(state, role, state['stages'][0]['native']['provider_session_id'])
                (self.project/'design.txt').write_text('changed baseline')
                original = self.state.read_bytes()
                report = self.e.report(); view = self.c.next()
                self.assertEqual(report['gates'][role], 'unverified')
                self.assertEqual(report['visual_review'], 'unverified' if role == 'visual_review' else 'stale')
                self.assertEqual(report['gates'], view['gates']['statuses'])
                self.assertIn('independence_collision:'+role+':'+sid, report['omissions'])
                self.assertFalse(report['workflow_complete'])
                self.assertEqual(self.state.read_bytes(), original)

    def test_r3_source_only_stales_reviews_preserves_design(self):
        v = self.r3_baseline()
        for _ in range(3): v = self.advance(v)
        (self.project/'app.py').write_text('changed source only')
        original = self.state.read_bytes()
        report = self.e.report(); view = self.c.next()
        self.assertEqual(report['human_selection'], 'pass')
        self.assertEqual(report['visual_review'], 'stale')
        self.assertEqual(report['independent_review'], 'stale')
        self.assertEqual(report['gates'], view['gates']['statuses'])
        self.assertFalse(report['workflow_complete'])
        self.assertEqual(self.state.read_bytes(), original)

    def test_r2_coherent_functional_and_visual_collisions_never_report_pass(self):
        for review_role in ('reviewer', 'visual_review'):
            for producer in ('frontend', 'backend', 'pre_design', 'coordinator'):
                for stale in (False, True):
                    with self.subTest(review=review_role, producer=producer, stale=stale):
                        self.state.unlink(missing_ok=True)
                        if hasattr(self, 'e'): self.e.path.unlink(missing_ok=True)
                        v = self.entry(plan(['backend','frontend'], pre='fresh', visual=True))
                        for _ in range(5): v = self.advance(v)
                        state = self.c._read()
                        session = ('coordinator' if producer == 'coordinator' else
                                   [x['native']['provider_session_id'] for x in state['stages'] if x['role'] == producer][-1])
                        sid = self.coherent_actor_rewrite(state, review_role, session)
                        if stale: (self.project/'app.py').write_text('changed after review '+sid)
                        report = self.e.report()
                        view = self.c.next()
                        field = 'independent_review' if review_role == 'reviewer' else 'visual_review'
                        self.assertEqual(report[field], 'unverified')
                        self.assertEqual(report['gates'][review_role], 'unverified')
                        self.assertEqual(view['gates']['statuses'][review_role], 'unverified')
                        self.assertEqual(report['omissions'], view['omissions'])
                        self.assertIn('independence_collision:'+review_role+':'+sid, report['omissions'])
                        self.assertFalse(report['workflow_complete'])
                        self.assertFalse(view['gates']['complete'])
                        # Public execution references still describe the exact observed stored bindings.
                        actual = [x for x in report['executions'] if x['stage_id'] == sid][0]
                        self.assertEqual(actual['native']['provider_session_id'], session)

    def test_r2_valid_independent_review_report_and_view_agree(self):
        v = self.entry(plan(visual=True))
        v = self.advance(self.advance(self.advance(v)))
        report = self.e.report(); view = self.c.next()
        self.assertTrue(report['workflow_complete'])
        self.assertEqual(report['gates'], view['gates']['statuses'])
        self.assertEqual(report['omissions'], [])
        self.assertEqual(view['omissions'], [])
        self.assertEqual((report['visual_review'], report['independent_review']), ('pass','pass'))

    def test_r2_collision_after_attach_blocks_pass_before_report(self):
        v = self.advance(self.entry(plan()))
        v = self.launch(v)
        state = self.c._read()
        session = state['stages'][0]['native']['provider_session_id']
        self.coherent_actor_rewrite(state, 'reviewer', session)
        report = self.e.report()
        self.assertEqual(report['independent_review'], 'unverified')
        self.assertFalse(report['workflow_complete'])
        self.assertEqual(self.c.next()['gates']['statuses']['reviewer'], 'unverified')
