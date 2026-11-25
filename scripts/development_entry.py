"""Explicit current-AI admission and reporting; no input hook, launcher or authentication."""
import copy
import os
from pathlib import Path

import development_workflow as dw
from lifecycle_io import digest, locked, now, private_file, read_json, save


def ref(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError('bounded_reference_required')
    return value


def native_check(value, observer):
    dw.exact(value, 'status source_ref observer')
    if value['status'] not in ('unknown', 'available', 'unavailable'):
        raise ValueError('invalid_native_status')
    if value['status'] != 'unknown':
        ref(value['source_ref'])
        if value['observer'] != observer:
            raise ValueError('coordinator_observed_source_required')
    elif value['source_ref'] is not None or value['observer'] is not None:
        raise ValueError('unknown_has_no_confirmed_source')
    return {'unknown': None, 'available': True, 'unavailable': False}[value['status']]


class Entry:
    """Version 1 journal binds one choice, request and exact controller path.

    acceptance is original AC, policy_acceptance is an explicit observed user choice.
    Neither the journal nor coordinator assertions constitute a security approval.
    """
    def __init__(self, controller, journal):
        self.c = controller
        self.path = Path(journal)
        if self.path.absolute() == self.c.path.absolute():
            raise ValueError('entry_controller_paths_must_differ')

    def _read(self):
        private_file(self.path, self.c.project)
        j = read_json(self.path)
        dw.exact(j, 'version binding controller request choice policy_acceptance native policy_observation observation created updated integrity')
        if (type(j['version']) is not int or j['version'] != 1 or j['binding'] != self.c.binding or
                j['controller'] != str(self.c.path.absolute()) or
                j['integrity'] != digest({k: v for k, v in j.items() if k != 'integrity'})):
            raise ValueError('entry_binding_or_integrity_mismatch')
        self._request(**j['request'])
        self._choice(j['choice'])
        native_check(j['native'], self.c.binding['coordinator'])
        if j['observation'] not in ('before_controller', 'late_observation'):
            raise ValueError('invalid_entry_observation')
        self._accepted(j['policy_acceptance'], j['policy_observation'])
        return j

    def _save(self, j):
        j['updated'] = now()
        j['integrity'] = digest({k: v for k, v in j.items() if k != 'integrity'})
        save(self.path, j, self.c.project)
        directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _request(self, kind, acceptance, files, review_required, plan=None):
        dw.direct_request(kind, False, None)
        if type(review_required) is not bool:
            raise ValueError('boolean_required')
        if not isinstance(acceptance, dict) or not 1 <= len(acceptance) <= 64:
            raise ValueError('original_acceptance_required')
        for k, v in acceptance.items():
            dw.text(k); dw.text(v)
        request = dict(kind=kind, acceptance=acceptance, files=dw.paths(self.c.project, files),
                       review_required=review_required)
        if plan is not None:
            from development_plan import validate_plan
            request['plan'] = copy.deepcopy(validate_plan(plan))
        return request

    def _choice(self, choice):
        dw.exact(choice, 'mode reason instruction_ref')
        if choice['mode'] not in ('workflow', 'direct'):
            raise ValueError('invalid_choice')
        ref(choice['reason']); ref(choice['instruction_ref'])

    def _accepted(self, value, candidate):
        if value is None:
            return False
        dw.exact(value, 'ref project sha256 limits')
        ref(value['ref'])
        if (candidate is None or value['project'] != str(self.c.project) or
                value['sha256'] != candidate['sha256'] or value['limits'] != candidate['value']):
            raise ValueError('policy_acceptance_scope_changed_reconfirm_explicitly')
        return candidate['value']['enabled']

    def enter(self, *, kind, choice, native, acceptance, files, review_required=True,
              policy_acceptance=None, plan=None):
        self._choice(choice)
        available = native_check(native, self.c.binding['coordinator'])
        # Small/conversation requests can stay stateless and avoid policy/role reads.
        if kind in ('conversation', 'small_change') and not self.path.exists():
            return dw.decision(self.c.project, kind, direct=choice['mode'] == 'direct', native_available=available)
        request = self._request(kind, acceptance, files, review_required, plan)
        private_file(self.c.path, self.c.project)
        candidate = None if choice['mode'] == 'direct' else dw.policy_snapshot(self.c.project)
        if choice['mode'] == 'direct':
            policy_acceptance = None  # Direct handling does not admit a delegation policy.
        with locked(self.path.with_suffix('.lock'), self.c.project):
            if self.path.exists():
                j = self._read()
                if j['request'] != request or j['choice'] != choice:
                    raise ValueError('entry_scope_choice_conflict_use_new_request')
                if policy_acceptance is None:
                    policy_acceptance = j['policy_acceptance']
                elif j['policy_acceptance'] is not None and policy_acceptance != j['policy_acceptance']:
                    raise ValueError('accepted_scope_conflict_use_new_request')
            else:
                j = dict(version=1, binding=self.c.binding, controller=str(self.c.path.absolute()),
                         request=request, choice=copy.deepcopy(choice), policy_acceptance=None,
                         native=native, policy_observation=None,
                         observation='late_observation' if self.c.path.exists() else 'before_controller',
                         created=now(), updated=None, integrity=None)
            policy_acceptance = copy.deepcopy(policy_acceptance)
            accepted = self._accepted(policy_acceptance, candidate)
            j.update(policy_acceptance=policy_acceptance, native=copy.deepcopy(native),
                     policy_observation=None if candidate is None else
                     dict(sha256=candidate['sha256'], value=candidate['value']))
            self._save(j)  # Persist choice BEFORE controller begin; restart may safely call begin again.
            if choice['mode'] == 'direct':
                return dict(decision='direct', review_conflict=review_required,
                            workflow_complete=False, entry_observation=j['observation'])
            if not accepted:
                return dict(decision='opt_in_required', native_status=native['status'],
                            action='select project scope and limits; record explicit user acceptance')
            # decision is advisory for new admission; existing controller next preserves exact operation.
            d = dw.decision(self.c.project, kind, native_available=available)
            if self.c.path.exists():
                with locked(self.c.path.with_suffix('.lock'), self.c.project):
                    s = self.c._read()
                    if s.get('plan') != request.get('plan'):
                        raise ValueError('entry_controller_plan_mismatch')
                    if s['acceptance'] != acceptance or s['files'] != request['files']:
                        raise ValueError('entry_controller_scope_mismatch')
                    if s['policy'] != policy_acceptance['limits'] or s['policy_source']['sha256'] != policy_acceptance['sha256']:
                        raise ValueError('entry_controller_policy_mismatch')
                return self.c.next(native_available=available)
            if d['decision'] != 'workflow':
                return d
            return self.c.begin(kind=kind, direct=False, native_available=available,
                                acceptance=acceptance, files=request['files'],
                                expected_policy={k: copy.deepcopy(policy_acceptance[k]) for k in ('sha256', 'limits')},
                                **({'plan': plan} if plan is not None else {}))

    def _report_save(self, output):
        destination = self.path.with_suffix('.report.json')
        if destination.absolute() in (self.path.absolute(), self.c.path.absolute()):
            raise ValueError('report_path_conflict')
        save(destination, output, self.c.project)
        return output

    def report(self, *, self_tests='unverified', skills=None, perspectives=None):
        """Derived report, never accepts execution/reviewer refs supplied by the caller.

        Caller self_tests describe solo checks only; controller developer receipts win.
        An audit with no entry is read-only and cannot backfill an admission.
        """
        if self_tests not in ('pass', 'fail', 'unverified', 'not_performed'):
            raise ValueError('invalid_self_tests')
        for values in (skills, perspectives):
            if values is not None:
                if not isinstance(values, list) or len(values) > 32:
                    raise ValueError('bounded_labels_required')
                for v in values: ref(v)
        with locked(self.path.with_suffix('.lock'), self.c.project):
            j = self._read() if self.path.exists() else None
            output = dict(version=1, binding=self.c.binding, workflow_complete=False,
                          entry_observation=j['observation'] if j else 'missing_entry',
                          choice=j['choice'] if j else None,
                          policy_acceptance=j['policy_acceptance'] if j else None,
                          policy_observation=j['policy_observation'] if j else None,
                          native=j['native'] if j else None, execution_mode='solo',
                          executions=[], skills=skills or [], perspectives=perspectives or [],
                          self_tests=self_tests, independent_review='not_performed', reviewed_snapshot=None,
                          omissions=[], verification='coordinator_observed_assertion_not_cryptographic_proof')
            output.update(strict_visual_quality_active=False, visual_quality_coverage='not_activated',
                          frontend_quality_active=False, frontend_prerequisite='unverified',
                          functionality='unverified', visual_quality='unverified', reference_match='unverified',
                          real_device=dict(status='unverified', source_ref=None, detail='No device observation recorded'))
            if j is None or j['observation'] != 'before_controller':
                output['omissions'].append('entry_missing_or_late_no_historical_launch_claim')
            if j and j['choice']['mode'] == 'direct' and j['request']['review_required']:
                output['omissions'].append('explicit_direct_conflicts_with_required_review')
            if not self.c.path.exists():
                output['omissions'].append('no_controller_independent_review')
                return self._report_save(output)
            with locked(self.c.path.with_suffix('.lock'), self.c.project):
                s = self.c._read()
                if j and (s['acceptance'] != j['request']['acceptance'] or s['files'] != j['request']['files']):
                    raise ValueError('entry_controller_scope_mismatch')
                if j and (j['policy_acceptance'] is None or
                          s['policy'] != j['policy_acceptance']['limits'] or
                          s['policy_source']['sha256'] != j['policy_acceptance']['sha256']):
                    raise ValueError('entry_controller_policy_mismatch')
                if s.get('plan') != (j['request'].get('plan') if j else s.get('plan')):
                    raise ValueError('entry_controller_plan_mismatch')
                if s['version'] == 3:
                    return self._report_plan(output, s, j)
                stages = s['stages']
                developers = set()
                valid = True
                for stage in stages:
                    n, result = stage['native'], stage['result']
                    if n is None:
                        valid = False
                        continue
                    if result is None:
                        valid = False
                    self.c._guard(n, stage['role'])
                    output['executions'].append(dict(role=stage['role'], stage_id=stage['id'], native=n))
                    if stage['role'] == 'developer':
                        developers.add(dw.actor(n))
                        if result: output['self_tests'] = result['self_test']
                    elif dw.actor(n) in developers or dw.actor(n) == dw.actor_from_coordinator(self.c.binding['coordinator_actor']):
                        valid = False
                    if result is not None:
                        receipts = [r for r in s['receipts'].values() if r['stage_id'] == stage['id'] and
                                    r['native'] == n and r['result'] == result and
                                    r['observer'] == self.c.binding['coordinator'] and r['terminal'] == 'succeeded']
                        if len(receipts) != 1: valid = False
                        if result['snapshot'] != stage['snapshot']:
                            valid = False
                        if stage['role'] == 'developer' and (result['self_test'] != 'pass' or result['outcome'] != 'pass'):
                            valid = False
                output['execution_mode'] = 'native' if output['executions'] else 'solo'
                last = stages[-1]
                r = last['result']
                passed = (valid and s['phase'] == 'completed' and last['role'] == 'reviewer' and
                          last['native'] is not None and r is not None and r['outcome'] == 'pass' and
                          r['self_test'] in ('pass', 'not_applicable') and
                          r['acceptance'] == {k: 'pass' for k in s['acceptance']} and
                          not any(f['required'] and not f['resolved'] for f in r['findings']) and
                          len(stages) >= 2 and stages[-2]['result'] is not None and
                          stages[-2]['result']['self_test'] == 'pass' and
                          stages[-2]['snapshot'] == last['snapshot'] == r['snapshot'])
                if passed and dw.snapshot(self.c.project, s['files']) != last['snapshot']:
                    output['independent_review'] = 'stale_snapshot'
                elif passed:
                    output['independent_review'] = 'pass'
                    output['reviewed_snapshot'] = last['snapshot']
                else:
                    reviews = [x for x in stages if x['role'] == 'reviewer' and x['native'] is not None]
                    if not reviews:
                        output['independent_review'] = 'not_performed'
                    else:
                        review = reviews[-1]
                        result = review['result']
                        receipts = [x for x in s['receipts'].values() if x['stage_id'] == review['id'] and
                                    x['native'] == review['native'] and x['result'] == result and
                                    x['observer'] == self.c.binding['coordinator']]
                        if result is None:
                            output['independent_review'] = 'pending'
                        elif len(receipts) != 1:
                            output['independent_review'] = 'unverified'
                        elif receipts[0]['terminal'] != 'succeeded' or result['outcome'] == 'fail':
                            output['independent_review'] = 'failed'
                        elif result['outcome'] == 'changes_requested':
                            output['independent_review'] = 'changes_requested'
                        else:
                            output['independent_review'] = 'unverified'
                output['workflow_complete'] = bool(j and j['choice']['mode'] == 'workflow' and
                    not output['omissions'] and output['independent_review'] == 'pass')
                output['functionality'] = output['independent_review']
                return self._report_save(output)

    def _report_plan(self, output, s, j):
        gates = self.c._gates(s)
        output.update(self.c._quality_coverage(s))
        output.update(version=2, plan=copy.deepcopy(s['plan']), plan_hash=s['plan_hash'],
                      gates=gates['statuses'], design_decisions=copy.deepcopy(s['design_decisions']),
                      reused_design=copy.deepcopy(s['plan']['reuse']), self_tests_by_stage=[],
                      design_outcome=gates['statuses'].get('pre_design', 'not_performed'),
                      human_selection=gates['statuses']['design_selection'] if s['plan']['human_selection'] else 'not_performed',
                      visual_review=gates['statuses'].get('visual_review', 'not_performed'),
                      independent_review=gates['statuses'].get('reviewer', 'not_performed'))
        output['omissions'].extend(gates['omissions'])
        valid = not gates['omissions']
        for stage in s['stages']:
            n, r = stage['native'], stage['result']
            if n is None:
                valid = False
                continue
            self.c._guard(n, stage['role'])
            output['executions'].append(dict(role=stage['role'], stage_id=stage['id'], native=n))
            if stage['role'] in ('frontend', 'backend'):
                output['self_tests_by_stage'].append(dict(stage_id=stage['id'], status=r['self_test'] if r else 'pending'))
            if r is None: valid = False
        tests = [x['status'] for x in output['self_tests_by_stage']]
        output['self_tests'] = 'pass' if tests and all(x == 'pass' for x in tests) else 'unverified'
        output['execution_mode'] = 'native' if output['executions'] else 'solo'
        output['workflow_complete'] = bool(j and j['choice']['mode'] == 'workflow' and not output['omissions'] and
            valid and s['phase'] == 'completed' and gates['complete'])
        if output['workflow_complete']: output['reviewed_snapshot'] = s['stages'][-1]['snapshot']
        return self._report_save(output)


def main(argv=None):
    import sys
    return dw.main(sys.argv[1:] if argv is None else argv)


if __name__ == '__main__':
    raise SystemExit(main())
