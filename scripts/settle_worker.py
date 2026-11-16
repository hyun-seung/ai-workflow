#!/usr/bin/env python3
"""Handle one delivered completion explicitly; no inbox, watcher, acceptance or launch."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

import completion_hook as hook
import completion_recovery as recovery
from dispatch_guard import ACTIVE, Ledger, lineage
from lifecycle_io import exited, locked, now, orca, private_file, read_json, save
from native_record import identifier
from startup import executable_for
from trace_store import Store


@contextmanager
def phase(attempt, name):
    row = dict(name=name, started_at=now())
    start = time.monotonic()
    try:
        yield
    finally:
        row.update(finished_at=now(), seconds=time.monotonic() - start)
        attempt['phases'].append(row)


def identity(snapshot, operation):
    hook.bounded_json(snapshot)
    if not isinstance(snapshot, dict) or snapshot.get('ok') is not True:
        raise ValueError('settle_invalid_snapshot')
    result = snapshot.get('result')
    if not isinstance(result, dict): raise ValueError('settle_invalid_snapshot')
    dispatch, worker, observation = (result.get(k) for k in ('dispatch', 'worker', 'observation'))
    if not all(isinstance(v, dict) for v in (dispatch, worker, observation)):
        raise ValueError('settle_invalid_snapshot')
    if (dispatch.get('id') != operation['dispatch_id'] or dispatch.get('taskId') != operation['task_id'] or
            dispatch.get('runId') != operation['run_id'] or worker.get('dispatchId') != operation['dispatch_id'] or
            not isinstance(dispatch.get('hostScope'), dict) or dispatch['hostScope'].get('kind') != 'local'):
        raise ValueError('settle_native_binding_mismatch')
    return dispatch, worker, observation


def verify_stop(config, key, operation_id, operation):
    row = recovery.receipt(config, key)
    recovery.bound(row, operation_id, operation)
    if row['status'] != 'collected': raise ValueError('settle_stop_not_collected')
    store = Store(config['trace_db'], readonly=True)
    try:
        recovery.collected(config, key, row, operation, store)
    finally:
        store.close()
    return row


def journal(path, binding):
    if not path.exists():
        return dict(version=1, binding=binding, release_intent=None, attempts=[], completed=None)
    value = read_json(path); hook.bounded_json(value)
    if (not isinstance(value, dict) or set(value) != {'version', 'binding', 'release_intent', 'attempts', 'completed'} or
            type(value['version']) is not int or value['version'] != 1 or value['binding'] != binding or
            not isinstance(value['attempts'], list) or len(value['attempts']) > 100):
        raise ValueError('settle_journal_binding_or_shape')
    intent = value['release_intent']
    if intent is not None and (not isinstance(intent, dict) or set(intent) != {'requested_at'} or
                               not isinstance(intent['requested_at'], str)):
        raise ValueError('settle_invalid_release_intent')
    done = value['completed']
    if done is not None and (not isinstance(done, dict) or done.get('status') != 'exited' or
                             done.get('operation_id') != binding['operation_id'] or
                             done.get('worker_exit_verified') is not True or
                             done.get('task_acceptance_evaluated') is not False):
        raise ValueError('settle_invalid_completed_record')
    return value


def settle(config, operation_id, key, directory, cli, release_settled=False):
    """At most two shows and one explicit release; a saved intent forbids resending."""
    identifier(operation_id)
    if os.environ.get('ORCA_AGENT_SESSION_ID') != config['coordinator']:
        raise ValueError('settle_coordinator_mismatch')
    ledger = Ledger(Path(config['ledger']).parent, config['project'], config['coordinator'])
    if str(ledger.path) != config['ledger']: raise ValueError('settle_ledger_path_mismatch')
    path = private_file(Path(directory) / (operation_id + '.settle.json'), config['project'])
    # Serialize with starts/followups, so the observed identity cannot be reopened
    # through this managed ledger during the release. No sleep or polling here.
    with locked(path.with_suffix('.lock'), config['project']), ledger.lock():
        data = ledger.read()
        catalog = recovery.catalog(config)
        if operation_id not in catalog or not catalog[operation_id]['ready']:
            raise ValueError('settle_operation_not_bound')
        operation = data['operations'][operation_id]
        binding = dict(config=config, operation_id=operation_id, receipt_key=key,
                       operation=hook.operation_binding(operation))
        state = journal(path, binding)
        attempt = dict(started_at=now(), phases=[])
        with phase(attempt, 'verify_saved_stop'):
            stop = verify_stop(config, key, operation_id, operation)
        if state['completed'] is not None:
            return dict(state['completed'], replay=True, fresh_observation=False)
        if len(state['attempts']) >= 100: raise ValueError('settle_attempt_limit')

        def finish(status, **extra):
            attempt.update(status=status, finished_at=now(), **extra)
            state['attempts'].append(attempt)
            result = dict(operation_id=operation_id, status=status, replay=False, fresh_observation=True,
                          worker_exit_verified=status == 'exited', task_acceptance_evaluated=False,
                          next_action='verify_task_result' if status == 'exited' else 'inspect_and_follow_native_guidance',
                          attempt=attempt)
            if status == 'lineage_active': result['fresh_observation'] = False
            if status == 'exited': state['completed'] = result
            save(path, state, config['project'])
            return result

        try:
            origin = lineage(data['operations'], operation_id)
            if any(oid != operation_id and op['state'] in ACTIVE and
                   lineage(data['operations'], oid) == origin for oid, op in data['operations'].items()):
                return finish('lineage_active', fresh_native_observation=False)
            with phase(attempt, 'observe_worker'):
                snapshot = orca(cli, 'orchestration', 'worker-show', '--dispatch', operation['dispatch_id'], '--full')
                dispatch, worker, observation = identity(snapshot, operation)
                save(path.with_name(operation_id + '.before.json'), snapshot, config['project'])
                attempt['agent_wait_observation'] = ('unavailable' if 'agentWait' not in observation
                                                     else 'none' if observation['agentWait'] is None else 'waiting')
            try:
                exited(snapshot, operation['task_id'], operation['dispatch_id'])
            except ValueError as error:
                if str(error) != 'worker_exit_unconfirmed': raise
                if not release_settled: return finish('awaiting_exit')
                if state['release_intent'] is not None: return finish('release_already_requested')
                if (dispatch.get('status') not in {'completed', 'failed'} or
                        worker.get('state') not in {'succeeded', 'failed'} or worker.get('stage') != 'settled' or
                        observation.get('exactWorker') is not True or observation.get('agentWait') is not None):
                    return finish('not_releasable')
                with phase(attempt, 'verify_closed_source'):
                    saved = read_json(stop['record_path'])
                    hook.verify_saved_measurement(config, stop['binding']['event'], operation, saved)
                # Write-ahead intent: a timeout/crash/unknown reply is not permission
                # to submit the mutation again, even after restarting this command.
                state['release_intent'] = dict(requested_at=now())
                save(path, state, config['project'])
                with phase(attempt, 'request_official_release'):
                    release = orca(cli, 'orchestration', 'worker-release', '--dispatch', operation['dispatch_id'], '--full')
                    save(path.with_name(operation_id + '.release.json'), release, config['project'])
                with phase(attempt, 'confirm_exact_exit'):
                    snapshot = orca(cli, 'orchestration', 'worker-show', '--dispatch', operation['dispatch_id'], '--full')
                    identity(snapshot, operation)
                    save(path.with_name(operation_id + '.after.json'), snapshot, config['project'])
                try:
                    exited(snapshot, operation['task_id'], operation['dispatch_id'])
                except ValueError as error:
                    if str(error) != 'worker_exit_unconfirmed': raise
                    return finish('awaiting_exit_after_release')
            with phase(attempt, 'record_exact_exit'):
                # Same checked snapshot, under the same ledger lock: avoid a third
                # query. Exact exit releases capacity; it never accepts the product.
                operation.update(state='released', outcome=snapshot['result']['worker']['state'],
                                 exit_evidence=dict(observed_at=now(), dispatch_id=operation['dispatch_id'], exact_worker=True),
                                 updated_at=now())
                ledger.write(data)
            return finish('exited')
        except (OSError, ValueError, KeyError, TypeError, RecursionError, sqlite3.Error, subprocess.TimeoutExpired) as error:
            finish('error', issue=recovery.issue(error))
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('config', 'operation-id', 'receipt-key', 'directory'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--orca-cli')
    parser.add_argument('--release-settled', action='store_true', help='Request official early release once after fresh closed-source validation')
    args = parser.parse_args(argv)
    try:
        config = hook.load_config(args.config)
        result = settle(config, args.operation_id, args.receipt_key, args.directory,
                        args.orca_cli or executable_for('orca', os.environ), args.release_settled)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result['status'] == 'exited' else 1
    except (OSError, ValueError, KeyError, TypeError, RecursionError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        print(json.dumps(dict(status='error', issue=recovery.issue(error))))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
