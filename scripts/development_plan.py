"""Opt-in v3 immutable role plans. V2 is delegated unchanged, never migrated.

All decisions/evidence are coordinator assertions, not authentication or permissions.
"""
import copy
import re
import uuid
from pathlib import Path
from lifecycle_io import digest, locked, private_file, read_json


def api():
    import development_workflow
    return development_workflow


DIMENSIONS = ('theme_reference', 'hierarchy', 'typography', 'spacing_density', 'alignment', 'consistency')
MINIMUM_QUALITY = {'theme_reference', 'hierarchy', 'spacing_density', 'consistency'}
CONTEXT = 'viewport theme content state'
INSPECTIONS = ('full_actual', 'full_enlarged', 'detail_actual', 'detail_enlarged')
FRONTEND_DECISIONS = ('components', 'tokens', 'layout', 'shared_components', 'separate_label_value_chevron')


def validate_frontend_baseline(value):
    d = api()
    d.exact(value, 'version reference_ref source_ref context decisions')
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('invalid_frontend_baseline_version')
    d.text(value['reference_ref']); d.text(value['source_ref'])
    validate_context(value['context'])
    d.exact(value['decisions'], ' '.join(FRONTEND_DECISIONS))
    for decision in value['decisions'].values():
        d.exact(decision, 'decision allowed_reason')
        d.text(decision['decision'])
        if decision['allowed_reason'] is not None: d.text(decision['allowed_reason'])


def validate_frontend_comparison(value):
    d = api()
    d.exact(value, 'version baseline_hash stage_id snapshot context comparisons')
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('invalid_frontend_comparison_version')
    for key, size in (('baseline_hash', 64), ('stage_id', 32), ('snapshot', 64)):
        if not isinstance(value[key], str) or not re.fullmatch('[a-f0-9]{'+str(size)+'}', value[key]):
            raise ValueError('invalid_frontend_comparison_binding')
    validate_context(value['context'])
    d.exact(value['comparisons'], ' '.join(FRONTEND_DECISIONS))
    for row in value['comparisons'].values():
        d.exact(row, 'status observation difference allowed_reason reference_ref implementation_ref')
        if row['status'] not in ('match', 'allowed_difference', 'fail', 'unverified'):
            raise ValueError('invalid_frontend_comparison_status')
        d.text(row['observation']); d.text(row['difference'])
        for key in ('allowed_reason', 'reference_ref', 'implementation_ref'):
            if row[key] is not None: d.text(row[key])


def validate_context(value):
    api().exact(value, CONTEXT)
    for v in value.values():
        if v is not None:
            api().text(v)


def validate_quality_contract(value):
    d = api()
    d.exact(value, 'version required context')
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('invalid_visual_quality_version')
    required = value['required']
    if (not isinstance(required, list) or any(not isinstance(x, str) for x in required) or
            len(required) != len(set(required)) or not MINIMUM_QUALITY <= set(required) <= set(DIMENSIONS)):
        raise ValueError('required_visual_criteria_missing')
    validate_context(value['context'])


def validate_quality_result(contract, value):
    d = api()
    d.exact(value, 'version reference_context implementation_context observations inspections prior_findings_rechecked device')
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('invalid_visual_quality_version')
    for key in ('reference_context', 'implementation_context'):
        validate_context(value[key])
    d.exact(value['observations'], ' '.join(DIMENSIONS))
    for row in value['observations'].values():
        d.exact(row, 'status detail reference_ref implementation_ref')
        if row['status'] not in ('pass', 'fail', 'unverified'):
            raise ValueError('invalid_visual_status')
        d.text(row['detail'])
        for key in ('reference_ref', 'implementation_ref'):
            if row[key] is not None: d.text(row[key])
    d.exact(value['inspections'], ' '.join(INSPECTIONS))
    for row in value['inspections'].values():
        d.exact(row, 'source_ref observation')
        for v in row.values():
            if v is not None: d.text(v)
    if type(value['prior_findings_rechecked']) is not bool:
        raise ValueError('boolean_required')
    d.exact(value['device'], 'status source_ref detail')
    if value['device']['status'] not in ('observed', 'unverified', 'not_performed'):
        raise ValueError('invalid_device_coverage')
    d.text(value['device']['detail'])
    if value['device']['source_ref'] is not None: d.text(value['device']['source_ref'])
    if value['device']['status'] == 'observed' and value['device']['source_ref'] is None:
        raise ValueError('device_observation_reference_required')


def validate_plan(value):
    d = api()
    strict = isinstance(value, dict) and value.get('version') in (2, 3)
    frontend = isinstance(value, dict) and value.get('version') == 3
    if frontend and 'frontend_baseline' not in value:
        raise ValueError('frontend_baseline_required_before_entry')
    d.exact(value, 'version implementation pre_design human_selection visual_review artifact reuse selection_ref' +
            (' visual_quality' if strict else '') + (' frontend_baseline' if frontend else ''))
    if type(value['version']) is not int or value['version'] not in (1, 2, 3):
        raise ValueError('invalid_plan_version')
    if value['implementation'] not in (['backend'], ['frontend'], ['frontend', 'backend'], ['backend', 'frontend']):
        raise ValueError('explicit_implementation_sequence_required')
    if value['pre_design'] not in ('none', 'fresh', 'reuse'):
        raise ValueError('invalid_design_mode')
    for k in ('human_selection', 'visual_review'):
        if type(value[k]) is not bool:
            raise ValueError('boolean_required')
    d.text(value['selection_ref'])
    if value['pre_design'] == 'none':
        if value['artifact'] is not None or value['reuse'] is not None or value['human_selection']:
            raise ValueError('design_required_for_selection')
    else:
        d.text(value['artifact'])
        if value['pre_design'] == 'reuse':
            validate_artifact(value['reuse'])
            if value['reuse']['path'] != value['artifact']:
                raise ValueError('reuse_artifact_mismatch')
        elif value['reuse'] is not None:
            raise ValueError('fresh_cannot_reuse')
    if strict:
        if 'frontend' not in value['implementation'] or not value['visual_review']:
            raise ValueError('strict_ui_requires_frontend_and_visual_review')
        validate_quality_contract(value['visual_quality'])
    if frontend:
        validate_frontend_baseline(value['frontend_baseline'])
        if value['frontend_baseline']['context'] != value['visual_quality']['context']:
            raise ValueError('frontend_baseline_context_mismatch')
    return value


def validate_artifact(a):
    d = api()
    d.exact(a, 'path version sha256 selection_ref')
    for k in ('path', 'version', 'selection_ref'):
        d.text(a[k])
    if not isinstance(a['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', a['sha256']):
        raise ValueError('artifact_hash_required')
    return a


def sequence(plan):
    return (['pre_design'] if plan['pre_design'] == 'fresh' else []) + plan['implementation'] + (
        ['visual_review'] if plan['visual_review'] else []) + ['reviewer']


def is_v3(s):
    return s['version'] == 3


class PlanMixin:
    def _frontend_baseline_omissions(self, s):
        if s['plan']['version'] != 3: return []
        baseline = s['plan']['frontend_baseline']
        context = baseline['context']
        if any(baseline[k].strip().lower() in ('unknown', 'unset', 'unverified') for k in ('reference_ref', 'source_ref')):
            return ['frontend_baseline_source_unverified:freeze_reference_and_accepted_decisions_before_execution']
        if any(row['decision'].strip().lower() in ('unknown', 'unset', 'unverified') for row in baseline['decisions'].values()):
            return ['frontend_baseline_decision_unverified:accept_component_token_layout_decisions_before_execution']
        if any(v is None or v.strip().lower() in ('unknown', 'unset', 'unverified') for v in context.values()):
            return ['frontend_baseline_context_unverified:freeze_known_context_before_execution']
        return []

    def _frontend_omissions(self, s, stage, r):
        if s['plan']['version'] != 3 or stage['role'] != 'frontend': return []
        missing = self._frontend_baseline_omissions(s)
        q = r.get('frontend_comparison') if r else None
        if q is None: return missing + ['frontend_self_comparison_missing:compare_before_independent_review']
        baseline = s['plan']['frontend_baseline']
        if (q['baseline_hash'] != digest(baseline) or q['stage_id'] != stage['id'] or
                q['snapshot'] != r['snapshot'] or q['context'] != baseline['context']):
            missing.append('frontend_self_comparison_binding_mismatch:baseline_stage_snapshot_context')
        captures = {e['source_ref'] for e in r['visual_evidence'] if e['kind'] == 'image' and
                    e['subject'] == 'implementation' and e['provenance'] == 'implementation_capture' and
                    e['implementation_stage_id'] == stage['id'] and e['snapshot'] == r['snapshot']}
        references = {e['source_ref'] for e in r['visual_evidence'] if e['kind'] == 'image' and
                      e['subject'] == 'design_reference'}
        for key, row in q['comparisons'].items():
            allowed = baseline['decisions'][key]['allowed_reason']
            if (row['status'] not in ('match', 'allowed_difference') or
                    (row['status'] == 'allowed_difference' and (allowed is None or row['allowed_reason'] != allowed)) or
                    row['reference_ref'] != baseline['reference_ref'] or row['reference_ref'] not in references or
                    row['implementation_ref'] not in captures):
                missing.append('frontend_self_comparison_unverified_or_failed:' + key)
        return missing

    def _quality_omissions(self, s, stage, r):
        q = r.get('visual_quality')
        if q is None: return ['strict_visual_quality_missing']
        contract = s['plan']['visual_quality']
        missing = []
        contexts = [contract['context'], q['reference_context'], q['implementation_context']]
        if any(any(c[k] is None or c[k].strip().lower() in ('unknown', 'unset', 'unverified') for c in contexts) or
               len({c[k] for c in contexts}) != 1 for k in CONTEXT.split()):
            missing.append('visual_comparison_context_unverified_or_mismatched')
        implemented = [x for x in s['stages'] if x['attempt'] < stage['attempt'] and
                       x['role'] in ('frontend', 'backend') and x['result'] and x['native']]
        last_id = implemented[-1]['id'] if implemented else None
        captures = {e['source_ref'] for e in r['visual_evidence'] if e['kind'] == 'image' and
                    e['subject'] == 'implementation' and e['provenance'] == 'implementation_capture' and
                    e['implementation_stage_id'] == last_id and e['snapshot'] == r['snapshot']}
        references = {e['source_ref'] for e in r['visual_evidence'] if e['kind'] == 'image' and
                      e['subject'] == 'design_reference'}
        for key in contract['required']:
            row = q['observations'][key]
            if row['status'] != 'pass' or row['reference_ref'] not in references or row['implementation_ref'] not in captures:
                missing.append('visual_criterion_unverified_or_failed:' + key)
        for key in INSPECTIONS:
            row = q['inspections'][key]
            if row['source_ref'] not in captures or row['observation'] is None:
                missing.append('visual_inspection_missing:' + key)
        # This is the current full normal screen, never an old crop after a fix.
        earlier_failures = any(x['attempt'] < stage['attempt'] and x['result'] and
                               not self._passed(s, x, x['result']) for x in s['stages'])
        if earlier_failures and not q['prior_findings_rechecked']:
            missing.append('prior_findings_not_rechecked')
        return missing

    def _quality_coverage(self, s):
        strict = s['plan']['version'] in (2, 3)
        gates = self._gates(s)['statuses']
        visual = [x for x in s['stages'] if x['role'] == 'visual_review' and x['result']]
        q = visual[-1]['result'].get('visual_quality') if visual else None
        status = gates.get('visual_review', 'not_performed')
        reference = 'unverified'
        if strict and q and status == 'pass':
            reference = 'pass'
        elif strict and q and q['observations']['theme_reference']['status'] == 'fail':
            reference = 'fail'
        return dict(strict_visual_quality_active=strict,
                    visual_quality_coverage={1:'legacy_not_strictly_certified', 2:'visual_only_v1_frontend_unverified',
                                             3:'strict_v2_with_frontend_prerequisites'}[s['plan']['version']],
                    frontend_quality_active=s['plan']['version'] == 3,
                    frontend_prerequisite=gates.get('frontend', 'not_performed') if s['plan']['version'] == 3 else 'unverified',
                    frontend_baseline=copy.deepcopy(s['plan'].get('frontend_baseline')),
                    frontend_baseline_hash=digest(s['plan']['frontend_baseline']) if s['plan']['version'] == 3 else None,
                    frontend_comparisons=[dict(stage_id=x['id'], comparison=copy.deepcopy(x['result'].get('frontend_comparison')))
                                          for x in s['stages'] if x['role'] == 'frontend' and x['result']],
                    functionality=gates.get('reviewer', 'not_performed'),
                    visual_quality=status if strict else 'unverified',
                    reference_match=reference, visual_quality_review=copy.deepcopy(q),
                    real_device=copy.deepcopy(q['device']) if q else
                        dict(status='unverified', source_ref=None, detail='No device observation recorded'))

    def _artifact(self, artifact):
        validate_artifact(artifact)
        d = api()
        # Same bounded no-follow snapshot primitive as source evidence.
        actual = d.snapshot(self.project, [artifact['path']])
        expected = digest([dict(path=artifact['path'], bytes=(self.project / artifact['path']).stat().st_size,
                                sha256=artifact['sha256'])])
        if actual != expected:
            raise ValueError('design_artifact_changed')
        return copy.deepcopy(artifact)

    def _read(self):
        d = api()
        private_file(self.path, self.project)
        s = read_json(self.path)
        if s.get('version') != 3:
            return super()._read()
        d.exact(s, 'version binding policy policy_source acceptance files phase round stages receipts integrity plan plan_hash design_decisions')
        if (type(s['version']) is not int or s['binding'] != self.binding or s['integrity'] != digest({k: v for k, v in s.items() if k != 'integrity'}) or
                s['plan_hash'] != digest(validate_plan(s['plan']))):
            raise ValueError('plan_binding_integrity_mismatch')
        d.policy(s['policy'])
        d.exact(s['policy_source'], 'path version sha256')
        if (s['policy_source']['path'] != str(self.project / d.POLICY) or s['policy_source']['version'] != 1 or
                not re.fullmatch('[a-f0-9]{64}', s['policy_source']['sha256']) or not s['policy']['enabled']):
            raise ValueError('invalid_policy_admission')
        d.paths(self.project, s['files'], check_files=False)
        if not isinstance(s['acceptance'], dict) or not 1 <= len(s['acceptance']) <= 64:
            raise ValueError('original_acceptance_required')
        for k, v in s['acceptance'].items():
            d.text(k); d.text(v)
        seq = sequence(s['plan'])
        if len(seq) > s['policy']['max_dispatches']: raise ValueError('underbudget_plan')
        if (not isinstance(s['stages'], list) or not 1 <= len(s['stages']) <= s['policy']['max_dispatches'] or
                type(s['round']) is not int or not 0 <= s['round'] <= s['policy']['max_fix_rounds'] or
                s['phase'] not in ('ready', 'running', 'completed', 'failed', 'exhausted', 'cancel_pending', 'cancelled') or
                not isinstance(s['receipts'], dict) or not isinstance(s['design_decisions'], list)):
            raise ValueError('invalid_plan_state')
        cursor, rounds, ids = 0, 0, set()
        for i, stage in enumerate(s['stages']):
            d.exact(stage, 'id role attempt intent native snapshot result')
            if (cursor >= len(seq) or stage['role'] != seq[cursor] or type(stage['attempt']) is not int or stage['attempt'] != i + 1 or stage['id'] in ids or
                    not re.fullmatch('[a-f0-9]{32}', stage['id'])):
                raise ValueError('invalid_plan_stage_sequence')
            ids.add(stage['id'])
            if not isinstance(stage['snapshot'], str) or not re.fullmatch('[a-f0-9]{64}', stage['snapshot']): raise ValueError('invalid_snapshot')
            if stage['intent'] is not None:
                d.validate_intent(stage['intent'])
                if stage['intent']['client'] != 'orca': raise ValueError('unsupported_client')
            if stage['native'] is not None:
                d.native(stage['native'])
                if stage['intent'] != d.intent_for(stage['native']): raise ValueError('intent_mismatch')
            r = stage['result']
            if r is not None:
                self._result(s, stage, r)
                if r['snapshot'] != stage['snapshot']: raise ValueError('result_snapshot_mismatch')
                matches = [v for v in s['receipts'].values() if v['stage_id'] == stage['id'] and v['result'] == r and v['native'] == stage['native']]
                if len(matches) != 1 or stage['native'] is None: raise ValueError('result_receipt_required')
                if not self._passed(s, stage, r) or matches[0]['terminal'] != 'succeeded':
                    cursor = 0
                    if i < len(s['stages']) - 1: rounds += 1
                else:
                    cursor += 1
                    if cursor == len(seq) and i < len(s['stages']) - 1: raise ValueError('stages_after_pass')
            elif i != len(s['stages']) - 1:
                raise ValueError('unfinished_historical_stage')
        if rounds != s['round']: raise ValueError('fix_round_mismatch')
        if len(s['receipts']) != sum(x['result'] is not None for x in s['stages']): raise ValueError('orphan_receipts')
        for key, r in s['receipts'].items():
            d.exact(r, 'id binding stage_id native observer source_tool terminal result')
            if (key != r['id'] or r['binding'] != self.binding or r['stage_id'] not in ids or
                    r['observer'] != self.binding['coordinator'] or r['terminal'] not in ('succeeded', 'failed', 'cancelled', 'stopped')):
                raise ValueError('invalid_receipt')
            d.text(r['source_tool']); d.native(r['native'])
        for decision in s['design_decisions']:
            self._decision(s, decision)
        if s['phase'] == 'completed' and (cursor != len(seq) or (s['plan']['human_selection'] and not s['design_decisions'])):
            raise ValueError('required_gates_bypassed')
        return s

    def begin(self, *, plan=None, **kwargs):
        d = api()
        if d.direct_request(kwargs['kind'], kwargs['direct'], kwargs['native_available']):
            return {'decision': 'direct'}
        if plan is None:
            if self.path.exists() and read_json(self.path).get('version') == 3:
                raise ValueError('accepted_plan_required_on_resume')
            return super().begin(**kwargs)
        plan = copy.deepcopy(validate_plan(plan))
        files = d.paths(self.project, kwargs['files'])
        acceptance = copy.deepcopy(kwargs['acceptance'])
        if not isinstance(acceptance, dict) or not 1 <= len(acceptance) <= 64: raise ValueError('original_acceptance_required')
        for k, v in acceptance.items(): d.text(k); d.text(v)
        candidate = d.policy_snapshot(self.project)
        if candidate is None or not candidate['value']['enabled']: return {'decision': 'opt_in_required'}
        if kwargs['native_available'] is not True:
            return {'decision': 'native_check_required' if kwargs['native_available'] is None else 'unavailable'}
        if plan['pre_design'] != 'none': d.paths(self.project, [plan['artifact']])
        if plan['pre_design'] == 'reuse': self._artifact(plan['reuse'])
        with locked(self.path.with_suffix('.lock'), self.project):
            actual = d.policy_snapshot(self.project)
            expected = kwargs.get('expected_policy')
            if candidate != actual: raise ValueError('policy_changed_during_admission')
            if expected is not None and expected != dict(sha256=actual['sha256'], limits=actual['value']):
                raise ValueError('accepted_policy_changed_before_admission')
            if self.path.exists():
                s = self._read()
                if not is_v3(s) or (s['plan'], s['files'], s['acceptance']) != (plan, files, acceptance):
                    raise ValueError('begin_plan_conflict_no_migration')
            else:
                if len(sequence(plan)) > actual['value']['max_dispatches']:
                    raise ValueError('required_plan_exceeds_total_dispatch_cap')
                if plan['pre_design'] == 'reuse': self._artifact(plan['reuse'])
                s = dict(version=3, binding=self.binding, policy=actual['value'],
                         policy_source=dict(path=str(self.project / d.POLICY), version=1, sha256=actual['sha256']),
                         acceptance=acceptance, files=files, phase='ready', round=0, stages=[], receipts={},
                         integrity=None, plan=plan, plan_hash=digest(plan), design_decisions=[])
                self._stage(s, sequence(plan)[0]); self._save(s)
            return self._view(s)

    def _stage(self, s, role):
        if not is_v3(s): return super()._stage(s, role)
        if len(s['stages']) >= s['policy']['max_dispatches']:
            s['phase'] = 'exhausted'; return
        snap = api().snapshot(self.project, s['files'])
        s['stages'].append(dict(id=uuid.uuid4().hex, role=role, attempt=len(s['stages'])+1,
                                intent=None, native=None, snapshot=snap, result=None))
        s['phase'] = 'ready'

    def _proposal(self, s):
        if s['plan']['pre_design'] == 'reuse': return s['plan']['reuse']
        rows = [x for x in s['stages'] if x['role'] == 'pre_design' and x['result'] and self._passed(s, x, x['result'])]
        return rows[-1]['result']['artifact'] if rows else None

    def _decision(self, s, value):
        d = api()
        d.exact(value, 'plan_hash artifact proposal_stage_id decision instruction_ref observer')
        validate_artifact(value['artifact'])
        d.text(value['instruction_ref'])
        if (value['plan_hash'] != s['plan_hash'] or value['decision'] != 'approved' or
                value['observer'] != self.binding['coordinator']): raise ValueError('explicit_exact_human_decision_required')
        if s['plan']['pre_design'] == 'reuse':
            if value['proposal_stage_id'] != 'reuse' or value['artifact'] != s['plan']['reuse']: raise ValueError('proposal_mismatch')
        else:
            stages = [x for x in s['stages'] if x['id'] == value['proposal_stage_id'] and x['role'] == 'pre_design' and x['result']]
            if len(stages) != 1 or stages[0]['result']['artifact'] != value['artifact']: raise ValueError('proposal_mismatch')

    def select_design(self, decision):
        with locked(self.path.with_suffix('.lock'), self.project):
            s = self._read()
            if not is_v3(s) or not s['plan']['human_selection']: raise ValueError('no_human_gate')
            self._decision(s, decision)
            proposal = self._proposal(s)
            if proposal is None or proposal != decision['artifact']: raise ValueError('current_proposal_required')
            self._artifact(proposal)
            stage = s['stages'][-1]
            if stage['role'] not in s['plan']['implementation'] or stage['intent'] is not None:
                raise ValueError('decision_before_implementation_reservation_required')
            if decision not in s['design_decisions']:
                s['design_decisions'].append(copy.deepcopy(decision))
                self._artifact(proposal)
                self._save(s)
            return self._view(s)

    def _design_ready(self, s):
        if s['plan']['pre_design'] == 'none': return True
        proposal = self._proposal(s)
        if proposal is None: return False
        try: self._artifact(proposal)
        except (ValueError, OSError): return False
        if not s['plan']['human_selection']: return True
        stage_id = ('reuse' if s['plan']['pre_design'] == 'reuse' else
                    [x['id'] for x in s['stages'] if x['role'] == 'pre_design'][-1])
        return any(x['artifact'] == proposal and x['proposal_stage_id'] == stage_id for x in s['design_decisions'])

    def _pre_reserve(self, s, stage):
        if not is_v3(s): return
        if stage['role'] == 'frontend' and self._frontend_baseline_omissions(s):
            raise ValueError(self._frontend_baseline_omissions(s)[0])
        if stage['role'] != 'pre_design' and not self._design_ready(s):
            raise ValueError('design_selection_pending_or_stale')
        if stage['role'] in ('visual_review', 'reviewer') and api().snapshot(self.project, s['files']) != stage['snapshot']:
            raise ValueError('code_changed_before_review')

    def attach(self, stage_id, execution):
        s = self._read()
        if not is_v3(s): return super().attach(stage_id, execution)
        d = api(); execution = copy.deepcopy(d.native(execution))
        with locked(self.path.with_suffix('.lock'), self.project):
            s = self._read(); stage = s['stages'][-1]
            if stage['id'] != stage_id or s['phase'] not in ('ready', 'running', 'cancel_pending'): raise ValueError('stale_stage')
            if stage['intent'] != d.intent_for(execution): raise ValueError('matching_prelaunch_intent_required')
            if stage['native'] is not None:
                if stage['native'] != execution: raise ValueError('worker_binding_conflict')
                return self._view(s)
            if self._review_collision(s, stage['role'], execution):
                raise ValueError('independent_provider_session_required')
            self._guard(execution, stage['role'])
            if stage['role'] == 'frontend' and self._frontend_baseline_omissions(s):
                raise ValueError(self._frontend_baseline_omissions(s)[0])
            if stage['role'] != 'pre_design' and not self._design_ready(s): raise ValueError('design_selection_pending_or_stale')
            if stage['role'] in ('visual_review', 'reviewer') and d.snapshot(self.project, s['files']) != stage['snapshot']: raise ValueError('code_changed_before_review')
            stage['native'] = execution
            if s['phase'] != 'cancel_pending': s['phase'] = 'running'
            self._save(s); return self._view(s)

    def _result(self, s, stage, r):
        d = api()
        strict = s['plan']['version'] in (2, 3)
        d.exact(r, 'outcome self_test snapshot acceptance findings artifact visual_evidence' +
                (' visual_quality' if strict and 'visual_quality' in r else '') +
                (' frontend_comparison' if s['plan']['version'] == 3 and 'frontend_comparison' in r else ''))
        if s['plan']['version'] == 3 and r.get('frontend_comparison') is not None:
            if stage['role'] != 'frontend': raise ValueError('frontend_comparison_wrong_role')
            validate_frontend_comparison(r['frontend_comparison'])
        if strict and r.get('visual_quality') is not None:
            validate_quality_result(s['plan']['visual_quality'], r['visual_quality'])
        if r['outcome'] not in ('pass', 'changes_requested', 'fail', 'unverified') or r['self_test'] not in ('pass', 'fail', 'not_applicable', 'unverified'):
            raise ValueError('invalid_result')
        if not isinstance(r['snapshot'], str) or not re.fullmatch('[a-f0-9]{64}', r['snapshot']): raise ValueError('snapshot_required')
        if not isinstance(r['acceptance'], dict) or set(r['acceptance']) != set(s['acceptance']) or any(v not in ('pass', 'fail', 'unverified') for v in r['acceptance'].values()): raise ValueError('original_ac_results_required')
        if not isinstance(r['findings'], list) or len(r['findings']) > 128: raise ValueError('findings_required')
        for f in r['findings']:
            d.exact(f, 'ac_id required resolved detail')
            if f['ac_id'] not in s['acceptance'] or type(f['required']) is not bool or type(f['resolved']) is not bool: raise ValueError('invalid_finding')
            d.text(f['detail'])
        if stage['role'] == 'pre_design':
            validate_artifact(r['artifact'])
            if r['artifact']['path'] != s['plan']['artifact']: raise ValueError('wrong_design_artifact')
        elif r['artifact'] is not None: raise ValueError('unexpected_artifact')
        if not isinstance(r['visual_evidence'], list) or len(r['visual_evidence']) > 32: raise ValueError('visual_evidence_required')
        for e in r['visual_evidence']:
            d.exact(e, 'kind subject provenance implementation_stage_id source_ref snapshot observer')
            if e['kind'] not in ('image', 'real_ui') or e['snapshot'] != r['snapshot'] or e['observer'] != self.binding['coordinator']: raise ValueError('invalid_visual_evidence')
            d.text(e['source_ref'])
            if e['subject'] == 'implementation':
                if ((e['kind'], e['provenance']) not in (('image', 'implementation_capture'), ('real_ui', 'real_ui_observation')) or
                        not isinstance(e['implementation_stage_id'], str) or not re.fullmatch('[a-f0-9]{32}', e['implementation_stage_id'])):
                    raise ValueError('invalid_implementation_evidence_provenance')
            elif e['subject'] == 'design_reference':
                if e['provenance'] not in ('generated_mockup', 'design_reference') or e['implementation_stage_id'] is not None:
                    raise ValueError('invalid_design_reference_provenance')
            else:
                raise ValueError('explicit_visual_evidence_subject_required')

    def _implementation_observed(self, s, stage, result):
        # Evidence describes an observation AFTER the last implementation duty.
        # Provenance is an explicit coordinator assertion, never inferred from image kind.
        implemented = [x for x in s['stages'] if x['attempt'] < stage['attempt'] and
                       x['role'] in ('frontend', 'backend') and x['native'] and x['result']]
        if not implemented:
            return False
        last = implemented[-1]
        if (last['role'] != s['plan']['implementation'][-1] or
                not self._passed(s, last, last['result']) or last['snapshot'] != result['snapshot']):
            return False
        return any(e['subject'] == 'implementation' and
                   (e['kind'], e['provenance']) in (('image', 'implementation_capture'), ('real_ui', 'real_ui_observation')) and
                   e['implementation_stage_id'] == last['id'] and e['snapshot'] == result['snapshot']
                   for e in result['visual_evidence'])

    def _review_collision(self, s, role, execution):
        if role not in ('visual_review', 'reviewer') or execution is None:
            return False
        d = api()
        return (d.actor(execution) == d.actor_from_coordinator(self.binding['coordinator_actor']) or
                any(x['native'] and d.actor(x['native']) == d.actor(execution)
                    for x in s['stages'] if x['role'] in ('frontend', 'backend', 'pre_design')))

    def _passed(self, s, stage, r):
        return (r['outcome'] == 'pass' and all(v == 'pass' for v in r['acceptance'].values()) and
                not any(f['required'] and not f['resolved'] for f in r['findings']) and r['self_test'] != 'fail' and
                (stage['role'] not in ('frontend', 'backend') or r['self_test'] == 'pass') and
                not self._frontend_omissions(s, stage, r) and
                (stage['role'] != 'visual_review' or (self._implementation_observed(s, stage, r) and
                 (s['plan']['version'] == 1 or not self._quality_omissions(s, stage, r)))))

    def complete(self, receipt):
        s = self._read()
        if not is_v3(s): return super().complete(receipt)
        d = api(); receipt = copy.deepcopy(receipt)
        d.exact(receipt, 'id binding stage_id native observer source_tool terminal result')
        d.text(receipt['id']); d.text(receipt['source_tool']); d.native(receipt['native'])
        if receipt['binding'] != self.binding or receipt['observer'] != self.binding['coordinator'] or receipt['terminal'] not in ('succeeded', 'failed', 'cancelled', 'stopped'): raise ValueError('receipt_binding_mismatch')
        with locked(self.path.with_suffix('.lock'), self.project):
            s = self._read()
            if receipt['id'] in s['receipts']:
                if receipt != s['receipts'][receipt['id']]: raise ValueError('conflicting_receipt_replay')
                return self._view(s)
            stage = s['stages'][-1]; r = receipt['result']
            if s['phase'] not in ('running', 'cancel_pending') or receipt['stage_id'] != stage['id'] or receipt['native'] != stage['native']: raise ValueError('stale_or_wrong_worker')
            self._guard(stage['native'], stage['role']); self._result(s, stage, r)
            current = d.snapshot(self.project, s['files'])
            if r['snapshot'] != current or (stage['role'] in ('reviewer', 'visual_review') and stage['snapshot'] != current): raise ValueError('review_snapshot_changed')
            if stage['role'] == 'pre_design': self._artifact(r['artifact'])
            stage['snapshot'] = current; stage['result'] = r; s['receipts'][receipt['id']] = receipt
            if s['phase'] == 'cancel_pending': s['phase'] = 'cancelled'
            elif receipt['terminal'] != 'succeeded': s['phase'] = 'failed'
            elif not self._passed(s, stage, r):
                if s['round'] >= s['policy']['max_fix_rounds'] or len(s['stages']) + len(sequence(s['plan'])) > s['policy']['max_dispatches']:
                    s['phase'] = 'exhausted'
                else:
                    s['round'] += 1; self._stage(s, sequence(s['plan'])[0])
            else:
                seq = sequence(s['plan']); index = seq.index(stage['role']) + 1
                if index == len(seq): s['phase'] = 'completed'
                else: self._stage(s, seq[index])
            self._save(s); return self._view(s)

    def _gates(self, s):
        current = api().snapshot(self.project, s['files'])
        seq = sequence(s['plan']); latest = {}; omissions = []
        for stage in s['stages']:
            r = stage['result']; status = 'pending' if stage['intent'] else 'not_performed'
            if r:
                receipt = [x for x in s['receipts'].values() if x['stage_id'] == stage['id']]
                status = ('failed' if not receipt or receipt[0]['terminal'] != 'succeeded' or r['outcome'] == 'fail' else
                          'pass' if self._passed(s, stage, r) else 'changes_requested' if r['outcome'] == 'changes_requested' else 'unverified')
                if stage['role'] in ('reviewer', 'visual_review') and stage['snapshot'] != current: status = 'stale'
            if self._review_collision(s, stage['role'], stage['native']):
                status = 'unverified'
                omissions.append('independence_collision:' + stage['role'] + ':' + stage['id'])
            latest[stage['role']] = status
        if s['plan']['version'] == 3:
            omissions.extend(self._frontend_baseline_omissions(s))
            frontend = [x for x in s['stages'] if x['role'] == 'frontend']
            if frontend:
                omissions.extend(self._frontend_omissions(s, frontend[-1], frontend[-1]['result']))
        if s['plan']['version'] in (2, 3):
            visual = [x for x in s['stages'] if x['role'] == 'visual_review']
            if not visual or visual[-1]['result'] is None:
                omissions.append('strict_visual_quality_pending')
            else:
                omissions.extend(self._quality_omissions(s, visual[-1], visual[-1]['result']))
        design = self._design_ready(s)
        if s['plan']['pre_design'] != 'none' and not design: latest['design_selection'] = 'pending'
        proposal = self._proposal(s)
        if proposal is not None:
            try: self._artifact(proposal)
            except (ValueError, OSError): latest['design_selection'] = 'stale'
        if design: latest['design_selection'] = 'pass' if s['plan']['pre_design'] != 'none' else 'not_performed'
        # A visual pass depends on the exact design baseline as well as source.
        # Derive freshness without changing receipts, failed/pending gates, or collision precedence.
        if latest.get('design_selection') == 'stale' and latest.get('visual_review') == 'pass':
            latest['visual_review'] = 'stale'
        return dict(statuses=latest, omissions=omissions, complete=design and not omissions and all(latest.get(k) == 'pass' for k in seq))

    def _view(self, s):
        if not is_v3(s): return super()._view(s)
        # Reuse common native instructions without claiming legacy backend responsibility.
        stage = s['stages'][-1]; role = stage['role']
        mapped = {'frontend': 'frontend', 'backend': 'backend', 'pre_design': 'designer', 'visual_review': 'designer', 'reviewer': 'verifier'}[role]
        view = dict(version=3, binding=s['binding'], phase=s['phase'], role=role, usage_role=mapped,
                    stage_id=stage['id'], attempt=stage['attempt'], launch_intent=copy.deepcopy(stage['intent']), launch_permission='none',
                    native=stage['native'], acceptance=s['acceptance'], files=s['files'], reviewed_snapshot=stage['snapshot'],
                    profile='native-default', usage_status='unmeasured', usage_complete=False, active_concurrency=1,
                    verification='coordinator_observed_assertion_not_cryptographic_proof', plan=copy.deepcopy(s['plan']),
                    plan_hash=s['plan_hash'], gates=self._gates(s), proposal=copy.deepcopy(self._proposal(s)),
                    instructions=[str(api().ROOT / 'roles' / (mapped+'-developer.md' if mapped in ('frontend', 'backend') else mapped+'.md')),
                                  str(api().ROOT / '.agents/skills' / ('ai-role-'+mapped) / 'SKILL.md')],
                    native_action='reserve pre-launch intent' if stage['intent'] is None else 'reconcile exact reserved operation; never relaunch')
        view.update(self._quality_coverage(s))
        view['omissions'] = view['gates']['omissions']
        reviews = [x for x in s['stages'] if x['role'] in ('pre_design', 'visual_review', 'reviewer') and x['result']]
        view['last_review'] = copy.deepcopy(reviews[-1]) if reviews else None
        view['unresolved_required_findings'] = ([copy.deepcopy(f) for f in reviews[-1]['result']['findings']
                                                if f['required'] and not f['resolved']] if reviews else [])
        return view
