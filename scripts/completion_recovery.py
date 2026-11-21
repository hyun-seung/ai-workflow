#!/usr/bin/env python3
"""Inspect received Stop receipts and explicitly replay one; no watcher or source search."""
import argparse
from itertools import islice
import json
from pathlib import Path
import re
import sqlite3
import sys

import completion_hook as hook
from lifecycle_io import digest, locked, now, private_file, read_json, save
from native_record import identifier
from trace_store import Store, stamp
from trace_usage import event_id, validate_usage, validate_event

MAX_ITEMS = 1000


def issue(error):
    value = str(error)
    return value if re.fullmatch(r'[a-z][a-z0-9_]{0,100}', value) else type(error).__name__


def catalog(config):
    data = read_json(private_file(config['ledger'], config['project']))
    hook.bounded_json(data)
    if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1 or
            data.get('coordinator') != config['coordinator'] or not isinstance(data.get('operations'), dict) or
            not isinstance(data.get('requests'), dict) or len(data['operations']) > MAX_ITEMS):
        raise ValueError('recovery_ledger_scope_or_limit')
    result, pairs = {}, set()
    for oid, op in data['operations'].items():
        identifier(oid)
        if not isinstance(op, dict) or not isinstance(op.get('plan'), dict):
            raise ValueError('invalid_recovery_operation')
        collection = op['plan'].get('usage_collection')
        if collection is None: continue
        hook.validate_collection(collection); identifier(op.get('request_id'))
        if op['plan'].get('project') != config['project'] or op['plan'].get('agent') != 'codex':
            raise ValueError('recovery_operation_scope_mismatch')
        ready = op.get('state') in {'bound', 'released'}
        if ready:
            for key in ('task_id', 'dispatch_id', 'run_id'): identifier(op.get(key))
            pair = op['task_id'], op['dispatch_id']
            if pair in pairs: raise ValueError('duplicate_registered_dispatch')
            pairs.add(pair)
        result[oid] = dict(operation=op, ready=ready)
    return result


def receipt(config, key):
    if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key):
        raise ValueError('invalid_receipt_key')
    path = private_file(Path(config['directory']) / (key + '.receipt.json'), config['project'])
    row = read_json(path); hook.bounded_json(row)
    if (not isinstance(row, dict) or type(row.get('version')) is not int or row['version'] != 1 or
            row.get('status') not in {'pending', 'collected'} or not isinstance(row.get('binding'), dict) or
            set(row['binding']) != {'config', 'event'} or row['binding']['config'] != config):
        raise ValueError('recovery_receipt_binding_mismatch')
    event = row['binding']['event']
    if not isinstance(event, dict) or set(event) != {'session_id', 'turn_id', 'cwd', 'transcript_path'}:
        raise ValueError('invalid_saved_stop_event')
    hook.event_input(dict(event, hook_event_name='Stop', stop_hook_active=False))
    if (Path(event['cwd']).resolve() != Path(config['project']) or
            key != digest(dict(session=event['session_id'], turn=event['turn_id']))):
        raise ValueError('recovery_receipt_key_or_project_mismatch')
    return row


def bound(row, oid, op):
    if row.get('operation_id') != oid or row.get('operation_binding') != hook.operation_binding(op):
        raise ValueError('hook_operation_binding_changed')


def collected(config, key, row, op, store):
    """Check saved metadata and the actual DB row, never the transcript path."""
    path = private_file(Path(config['directory']) / (key + '.metadata.json'), config['project'])
    if row.get('record_path') != str(path): raise ValueError('recovery_record_path_mismatch')
    record = read_json(path); hook.bounded_json(record)
    if digest(record) != row.get('record_sha256'): raise ValueError('hook_record_changed')
    if not isinstance(record, dict): raise ValueError('invalid_saved_metadata')
    usage = record.get('dispatch_usage')
    validate_usage(usage)
    event = row['binding']['event']; collection = op['plan']['usage_collection']
    if (usage['scope'] != 'dispatch' or usage['request_id'] != op['request_id'] or
            usage['role'] != collection['role'] or usage['task_id'] != op['task_id'] or
            usage['dispatch_id'] != op['dispatch_id'] or usage['session_id'] != event['session_id'] or
            usage['interval']['native_turn_id'] != event['turn_id'] or
            row.get('event_id') != event_id(usage['measurement_key'])):
        raise ValueError('recovery_usage_binding_mismatch')
    found = store.db.execute("SELECT body FROM records WHERE kind='event' AND id=?", (row['event_id'],)).fetchone()
    if not found: raise ValueError('usage_missing_from_database')
    expected = dict(id=row['event_id'], request_id=op['request_id'], actor_id=collection['actor_id'], kind='usage',
                    source='trace.native-usage', source_id=usage['measurement_key'], completeness='partial',
                    time=stamp(usage['interval']['finished_at']),
                    detail=dict(version=1, task_title=collection['task_title'], usage=usage))
    actual = json.loads(found[0]); actual.pop('detail_sha256', None)
    if actual != expected: raise ValueError('usage_database_binding_or_value_mismatch')


def pending(config, key, row):
    if 'snapshot' not in row:
        if 'record_path' in row or 'record_sha256' in row:
            raise ValueError('incomplete_saved_import_intent')
        return
    path = private_file(Path(config['directory']) / (key + '.metadata.json'), config['project'])
    if row.get('record_path') != str(path) or digest(row['snapshot']) != row.get('record_sha256'):
        raise ValueError('hook_record_changed')
    if path.exists() and digest(read_json(path)) != row['record_sha256']:
        raise ValueError('hook_record_changed')


def audit_path(config, key):
    return private_file(Path(config['directory']) / 'recovery' / (key + '.json'), config['project'])


def audit_binding(row):
    return digest(dict(binding=row['binding'], operation=row.get('operation_binding')))


def read_audit(config, key, row):
    path = Path(config['directory']) / 'recovery' / (key + '.json')
    if not path.exists() and not path.is_symlink(): return None
    data = read_json(private_file(path, config['project'])); hook.bounded_json(data)
    if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1 or
            data.get('key') != key or data.get('binding_sha256') != audit_binding(row) or
            type(data.get('attempts')) is not int or not 1 <= data['attempts'] <= MAX_ITEMS or
            data.get('outcome') not in {'running', 'pending', 'collected', 'error'}):
        raise ValueError('recovery_audit_binding_mismatch')
    return data


def coordinator_coverage(store, request_ids):
    """Validate only saved observations in the selected DB snapshot; open no source."""
    count, errors = 0, []
    for rid in sorted(request_ids):
        for eid, body in store.db.execute(
                "SELECT id,body FROM records WHERE kind='event' AND request_id=?", (rid,)):
            try:
                event = json.loads(body)
                if event.get('source') != 'trace.native-usage': continue
                usage = event.get('detail', {}).get('usage', {})
                if not isinstance(usage, dict): raise ValueError('invalid_coordinator_observation')
                if usage.get('scope') == 'dispatch' and usage.get('role') != 'coordinator': continue
                validate_event(event)
                if (event.get('id') != eid or event.get('kind') != 'usage' or event.get('completeness') != 'partial' or
                        event['request_id'] != rid or usage['scope'] != 'native_turn' or
                        usage['role'] != 'coordinator' or event['actor_id'] != usage['actor_id']):
                    raise ValueError('coordinator_observation_binding_mismatch')
                found = store.db.execute("SELECT body FROM records WHERE kind='actor' AND id=? AND request_id=?",
                                         (event['actor_id'], rid)).fetchone()
                actor = json.loads(found[0]) if found else {}
                if (actor.get('id') != event['actor_id'] or actor.get('request_id') != rid or
                        actor.get('role') != 'coordinator' or actor.get('parent_id') is not None):
                    raise ValueError('coordinator_observation_actor_mismatch')
                count += 1
            except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as error:
                errors.append(dict(event_id=eid, reason=issue(error)))
    return count, errors


def retryable_pending(config, key, row, op):
    if 'snapshot' not in row: return
    snapshot = row['snapshot']
    if not isinstance(snapshot, dict): raise ValueError('invalid_saved_metadata')
    usage = snapshot.get('dispatch_usage'); validate_usage(usage)
    event = row['binding']['event']; collection = op['plan']['usage_collection']
    if (usage['scope'] != 'dispatch' or usage['request_id'] != op['request_id'] or
            usage['role'] != collection['role'] or usage['task_id'] != op['task_id'] or
            usage['dispatch_id'] != op['dispatch_id'] or usage['session_id'] != event['session_id'] or
            usage['interval']['native_turn_id'] != event['turn_id']):
        raise ValueError('recovery_usage_binding_mismatch')


def actions(operations, rows):
    result = []
    for op in operations:
        oid = op['operation_id']
        observed = [r for r in rows if r.get('operation_id') == oid]
        if op['status'] in {'missing', 'not_ready'}:
            result.append(dict(operation_id=oid, action='provide_explicit_source' if op['status'] == 'missing'
                               else 'inspect_native_receipt', reason=op['reason']))
        for row in observed:
            if row['status'] == 'collected': continue
            result.append(dict(operation_id=oid, receipt_key=row['receipt_key'],
                               action='retry_saved_receipt' if row['status'] == 'pending' and
                               op['status'] == 'pending' and len(observed) == 1 else
                               'inspect_native_receipt' if row['reason'] == 'operation_not_ready_for_collection'
                               else 'inspect_binding_or_storage', reason=row['reason']))
    return result


def recovery_plan(operations, suggested_actions):
    """Explain prerequisites from saved evidence only; never attempt recovery."""
    result = []
    for op in operations:
        steps = [a for a in suggested_actions if a['operation_id'] == op['operation_id']]
        retryable = (op['status'] == 'pending' and len(steps) == 1 and
                     steps[0]['action'] == 'retry_saved_receipt')
        if op['status'] == 'collected':
            disposition, condition = 'verified_received_receipts', 'none'
        elif op['status'] == 'missing':
            disposition = 'blocked_missing_input'
            condition = 'genuine_stop_or_official_source_with_exact_binding'
        elif op['status'] == 'not_ready':
            disposition, condition = 'blocked_native_binding', 'exact_native_launch_binding_confirmed'
        elif retryable:
            disposition, condition = 'explicit_retry_available', 'pinned_source_ready_for_revalidation'
        else:
            disposition, condition = 'inspection_required', 'binding_storage_or_receipt_ambiguity_resolved'
        item = dict(operation_id=op['operation_id'], disposition=disposition,
                    retry_saved_receipt_available=retryable, resume_when=condition,
                    new_evidence_required=op['status'] != 'collected')
        if retryable:
            item['receipt_key'] = steps[0]['receipt_key']
        if op['status'] == 'missing':
            # Absence alone cannot establish hook distrust, restart, or process failure.
            item.update(root_cause='undetermined', explicit_source_does_not_replace_stop=True)
        result.append(item)
    return dict(mode='diagnostic_only', automatic_retry=False, source_reads=False,
                reconstructs_missing_stop=False, full_usage_complete=False, operations=result)


def inspect(config, request_id=None):
    if request_id is not None: identifier(request_id)
    ops = catalog(config)
    selected = {oid: item for oid, item in ops.items() if request_id is None or item['operation']['request_id'] == request_id}
    if request_id is not None and not selected: raise ValueError('no_registered_usage_operations_for_request')
    rows, errors = [], []
    paths = sorted(islice(Path(config['directory']).glob('*.receipt.json'), MAX_ITEMS + 1))
    if len(paths) > MAX_ITEMS: raise ValueError('recovery_receipt_limit_exceeded')
    store = Store(config['trace_db'], readonly=True)
    try:
        store.db.execute('BEGIN')  # one read-only DB snapshot for this observation
        for path in paths:
            key = path.name.removesuffix('.receipt.json')
            item = dict(receipt_key=key, status='needs_attention')
            try:
                row = receipt(config, key); oid = row.get('operation_id')
                if oid in ops and oid not in selected: continue
                if oid not in selected: raise ValueError('receipt_not_bound_to_registered_operation')
                op = selected[oid]['operation']; item['operation_id'] = oid
                bound(row, oid, op)
                if not selected[oid]['ready']: raise ValueError('operation_not_ready_for_collection')
                if row['status'] == 'collected':
                    collected(config, key, row, op, store)
                    item.update(status='collected', reason='saved_receipt_and_usage_verified')
                else:
                    pending(config, key, row)
                    retryable_pending(config, key, row, op)
                    item.update(status='pending', reason='saved_import_intent' if 'snapshot' in row else 'turn_not_closed_or_collection_incomplete')
                audit = read_audit(config, key, row)
                if audit:
                    item['last_recovery'] = {k:audit.get(k) for k in ('outcome', 'issue', 'attempts', 'time')}
            except (OSError, ValueError, KeyError, TypeError, RecursionError, sqlite3.Error) as error:
                item.update(status='needs_attention', reason=issue(error))
                if 'operation_id' not in item: errors.append(dict(receipt_key=key, reason=issue(error)))
            rows.append(item)
        coordinator_count, coordinator_errors = coordinator_coverage(
            store, {request_id} if request_id is not None else
            {item['operation']['request_id'] for item in selected.values()})
    finally: store.close()
    operations = []
    for oid, item in selected.items():
        observed = [r for r in rows if r.get('operation_id') == oid]
        states = {r['status'] for r in observed}
        status = 'needs_attention' if 'needs_attention' in states else 'pending' if 'pending' in states else 'collected' if states else 'missing' if item['ready'] else 'not_ready'
        operations.append(dict(operation_id=oid, request_id=item['operation']['request_id'], status=status,
                               reason='no_received_stop_receipt' if status == 'missing' else 'launch_not_confirmed' if status == 'not_ready' else None,
                               received_receipts=len(observed)))
    counts = {name:sum(o['status'] == name for o in operations) for name in ('collected', 'pending', 'missing', 'not_ready', 'needs_attention')}
    state = 'no_registered_operations' if not operations and not errors and not coordinator_errors else 'attention_required' if errors or coordinator_errors or any(o['status'] != 'collected' for o in operations) else 'observed_receipts_verified'
    request_state = 'no_registered_operations' if not operations and not coordinator_errors else 'attention_required' if coordinator_errors or any(
        o['status'] != 'collected' for o in operations) else 'observed_receipts_verified'
    suggested = actions(operations, rows)
    return dict(version=1, observed_at=now(), status=state,
                request_status=request_state, unassociated_receipts=errors,
                suggested_actions=suggested, recovery_plan=recovery_plan(operations, suggested),
                coordinator_observation_errors=coordinator_errors,
                coverage_summary=dict(registered_worker_operations=len(operations),
                                      verified_worker_receipts=sum(r['status'] == 'collected' for r in rows),
                                      recorded_coordinator_turns=coordinator_count,
                                      unassociated_receipts=len(errors), full_usage_complete=False),
                coverage='registered_operations_and_received_Stop_events_only', full_usage_complete=False,
                expected_operations=len(operations), counts=counts, operations=operations, receipts=rows, receipt_errors=errors)


def retry(config, operation_id, key):
    identifier(operation_id)
    # Require a previously pinned event; no new source paths or guessed bindings.
    row = receipt(config, key); ops = catalog(config)
    if operation_id not in ops or not ops[operation_id]['ready']: raise ValueError('operation_not_ready_for_collection')
    bound(row, operation_id, ops[operation_id]['operation'])
    if row['status'] == 'pending':
        pending(config, key, row)
        retryable_pending(config, key, row, ops[operation_id]['operation'])
    path = audit_path(config, key)
    with locked(path.with_suffix('.lock'), config['project']):
        previous = read_audit(config, key, row)
        attempts = previous['attempts'] + 1 if previous else 1
        if attempts > MAX_ITEMS: raise ValueError('recovery_attempt_limit_exceeded')
        audit = dict(version=1, key=key, operation_id=operation_id, binding_sha256=audit_binding(row),
                     attempts=attempts, time=now(), outcome='running', issue=None)
        save(path, audit, config['project'])
        try:
            event = row['binding']['event']
            result = hook.collect(config, dict(event, hook_event_name='Stop', stop_hook_active=False), attempts=1, delay=0)
            if result['status'] not in {'collected', 'pending'}: raise ValueError('recovery_did_not_collect_registered_event')
            audit.update(outcome=result['status'])
        except (OSError, ValueError, KeyError, TypeError, RecursionError, sqlite3.Error) as error:
            audit.update(outcome='error', issue=issue(error))
        audit['time'] = now(); save(path, audit, config['project'])
        return dict(audit, worker_exit_verified=False, task_acceptance_evaluated=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('status'); p.add_argument('--request-id')
    p = sub.add_parser('retry'); p.add_argument('--operation-id', required=True); p.add_argument('--receipt-key', required=True)
    args = parser.parse_args(argv)
    try:
        config = hook.load_config(args.config)
        result = inspect(config, args.request_id) if args.command == 'status' else retry(config, args.operation_id, args.receipt_key)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get('status') == 'observed_receipts_verified' or result.get('outcome') == 'collected' else 1
    except (OSError, ValueError, KeyError, TypeError, RecursionError, sqlite3.Error) as error:
        print(json.dumps(dict(status='error', issue=issue(error))))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
