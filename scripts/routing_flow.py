#!/usr/bin/env python3
"""Event-driven routing glue for the current AI; no classifier or background loop."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import dispatch_guard as guard
from lifecycle_io import digest, locked, private_file, read_json, save
from request_router import identifier, load_catalog, obj, route, unique, reject_constant
import routing_choices as choices
from routing_dispatch import launch_selected, completion_expectations, compare_profiles
from startup import executable_for, startup

REFERENCE = {'session_id', 'request_id', 'revision', 'proposal_id', 'proposal_version'}


def exact(value, required, optional=()):
    obj(value, set(required) | set(optional), 'flow_input')
    if not set(required) <= set(value):
        raise ValueError('incomplete_flow_input')
    return value


def capability(target, current, session, environ):
    if target['execution'] == 'current':
        return ('same_ai_guidance' if all(target[k] == current[k] for k in ('model', 'effort'))
                else 'manual_profile_change_required')
    if not target['model'] or not target['effort']:
        return 'explicit_worker_profile_required'
    if environ.get('ORCA_AGENT_SESSION_ID') != session or environ.get('PASEO_AGENT_ID'):
        return 'matching_orca_session_required'
    return 'managed_worker_preflight_required'


def reference(state, pid):
    item = state['proposals'][pid]
    return dict(session_id=state['session_id'], request_id=state['request_id'], revision=item['revision'],
                proposal_id=pid, proposal_version=item['version'])


def selected(state, ref, *, accepted=False):
    exact(ref, REFERENCE)
    identifier(ref['proposal_id'])
    choices.revision(ref['revision']); choices.revision(ref['proposal_version'])
    item = state['proposals'].get(ref['proposal_id'])
    if item is None or reference(state, ref['proposal_id']) != ref:
        raise ValueError('stale_flow_reference')
    if accepted and (state['selection'] != ref['proposal_id'] or item['status'] != 'accepted'):
        raise ValueError('current_accepted_selection_required')
    return item


def read_state(path, project):
    state = choices.validate(read_json(path)) if path.exists() else None
    if state is not None and state['project'] != str(project):
        raise ValueError('choice_project_mismatch')
    return state


def output(action, **fields):
    return dict(fields, action=action, runtime_applied=False, classifier_model_calls=0)


def request(state, payload, project, environ):
    exact(payload, {'context', 'followup'}, {'analysis', 'reconsider'})
    if type(payload['followup']) is not bool or type(payload.get('reconsider', False)) is not bool:
        raise ValueError('explicit_flow_booleans_required')
    live = choices.context(payload['context'])
    if live['project'] != str(project):
        raise ValueError('choice_project_mismatch')
    loader = lambda: load_catalog(project)
    updated = state
    if state is not None:
        updated, _ = choices.operate(state, 'advance', live, loader)
        if 'analysis' not in payload:
            reused = choices.reuse_selection(updated, dict(live, followup=payload['followup']), loader)
            if reused['action'] == 'reuse':
                return updated, output('reuse', profile=reused['profile'],
                                       selection=reference(updated, updated['selection']))
    value = dict({key: live[key] for key in choices.ROUTE_KEYS}, version=1)
    if 'analysis' in payload:
        value['analysis'] = payload['analysis']
    result = route(value, loader)
    if result['action'] != 'propose':
        return updated, output(result['action'], routing=result)
    support = capability(result['profile'], live['current'], live['session_id'], environ)
    if support in ('manual_profile_change_required', 'explicit_worker_profile_required', 'matching_orca_session_required'):
        # Never offer an executable acceptance for an unconnected profile change.
        return updated, output('manual' if support == 'manual_profile_change_required' else 'blocked',
                               reason=support, profile=result['profile'], executable=False)
    if updated is None:
        updated, _ = choices.operate(None, 'init', live, loader)
    matching = [(pid, item) for pid, item in updated['proposals'].items()
                if item['revision'] == live['revision'] and item['target'] == result['profile']]
    if matching:
        pid, item = max(matching, key=lambda pair: pair[1]['version'])
        if item['status'] in ('pending', 'accepted'):
            # No repeat prompt or instruction body on duplicate request events.
            return updated, output('pending' if item['status'] == 'pending' else 'selected',
                                   selection=reference(updated, pid), replay=True, capability=support)
        if not payload.get('reconsider', False):
            return updated, output('direct', reason='previous_choice_declined', replay=True)
    pid = 'proposal_' + str(updated['sequence'] + 1)
    updated, offered = choices.operate(updated, 'offer', dict(proposal_id=pid, analysis=payload['analysis']), loader)
    return updated, output('propose', selection=reference(updated, pid), routing=offered['routing'], capability=support)


def require_orca(state, environ):
    if environ.get('ORCA_AGENT_SESSION_ID') != state['session_id'] or environ.get('PASEO_AGENT_ID'):
        raise ValueError('matching_orca_session_required')


def bound_ledger(state, path, ref, environ):
    require_orca(state, environ)
    item = selected(state, ref)
    binding = item.get('dispatch_binding')
    if binding is None:
        raise ValueError('selection_has_no_dispatch_binding')
    ledger = guard.Ledger(Path(binding['ledger']).parent, Path(state['project']), state['session_id'])
    if str(ledger.path) != binding['ledger']:
        raise ValueError('dispatch_ledger_path_mismatch')
    with ledger.lock():
        operation = ledger.read()['operations'].get(binding['operation_id'])
        if operation is None or digest(operation['plan']) != binding['plan_digest']:
            raise ValueError('dispatch_binding_changed')
        expected = dict(file=str(path), revision=ref['revision'], proposal_id=ref['proposal_id'],
                        proposal_version=ref['proposal_version'], target=item['target'])
        if (operation['request_id'] != state['request_id'] or
                operation['plan']['project'] != state['project'] or operation['plan'].get('routing_choice') != expected):
            raise ValueError('dispatch_choice_mismatch')
    return ledger, binding['operation_id']


def worker_output(operation, *, replay=None, include_receipt=True):
    fields = {key: operation.get(key) for key in ('state', 'task_id', 'dispatch_id', 'run_id', 'outcome', 'release', 'returncode')}
    fields['replay'] = operation.get('replay', False) if replay is None else replay
    if 'cancellation_evidence' in operation:
        fields['cancellation_evidence'] = operation['cancellation_evidence']
    if operation.get('stop_request'):
        fields['stop_request'] = operation['stop_request']
    elif include_receipt and operation.get('receipt') and not fields['replay']:
        fields['receipt'] = operation['receipt']  # Preserve native next/recovery instructions.
    return output('worker', **fields)


def run_start(state, path, payload, environ, cli):
    exact(payload, {'selection', 'directory', 'operation_id', 'spec_file', 'limits'}, {'writes', 'depends_on', 'collection'})
    ref = payload['selection']
    item = selected(state, ref, accepted=True)
    require_orca(state, environ)
    if item['target']['execution'] != 'worker':
        raise ValueError('worker_selection_required')
    for key in ('directory', 'spec_file'):
        if not isinstance(payload[key], str) or not Path(payload[key]).is_absolute():
            raise ValueError('absolute_private_worker_paths_required')
    spec = private_file(Path(payload['spec_file']), Path(state['project']))
    identifier(payload['operation_id'])
    exact(payload['limits'], {'max_active_workers', 'max_dispatches_per_request'})
    if any(type(v) is not int or v < 1 for v in payload['limits'].values()):
        raise ValueError('explicit_positive_delegation_limits_required')
    for key in ('writes', 'depends_on'):
        if not isinstance(payload.get(key, []), list) or any(not isinstance(v, str) for v in payload.get(key, [])):
            raise ValueError('invalid_worker_scope_list')
    for dependency in payload.get('depends_on', []):
        identifier(dependency)
    collection = None
    if 'collection' in payload:
        from completion_hook import validate_collection
        exact(payload['collection'], {'actor_id', 'task_title'})
        collection = validate_collection(dict(payload['collection'], role=item['target']['role']))
    # Read-only native preparation is event-driven, never part of ordinary answers.
    prepared = startup(Path(state['project']), explicit='orca', environ=dict(environ, ORCA_CLI_COMMAND=str(cli)))
    if prepared['environment'] != 'orca' or not prepared['connection']['session_identity_confirmed']:
        raise ValueError('orca_preflight_required')
    ledger = guard.Ledger(payload['directory'], Path(state['project']), state['session_id'])
    args = SimpleNamespace(project=state['project'], command='start', worktree='current', agent='codex',
                           spec_file=spec, model=item['target']['model'], effort=item['target']['effort'],
                           writes=payload.get('writes', []), depends_on=payload.get('depends_on', []),
                           subject='Selected request', request_id=state['request_id'], operation_id=payload['operation_id'],
                           routing_choice_file=path, routing_revision=ref['revision'],
                           routing_proposal=ref['proposal_id'], routing_proposal_version=ref['proposal_version'])
    if collection:
        args.usage_role, args.usage_actor_id, args.usage_task_title = (collection[k] for k in ('role', 'actor_id', 'task_title'))
    plan, text = guard.make_plan(args, ledger)
    return worker_output(launch_selected(args, ledger, plan, text, payload['limits'], cli, guard.launch))


@contextmanager
def completion_claim(artifacts, owner, project):
    """Reserve only unused artifact names; permit our own interrupted collection."""
    claim = artifacts['collection']
    with locked(claim.with_suffix('.lock'), project):
        if claim.exists():
            if read_json(claim) != owner:
                raise ValueError('completion_owner_changed')
        else:
            if any(path.exists() for key, path in artifacts.items() if key != 'collection'):
                raise ValueError('unowned_completion_artifact_use_fresh_directory')
            save(claim, owner, project)
        yield


def run_finish(state, path, payload, environ, cli):
    """Collect one explicitly bound source; no discovery, cleanup or model call."""
    exact(payload, {'selection', 'directory'}, {'source', 'trace'})
    ref = payload['selection']
    ledger, operation_id = bound_ledger(state, path, ref, environ)
    with ledger.lock():
        operation = ledger.read()['operations'][operation_id]
    if not isinstance(payload['directory'], str) or not Path(payload['directory']).is_absolute():
        raise ValueError('absolute_private_completion_directory_required')
    if not operation.get('dispatch_id') or not operation.get('task_id'):
        return output('collection_deferred', reason='worker_not_started' if operation.get('cancellation_evidence') else
                      'native_binding_unconfirmed', worker=worker_output(operation, include_receipt=False))
    if 'source' not in payload:
        return output('collection_deferred', reason='official_session_source_required',
                      worker=worker_output(ledger.refresh(operation_id, cli), include_receipt=False))
    source = exact(payload['source'], {'session_file', 'session_id'})
    identifier(source['session_id'])
    if not isinstance(source['session_file'], str) or not Path(source['session_file']).is_absolute():
        raise ValueError('absolute_private_session_file_required')
    project = Path(state['project'])
    source_path = private_file(Path(source['session_file']), project)
    collection = operation['plan'].get('usage_collection')
    trace_path = None
    if 'trace' in payload:
        exact(payload['trace'], {'db'})
        if not collection:
            raise ValueError('trace_requires_launch_collection_binding')
        value = payload['trace']['db']
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError('absolute_private_trace_db_required')
        trace_path = private_file(Path(value), project)
        if not trace_path.is_file():
            raise ValueError('trace_db_must_already_exist')
    dispatch_id = identifier(operation['dispatch_id'])
    artifacts = {kind: private_file(Path(payload['directory']) / (dispatch_id + '.' + kind + '.json'), project)
                 for kind in ('completion', 'worker', 'metadata', 'collection')}
    outputs = set(artifacts.values()) | {artifacts[key].with_suffix('.lock') for key in ('completion', 'collection')}
    protected = {path, path.with_suffix('.lock'), ledger.path, ledger.path.with_suffix('.lock'), source_path}
    if trace_path:
        trace_files = {private_file(Path(str(trace_path) + suffix), project) for suffix in ('', '-wal', '-shm', '-journal')}
        if trace_files & protected:
            raise ValueError('trace_db_collides_with_input_or_state')
        protected.update(trace_files)
    if outputs & protected:
        raise ValueError('completion_output_collides_with_input_or_state')
    target = operation['plan']['routing_choice']['target']
    role = target['role']
    request_id = state['request_id'] if role is not None else None
    expected = completion_expectations(operation, {}, request_id, role)
    owner = dict(version=1, project=state['project'], session_id=state['session_id'], request_id=state['request_id'],
                 operation_id=operation_id, plan_digest=digest(operation['plan']), selection=ref,
                 source=dict(session_file=str(source_path), session_id=source['session_id']))
    with completion_claim(artifacts, owner, project):
        from finish_worker import finish
        result = finish(cli, project, source_path, source['session_id'], operation['task_id'], dispatch_id,
                        Path(payload['directory']), expected, request_id, role)
        from compare_routing import parse, read_private
        raw_record = read_private(result['record_path'], project)
        record = parse(raw_record)
        if digest(record) != result['record_digest']:
            raise ValueError('completion_record_changed')
        comparison_reference = dict(path=result['record_path'], sha256=hashlib.sha256(raw_record).hexdigest())
        profile_result = compare_profiles(operation, record)
        # Exit and measurement verification are separate: missing usage must not
        # retain the slot of a worker whose exact physical exit is already proven.
        worker = ledger.refresh(operation_id, cli)
        usage = record['dispatch_usage']
        measured = usage['status'] == 'measured' and usage['task_attributed'] is True
        trace = {'status': 'not_requested'}
        if trace_path:
            if not measured:
                trace = {'status': 'deferred', 'reason': 'attributed_dispatch_usage_required'}
            else:
                from trace_store import Store
                from trace_usage import import_usage
                store = Store(trace_path)
                try:
                    imported = import_usage(store, result['record_path'], collection['actor_id'], collection['task_title'])
                finally:
                    store.close()
                trace = dict(status='imported', event_id=imported['event_id'])
        collected = (measured and result['verification'] not in ('fail', 'unverified') and
                     profile_result['status'] == 'pass')
        return output('completion', collection_status='collected' if collected else 'partial',
                      selection=ref, completion={k: result.get(k) for k in
                          ('record_path', 'record_digest', 'replay', 'verification', 'dispatch_usage_status',
                           'task_attributed', 'measurement_key')}, profile=profile_result,
                      usage=dict(status=usage['status'], scope='dispatch', task_attributed=usage['task_attributed'],
                                 measurements=usage['measurements'] if measured else None,
                                 request_total_measured=False), trace=trace, worker=worker_output(worker, include_receipt=False),
                      comparison_reference=comparison_reference,
                      quality_evaluated=False, cleanup_performed=False)


def handle(action, path, project, payload, *, environ=None, cli=None):
    environ = os.environ if environ is None else environ
    project = Path(project).resolve(strict=True)
    if not project.is_dir():
        raise ValueError('project_not_directory')
    path = private_file(Path(path), project)
    cancellation = None
    with locked(path.with_suffix('.lock'), project):
        state = read_state(path, project)
        if action == 'request':
            updated, result = request(state, payload, project, environ)
            if updated is not None and updated != state:
                save(path, updated, project)
            return result
        if state is None:
            raise ValueError('choice_state_missing')
        if action == 'status':
            exact(payload, set())
            return output('status', revision=state['revision'], selection=state['selection'], proposals={
                pid: dict(reference(state, pid), status=item['status'], target=item['target'],
                          worker_bound='dispatch_binding' in item) for pid, item in state['proposals'].items()})
        if action == 'context':
            exact(payload, {'selection'})
            selected(state, payload['selection'], accepted=True)
            return output('guidance', **choices.role_context(state, project))
        if action == 'choose':
            exact(payload, REFERENCE | {'choice'})
            item = selected(state, {k: payload[k] for k in REFERENCE})
            if payload['choice'] == 'accept':
                support = capability(item['target'], state['current'], state['session_id'], environ)
                if support not in ('same_ai_guidance', 'managed_worker_preflight_required'):
                    raise ValueError(support)
            updated, result = choices.operate(state, 'choose', payload, lambda: load_catalog(project))
            guidance = None
            if payload['choice'] == 'accept' and not result['replay'] and item['target']['execution'] == 'current':
                # Prepare before saving acceptance, so a failed read can be retried.
                guidance = choices.role_context(updated, project)
            if updated != state:
                save(path, updated, project)
            state = updated
            if payload['choice'] == 'cancel' and item.get('dispatch_binding'):
                cancellation = {k: payload[k] for k in REFERENCE}
            else:
                return output('chosen', choice=payload['choice'], replay=result['replay'],
                              selection=reference(state, payload['proposal_id']), guidance=guidance,
                              next_action='start' if payload['choice'] == 'accept' and item['target']['execution'] == 'worker' else None)
        elif action not in ('start', 'refresh', 'stop', 'finish'):
            raise ValueError('unknown_flow_action')
    # Never acquire the choice lock again while holding it. The launch bridge
    # reacquires and rechecks the choice until native submission has completed.
    if cancellation is not None:
        try:
            cli = cli or executable_for('orca', environ)
            ledger, operation_id = bound_ledger(state, path, cancellation, environ)
            result = worker_output(ledger.stop(operation_id, cli))
            result['choice'] = 'cancel'
            return result
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            return output('cancellation_pending', selection=cancellation, selection_cancelled=True,
                          runtime_stopped=False, reason=str(error) if isinstance(error, ValueError) else type(error).__name__)
    cli = cli or executable_for('orca', environ)
    if action == 'start':
        return run_start(state, path, payload, environ, cli)
    if action == 'finish':
        return run_finish(state, path, payload, environ, cli)
    exact(payload, {'selection'})
    ref = payload['selection']
    ledger, operation_id = bound_ledger(state, path, ref, environ)
    operation = ledger.stop(operation_id, cli) if action == 'stop' else ledger.refresh(operation_id, cli)
    result = worker_output(operation)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('request', 'choose', 'context', 'start', 'refresh', 'stop', 'finish', 'status'))
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--orca-cli')
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(choices.MAX_BYTES + 1)
        if len(raw) > choices.MAX_BYTES:
            raise ValueError('flow_input_too_large')
        payload = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
        result = handle(args.action, args.state, args.project, payload, cli=args.orca_cli)
        print(json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
        if result['action'] == 'cancellation_pending':
            return 2
        if result['action'] == 'collection_deferred' or (result['action'] == 'completion' and result['collection_status'] != 'collected'):
            return 1
        if result['action'] == 'worker' and result['state'] not in ('bound', 'released'):
            return 2
        if result['action'] == 'worker' and result['state'] == 'bound' and result.get('returncode') != 0:
            return 2
        if args.action in ('stop', 'choose') and result.get('stop_request') and result['state'] != 'released':
            return 2
        return 0
    except guard.LimitReached as error:
        print('routing_flow: deferred: ' + str(error), file=sys.stderr)
        return 3
    except (ValueError, TypeError, KeyError, OSError, UnicodeError, RecursionError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        print('routing_flow: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
