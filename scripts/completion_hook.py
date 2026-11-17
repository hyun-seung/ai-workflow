#!/usr/bin/env python3
"""Collect registered Orca/Codex usage from a Stop hook; never approve or clean up."""
import argparse
import json
import shlex
import sqlite3
from pathlib import Path
import sys
import time

from dispatch_usage import ROLE_IDS
from lifecycle_io import digest, locked, private_file, read_json, save
from native_record import identifier, read_session, inspect_session
from trace_observation import read_input, reject_nonfinite
from trace_store import Store
from trace_usage import import_usage

MAX_INPUT = 1024 * 1024


def bounded_json(value):
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > 64: raise ValueError('hook_json_too_deep')
        if isinstance(item, dict): stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list): stack.extend((v, depth + 1) for v in item)
    reject_nonfinite(value)


def validate_collection(value):
    if (not isinstance(value, dict) or set(value) != {'role', 'actor_id', 'task_title'} or
            value.get('role') not in ROLE_IDS or not isinstance(value.get('task_title'), str) or
            not 1 <= len(value['task_title'].strip()) <= 200):
        raise ValueError('invalid_usage_collection')
    identifier(value['actor_id'])
    return value


def load_config(filename):
    path, raw, config = read_input(filename)
    bounded_json(config)
    if (not isinstance(config, dict) or set(config) != {'version', 'project', 'ledger', 'directory', 'trace_db', 'coordinator'} or
            type(config['version']) is not int or config['version'] != 1):
        raise ValueError('invalid_completion_hook_config')
    if not isinstance(config['project'], str): raise ValueError('invalid_hook_project')
    project = Path(config['project'])
    if not project.is_absolute() or not project.is_dir() or project.resolve() != project:
        raise ValueError('hook_project_must_be_existing_absolute_directory')
    identifier(config['coordinator'])
    private_file(path, project)
    for key in ('ledger', 'directory', 'trace_db'):
        if not isinstance(config[key], str) or not Path(config[key]).is_absolute():
            raise ValueError('hook_paths_must_be_absolute')
    for key in ('ledger', 'trace_db'):
        target = private_file(config[key], project)
        if not target.is_file(): raise ValueError('hook_input_must_already_exist')
    private_file(Path(config['directory'])/'probe.json', project)
    store = Store(config['trace_db'], readonly=True); store.close()
    return config


def event_input(payload):
    bounded_json(payload)
    if not isinstance(payload, dict) or payload.get('hook_event_name') != 'Stop':
        raise ValueError('only_stop_hook_supported')
    for key in ('session_id', 'turn_id'): identifier(payload.get(key))
    for key in ('cwd', 'transcript_path'):
        if not isinstance(payload.get(key), str) or not Path(payload[key]).is_absolute():
            raise ValueError('hook_source_path_unavailable')
    if type(payload.get('stop_hook_active')) is not bool:
        raise ValueError('hook_stop_state_unknown')
    # Never persist the assistant message or permission/model fields from stdin.
    return {key: payload[key] for key in ('session_id', 'turn_id', 'cwd', 'transcript_path')}


def select_operation(config, source):
    data = read_json(config['ledger'])
    bounded_json(data)
    if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1 or
            data.get('coordinator') != config['coordinator'] or not isinstance(data.get('operations'), dict) or
            not isinstance(data.get('requests'), dict)):
        raise ValueError('hook_ledger_scope_mismatch')
    assignments = {(a['task_id'], a['dispatch_id']) for a in source['assignments']}
    operations = []
    for operation_id, operation in data['operations'].items():
        identifier(operation_id)
        if not isinstance(operation, dict) or not isinstance(operation.get('plan'), dict):
            raise ValueError('invalid_hook_ledger_operation')
        for key in ('task_id', 'dispatch_id', 'run_id'):
            if operation.get(key) is not None: identifier(operation[key])
        collection = operation['plan'].get('usage_collection')
        if collection is None or (operation.get('task_id'), operation.get('dispatch_id')) not in assignments:
            continue
        validate_collection(collection)
        if (operation['plan'].get('project') != config['project'] or operation['plan'].get('agent') != 'codex' or
                operation.get('state') not in {'bound', 'released'}):
            raise ValueError('hook_operation_not_confirmed')
        for key in ('task_id', 'dispatch_id', 'run_id'): identifier(operation.get(key))
        identifier(operation_id); identifier(operation['request_id'])
        operations.append((operation_id, operation))
    return operations


def turn_operations(config, source, turn_id):
    starts = [b['line'] for b in source['boundaries'] if b['kind'] == 'task_started' and b['turn_id'] == turn_id]
    if len(starts) != 1: return []
    start = starts[0]
    end = next((b['line'] for b in source['boundaries'] if b['line'] > start), source['lines'] + 1)
    pairs = {(a['task_id'], a['dispatch_id']) for a in source['assignments'] if start < a['line'] < end}
    return [(oid, op) for oid, op in select_operation(config, source) if (op['task_id'], op['dispatch_id']) in pairs]


def pin_operation(old, binding, operation_id, operation, receipt_path, project):
    if old and 'operation_binding' in old:
        if old['operation_id'] != operation_id or old['operation_binding'] != operation_binding(operation):
            raise ValueError('hook_operation_binding_changed')
        return old
    intent = dict(old or {}, version=1, binding=binding, status='pending', operation_id=operation_id,
                  operation_binding=operation_binding(operation), worker_exit_verified=False,
                  task_acceptance_evaluated=False)
    save(receipt_path, intent, project)
    return intent


def verify_saved_measurement(config, event, operation, saved, current=None):
    collection = operation['plan']['usage_collection']
    if current is None:
        current = inspect_session(config['project'], event['transcript_path'], event['session_id'],
                                  operation['task_id'], operation['dispatch_id'],
                                  request_id=operation['request_id'], role=collection['role'])
    if current['dispatch_usage'] != saved['dispatch_usage']:
        raise ValueError('hook_measurement_changed')
    interval = saved['dispatch_usage']['interval']
    def contexts(record):
        return [c for c in record['observed_contexts'] if interval['start_line'] < c['line'] < interval['end_line']]
    if (current['binding'] != saved['binding'] or current['session'] != saved['session'] or
            current['source']['path'] != saved['source']['path'] or contexts(current) != contexts(saved)):
        raise ValueError('hook_source_context_or_binding_changed')
    # A later turn may be appended legitimately. Compare the measured interval,
    # baseline/increments and its contexts, not the whole growing file's hash.


def collect(config, payload, attempts=5, delay=0.5):
    event = event_input(payload)
    if Path(event['cwd']).resolve() != Path(config['project']):
        return dict(status='ignored', reason='different_project')
    project = Path(config['project'])
    # Ignore unregistered sessions without creating per-session files. A short
    # bounded grace period covers a Stop racing the managed launch receipt.
    for attempt in range(attempts):
        try:
            source = read_session(project, event['transcript_path'], event['session_id'])
            if select_operation(config, source): break
        except ValueError as error:
            if str(error) != 'session_changed_during_read': raise
        if attempt + 1 < attempts: time.sleep(delay)
    else:
        return dict(status='ignored', reason='no_registered_dispatch')
    key = digest(dict(session=event['session_id'], turn=event['turn_id']))
    receipt_path = private_file(Path(config['directory'])/(key+'.receipt.json'), project)
    binding = dict(event=event, config=config)
    # Five bounded reads accommodate an async Stop handler arriving before the
    # source writer flushes task_complete. There is no native inbox polling.
    with locked(receipt_path.with_suffix('.lock'), project):
        old = None
        if receipt_path.exists():
            old = read_json(receipt_path)
            bounded_json(old)
            if old['binding'] != binding: raise ValueError('hook_event_binding_conflict')
            if old['status'] == 'collected':
                record_path = private_file(old['record_path'], project)
                saved = read_json(record_path)
                if digest(saved) != old['record_sha256']:
                    raise ValueError('hook_record_changed')
                operation = next((o for oid, o in select_operation(config, source) if oid == old['operation_id']), None)
                if operation is None or operation_binding(operation) != old['operation_binding']:
                    raise ValueError('hook_operation_binding_changed')
                verify_saved_measurement(config, event, operation, saved)
                append_usage(config, record_path, operation['plan']['usage_collection'])
                return dict(old, replay=True)
        issue = 'unregistered_or_unclosed_turn'
        for attempt in range(attempts):
            try:
                source = read_session(project, event['transcript_path'], event['session_id'])
                operations = select_operation(config, source)
                candidates = turn_operations(config, source, event['turn_id'])
                if len(candidates) > 1: raise ValueError('ambiguous_hook_operation')
                if candidates:
                    old = pin_operation(old, binding, *candidates[0], receipt_path, project)
                matches = []
                for operation_id, operation in operations:
                    collection = operation['plan']['usage_collection']
                    record = inspect_session(project, event['transcript_path'], event['session_id'],
                                             operation['task_id'], operation['dispatch_id'],
                                             request_id=operation['request_id'], role=collection['role'])
                    usage = record['dispatch_usage']
                    if (usage.get('interval') or {}).get('native_turn_id') == event['turn_id'] and usage['status'] == 'measured':
                        matches.append((operation_id, operation, record))
                if len(matches) > 1: raise ValueError('ambiguous_hook_operation')
                if matches:
                    operation_id, operation, record = matches[0]
                    old = pin_operation(old, binding, operation_id, operation, receipt_path, project)
                    collection = operation['plan']['usage_collection']
                    record_path = private_file(Path(config['directory'])/(key+'.metadata.json'), project)
                    if 'snapshot' in old:
                        if digest(old['snapshot']) != old['record_sha256']:
                            raise ValueError('hook_record_changed')
                        verify_saved_measurement(config, event, operation, old['snapshot'], record)
                    else:
                        if record_path.exists(): raise ValueError('hook_orphan_record_requires_review')
                        # Durable intent precedes both the snapshot file and DB
                        # append, so every partial-failure retry keeps its plan.
                        old = dict(old, snapshot=record, record_path=str(record_path), record_sha256=digest(record))
                        save(receipt_path, old, project)
                    if record_path.exists():
                        prior = read_json(record_path)
                        if digest(prior) != old['record_sha256']: raise ValueError('hook_record_changed')
                    else:
                        save(record_path, old['snapshot'], project)
                    imported = append_usage(config, record_path, collection)
                    receipt = dict(version=1, binding=binding, status='collected', operation_id=operation_id,
                                   event_id=imported['event_id'], record_path=str(record_path), operation_binding=operation_binding(operation),
                                   record_sha256=digest(read_json(record_path)), worker_exit_verified=False,
                                   task_acceptance_evaluated=False)
                    save(receipt_path, receipt, project)
                    return dict(receipt, replay=False)
            except ValueError as error:
                # Only a source-write race is automatically retried. Other bad
                # source identities, records and DB conflicts remain errors.
                if str(error) != 'session_changed_during_read': raise
                issue = str(error)
            if attempt + 1 < attempts: time.sleep(delay)
        receipt = dict(old or {}, version=1, binding=binding, status='pending', issue=issue,
                       worker_exit_verified=False, task_acceptance_evaluated=False)
        save(receipt_path, receipt, project)
        return receipt



def operation_binding(operation):
    return {key:operation.get(key) for key in ('request_id', 'task_id', 'dispatch_id', 'run_id', 'plan')}


def append_usage(config, record_path, collection):
    if not Path(config['trace_db']).is_file(): raise ValueError('hook_trace_db_missing')
    store = Store(config['trace_db'])
    try: return import_usage(store, record_path, collection['actor_id'], collection['task_title'])
    finally: store.close()

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--print-hook', action='store_true', help='Print candidate definition only; never install or trust it')
    args = parser.parse_args(argv)
    try:
        if args.print_hook:
            load_config(args.config)
            command = shlex.join([sys.executable, str(Path(__file__).resolve()), '--config', str(Path(args.config).resolve())])
            print(json.dumps(dict(description='Collect registered Orca/Codex usage after Stop',
                                  hooks={'Stop':[dict(hooks=[dict(type='command',command=command,**{'async':True},timeout=30)])]}), indent=2))
            return 0
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT: raise ValueError('hook_input_oversized')
        from trace_observation import unique, invalid_number, finite_float
        payload = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_number, parse_float=finite_float)
        result = collect(load_config(args.config), payload)
        # Stop hook stdout is JSON, never a continuation or permission decision.
        if result['status'] == 'pending':
            print(json.dumps({'systemMessage': 'Usage collection pending: source turn is unregistered or not closed.'}))
        else: print('{}')
        return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError, sqlite3.Error) as error:
        if args.print_hook:
            print('Hook definition failed: '+type(error).__name__,file=sys.stderr);return 2
        print(json.dumps({'systemMessage': 'Usage collection failed: ' + type(error).__name__}))
        return 0  # telemetry failure must never block/continue the work turn


if __name__ == '__main__':
    raise SystemExit(main())
