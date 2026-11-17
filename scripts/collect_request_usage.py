#!/usr/bin/env python3
"""Explicit finite source recovery; never synthesize Stop delivery."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from completion_hook import (bounded_json, load_config, operation_binding,
                             validate_collection, verify_saved_measurement)
from finish_worker import finish
from lifecycle_io import locked, private_file, read_json, save, now, orca, exited
from native_record import identifier, inspect_session, read_session, _SelectedSource
from native_turn_record import inspect_turn, _inspect_parsed_turn, load_binding, validate_binding
from trace_store import Store
from trace_usage import import_usage


def shape(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError('invalid_manifest_shape')


def path(value):
    if not isinstance(value, str) or not Path(value).is_absolute() or str(Path(value).resolve()) != value:
        raise ValueError('canonical_absolute_path_required')
    return Path(value)


def load_manifest(filename):
    p, raw, m = load_binding(filename)
    bounded_json(m)
    shape(m, 'schema_version id hook_config request_id coordinator_id coordinator_turns worker_sources')
    if type(m['schema_version']) is not int or m['schema_version'] != 1:
        raise ValueError('invalid_manifest_version')
    for k in ('id', 'request_id', 'coordinator_id'): identifier(m[k])
    path(m['hook_config'])
    if any(not isinstance(m[k], list) for k in ('coordinator_turns', 'worker_sources')):
        raise ValueError('invalid_manifest_items')
    if not 1 <= len(m['coordinator_turns']) + len(m['worker_sources']) <= 100:
        raise ValueError('manifest_item_limit')
    seen = set()
    for t in m['coordinator_turns']:
        shape(t, 'session_file session_id binding_file task_title')
        path(t['session_file']); path(t['binding_file']); identifier(t['session_id'])
        if not isinstance(t['task_title'], str) or not 1 <= len(t['task_title'].strip()) <= 200:
            raise ValueError('invalid_task_title')
        key = ('turn', t['binding_file'])
        if key in seen: raise ValueError('duplicate_manifest_item')
        seen.add(key)
    for w in m['worker_sources']:
        shape(w, 'operation_id session_file session_id expected')
        identifier(w['operation_id']); identifier(w['session_id']); path(w['session_file'])
        shape(w['expected'], 'model effort reviewer')
        if any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in w['expected'].values()):
            raise ValueError('invalid_expected_profile')
        key = ('worker', w['operation_id'])
        if key in seen: raise ValueError('duplicate_manifest_item')
        seen.add(key)
    return p, raw, m


def stable_record(destination, current, project, worker=None):
    """Retain original bytes, but only after validating today's source."""
    if destination.exists():
        saved = read_json(destination)
        if worker:
            config, entry, operation = worker
            verify_saved_measurement(config, dict(transcript_path=entry['session_file'],
                session_id=entry['session_id']), operation, saved, current)
            if current['verification'] != saved['verification']:
                raise ValueError('worker_profile_changed')
        else:
            for k in ('turn_binding', 'turn_usage', 'session', 'binding_source', 'recovery_contexts'):
                if saved[k] != current[k]: raise ValueError('coordinator_measurement_changed')
            if saved['source']['path'] != current['source']['path']:
                raise ValueError('coordinator_source_changed')
        return saved
    save(destination, current, project)
    return current


def validate_native(snapshot, operation):
    """Verify installed worker-show identity without assuming worker.runId exists."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('result'), dict):
        raise ValueError('invalid_native_worker_snapshot')
    result = snapshot['result']
    dispatch, worker = result.get('dispatch'), result.get('worker')
    if not isinstance(dispatch, dict) or not isinstance(worker, dict):
        raise ValueError('invalid_native_worker_identity')
    host = dispatch.get('hostScope')
    if (dispatch.get('id') != operation['dispatch_id'] or
            dispatch.get('taskId') != operation['task_id'] or
            dispatch.get('runId') != operation['run_id'] or
            not isinstance(host, dict) or host.get('kind') != 'local' or
            worker.get('dispatchId') != operation['dispatch_id'] or
            ('runId' in worker and worker['runId'] != operation['run_id'])):
        raise ValueError('native_worker_mapping_mismatch')
    if not isinstance(result.get('observation'), dict):
        raise ValueError('invalid_native_worker_observation')
    exited(snapshot, operation['task_id'], operation['dispatch_id'])


def fresh_native(cli, operation, directory, project):
    snapshot = orca(cli, 'orchestration', 'worker-show', '--dispatch', operation['dispatch_id'], '--full')
    # Keep a separate bounded observation for each invocation; original finish evidence is immutable.
    fd, name = tempfile.mkstemp(prefix='native-check-', suffix='.json', dir=directory)
    os.close(fd)
    evidence = private_file(name, project)
    save(evidence, snapshot, project)
    validate_native(snapshot, operation)
    return evidence


def collect(manifest_file, directory, cli):
    manifest_path, raw, m = load_manifest(manifest_file)
    config = load_config(m['hook_config']); project = Path(config['project'])
    if os.environ.get('ORCA_AGENT_SESSION_ID') != config['coordinator']:
        raise ValueError('native_coordinator_environment_mismatch')
    _, config_raw, _ = load_binding(m['hook_config'])
    ledger = read_json(config['ledger']); bounded_json(ledger)
    if (not isinstance(ledger, dict) or type(ledger.get('version')) is not int or ledger.get('version') != 1 or ledger.get('coordinator') != config['coordinator'] or
            not isinstance(ledger.get('operations'), dict)):
        raise ValueError('ledger_scope_mismatch')
    directory = path(str(directory))
    inputs = [manifest_path, Path(m['hook_config']), Path(config['ledger']), Path(config['trace_db'])]
    inputs += [Path(t[k]) for t in m['coordinator_turns'] for k in ('session_file', 'binding_file')]
    inputs += [Path(w['session_file']) for w in m['worker_sources']]
    if any(directory == p.parent or directory.is_relative_to(p.parent) for p in inputs):
        raise ValueError('output_inside_input_root')
    journal_path = private_file(directory / (m['id'] + '.journal.json'), project)
    pin = dict(manifest_path=str(manifest_path), manifest_sha256=hashlib.sha256(raw).hexdigest(),
               config_sha256=hashlib.sha256(config_raw).hexdigest(), config=config, bindings=[], workers=[])
    bindings = []; operations = []; native = set()
    for t in m['coordinator_turns']:
        bp, br, b = load_binding(t['binding_file']); bounded_json(b); validate_binding(b)
        if b['request_id'] != m['request_id'] or b['actor_id'] != m['coordinator_id']:
            raise ValueError('turn_request_root_mismatch')
        key = (t['session_id'], b['native_turn_id'])
        if key in native: raise ValueError('duplicate_native_turn')
        native.add(key); bindings.append(b)
        pin['bindings'].append(dict(path=str(bp), sha256=hashlib.sha256(br).hexdigest()))
    for w in m['worker_sources']:
        op = ledger['operations'].get(w['operation_id'])
        if not isinstance(op, dict): raise ValueError('unregistered_worker')
        plan = op.get('plan')
        if not isinstance(plan, dict): raise ValueError('invalid_worker_plan')
        validate_collection(plan.get('usage_collection'))
        if (op.get('request_id') != m['request_id'] or op.get('state') not in ('bound', 'released') or
                plan.get('project') != str(project) or plan.get('agent') != 'codex'):
            raise ValueError('worker_registration_mismatch')
        for k in ('task_id', 'dispatch_id', 'run_id'): identifier(op.get(k))
        operations.append(op); pin['workers'].append(operation_binding(op))
    # Root existence is checked read-only, before even asking the native runtime.
    live = Store(config['trace_db'], readonly=True)
    try:
        row = live.db.execute("SELECT body FROM records WHERE kind='actor' AND id=? AND request_id=?",
                              (m['coordinator_id'], m['request_id'])).fetchone()
        if not row or json.loads(row[0]).get('parent_id') is not None or json.loads(row[0]).get('role') != 'coordinator':
            raise ValueError('existing_request_root_required')
    finally: live.close()
    with locked(journal_path.with_suffix('.lock'), project):
        if journal_path.exists():
            journal = read_json(journal_path)
            if journal['pin'] != pin: raise ValueError('collection_rebinding_refused')
        else:
            journal = dict(version=1, pin=pin, time=now())
            save(journal_path, journal, project)
        selected = []
        selected_turns = {}
        sources = _SelectedSource()
        turns = enumerate(zip(m['coordinator_turns'], bindings))
        for i, (t, b) in sorted(turns, key=lambda item: (item[1][0]['session_file'], item[1][0]['session_id'])):
            data = sources.read(project, t['session_file'], t['session_id'])
            current = _inspect_parsed_turn(data, b)
            current['binding_source'] = pin['bindings'][i]
            if data['sha256'] != current['source']['sha256']:
                raise ValueError('source_changed_between_inspections')
            interval = current['turn_usage'].get('interval')
            current['recovery_contexts'] = [c for c in data['contexts'] if interval and interval['start_line'] < c['line'] < interval['end_line']]
            if current['turn_usage']['status'] != 'measured': raise ValueError('coordinator_source_unavailable')
            dest = private_file(directory / (m['id'] + '.turn.' + str(i) + '.json'), project)
            saved = stable_record(dest, current, project)
            selected_turns[i] = (dest, m['coordinator_id'], t['task_title'], saved['turn_usage']['measurement_key'])
        selected.extend(selected_turns[i] for i in sorted(selected_turns))
        for i, (w, op) in enumerate(zip(m['worker_sources'], operations)):
            c = op['plan']['usage_collection']
            live_snapshot = fresh_native(cli, op, directory, project)
            result = finish(cli, project, w['session_file'], w['session_id'], op['task_id'],
                op['dispatch_id'], directory / (m['id'] + '.workers'), w['expected'], m['request_id'], c['role'])
            # finish's replay deliberately caches; independently reread the source and profile.
            snapshot = Path(result['record_path']).with_name(op['dispatch_id'] + '.worker.json')
            validate_native(read_json(snapshot), op)
            current = inspect_session(project, w['session_file'], w['session_id'], op['task_id'],
                op['dispatch_id'], w['expected'], live_snapshot, m['request_id'], c['role'])
            if current['verification']['status'] != 'pass' or current['dispatch_usage']['status'] != 'measured':
                raise ValueError('worker_source_unavailable_or_profile_failed')
            dest = Path(result['record_path'])
            saved = stable_record(dest, current, project, (config, w, op))
            selected.append((dest, c['actor_id'], c['task_title'], saved['dispatch_usage']['measurement_key']))
        evidence = {str(dest): hashlib.sha256(dest.read_bytes()).hexdigest() for dest, _, _, _ in selected}
        if 'evidence' in journal and journal['evidence'] != evidence:
            raise ValueError('saved_evidence_changed')
        if 'evidence' not in journal:
            journal['evidence'] = evidence
            save(journal_path, journal, project)
        # The clone stages all imports, including overlap validation. Only deltas cross to live.
        with tempfile.TemporaryDirectory(prefix='stage-', dir=directory) as tmp:
            clone_path = Path(tmp) / 'trace.sqlite3'
            live = Store(config['trace_db'], readonly=True)
            target = sqlite3.connect(clone_path)
            try: live.db.backup(target)
            finally: target.close(); live.close()
            clone_path.chmod(0o600)
            clone = Store(clone_path)
            try:
                before_events = {r[0] for r in clone.db.execute("SELECT id FROM records WHERE kind='event'")}
                before_collections = {r[0] for r in clone.db.execute('SELECT id FROM collections')}
                for dest, actor, title, _ in selected: import_usage(clone, dest, actor, title)
                event = dict(id='source-recovery-' + m['id'], request_id=m['request_id'], actor_id=m['coordinator_id'],
                    kind='explanation', source='trace.manual-source-recovery', source_id=m['id'],
                    time=journal['time'], completeness='partial', detail=dict(manifest_id=m['id'],
                    manifest_sha256=pin['manifest_sha256'], measurement_keys=[s[3] for s in selected],
                    stop_delivery_inferred=False, full_usage_complete=False))
                clone.import_bundle(dict(schema_version=1, requests=[], actors=[], events=[event], collections=[]))
                events = [json.loads(body) for key, body in clone.db.execute("SELECT id,body FROM records WHERE kind='event'") if key not in before_events]
                collections = [json.loads(body) for key, body in clone.db.execute('SELECT id,body FROM collections') if key not in before_collections]
            finally: clone.close()
            live = Store(config['trace_db'])
            def guard_commit(action, argument, _second, _database, _trigger):
                if action == sqlite3.SQLITE_TRANSACTION and argument == 'COMMIT':
                    try:
                        sources.check()
                    except (OSError, ValueError):
                        return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            try:
                sources.check()
                live.db.set_authorizer(guard_commit)
                live.import_bundle(dict(schema_version=1, requests=[], actors=[], events=events, collections=collections))
            finally:
                live.db.set_authorizer(None)
                live.close()
        return dict(status='collected', manifest_id=m['id'], measurement_keys=[s[3] for s in selected],
                    stop_delivery_inferred=False, full_usage_complete=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True); parser.add_argument('--directory', required=True)
    parser.add_argument('--orca-cli', required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(collect(args.manifest, args.directory, args.orca_cli))); return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError, sqlite3.Error, subprocess.TimeoutExpired):
        print(json.dumps(dict(status='error', reason='request_usage_collection_failed')), file=sys.stderr); return 2


if __name__ == '__main__':
    raise SystemExit(main())
