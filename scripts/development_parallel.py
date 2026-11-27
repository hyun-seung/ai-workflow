"""Opt-in finite work-unit batch around Entry/Controller; never launches a worker.

All units use one logical request, coordinator ledger and cumulative quota.
Declared reads are conservative exclusive guard scopes, not an OS sandbox.
"""
import argparse
import copy
from contextlib import ExitStack
import itertools
import json
import os
from pathlib import Path

import development_workflow as dw
from development_entry import Entry, native_check, ref
from development_plan import sequence, validate_plan
from dispatch_guard import Ledger
from harness_policy import load as harness_policy
from lifecycle_io import digest, locked, private_file, read_json, save
from native_record import identifier


def positive(value):
    if type(value) is not int or not 1 <= value <= 1024:
        raise ValueError('finite_positive_capacity_required')
    return value


def validate_batch_plan(project, value, *, check_files=True):
    value = copy.deepcopy(dw.exact(value, 'version acceptance units'))
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('unsupported_batch_version')
    ac = value['acceptance']
    if not isinstance(ac, dict) or not 1 <= len(ac) <= 64:
        raise ValueError('original_acceptance_required')
    for key, detail in ac.items(): dw.text(key); dw.text(detail)
    units = value['units']
    if not isinstance(units, list) or not 1 <= len(units) <= 4:
        raise ValueError('one_to_four_explicit_units_required')
    ids, covered = set(), set()
    for u in units:
        dw.exact(u, 'id acceptance_ids files reads writes resources depends_on plan')
        identifier(u['id'])
        if u['id'] in ids: raise ValueError('duplicate_unit')
        ids.add(u['id'])
        u['files'] = dw.paths(project, u['files'], check_files=check_files)
        for key in ('reads', 'writes'):
            if not isinstance(u[key], list): raise ValueError('scope_list_required')
            u[key] = dw.paths(project, u[key], check_files=check_files) if u[key] else []
        for key in ('acceptance_ids', 'resources', 'depends_on'):
            items = u[key]
            if (not isinstance(items, list) or len(items) > 64 or
                    any(not isinstance(x, str) for x in items) or len(set(items)) != len(items)):
                raise ValueError('bounded_unique_list_required')
            for x in items: dw.text(x)
            u[key] = sorted(items)
        if not u['acceptance_ids'] or not set(u['acceptance_ids']) <= set(ac):
            raise ValueError('original_unit_acceptance_required')
        covered.update(u['acceptance_ids'])
        if any(not any(Path(f).is_relative_to(Path(p)) for p in u['reads'] + u['writes']) for f in u['files']):
            raise ValueError('snapshot_files_require_declared_scope')
        if u['plan'] is not None:
            validate_plan(u['plan'])
            if u['plan']['pre_design'] != 'none':
                artifact = u['plan']['artifact']
                dw.paths(project, [artifact], check_files=check_files)
                declared = u['writes'] if u['plan']['pre_design'] == 'fresh' else u['reads'] + u['writes']
                if not any(Path(artifact).is_relative_to(Path(p)) for p in declared):
                    raise ValueError('design_artifact_requires_declared_scope')
    if covered != set(ac): raise ValueError('unassigned_original_acceptance')
    done = set()
    while len(done) < len(ids):
        ready = {u['id'] for u in units if u['id'] not in done and set(u['depends_on']) <= done}
        if not ready: raise ValueError('unknown_or_cyclic_dependencies')
        done.update(ready)
    return value


def scopes(project, unit):
    # Guard already enforces directory-prefix exclusion. Synthetic resource paths
    # are lock names only: no file or worktree is created at these paths.
    return sorted(set([str(project / p) for p in unit['reads'] + unit['writes']] +
                      [str(project / '.development-resources' / digest(r)) for r in unit['resources']]))


def conflict(a, b):
    return any(Path(x).is_relative_to(Path(y)) or Path(y).is_relative_to(Path(x)) for x in a for y in b)


def compatible(project, units, limit):
    # At most four units; exact bounded subset selection avoids a greedy false cap.
    for size in range(min(limit, len(units)), 0, -1):
        for subset in itertools.combinations(units, size):
            if all(not conflict(scopes(project, a), scopes(project, b)) for a, b in itertools.combinations(subset, 2)):
                return [u['id'] for u in subset]
    return []


def width(project, units):
    # Enumerate all reachable completed frontiers, not just initial ready nodes.
    best = 1
    for size in range(len(units) + 1):
        for subset in itertools.combinations(units, size):
            done = {u['id'] for u in subset}
            if any(not set(u['depends_on']) <= done for u in subset): continue
            ready = [u for u in units if u['id'] not in done and set(u['depends_on']) <= done]
            best = max(best, len(compatible(project, ready, len(units))))
    return best


def roles(unit):
    return sequence(unit['plan']) if unit['plan'] is not None else ['developer', 'reviewer']


class UnitController(dw.Controller):
    """Legacy state/receipts stay byte-compatible; only batch guard binding differs."""
    def __init__(self, batch, manifest, unit):
        super().__init__(batch.project, batch.binding['request_id'], batch.binding['coordinator'],
                         batch.path.parent / ('unit-' + digest(unit['id'])[:24] + '.json'),
                         batch.binding['coordinator_actor'])
        self.batch, self.manifest, self.unit = batch, manifest, unit

    def _guard_concurrency(self):
        return self.manifest['selection']['max_active_workers']

    def reserve(self, stage_id, intent):
        entry = Entry(self, self.path.with_suffix('.entry.json'))
        if self._read()['stages'][-1]['intent'] is not None:
            # Replay/cancellation retains custody even if predecessor proof is bad.
            with locked(entry.path.with_suffix('.lock'), self.project):
                return super().reserve(stage_id, intent)
        proofs = self.batch._predecessor_proofs(self.manifest, self.unit)
        entries = [entry] + [p['entry'] for p in proofs.values()]
        with ExitStack() as locks:
            # All Entry locks first, then predecessor controllers, each in path
            # order. The existing reserve takes the target controller and ledger
            # locks last. No report or reentrant flock inside this boundary.
            for e in sorted(entries, key=lambda e: str(e.path)):
                locks.enter_context(locked(e.path.with_suffix('.lock'), self.project))
            for e in sorted(entries[1:], key=lambda e: str(e.c.path)):
                locks.enter_context(locked(e.c.path.with_suffix('.lock'), self.project))
            self._admission_proofs = proofs
            self._reserved_guard = None
            try:
                granted = super().reserve(stage_id, intent)
                if granted['launch_permission'] == 'submit_once':
                    granted['guard'] = self._reserved_guard
                return granted
            finally:
                del self._admission_proofs, self._reserved_guard

    def _pre_reserve(self, state, stage):
        self.batch._child_contract(self.manifest, self.unit, state=state)
        super()._pre_reserve(state, stage)

    def _save(self, state):
        if hasattr(self, '_admission_proofs'):
            # Called by the existing reserve while its target Controller AND
            # ledger locks are held, immediately before durable submit-once intent.
            # Compare the exact inputs of Entry.report rather than duplicate its
            # functional/visual/independent-review gates or reacquire its locks.
            states = self.batch._validate_predecessor_proofs(self.manifest, self._admission_proofs)
            self.batch._child_contract(self.manifest, self.unit, state=state)
            actual = dw.policy_snapshot(self.project)
            if actual is None or self.manifest['selection']['policy'] != dict(sha256=actual['sha256'], limits=actual['value']):
                raise ValueError('accepted_policy_changed_before_reservation')
            states[self.unit['id']] = state
            stage = state['stages'][-1]; intent = stage['intent']
            dependencies = self.batch.dependencies(self.manifest, self.unit, intent['guard_operation_id'], states=states)
            self._reserved_guard = self.batch._guard_arguments(self.manifest, self.unit, stage, dependencies)
        super()._save(state)

    def _view(self, state):
        view = super()._view(state)
        view.update(active_concurrency=self._guard_concurrency(), unit_id=self.unit['id'])
        return view

    def _guard(self, execution, role):
        super()._guard(execution, role)
        if execution['guard_directory'] != self.manifest['guard_directory']:
            raise ValueError('batch_shared_ledger_required')
        data = self.batch.ledger(self.manifest).read()
        operation = data['operations'][execution['guard_operation_id']]
        p = operation['plan']
        if (p.get('writes') != scopes(self.project, self.unit) or p.get('worktree') != 'current' or
                p.get('depends_on') != self.batch.dependencies(self.manifest, self.unit, execution['guard_operation_id'])):
            raise ValueError('batch_guard_scope_or_dependency_mismatch')


class Batch:
    def __init__(self, project, request_id, coordinator, state_path, coordinator_actor):
        base = dw.Controller(project, request_id, coordinator, state_path, coordinator_actor)
        identifier(request_id); identifier(coordinator)
        self.project, self.binding, self.path = base.project, base.binding, base.path.absolute()

    def ledger(self, m):
        return Ledger(m['guard_directory'], self.project, self.binding['coordinator'])

    def controller(self, m, unit):
        return UnitController(self, m, unit)

    def entry(self, m, unit):
        c = self.controller(m, unit)
        return Entry(c, c.path.with_suffix('.entry.json'))

    def _read(self):
        private_file(self.path, self.project)
        m = read_json(self.path)
        dw.exact(m, 'version binding selection policy_acceptance concurrency_acceptance choice guard_directory integrity')
        if (m['version'] != 1 or m['binding'] != self.binding or
                m['integrity'] != digest({k: v for k, v in m.items() if k != 'integrity'})):
            raise ValueError('batch_binding_integrity_mismatch')
        # Structural reads/cancellation preserve custody even after product paths
        # become unavailable. New reservations still recheck filesystem scopes.
        validate_batch_plan(self.project, m['selection']['plan'], check_files=False)
        if m['concurrency_acceptance']['selection_sha256'] != digest(m['selection']):
            raise ValueError('accepted_batch_scope_changed')
        self._bind(m, create=False)
        return m

    def _bind(self, m, *, create):
        # Bind a logical request before any child intent, even when no guard call
        # has happened yet. A second state path must not orphan unknown intents.
        ledger = self.ledger(m)
        path = ledger.path.with_name('development-batches.json')
        with ledger.lock():
            private_file(path, self.project)
            index = read_json(path) if path.exists() else dict(version=1, coordinator=self.binding['coordinator'], requests={})
            dw.exact(index, 'version coordinator requests')
            if index['version'] != 1 or index['coordinator'] != self.binding['coordinator']:
                raise ValueError('batch_registry_binding_mismatch')
            expected = dict(binding=self.binding, state=str(self.path), selection_sha256=digest(m['selection']))
            old = index['requests'].get(self.binding['request_id'])
            if old is not None:
                if old != expected: raise ValueError('logical_request_already_bound_to_batch')
                return
            if not create: raise ValueError('batch_registry_missing_no_retroactive_adoption')
            data = ledger.read()
            if self.binding['request_id'] in data['requests'] or any(
                    o['request_id'] == self.binding['request_id'] for o in data['operations'].values()):
                raise ValueError('existing_request_cannot_be_migrated_to_batch')
            index['requests'][self.binding['request_id']] = expected
            save(path, index, self.project)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(directory)
            finally: os.close(directory)

    def propose(self, *, plan: dict, native, max_active_workers, reason, config=None):
        p = validate_batch_plan(self.project, plan)
        positive(max_active_workers); ref(reason)
        dw.exact(native, 'client status capacity source_ref observer')
        available = native_check({k: native[k] for k in ('status', 'source_ref', 'observer')}, self.binding['coordinator'])
        if native['client'] not in ('orca', 'codex', 'paseo'): raise ValueError('unsupported_native_client')
        if available is not True:
            if native['capacity'] is not None: raise ValueError('unconfirmed_capacity')
            return dict(decision='native_check_required' if available is None else 'unavailable')
        if native['capacity'] is None: return dict(decision='native_check_required')
        positive(native['capacity'])
        if native['client'] != 'orca': return dict(decision='unavailable', reason='unsupported_reservation_adapter')
        h = harness_policy(self.project, config)
        cap = min(max_active_workers, native['capacity'], h['limits']['max_active_workers'] or max_active_workers,
                  width(self.project, p['units']))
        policy = dw.policy_snapshot(self.project)
        if policy is None or not policy['value']['enabled']: return dict(decision='opt_in_required')
        if (h['limits']['max_dispatches_per_request'] is not None and
                policy['value']['max_dispatches'] > h['limits']['max_dispatches_per_request']):
            raise ValueError('policy_exceeds_narrower_harness_dispatch_limit')
        if sum(len(roles(u)) for u in p['units']) > policy['value']['max_dispatches']:
            raise ValueError('insufficient_required_review_budget')
        selection = dict(plan=p, native=copy.deepcopy(native), requested_max_active_workers=max_active_workers,
                         max_active_workers=cap, dependency_width=width(self.project, p['units']), reason=reason,
                         decision_maker=h['delegation_decision_maker'], harness_limits=h['limits'],
                         policy=dict(sha256=policy['sha256'], limits=policy['value']))
        ready = [u for u in p['units'] if not u['depends_on']]
        return dict(decision='workflow', selection=selection, selection_sha256=digest(selection),
                    ready=compatible(self.project, ready, cap))

    def enter(self, *, choice, policy_acceptance=None, concurrency_acceptance=None,
              guard_directory=None, **proposal):
        dw.exact(choice, 'mode reason instruction_ref')
        if choice['mode'] == 'direct':
            return dict(decision='direct', workflow_complete=False, review_conflict=True)
        if choice['mode'] != 'workflow': raise ValueError('invalid_choice')
        ref(choice['reason']); ref(choice['instruction_ref'])
        selection = self.propose(**proposal)
        if selection['decision'] != 'workflow': return selection
        selected = selection['selection']
        if policy_acceptance is None or concurrency_acceptance is None:
            if self.path.exists():
                previous = self._read()
                policy_acceptance = policy_acceptance or previous['policy_acceptance']
                concurrency_acceptance = concurrency_acceptance or previous['concurrency_acceptance']
            else:
                return dict(decision='opt_in_required', selection=selected, selection_sha256=digest(selected))
        dw.exact(policy_acceptance, 'ref project sha256 limits'); ref(policy_acceptance['ref'])
        if (policy_acceptance['project'] != str(self.project) or
                {k: policy_acceptance[k] for k in ('sha256', 'limits')} != selected['policy']):
            raise ValueError('policy_acceptance_scope_changed_reconfirm_explicitly')
        dw.exact(concurrency_acceptance, 'ref selection_sha256'); ref(concurrency_acceptance['ref'])
        if concurrency_acceptance['selection_sha256'] != digest(selected):
            raise ValueError('accepted_batch_scope_changed')
        if not isinstance(guard_directory, str) or guard_directory != str(Path(guard_directory).absolute()):
            raise ValueError('canonical_guard_directory_required')
        candidate = dw.policy_snapshot(self.project)
        with locked(self.path.with_suffix('.lock'), self.project):
            m = dict(version=1, binding=self.binding, selection=selected, policy_acceptance=policy_acceptance,
                     concurrency_acceptance=concurrency_acceptance, choice=choice, guard_directory=guard_directory)
            m['integrity'] = digest(m)
            if self.path.exists():
                if self._read() != m: raise ValueError('accepted_batch_scope_changed')
            else:
                actual = dw.policy_snapshot(self.project)
                if candidate != actual: raise ValueError('policy_changed_during_admission')
                if actual is None or selected['policy'] != dict(sha256=actual['sha256'], limits=actual['value']):
                    raise ValueError('accepted_policy_changed_before_admission')
                for u in selected['plan']['units']:
                    e = self.entry(m, u)
                    if e.path.exists() or e.c.path.exists(): raise ValueError('existing_unit_cannot_be_adopted')
                self._bind(m, create=True)
                dw.Controller._save(self, m)  # Existing fsync/atomic private storage, before any child admission.
            # Partial admission crash is resumable; never re-creates an existing stage.
            for u in selected['plan']['units']:
                e = self.entry(m, u)
                if e.path.exists():
                    self._child_contract(m, u, journal_only=not e.c.path.exists())
                elif e.c.path.exists():
                    raise ValueError('batch_child_entry_missing:' + u['id'])
                e.enter(kind='development', choice=choice,
                        native={k: selected['native'][k] for k in ('status', 'source_ref', 'observer')},
                        acceptance={k: selected['plan']['acceptance'][k] for k in u['acceptance_ids']},
                        files=u['files'], plan=u['plan'], policy_acceptance=policy_acceptance)
            return self._view(m)

    def states(self, m):
        return {u['id']: self.controller(m, u)._read() for u in m['selection']['plan']['units']}

    def _child_contract(self, m, unit, *, state=None, journal_only=False):
        """Compare recovered evidence with the original admission, without repair.

        Kept separate from structural reads/attach/cancel so invalid contract
        proof never discards an existing intent or native execution's custody.
        """
        e = self.entry(m, unit)
        s = None if journal_only else e.c._read() if state is None else state
        j = e._read()  # Also validates expected binding, controller path and integrity.
        acceptance = {k: m['selection']['plan']['acceptance'][k] for k in unit['acceptance_ids']}
        request = dict(kind='development', acceptance=acceptance, files=unit['files'], review_required=True)
        if unit['plan'] is not None: request['plan'] = unit['plan']
        source = dict(path=str(self.project / dw.POLICY), version=dw.POLICY_VERSION,
                      sha256=m['policy_acceptance']['sha256'])
        expected_state = dict(version=3 if unit['plan'] is not None else 2, binding=self.binding,
                              acceptance=acceptance, files=unit['files'], plan=unit['plan'],
                              policy=m['policy_acceptance']['limits'], policy_source=source)
        expected_entry = dict(binding=self.binding, controller=str(e.c.path.absolute()), request=request,
                              choice=m['choice'], policy_acceptance=m['policy_acceptance'],
                              native={k: m['selection']['native'][k] for k in ('status', 'source_ref', 'observer')},
                              policy_observation=dict(sha256=source['sha256'], value=m['policy_acceptance']['limits']),
                              observation='before_controller')
        checks = [('entry', j, expected_entry)]
        if not journal_only: checks.append(('controller', s, expected_state))
        for label, actual, expected in checks:
            for key, value in expected.items():
                if actual.get(key) != value:
                    raise ValueError('batch_child_contract_mismatch:' + unit['id'] + ':' + label + '.' + key)

    def _predecessor_proofs(self, m, unit):
        """Read Entry's existing completion proof, then pin its exact inputs.

        Reporting owns its own Entry/controller locks. Optimistic collection is
        only evidence gathering: all inputs are compared again under the entire
        predecessor lock set and the target's reservation ledger lock before save.
        """
        required = set(unit['depends_on'])
        pending = list(required)
        while pending:
            for key in self._unit(m, pending.pop())['depends_on']:
                if key not in required: required.add(key); pending.append(key)
        proofs = {}
        for key in sorted(required):
            u = self._unit(m, key); e = self.entry(m, u)
            state, journal = e.c._read(), e._read()
            self._child_contract(m, u, state=state)
            with self.ledger(m).lock():
                operations = self._released_operations(m, state, self.ledger(m).read())
            report = e.report()
            if not report['workflow_complete']:
                raise ValueError('batch_predecessor_review_incomplete:' + key)
            proofs[key] = dict(entry=e, unit=u, state=state, journal=journal,
                               operations=operations, reviewed_snapshot=report['reviewed_snapshot'])
        return proofs

    def _released_operations(self, m, state, data):
        operations = {}
        if state['phase'] != 'completed': raise ValueError('batch_predecessor_not_completed')
        for stage in state['stages']:
            intent, native = stage['intent'], stage['native']
            if intent is None or native is None or intent != dw.intent_for(native):
                raise ValueError('batch_predecessor_final_intent_required')
            op = intent['guard_operation_id']; row = data['operations'].get(op)
            if (intent['guard_directory'] != m['guard_directory'] or row is None or
                    row['request_id'] != self.binding['request_id'] or row['state'] != 'released' or
                    row.get('outcome') != 'succeeded' or row.get('task_id') != native['task_id'] or
                    row.get('dispatch_id') != native['dispatch_id']):
                raise ValueError('batch_predecessor_not_successfully_released')
            if op in operations: raise ValueError('batch_operation_already_owned')
            operations[op] = copy.deepcopy(row)
        return operations

    def _validate_predecessor_proofs(self, m, proofs):
        # Caller owns all relevant Entry/controller locks and the shared ledger.
        data = self.ledger(m).read()
        states = {}
        for key, proof in proofs.items():
            e = proof['entry']; state, journal = e.c._read(), e._read()
            self._child_contract(m, proof['unit'], state=state)
            if state != proof['state'] or journal != proof['journal']:
                raise ValueError('batch_predecessor_changed_during_reservation:' + key)
            if (dw.snapshot(self.project, state['files']) != proof['reviewed_snapshot'] or
                    (state['version'] == 3 and not e.c._gates(state)['complete'])):
                raise ValueError('batch_predecessor_review_stale:' + key)
            if self._released_operations(m, state, data) != proof['operations']:
                raise ValueError('batch_predecessor_operations_changed:' + key)
            states[key] = state
        return states

    def dependencies(self, m, unit, operation_id=None, *, states=None):
        if states is None: states = self.states(m)
        deps = []
        for key in unit['depends_on']:
            intent = states[key]['stages'][-1]['intent']
            if intent is None: raise ValueError('batch_predecessor_final_intent_required:' + key)
            deps.append(intent['guard_operation_id'])
        for stage in states[unit['id']]['stages']:
            if stage['intent']:
                op = stage['intent']['guard_operation_id']
                if op == operation_id: break
                deps.append(op)
        return sorted(set(deps))

    def _guard_arguments(self, m, unit, stage, dependencies):
        intent = stage['intent']
        return dict(request_id=self.binding['request_id'], operation_id=intent['guard_operation_id'],
            directory=m['guard_directory'], project=str(self.project), worktree='current',
            max_active_workers=m['selection']['max_active_workers'],
            max_dispatches_per_request=m['policy_acceptance']['limits']['max_dispatches'],
            writes=scopes(self.project, unit), depends_on=dependencies,
            usage_actor_id=intent['usage_actor_id'], usage_role={'developer': 'backend', 'reviewer': 'verifier',
                'backend': 'backend', 'frontend': 'frontend', 'pre_design': 'designer', 'visual_review': 'designer'}[stage['role']])

    def _status(self, m):
        states, state_errors = {}, {}
        for unit in m['selection']['plan']['units']:
            try: states[unit['id']] = self.controller(m, unit)._read()
            except (ValueError, OSError) as error: state_errors[unit['id']] = str(error)
        data = self.ledger(m).read()
        limits = dict(max_active_workers=m['selection']['max_active_workers'],
                      max_dispatches_per_request=m['policy_acceptance']['limits']['max_dispatches'])
        known = data['requests'].get(self.binding['request_id'])
        if known is not None and known != limits: raise ValueError('batch_guard_limits_changed')
        reservations, occupied = {}, set()
        for uid, s in states.items():
            for stage in s['stages']:
                if stage['intent'] is None: continue
                op = stage['intent']['guard_operation_id']
                if op in reservations: raise ValueError('batch_operation_already_owned')
                reservations[op] = uid
                # A receipt/completion alone never frees a live/unknown reservation.
                if data['operations'].get(op, {}).get('state') != 'released': occupied.add(op)
        for op, row in data['operations'].items():
            if row['state'] != 'released': occupied.add(op)
        used = set(reservations) | {op for op, row in data['operations'].items() if row['request_id'] == self.binding['request_id']}
        return states, data, reservations, occupied, used, limits, state_errors

    def _view(self, m):
        states, data, reserved, occupied, used, limits, state_errors = self._status(m)
        reports, contract_errors, views = {}, dict(state_errors), {}
        for u in m['selection']['plan']['units']:
            try:
                self._child_contract(m, u, state=states.get(u['id']))
            except (ValueError, OSError) as error:
                contract_errors[u['id']] = str(error)
            try:
                if u['id'] in contract_errors: raise ValueError(contract_errors[u['id']])
                reports[u['id']] = self.entry(m, u).report()
                try:
                    self._child_contract(m, u)  # Do not accept a changed recovery during reporting.
                except (ValueError, OSError) as error:
                    contract_errors[u['id']] = str(error)
                    raise
            except (ValueError, OSError) as error:
                # Unknown/mismatched native evidence must block completion without
                # hiding the occupied reservation or authorizing another launch.
                reports[u['id']] = dict(workflow_complete=False, independent_review='unverified',
                                        omissions=['unit_report_unverified'], error=str(error))
            try:
                views[u['id']] = self.controller(m, u).next()
            except (ValueError, OSError) as error:
                views[u['id']] = dict(phase='unverified', launch_permission='none', error=str(error),
                                      native_action='preserve evidence; reconcile original controller and ledger')
            if u['id'] in contract_errors:
                views[u['id']]['contract_error'] = contract_errors[u['id']]
        done = {uid for uid, r in reports.items() if r['workflow_complete'] and
                all(data['operations'].get(op, {}).get('state') == 'released' and
                    data['operations'][op].get('outcome') == 'succeeded'
                    for op, owner in reserved.items() if owner == uid)}
        active_scopes = [row['plan'].get('writes', []) for row in data['operations'].values() if row['state'] != 'released']
        active_scopes += [scopes(self.project, u) for u in m['selection']['plan']['units']
                          if any(op in occupied for op, owner in reserved.items() if owner == u['id'])]
        cap = min([limits['max_active_workers']] + [data['requests'][o['request_id']]['max_active_workers']
                  for o in data['operations'].values() if o['state'] != 'released'])
        candidates, pending = [], 0
        for u in m['selection']['plan']['units']:
            if u['id'] in contract_errors: continue
            s = states[u['id']]; stage = s['stages'][-1]
            if s['phase'] in ('ready', 'running'):
                seq = roles(u)
                pending += len(seq) - seq.index(stage['role']) - (stage['intent'] is not None)
            if (s['phase'] == 'ready' and stage['intent'] is None and set(u['depends_on']) <= done and
                    not any(op in occupied for op, owner in reserved.items() if owner == u['id']) and
                    not any(conflict(scopes(self.project, u), other) for other in active_scopes)):
                candidates.append(u)
        budget_ok = None if contract_errors else len(used) + pending <= limits['max_dispatches_per_request']
        ready = compatible(self.project, candidates, max(0, cap - len(occupied))) if budget_ok else []
        # All requests constrain scheduling. Only this request's reservations and
        # ledger rows constrain its completion; unowned same-request rows count.
        request_occupied = occupied & used
        return dict(version=1, binding=self.binding, max_active_workers=limits['max_active_workers'],
                    occupied_count=len(occupied), request_occupied_count=len(request_occupied),
                    request_operations={op: copy.deepcopy(row) for op, row in data['operations'].items()
                                        if row['request_id'] == self.binding['request_id']},
                    unverified_custody=state_errors, contract_errors=contract_errors,
                    reserved_dispatches=len(used), remaining_required_dispatches=None if contract_errors else pending,
                    ready=ready, ready_count=len(ready), budget_sufficient=budget_ok, launch_permission='none',
                    units=views,
                    unit_reports=reports, verification='coordinator_observed_assertion_not_cryptographic_proof')

    def next(self):
        with locked(self.path.with_suffix('.lock'), self.project): return self._view(self._read())

    def _unit(self, m, unit_id):
        for u in m['selection']['plan']['units']:
            if u['id'] == unit_id: return u
        raise ValueError('unknown_unit')

    def reserve(self, *, unit_id):
        with locked(self.path.with_suffix('.lock'), self.project):
            m = self._read(); unit = self._unit(m, unit_id); c = self.controller(m, unit)
            stage = c._read()['stages'][-1]
            if stage['intent'] is not None: return c.next()  # No second submit permission.
            validate_batch_plan(self.project, m['selection']['plan'])
            view = self._view(m)
            if unit_id not in view['ready']: raise ValueError('unit_not_ready_or_capacity_or_review_budget')
            # Recheck exact accepted policy at each new reservation; resume/unknown is read-only.
            actual = dw.policy_snapshot(self.project)
            if actual is None or m['selection']['policy'] != dict(sha256=actual['sha256'], limits=actual['value']):
                raise ValueError('accepted_policy_changed_before_reservation')
            token = digest([self.binding, unit_id, stage['id']])
            intent = dict(client='orca', guard_directory=m['guard_directory'],
                          guard_operation_id='batch-' + token, usage_actor_id='actor-' + token)
            # Controller pins prerequisite proof through intent save and constructs
            # exact guard arguments from it; never reread dependencies after grant.
            return c.reserve(stage['id'], intent)

    def attach(self, *, unit_id, stage_id, execution):
        with locked(self.path.with_suffix('.lock'), self.project):
            m = self._read(); unit = self._unit(m, unit_id); states = self.states(m)
            role = states[unit_id]['stages'][-1]['role']
            # Independence is request-wide, including authors of other units.
            review = role in ('reviewer', 'visual_review')
            for state in states.values():
                for stage in state['stages']:
                    other = stage['native']
                    if (other and (stage['role'] in ('reviewer', 'visual_review')) != review and
                            dw.actor(other) == dw.actor(execution)):
                        raise ValueError('independent_provider_session_required_across_batch')
            return self.controller(m, unit).attach(stage_id, execution)

    def complete(self, *, unit_id, receipt, capture_manifest=None):
        with locked(self.path.with_suffix('.lock'), self.project):
            m = self._read(); c = self.controller(m, self._unit(m, unit_id))
            return c.complete(receipt) if capture_manifest is None else c.checked_complete(receipt, capture_manifest=capture_manifest)

    def select_design(self, *, unit_id, decision):
        with locked(self.path.with_suffix('.lock'), self.project):
            m = self._read()
            return self.controller(m, self._unit(m, unit_id)).select_design(decision)

    def cancel(self):
        with locked(self.path.with_suffix('.lock'), self.project):
            m = self._read()
            result = {}
            for u in m['selection']['plan']['units']:
                try: result[u['id']] = self.controller(m, u).cancel()
                except (ValueError, OSError) as error:
                    result[u['id']] = dict(phase='unverified', error=str(error), launch_permission='none',
                                           native_action='preserve evidence; reconcile original controller and ledger')
            return result

    def report(self):
        with locked(self.path.with_suffix('.lock'), self.project):
            m = self._read(); view = self._view(m)
            states, data, reserved, _, _, _, _ = self._status(m)
            authors, reviewers = set(), set()
            for s in states.values():
                for stage in s['stages']:
                    if stage['native']:
                        (reviewers if stage['role'] in ('reviewer', 'visual_review') else authors).add(dw.actor(stage['native']))
            omissions = []
            if authors & reviewers: omissions.append('batch_independence_collision')
            if view['request_occupied_count']: omissions.append('unreleased_or_unknown_operations')
            for op, row in data['operations'].items():
                if row['request_id'] == self.binding['request_id']:
                    if op not in reserved: omissions.append('unowned_request_operation:' + op)
                    if row['state'] == 'released' and row.get('outcome') != 'succeeded':
                        omissions.append('unsuccessful_release:' + op)
            for uid, report in view['unit_reports'].items():
                if not report['workflow_complete']: omissions.append('required_unit_incomplete:' + uid)
            view.update(workflow_complete=not omissions, omissions=omissions, selection=m['selection'],
                        concurrency_acceptance=m['concurrency_acceptance'])
            # Unit completion cannot be mistaken for whole-request completion.
            destination = self.path.with_suffix('.report.json')
            if destination == self.path: raise ValueError('batch_report_path_conflict')
            save(destination, view, self.project)
            return view


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('propose', 'enter', 'next', 'reserve', 'attach', 'complete', 'select_design', 'cancel', 'report'))
    for key in ('project', 'request-id', 'coordinator', 'coordinator-actor', 'state'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--input')
    args = parser.parse_args(argv)
    try:
        b = Batch(args.project, args.request_id, args.coordinator, args.state, args.coordinator_actor)
        output = getattr(b, args.command)(**(read_json(args.input) if args.input else {}))
        print(json.dumps(output, ensure_ascii=False)); return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)})); return 2


if __name__ == '__main__':
    raise SystemExit(main())
