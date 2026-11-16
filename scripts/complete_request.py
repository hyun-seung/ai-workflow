#!/usr/bin/env python3
"""Complete one explicitly accepted request through existing lifecycle guards."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
import time

import completion_hook as hook
import completion_recovery as recovery
import finish_worker as fw
import native_turn_record as turns
import workspace_resources as wr
from harness_policy import load as load_policy
from lifecycle_io import digest, locked, now, private_file, read_json, save
from native_record import identifier
from trace_closeout import artifact, close_request, open_artifact
from trace_store import Store
from trace_usage import import_usage

FIELDS = {'schema_version', 'id', 'project', 'request_id', 'coordinator_id', 'hook_config',
          'operations', 'coordinator_turns', 'resources', 'closeout_file', 'retry_pending'}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def exact(value, keys):
    require(isinstance(value, dict) and set(value) == set(keys), 'batch_invalid_fields')


def path(value):
    require(isinstance(value, str) and Path(value).is_absolute() and '..' not in Path(value).parts,
            'batch_invalid_path')
    target = Path(value)
    require(target.resolve() == target, 'batch_path_alias')
    return target


def fingerprint(filename, json_required=True):
    if not json_required:
        with os.fdopen(open_artifact(filename), 'rb') as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and
                    before.st_size <= 1024 * 1024, 'batch_invalid_acceptance_file')
            raw = stream.read(1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        with os.fdopen(open_artifact(filename), 'rb') as stream:
            current = os.fstat(stream.fileno())
        require(len(raw) <= 1024 * 1024 and
                all(getattr(before, f) == getattr(after, f) == getattr(current, f) for f in fields),
                'batch_acceptance_changed_while_reading')
        return hashlib.sha256(raw).hexdigest()
    # Existing bounded reader also rejects duplicate keys and non-finite numbers.
    _, raw, value = hook.read_input(filename)
    hook.bounded_json(value)
    return hashlib.sha256(raw).hexdigest()


def validate_artifact_locations(closeout, cfg, output, resource_rows):
    """Exclude lifecycle-owned paths, without excluding stable sibling reports.

    Unshared files also rule out inode aliases outside these lexical scopes,
    including aliases to SQLite sidecars and files replaced during finalization.
    This check is repeated by prepare before each destructive resource step.
    """
    directories = [Path(cfg['directory']), output]
    directories += [path(row['directory']) for row in resource_rows]
    db = Path(cfg['trace_db']); ledger = Path(cfg['ledger'])
    files = [db, *(Path(str(db) + suffix) for suffix in ('-wal', '-shm', '-journal')),
             ledger, ledger.with_suffix('.lock')]
    require(isinstance(closeout.get('artifacts'), list), 'batch_invalid_artifacts')
    for row in closeout['artifacts']:
        require(isinstance(row, dict), 'batch_invalid_artifact')
        candidate = path(row.get('path'))
        require(candidate not in files and
                not any(candidate.is_relative_to(d) or d.is_relative_to(candidate) for d in directories),
                'batch_artifact_in_mutable_storage')
        try:
            fd = open_artifact(candidate)
        except FileNotFoundError:
            # Archive results can be created by the later all-resource N pass.
            continue
        with os.fdopen(fd, 'rb') as stream:
            require(os.fstat(stream.fileno()).st_nlink == 1, 'batch_artifact_shared_inode')


def prepare(manifest_file, directory, config, auto_cleanup):
    m = read_json(path(str(manifest_file))); hook.bounded_json(m)
    exact(m, FIELDS)
    require(type(m['schema_version']) is int and m['schema_version'] == 1 and
            type(m['retry_pending']) is bool, 'batch_invalid_version_or_retry')
    for key in ('id', 'request_id', 'coordinator_id'):
        identifier(m[key])
    project = path(m['project']); output = path(str(directory))
    raw_cfg = read_json(path(m['hook_config']))
    require(isinstance(raw_cfg, dict) and isinstance(raw_cfg.get('directory'), str) and
            path(raw_cfg['directory']).is_dir(), 'batch_hook_directory_must_exist')
    cfg = hook.load_config(path(m['hook_config']))
    require(cfg['project'] == str(project), 'batch_project_mismatch')
    require(os.environ.get('ORCA_AGENT_SESSION_ID') == cfg['coordinator'],
            'batch_native_coordinator_mismatch')
    policy = load_policy(project, config, auto_cleanup)
    for key in ('operations', 'resources', 'coordinator_turns'):
        require(isinstance(m[key], list) and len(m[key]) <= 100, 'batch_input_limit')
    selected = {}
    for row in m['operations']:
        exact(row, ('operation_id', 'expected')); identifier(row['operation_id'])
        exact(row['expected'], ('model', 'effort', 'reviewer'))
        require(all(isinstance(v, str) and 0 < len(v.strip()) <= 200 for v in row['expected'].values()),
                'batch_expected_profile_required')
        require(row['operation_id'] not in selected, 'batch_duplicate_operation')
        selected[row['operation_id']] = row
    catalog = recovery.catalog(cfg)
    ops = {k: v['operation'] for k, v in catalog.items() if v['operation']['request_id'] == m['request_id']}
    require(set(ops) == set(selected), 'batch_operation_set_mismatch')
    require(all(catalog[k]['ready'] for k in ops), 'batch_operation_not_ready')
    store = Store(cfg['trace_db'], readonly=True)
    try:
        bundle = store.request(m['request_id'])
    finally:
        store.close()
    require(bundle is not None, 'batch_request_missing')
    actors = {a['id']: a for a in bundle['actors']}
    require(m['coordinator_id'] in actors and actors[m['coordinator_id']].get('parent_id') is None
            and actors[m['coordinator_id']].get('role') == 'coordinator',
            'batch_coordinator_mismatch')
    for op in ops.values():
        c = op['plan']['usage_collection']; a = actors.get(c['actor_id'], {})
        require(a.get('request_id') == m['request_id'] and a.get('role') == c['role'],
                'batch_actor_mismatch')
    closeout = read_json(path(m['closeout_file'])); hook.bounded_json(closeout)
    require(isinstance(closeout, dict), 'batch_invalid_closeout')
    require(closeout.get('request_id') == m['request_id'] and closeout.get('coordinator_id') == m['coordinator_id']
            and closeout.get('status') == 'completed' and closeout.get('verification') == 'pass',
            'batch_acceptance_required')
    protected = [path(str(manifest_file)), path(m['hook_config']), path(m['closeout_file']),
                 path(cfg['ledger']), path(cfg['trace_db']), path(cfg['directory'])]
    protected += [path(p) for p in policy['sources']]
    receipt_bindings = {}
    receipt_paths = list(Path(cfg['directory']).glob('*.receipt.json'))
    require(len(receipt_paths) <= recovery.MAX_ITEMS, 'batch_receipt_limit')
    for rp in receipt_paths:
        saved = read_json(rp); hook.bounded_json(saved)
        require(isinstance(saved, dict), 'batch_invalid_receipt')
        if saved.get('operation_id') in ops:
            key = rp.name.removesuffix('.receipt.json')
            receipt = recovery.receipt(cfg, key)
            recovery.bound(receipt, receipt['operation_id'], ops[receipt['operation_id']])
            receipt_bindings[key] = dict(binding=receipt['binding'], operation_binding=receipt['operation_binding'])
            protected.append(path(receipt['binding']['event']['transcript_path']))
    resources = []; ids = set()
    for row in m['resources']:
        exact(row, ('directory', 'resource_id', 'acceptance', 'operation_ids'))
        require(isinstance(row['operation_ids'], list) and row['operation_ids'] and
                len(row['operation_ids']) <= 100 and len(set(row['operation_ids'])) == len(row['operation_ids'])
                and set(row['operation_ids']) == set(ops), 'batch_resource_operations')
        rp = wr.record_path(project, path(row['directory']), row['resource_id'])
        require(str(rp) not in ids, 'batch_duplicate_resource'); ids.add(str(rp))
        record = wr.load_record(rp, project, row['resource_id'])
        require(record['binding']['request_id'] == m['request_id'], 'batch_resource_request_mismatch')
        wr.scope(project, *(record['binding'][k] for k in ('source', 'allowed_root', 'archive')), rp)
        protected += [rp, path(row['acceptance'])]
        resources.append(record)
    turn_ids = set()
    for row in m['coordinator_turns']:
        exact(row, ('session_file', 'session_id', 'binding_file', 'task_title'))
        identifier(row['session_id'])
        require(isinstance(row['task_title'], str) and 0 < len(row['task_title'].strip()) <= 200,
                'batch_turn_title')
        _, _, binding = turns.load_binding(path(row['binding_file'])); turns.validate_binding(binding)
        require(binding['request_id'] == m['request_id'] and binding['actor_id'] == m['coordinator_id'],
                'batch_turn_actor_mismatch')
        key = (row['session_id'], binding['native_turn_id'])
        require(key not in turn_ids, 'batch_duplicate_turn'); turn_ids.add(key)
        protected += [path(row['session_file']), path(row['binding_file'])]
    sources = [path(r['binding']['source']) for r in resources]
    archives = [path(r['binding']['archive']) for r in resources]
    require(not any(p.is_relative_to(output) or output.is_relative_to(p) for p in protected),
            'batch_output_input_overlap')
    for i, archive in enumerate(archives):
        require(not any(archive.is_relative_to(p) or p.is_relative_to(archive) for p in archives[:i]),
                'batch_archive_overlap')
    for i, source in enumerate(sources):
        require(not any(p.is_relative_to(source) or source.is_relative_to(p) for p in protected + [output] + archives),
                'batch_protected_path_overlap')
        require(not any(source.is_relative_to(p) or p.is_relative_to(source) for p in sources[:i]),
                'batch_resource_overlap')
    require(not any(output.is_relative_to(a) or a.is_relative_to(output) for a in archives),
            'batch_journal_archive_overlap')
    validate_artifact_locations(closeout, cfg, output, m['resources'])
    frozen_files = protected[:3] + [path(row['acceptance']) for row in m['resources']]
    frozen_files += [path(row['binding_file']) for row in m['coordinator_turns']]
    frozen_files += [path(p) for p in policy['sources']]
    acceptances = {path(row['acceptance']) for row in m['resources']}
    pin = dict(manifest=m, files={str(p): fingerprint(p, p not in acceptances) for p in frozen_files}, policy=policy,
               receipt_bindings=receipt_bindings,
               operations={k: hook.operation_binding(v) for k, v in ops.items()},
               resources=[dict(binding=r['binding'], root_identity=r['root_identity']) for r in resources])
    return m, cfg, policy, ops, resources, closeout, pin


def terminalize(store, manifest, operations):
    """Only explicitly accepted, measured actors may acquire completed status."""
    ids = {manifest['coordinator_id']} | {op['plan']['usage_collection']['actor_id']
                                              for op in operations.values()}
    bundle = store.request(manifest['request_id'])
    require(bundle is not None, 'batch_request_missing')
    actors = [dict(a, status='completed') for a in bundle['actors'] if a['id'] in ids]
    store.import_bundle(dict(schema_version=1, requests=[dict(r, status='completed')
                                                        for r in bundle['requests']], actors=actors, events=[]))


def run(manifest_file, directory, orca_cli, config=None, auto_cleanup=None):
    m, cfg, policy, ops, resources, closeout, pin = prepare(manifest_file, directory, config, auto_cleanup)
    project = Path(m['project'])
    journal = private_file(Path(directory) / (m['request_id'] + '.batch.json'), project)
    with locked(journal.with_suffix('.lock'), project):
        state = read_json(journal) if journal.exists() else dict(version=1, id=m['id'], pin=pin, stages=[])
        require(state.get('pin') == pin and state.get('id') == m['id'], 'batch_input_binding_changed')
        replay = state.get('status') == 'complete'
        target_journal = journal.with_name(journal.stem + '.replay.json') if replay else journal
        if replay:
            state.update(stages=[], status='verifying_replay')
        else:
            save(journal, state, project)
        def stage(name):
            state['stages'].append(dict(stage=name, time=now(), elapsed_seconds=time.monotonic() - started))
            save(target_journal, state, project)
        def recheck():
            current = prepare(manifest_file, directory, config, auto_cleanup)[-1]
            require(current == pin, 'batch_input_binding_changed')
        started = time.monotonic()
        try:
            diagnostic = recovery.inspect(cfg, m['request_id'])
            state['diagnostic'] = diagnostic; stage('inspected')
            chosen = {}
            for oid, op in ops.items():
                rows = [r for r in diagnostic['receipts'] if r.get('operation_id') == oid]
                require(len(rows) == 1 and rows[0]['status'] in {'collected', 'pending'}, 'batch_receipt_attention')
                row = rows[0]; key = row['receipt_key']
                if row['status'] == 'pending':
                    require(m['retry_pending'], 'batch_pending_requires_retry')
                    result = recovery.retry(cfg, oid, key)
                    require(result['outcome'] == 'collected', 'batch_pending_not_collected')
                receipt = recovery.receipt(cfg, key); recovery.bound(receipt, oid, op)
                require(receipt['status'] == 'collected', 'batch_receipt_not_collected')
                chosen[oid] = (key, receipt)
            # Valid but unbound events remain visible; they cannot authorize
            # collection or cleanup for this request. Malformed global records block.
            require(all(r.get('reason') == 'receipt_not_bound_to_registered_operation'
                        for r in diagnostic.get('receipt_errors', [])),
                    'batch_unassociated_receipt_attention')
            require(not diagnostic.get('coordinator_observation_errors'),
                    'batch_coordinator_observation_attention')
            receipt_pin = {oid: digest(row) for oid, (_, row) in chosen.items()}
            require(state.get('receipts', receipt_pin) == receipt_pin, 'batch_receipt_changed')
            state['receipts'] = receipt_pin; stage('receipts_bound')
            store = Store(cfg['trace_db'])
            try:
                for row in m['operations']:
                    oid = row['operation_id']; op = ops[oid]; c = op['plan']['usage_collection']
                    key, receipt = chosen[oid]; event = receipt['binding']['event']
                    require(not any(path(event['transcript_path']).is_relative_to(path(r['binding']['source']))
                                    for r in resources), 'batch_worker_source_inside_cleanup')
                    recovery.collected(cfg, key, receipt, op, store)
                    result = fw.finish(orca_cli, project, event['transcript_path'], event['session_id'],
                                       op['task_id'], op['dispatch_id'], directory, row['expected'],
                                       m['request_id'], c['role'])
                    require(result['verification'] == 'pass' and result['dispatch_usage_status'] == 'measured',
                            'batch_worker_profile_or_usage')
                    current = read_json(result['record_path'])
                    if result['replay']:
                        fw.exited(fw.orca(orca_cli, 'orchestration', 'worker-show', '--dispatch',
                                          op['dispatch_id'], '--full'), op['task_id'], op['dispatch_id'])
                        current = fw.inspect_session(project, event['transcript_path'], event['session_id'],
                                                     op['task_id'], op['dispatch_id'], row['expected'],
                                                     Path(directory) / (op['dispatch_id'] + '.worker.json'),
                                                     m['request_id'], c['role'])
                        require(current['verification']['status'] == 'pass', 'batch_fresh_profile_failed')
                    hook.verify_saved_measurement(cfg, event, op, read_json(receipt['record_path']),
                                                  current)
                    import_usage(store, result['record_path'], c['actor_id'], c['task_title'])
                for index, row in enumerate(m['coordinator_turns']):
                    _, _, binding = turns.load_binding(row['binding_file'])
                    record = turns.inspect_turn(project, row['session_file'], row['session_id'], binding)
                    require(record['turn_usage']['status'] == 'measured', 'batch_turn_unmeasured')
                    target = journal.with_name(m['request_id'] + '.turn-' + str(index) + '.json')
                    if target.exists():
                        old = read_json(target)
                        require(all(old[k] == record[k] for k in ('turn_binding', 'turn_usage', 'session')) and
                                old['source']['path'] == record['source']['path'], 'batch_turn_source_changed')
                    else:
                        save(target, record, project)
                    import_usage(store, target, m['coordinator_id'], row['task_title'])
                coordinator_coverage, coverage_errors = recovery.coordinator_coverage(store, {m['request_id']})
                require(not coverage_errors, 'batch_coordinator_observation_attention')
                stage('usage_verified')
                def finalize(mode):
                    for row in m['resources']:
                        recheck()
                        for oid, (key, receipt) in chosen.items():
                            require(digest(recovery.receipt(cfg, key)) == state['receipts'][oid],
                                    'batch_receipt_changed')
                        if mode == 'Y':
                            require([artifact(r) for r in closeout['artifacts']] == state['artifacts'],
                                    'batch_artifact_changed')
                        workers = [dict(task_id=ops[k]['task_id'], dispatch_id=ops[k]['dispatch_id'])
                                   for k in row['operation_ids']]
                        wr.finalize(project, row['directory'], row['resource_id'], row['acceptance'],
                                    workers, orca_cli, config, mode)
                finalize('N'); stage('archived')
                pinned = []
                for row in closeout['artifacts']:
                    p = path(row['path'])
                    require(not any(p.is_relative_to(path(r['binding']['source'])) for r in resources),
                            'batch_artifact_in_cleanup_source')
                    pinned.append(artifact(row))
                require(state.get('artifacts', pinned) == pinned, 'batch_artifact_changed')
                state['artifacts'] = pinned; stage('artifacts_pinned')
                # artifact() returns output metadata; the closeout input contract
                # accepts the pinned hash, but does not accept the computed size.
                bound_closeout = dict(closeout, artifacts=[
                    {key: value for key, value in row.items() if key != 'bytes'}
                    for row in pinned])
                with tempfile.TemporaryDirectory(prefix='batch-preflight-', dir=journal.parent) as temp:
                    copy = Path(temp) / 'trace.sqlite'
                    target = sqlite3.connect(copy); store.db.backup(target); target.close(); copy.chmod(0o600)
                    trial = Store(copy)
                    try:
                        terminalize(trial, m, ops)
                        close_request(trial, bound_closeout)
                    finally:
                        trial.close()
                stage('preflight_pass')
                recheck()
                require([artifact(r) for r in closeout['artifacts']] == pinned, 'batch_artifact_changed')
                if policy['auto_cleanup'] == 'Y':
                    finalize('Y')
                stage('cleanup_' + policy['auto_cleanup'])
                recheck()
                require([artifact(r) for r in closeout['artifacts']] == pinned, 'batch_artifact_changed')
                terminalize(store, m, ops)
                result = close_request(store, bound_closeout)
                state.update(status='complete', result=result, full_usage_complete=False,
                             worker_receipts=len(chosen), coordinator_turns=len(m['coordinator_turns']),
                             recorded_coordinator_turns=coordinator_coverage)
                stage('complete')
                return dict(status='complete', journal=str(journal), result=result, replay=replay,
                            unassociated_receipts=len(diagnostic.get('receipt_errors', [])),
                            coordinator_turns=len(m['coordinator_turns']),
                            recorded_coordinator_turns=coordinator_coverage, full_usage_complete=False)
            finally:
                store.close()
        except Exception as error:
            if state.get('status') != 'complete':
                state.update(status='attention_required', issue=recovery.issue(error)); stage('failed')
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'directory', 'orca-cli'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--config'); parser.add_argument('--auto-cleanup', choices=('Y', 'N'))
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(args.manifest, args.directory, args.orca_cli, args.config, args.auto_cleanup)))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError, sqlite3.Error,
            subprocess.TimeoutExpired) as error:
        print(json.dumps(dict(status='error', issue=recovery.issue(error))))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
