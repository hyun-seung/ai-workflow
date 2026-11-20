"""Bounded durable instructions for the current AI; never launches a process/model."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from lifecycle_io import digest, locked, private_file, read_json, save
from native_record import identifier, unique_object, reject_constant

VERSION = 2
POLICY_VERSION = 1
POLICY = '.ai-development-policy.json'
ROOT = Path(__file__).resolve().parents[1]
MAX_FILES, MAX_BYTES = 32, 2 * 1024 * 1024


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError('invalid_schema')
    return value


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError('nonempty_bounded_text_required')
    return value


def policy(value):
    exact(value, 'version enabled max_dispatches max_fix_rounds')
    if (type(value['version']) is not int or value['version'] != POLICY_VERSION or
            type(value['enabled']) is not bool or type(value['max_dispatches']) is not int or
            not 2 <= value['max_dispatches'] <= 8 or type(value['max_fix_rounds']) is not int or
            not 0 <= value['max_fix_rounds'] <= 3):
        raise ValueError('invalid_policy')
    return value


def direct_request(kind, direct, native_available):
    if kind not in ('conversation', 'small_change', 'development'):
        raise ValueError('semantic_kind_required')
    if type(direct) is not bool or (native_available is not None and type(native_available) is not bool):
        raise ValueError('boolean_required')
    return direct or kind != 'development'


def policy_snapshot(project):
    """Read one no-follow policy snapshot plus identity for admission recheck."""
    project = Path(project).absolute()
    if any(p.is_symlink() for p in (project, *project.parents)):
        raise ValueError('policy_project_symlink')
    directory = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fd = os.open(POLICY, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError('policy_not_regular')
            raw = stream.read(65537)
            after = os.fstat(stream.fileno())
        current = os.stat(POLICY, dir_fd=directory, follow_symlinks=False)
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if (identity(before) != identity(after) or identity(after) != identity(current) or
                not stat.S_ISREG(current.st_mode) or len(raw) > 65536):
            raise ValueError('policy_changed_during_read')
        value = policy(json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant))
        return dict(value=value, identity=identity(current), sha256=hashlib.sha256(raw).hexdigest())
    finally:
        os.close(directory)


def decision(project, kind, direct=False, native_available=None):
    if direct_request(kind, direct, native_available):
        return {'decision': 'direct'}
    candidate = policy_snapshot(project)
    if candidate is None or not candidate['value']['enabled']:
        return {'decision': 'opt_in_required'}
    return {'decision': ('native_check_required' if native_available is None else
                         'workflow' if native_available else 'unavailable'),
            'native_action': 'confirm official tools and reserve pre-launch intent; preserve native defaults'}


def paths(project, files, check_files=True):
    if (not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES or
            any(not isinstance(p, str) for p in files) or len(set(files)) != len(files)):
        raise ValueError('small_explicit_file_list_required')
    for name in files:
        p = Path(name)
        if (p.is_absolute() or str(p) != name or any(x in ('..', '.') or x.startswith('.') or
                re.search(r'(secret|credential|token|password|private[_-]?key)', x, re.I) or
                x.lower().endswith(('.pem', '.key', '.p12')) for x in p.parts)):
            raise ValueError('unsafe_product_path')
        target = project / p
        if check_files and (any(q.is_symlink() for q in (target, *target.parents)) or not target.resolve().is_relative_to(project)):
            raise ValueError('unsafe_product_path')
    return sorted(files)


def snapshot(project, files):
    rows, total = [], 0
    for name in paths(project, files):
        # O_NOFOLLOW and regular-file checks also reject a late leaf symlink/FIFO.
        directory = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = Path(name).parts
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('product_not_regular')
                before = os.fstat(stream.fileno())
                raw = stream.read(MAX_BYTES + 1)
                after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError('product_changed_during_snapshot')
        finally:
            os.close(directory)
        total += len(raw)
        if total > MAX_BYTES:
            raise ValueError('product_snapshot_too_large')
        rows.append({'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    return digest(rows)


def native(value):
    exact(value, 'client handle provider provider_session_id task_id dispatch_id guard_directory guard_operation_id usage_actor_id')
    if value['client'] not in ('orca', 'paseo', 'codex'):
        raise ValueError('unsupported_native_client')
    for k in ('handle', 'provider', 'provider_session_id'):
        text(value[k])
    if value['client'] == 'orca':
        for k in ('task_id', 'dispatch_id', 'guard_directory', 'guard_operation_id', 'usage_actor_id'):
            text(value[k])
    elif any(value[k] is not None for k in ('task_id', 'dispatch_id', 'guard_directory', 'guard_operation_id', 'usage_actor_id')):
        raise ValueError('foreign_client_identity')
    return value


def validate_intent(value):
    exact(value, 'client guard_directory guard_operation_id usage_actor_id')
    if value['client'] not in ('orca', 'paseo', 'codex'):
        raise ValueError('unsupported_native_client')
    if value['client'] == 'orca':
        for key in ('guard_directory', 'guard_operation_id', 'usage_actor_id'):
            text(value[key])
        identifier(value['guard_operation_id']); identifier(value['usage_actor_id'])
        if str(Path(value['guard_directory']).absolute()) != value['guard_directory']:
            raise ValueError('canonical_guard_directory_required')
    elif any(value[k] is not None for k in ('guard_directory', 'guard_operation_id', 'usage_actor_id')):
        raise ValueError('foreign_client_identity')
    return value


def intent_for(execution):
    return {k: execution[k] for k in ('client', 'guard_directory', 'guard_operation_id', 'usage_actor_id')}


def actor(value):
    # Host app and handle never confer independence from the physical provider session.
    return value['provider'], value['provider_session_id']


class LegacyController:
    def __init__(self, project, request_id, coordinator, state_path, coordinator_actor):
        supplied = Path(project).absolute()
        if any(p.is_symlink() for p in (supplied, *supplied.parents)):
            raise ValueError('project_symlink')
        self.project = supplied.resolve(strict=True)
        if not self.project.is_dir():
            raise ValueError('project_not_directory')
        actor_from_coordinator(text(coordinator_actor))
        self.binding = dict(project=str(self.project), request_id=text(request_id), coordinator=text(coordinator), coordinator_actor=coordinator_actor)
        self.path = Path(state_path)

    def _read(self):
        private_file(self.path, self.project)
        s = read_json(self.path)
        if not isinstance(s, dict) or s.get('version') != VERSION:
            raise ValueError('unsupported_workflow_schema_preserve_original_no_migration')
        exact(s, 'version binding policy policy_source acceptance files phase round stages receipts integrity')
        if type(s['version']) is not int or s['version'] != VERSION or s['binding'] != self.binding:
            raise ValueError('workflow_binding_mismatch')
        if s['integrity'] != digest({k: v for k, v in s.items() if k != 'integrity'}):
            raise ValueError('state_integrity_mismatch')
        policy(s['policy'])
        exact(s['policy_source'], 'path version sha256')
        if (s['policy_source']['path'] != str(self.project / POLICY) or
                s['policy_source']['version'] != POLICY_VERSION or not s['policy']['enabled'] or
                not re.fullmatch('[a-f0-9]{64}', s['policy_source']['sha256'])):
            raise ValueError('invalid_policy_admission')
        exact(s['binding'], 'project request_id coordinator coordinator_actor')
        if (not isinstance(s['acceptance'], dict) or not 1 <= len(s['acceptance']) <= 64 or
                type(s['round']) is not int or not 0 <= s['round'] <= s['policy']['max_fix_rounds'] or
                s['phase'] not in ('ready', 'running', 'cancel_pending', 'cancelled', 'failed', 'completed', 'exhausted') or
                not isinstance(s['stages'], list) or not 1 <= len(s['stages']) <= s['policy']['max_dispatches'] or
                not isinstance(s['receipts'], dict)):
            raise ValueError('invalid_state_schema')
        for k, v in s['acceptance'].items():
            text(k); text(v)
        paths(self.project, s['files'], check_files=False)
        ids = set()
        for i, stage in enumerate(s['stages']):
            exact(stage, 'id role attempt intent native snapshot result')
            if (stage['id'] in ids or not re.fullmatch('[a-f0-9]{32}', stage['id']) or
                    type(stage['attempt']) is not int or stage['attempt'] != i + 1 or
                    stage['role'] != ('developer' if i % 2 == 0 else 'reviewer')):
                raise ValueError('invalid_stage_schema')
            ids.add(stage['id'])
            if stage['intent'] is not None:
                validate_intent(stage['intent'])
                if stage['intent']['client'] != 'orca':
                    raise ValueError('unsupported_stored_execution_client')
            if stage['native'] is not None:
                native(stage['native'])
                if stage['native']['client'] != 'orca':
                    raise ValueError('unsupported_stored_execution_client')
                if stage['intent'] != intent_for(stage['native']):
                    raise ValueError('execution_intent_mismatch')
            if stage['snapshot'] is not None and not re.fullmatch('[a-f0-9]{64}', stage['snapshot']):
                raise ValueError('invalid_snapshot_schema')
            if stage['result'] is not None:
                exact(stage['result'], 'outcome self_test snapshot acceptance findings')
        for key, receipt in s['receipts'].items():
            exact(receipt, 'id binding stage_id native observer source_tool terminal result')
            native(receipt['native'])
            if receipt['native']['client'] != 'orca':
                raise ValueError('unsupported_stored_execution_client')
            if receipt['id'] != key or receipt['binding'] != self.binding or receipt['stage_id'] not in ids:
                raise ValueError('invalid_receipt_schema')
        return s

    def _save(self, s):
        s['integrity'] = digest({k: v for k, v in s.items() if k != 'integrity'})
        save(self.path, s, self.project)
        directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)  # Persist rename before returning any submit-once permission.
        finally:
            os.close(directory)

    def _stage(self, s, role):
        if len(s['stages']) >= s['policy']['max_dispatches']:
            s['phase'] = 'exhausted'
            return
        reviewed = s['stages'][-1]['snapshot'] if role == 'reviewer' else None
        s['stages'].append(dict(id=uuid.uuid4().hex, role=role, attempt=len(s['stages']) + 1,
                                intent=None, native=None, snapshot=reviewed, result=None))
        s['phase'] = 'ready'

    def begin(self, *, kind, direct, native_available, acceptance, files, expected_policy=None):
        if direct_request(kind, direct, native_available):
            return {'decision': 'direct'}
        if (not isinstance(acceptance, dict) or not 1 <= len(acceptance) <= 64):
            raise ValueError('original_acceptance_required')
        for k, v in acceptance.items():
            text(k); text(v)
        files = paths(self.project, files)
        if expected_policy is not None:
            expected_policy = copy.deepcopy(exact(expected_policy, 'sha256 limits'))
            policy(expected_policy['limits'])
            if (not isinstance(expected_policy['sha256'], str) or
                    not re.fullmatch('[a-f0-9]{64}', expected_policy['sha256'])):
                raise ValueError('invalid_expected_policy')
        # Existing request resumes its frozen admitted policy, including after revocation.
        # This does not grant a new launch; pending operations still need reconciliation.
        candidate = None if self.path.exists() else policy_snapshot(self.project)
        if not self.path.exists():
            if candidate is None or not candidate['value']['enabled']:
                return {'decision': 'opt_in_required'}
            if native_available is None:
                return {'decision': 'native_check_required'}
            if not native_available:
                return {'decision': 'unavailable'}
        with locked(self.path.with_suffix('.lock'), self.project):
            if self.path.exists():
                s = self._read()
                if (s['acceptance'], s['files']) != (acceptance, files):
                    raise ValueError('begin_conflict')
                if expected_policy is not None and (s['policy'] != expected_policy['limits'] or
                        s['policy_source']['sha256'] != expected_policy['sha256']):
                    raise ValueError('accepted_policy_changed_before_admission')
            else:
                # Re-open the actual project policy under admission lock: content,
                # version, inode and symlink safety must still match the admitted snapshot.
                actual = policy_snapshot(self.project)
                if actual is None or not actual['value']['enabled']:
                    return {'decision': 'opt_in_required'}
                if expected_policy is not None and (actual['value'] != expected_policy['limits'] or
                        actual['sha256'] != expected_policy['sha256']):
                    raise ValueError('accepted_policy_changed_before_admission')
                if candidate != actual:
                    raise ValueError('policy_changed_during_admission')
                s = dict(version=VERSION, binding=self.binding, policy=actual['value'],
                         policy_source=dict(path=str(self.project / POLICY), version=POLICY_VERSION,
                                            sha256=actual['sha256']), acceptance=acceptance,
                         files=files, phase='ready', round=0, stages=[], receipts={}, integrity=None)
                self._stage(s, 'developer')
                self._save(s)
            return self._view(s)

    def reserve(self, stage_id, intent):
        intent = copy.deepcopy(validate_intent(intent))
        with locked(self.path.with_suffix('.lock'), self.project):
            s = self._read(); stage = s['stages'][-1]
            if stage['id'] != stage_id or s['phase'] not in ('ready', 'running', 'cancel_pending'):
                raise ValueError('stale_stage')
            if stage['intent'] is not None:
                if stage['intent'] != intent:
                    raise ValueError('conflicting_launch_reservation')
                return self._view(s)  # Never reissue submit-once, including after restart.
            if s['phase'] != 'ready':
                raise ValueError('cannot_reserve_cancelled_stage')
            if hasattr(self, '_pre_reserve'):
                self._pre_reserve(s, stage)
            if intent['client'] != 'orca':
                view = self._view(s)
                view.update(phase='unavailable', native_action='no supported official idempotency/reconciliation adapter for this client')
                return view
            if any(x['intent'] and (x['intent']['guard_directory'], x['intent']['guard_operation_id']) ==
                   (intent['guard_directory'], intent['guard_operation_id']) for x in s['stages']):
                raise ValueError('guard_operation_already_owned')
            from dispatch_guard import Ledger
            ledger = Ledger(intent['guard_directory'], self.project, self.binding['coordinator'])
            with ledger.lock():
                if intent['guard_operation_id'] in ledger.read()['operations']:
                    raise ValueError('preexisting_operation_cannot_be_retroactively_reserved')
                if stage['role'] == 'reviewer' and snapshot(self.project, s['files']) != stage['snapshot']:
                    raise ValueError('code_changed_before_review')
                stage['intent'] = intent
                self._save(s)  # Durable BEFORE permission to invoke dispatch_guard is returned.
            view = self._view(s)
            view.update(launch_permission='submit_once', native_action='submit exact reserved dispatch_guard operation once; on any uncertainty reconcile same operation')
            return view

    def _guard(self, n, role):
        if n['client'] != 'orca':
            raise ValueError('unsupported_actual_execution_client')
        from dispatch_guard import Ledger
        ledger = Ledger(n['guard_directory'], self.project, self.binding['coordinator'])
        with ledger.lock():
            data = ledger.read()
            op = data['operations'].get(n['guard_operation_id'], {})
            plan = op.get('plan', {})
            usage = plan.get('usage_collection', {})
            limits = data['requests'].get(self.binding['request_id'], {})
            if (op.get('state') not in ('bound', 'released') or op.get('request_id') != self.binding['request_id'] or
                    op.get('dispatch_id') != n['dispatch_id'] or op.get('task_id') != n['task_id'] or
                    plan.get('project') != str(self.project) or plan.get('model') is not None or
                    plan.get('effort') is not None or usage.get('actor_id') != n['usage_actor_id'] or
                    usage.get('role') != ({'developer': 'backend', 'reviewer': 'verifier', 'frontend': 'frontend', 'backend': 'backend', 'pre_design': 'designer', 'visual_review': 'designer'}[role]) or
                    not usage.get('task_title') or limits.get('max_active_workers') != 1 or
                    limits.get('max_dispatches_per_request') != self._read()['policy']['max_dispatches']):
                raise ValueError('orca_registered_guard_usage_binding_required')

    def attach(self, stage_id, execution):
        execution = copy.deepcopy(native(execution))
        with locked(self.path.with_suffix('.lock'), self.project):
            s = self._read(); stage = s['stages'][-1]
            if stage['id'] != stage_id or s['phase'] not in ('ready', 'running', 'cancel_pending'):
                raise ValueError('stale_stage')
            if stage['intent'] is None or stage['intent'] != intent_for(execution):
                raise ValueError('matching_prelaunch_intent_required')
            if stage['native'] is not None:
                if stage['native'] != execution:
                    raise ValueError('worker_binding_conflict')
                return self._view(s)
            if stage['role'] == 'reviewer' and (actor(execution) == actor_from_coordinator(self.binding['coordinator_actor']) or
                    any(x['native'] and actor(x['native']) == actor(execution)
                        for x in s['stages'] if x['role'] == 'developer')):
                raise ValueError('independent_provider_session_required')
            self._guard(execution, stage['role'])
            stage['native'] = execution
            if stage['role'] == 'reviewer':
                stage['snapshot'] = snapshot(self.project, s['files'])
                if stage['snapshot'] != s['stages'][-2]['snapshot']:
                    raise ValueError('code_changed_before_review')
            if s['phase'] != 'cancel_pending':
                s['phase'] = 'running'
            self._save(s)
            return self._view(s)

    def complete(self, receipt):
        receipt = copy.deepcopy(receipt)
        exact(receipt, 'id binding stage_id native observer source_tool terminal result')
        text(receipt['id']); text(receipt['source_tool']); native(receipt['native'])
        if receipt['binding'] != self.binding or receipt['observer'] != self.binding['coordinator']:
            raise ValueError('receipt_binding_mismatch')
        if receipt['terminal'] not in ('succeeded', 'failed', 'cancelled', 'stopped'):
            raise ValueError('coordinator_observed_terminal_required')
        with locked(self.path.with_suffix('.lock'), self.project):
            s = self._read()
            old = s['receipts'].get(receipt['id'])
            if old is not None:
                if old != receipt:
                    raise ValueError('conflicting_receipt_replay')
                return self._view(s)
            stage = s['stages'][-1]
            if (s['phase'] not in ('running', 'cancel_pending') or stage['id'] != receipt['stage_id'] or
                    stage['native'] != receipt['native']):
                raise ValueError('stale_or_wrong_worker')
            result = exact(receipt['result'], 'outcome self_test snapshot acceptance findings')
            if result['outcome'] not in ('pass', 'changes_requested', 'fail'):
                raise ValueError('invalid_outcome')
            if result['self_test'] not in ('pass', 'fail', 'not_applicable'):
                raise ValueError('explicit_self_test_required')
            if (result['snapshot'] is not None and
                    (not isinstance(result['snapshot'], str) or not re.fullmatch('[a-f0-9]{64}', result['snapshot']))):
                raise ValueError('invalid_snapshot_schema')
            if (not isinstance(result['acceptance'], dict) or
                    not set(result['acceptance']).issubset(s['acceptance']) or
                    any(v not in ('pass', 'fail', 'unverified') for v in result['acceptance'].values())):
                raise ValueError('invalid_ac_results')
            if not isinstance(result['findings'], list) or len(result['findings']) > 128:
                raise ValueError('findings_required')
            for f in result['findings']:
                exact(f, 'ac_id required resolved detail')
                if f['ac_id'] not in s['acceptance'] or type(f['required']) is not bool or type(f['resolved']) is not bool:
                    raise ValueError('invalid_finding')
                text(f['detail'])
            if s['phase'] == 'cancel_pending':
                s['phase'] = 'cancelled'
            elif receipt['terminal'] != 'succeeded' or result['outcome'] == 'fail':
                s['phase'] = 'failed'
            elif stage['role'] == 'developer':
                if result['self_test'] != 'pass' or result['outcome'] != 'pass':
                    s['phase'] = 'failed'
                else:
                    stage['snapshot'] = snapshot(self.project, s['files'])
                    if result['snapshot'] != stage['snapshot']:
                        raise ValueError('developer_snapshot_mismatch')
                    self._stage(s, 'reviewer')
            else:
                if (result['snapshot'] != stage['snapshot'] or snapshot(self.project, s['files']) != stage['snapshot']):
                    raise ValueError('review_snapshot_changed')
                if (not isinstance(result['acceptance'], dict) or set(result['acceptance']) != set(s['acceptance']) or
                        any(v not in ('pass', 'fail', 'unverified') for v in result['acceptance'].values())):
                    raise ValueError('original_ac_results_required')
                failures = any(v != 'pass' for v in result['acceptance'].values()) or any(
                    f['required'] and not f['resolved'] for f in result['findings'])
                if result['outcome'] == 'pass':
                    if failures or result['self_test'] == 'fail':
                        raise ValueError('required_unresolved_findings')
                    s['phase'] = 'completed'
                elif s['round'] >= s['policy']['max_fix_rounds']:
                    s['phase'] = 'exhausted'
                else:
                    s['round'] += 1
                    self._stage(s, 'developer')
            stage['result'] = result
            s['receipts'][receipt['id']] = receipt
            self._save(s)
            return self._view(s)

    def cancel(self):
        with locked(self.path.with_suffix('.lock'), self.project):
            s = self._read()
            if s['phase'] in ('ready', 'running'):
                s['phase'] = 'cancel_pending'
                self._save(s)
            return self._view(s)

    def next(self, native_available=True):
        if native_available is not None and type(native_available) is not bool:
            raise ValueError('boolean_required')
        with locked(self.path.with_suffix('.lock'), self.project):
            view = self._view(self._read())
            if native_available is not True and view['phase'] in ('ready', 'running', 'cancel_pending'):
                view['pending_phase'] = view['phase']
                view['phase'] = 'native_check_required' if native_available is None else 'unavailable'
                view['native_action'] = 'pending: check official native tools; preserve exact handle'
            return view

    def _view(self, s):
        stage = s['stages'][-1]
        role = stage['role']
        action = {'ready': ('reserve pre-launch intent before any native invocation' if stage['intent'] is None else
                            'reconcile/observe exact reserved guard operation; outcome may be unknown; do not launch again'),
                  'running': 'observe exact native execution and collect terminal result with official tools',
                  'cancel_pending': 'reconcile/stop exact native handle and obtain official terminal receipt'}.get(s['phase'], 'none')
        reviews = [x for x in s['stages'] if x['role'] == 'reviewer' and x['result'] is not None]
        last_review = (dict(stage_id=reviews[-1]['id'], attempt=reviews[-1]['attempt'],
                            result=copy.deepcopy(reviews[-1]['result'])) if reviews else None)
        unresolved = ([f for f in last_review['result']['findings'] if f['required'] and not f['resolved']]
                      if last_review else [])
        if role == 'developer' and s['round'] > 0 and s['phase'] == 'ready':
            action += '; pass original AC, last_review and unresolved_required_findings to developer for correction'
        return dict(version=VERSION, binding=s['binding'], phase=s['phase'], role=role,
                    launch_intent=copy.deepcopy(stage['intent']), launch_permission='none',
                    last_review=last_review, unresolved_required_findings=unresolved,
                    stage_id=stage['id'], attempt=stage['attempt'], native_action=action,
                    native=stage['native'], acceptance=s['acceptance'], files=s['files'],
                    reviewed_snapshot=stage['snapshot'], profile='native-default',
                    instructions=[str(ROOT / 'roles' / ('backend-developer.md' if role == 'developer' else 'verifier.md')),
                                  str(ROOT / '.agents/skills' / ('ai-role-backend' if role == 'developer' else 'ai-role-verifier') / 'SKILL.md')],
                    verification='coordinator_observed_assertion_not_cryptographic_proof',
                    usage_status='unmeasured', usage_complete=False, active_concurrency=1)


from development_plan import PlanMixin


class Controller(PlanMixin, LegacyController):
    pass


def actor_from_coordinator(coordinator):
    # Coordinator identity is a canonical provider/session pair, not an app handle.
    parts = coordinator.split('/', 1)
    if len(parts) != 2 or not all(parts):
        raise ValueError('coordinator_must_be_provider_slash_session')
    return tuple(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['entry', 'report', 'decision', 'begin', 'next', 'reserve', 'attach', 'complete', 'cancel', 'select_design'])
    parser.add_argument('--project', required=True)
    parser.add_argument('--request-id')
    parser.add_argument('--coordinator')
    parser.add_argument('--coordinator-actor', help='canonical provider/session identity')
    parser.add_argument('--state')
    parser.add_argument('--entry-state', help='private development entry journal')
    parser.add_argument('--input', help='JSON object with API keyword arguments (receipt itself for complete)')
    args = parser.parse_args(argv)
    try:
        payload = read_json(args.input) if args.input else {}
        if args.command == 'decision':
            exact(payload, 'kind direct native_available')
            output = decision(args.project, **payload)
        else:
            actor_from_coordinator(args.coordinator_actor or '')
            controller = Controller(args.project, args.request_id, args.coordinator, args.state, args.coordinator_actor)
            if args.command in ('entry', 'report'):
                from development_entry import Entry
                entry = Entry(controller, args.entry_state)
                output = entry.enter(**payload) if args.command == 'entry' else entry.report(**payload)
            elif args.command == 'complete':
                output = controller.complete(payload)
            else:
                output = getattr(controller, args.command)(**payload)
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
