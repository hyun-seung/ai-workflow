#!/usr/bin/env python3
"""Generate a review-only completion manifest without executing lifecycle actions."""
import argparse
from itertools import islice
import json
import os
from pathlib import Path
import stat
import sqlite3
import tempfile

import complete_request as batch
import completion_hook as hook
import completion_recovery as recovery
import native_turn_record as turns
import workspace_resources as wr
from dispatch_guard import receipt_binding
from lifecycle_io import private_file
from native_record import identifier
from trace_store import Store
from trace_closeout import validate_detail
from trace_usage import validate_usage

FIELDS = {'schema_version', 'id', 'hook_config', 'request_id', 'expected_reviewer',
          'resource_directories', 'acceptance_file', 'closeout_file', 'coordinator_turns', 'retry_pending'}
require = batch.require


def existing(value, project=None, directory=False):
    p = batch.path(str(value))
    require(p.is_dir() if directory else p.is_file(), 'draft_reference_missing')
    require(not any(x.is_symlink() for x in (p, *p.parents)), 'draft_path_alias')
    if project is not None:
        # Parent existence is required before private_file, which otherwise creates it.
        require(p.parent.is_dir(), 'draft_parent_missing')
        if directory:
            private_file(p / 'probe.json', project)
        else:
            private_file(p, project)
    if not directory:
        require(p.stat().st_nlink == 1, 'draft_shared_inode')
    return p


def read(value, project=None):
    p = existing(value, project)
    _, _, data = hook.read_input(p)
    hook.bounded_json(data)
    return data


def profile(oid, ops, visiting=None):
    visiting = set() if visiting is None else visiting
    require(oid not in visiting and oid in ops, 'draft_lineage_cycle_or_missing')
    op = ops[oid]; plan = op['plan']; action = plan.get('action')
    require(action in {'start', 'retry', 'followup'}, 'draft_unsupported_action')
    values = {k: plan.get(k) for k in ('model', 'effort')}
    origins = {k: oid for k in values}
    if action == 'followup':
        previous = plan.get('previous'); require(previous in ops, 'draft_predecessor_missing')
        old = ops[previous]
        require(old['request_id'] == op['request_id'] and
                all(old['plan'].get(k) == plan.get(k) for k in ('project', 'agent')) and
                old['state'] == 'released' and
                all(plan.get('previous_' + k) == old.get(k + '_id') for k in ('dispatch', 'task', 'run')),
                'draft_predecessor_binding_mismatch')
        inherited, source = profile(previous, ops, visiting | {oid})
        for key in values:
            require(values[key] is None or values[key] == inherited[key], 'draft_profile_conflict')
            values[key] = inherited[key]; origins[key] = source[key]
    require(all(isinstance(v, str) and 0 < len(v.strip()) <= 200 for v in values.values()),
            'draft_explicit_launch_profile_required')
    return values, origins


def selection(data, ops, cfg, project):
    """Check saved metadata only; never admit or read the declared transcript."""
    rows = data['operation_sources']
    require(isinstance(rows, list) and len(rows) <= 100, 'draft_source_limit')
    selected = {}; protected = []
    for row in rows:
        batch.exact(row, ('operation_id', 'source'))
        oid = row['operation_id']; identifier(oid)
        require(oid in ops and oid not in selected, 'draft_source_coverage')
        source = row['source']
        require(isinstance(source, dict), 'draft_invalid_source')
        if source.get('kind') == 'stop':
            batch.exact(source, ('kind',))
        else:
            batch.exact(source, ('kind', 'record_file'))
            require(source['kind'] == 'explicit_source', 'draft_invalid_source')
            value = source['record_file']
            require(isinstance(value, str) and Path(value).is_absolute() and
                    str(Path(value).resolve(strict=True)) == value, 'draft_source_path')
            saved = read(value, project)
            require(isinstance(saved, dict), 'draft_invalid_metadata')
            op = ops[oid]; role = op['plan']['usage_collection']['role']
            require(type(saved.get('schema_version')) is int and saved['schema_version'] == 1 and
                    saved.get('kind') == 'orca_codex_metadata' and saved.get('environment') == 'orca' and
                    saved.get('provider') == 'codex' and saved.get('project') == str(project) and
                    op['plan']['project'] == str(project), 'draft_metadata_identity')
            context = saved.get('request_context')
            require(isinstance(context, dict) and context.get('request_id') == op['request_id'] and
                    context.get('role') == role, 'draft_metadata_context')
            require(saved['verification']['status'] == 'pass', 'draft_metadata_verification')
            usage = saved['dispatch_usage']; validate_usage(usage)
            require(all(saved['binding'][k] == op[k] and usage[k] == op[k]
                        for k in ('task_id', 'dispatch_id')) and
                    usage['actor_id'] == op['dispatch_id'] and usage['request_id'] == op['request_id'] and
                    usage['role'] == role and saved['session']['id'] == usage['session_id'],
                    'draft_metadata_binding')
            declared = saved['source']['path']
            require(isinstance(declared, str) and Path(declared).is_absolute() and
                    str(Path(declared).resolve()) == declared, 'draft_session_path')
            protected.extend((Path(value), Path(declared)))
        selected[oid] = source.copy()
    require(set(selected) == set(ops), 'draft_source_coverage')
    return selected, protected


def build(input_file, output_file):
    data = read(input_file)
    version = data.get('schema_version') if isinstance(data, dict) else None
    batch.exact(data, FIELDS | {'operation_sources'} if type(version) is int and version == 2 else FIELDS)
    require(type(data['schema_version']) is int and data['schema_version'] in (1, 2) and
            type(data['retry_pending']) is bool, 'draft_invalid_version_or_retry')
    for key in ('id', 'request_id'): identifier(data[key])
    require(isinstance(data['expected_reviewer'], str) and 0 < len(data['expected_reviewer'].strip()) <= 200,
            'draft_reviewer_required')
    for key in ('resource_directories', 'coordinator_turns'):
        require(isinstance(data[key], list) and len(data[key]) <= 100, 'draft_input_limit')
    raw_cfg = read(data['hook_config'])
    require(isinstance(raw_cfg, dict), 'draft_invalid_hook_config')
    project = existing(raw_cfg.get('project'), directory=True)
    existing(input_file, project); existing(data['hook_config'], project)
    for key in ('ledger', 'trace_db'): existing(raw_cfg.get(key), project)
    existing(raw_cfg.get('directory'), project, directory=True)
    cfg = hook.load_config(data['hook_config'])
    require(os.environ.get('ORCA_AGENT_SESSION_ID') == cfg['coordinator'], 'draft_native_coordinator_mismatch')
    read(cfg['ledger'], project)
    catalog = recovery.catalog(cfg)
    ops = {k: v['operation'] for k, v in catalog.items() if v['operation']['request_id'] == data['request_id']}
    require(len(ops) <= 100 and all(catalog[k]['ready'] for k in ops), 'draft_operation_limit_or_unready')
    store = Store(cfg['trace_db'], readonly=True)
    try: bundle = store.request(data['request_id'])
    finally: store.close()
    require(bundle is not None, 'draft_request_missing')
    actors = {a['id']: a for a in bundle['actors']}
    roots = [a['id'] for a in actors.values() if a.get('parent_id') is None and a.get('role') == 'coordinator']
    require(len(roots) == 1, 'draft_unique_root_required'); root = roots[0]
    selected, source_paths = selection(data, ops, cfg, project) if version == 2 else ({}, [])
    operations = []; origins = {}
    for oid in sorted(ops):
        op = ops[oid]
        require(receipt_binding(op.get('receipt')) == {k: op[k] for k in ('task_id', 'dispatch_id', 'run_id')}, 'draft_native_binding_mismatch')
        c = op['plan']['usage_collection']; a = actors.get(c['actor_id'], {})
        require(a.get('request_id') == data['request_id'] and a.get('role') == c['role'], 'draft_actor_mismatch')
        values, origins[oid] = profile(oid, ops)
        operations.append(dict(operation_id=oid, expected=dict(values, reviewer=data['expected_reviewer'])))
        if version == 2: operations[-1]['source'] = selected[oid]
    acceptance = existing(data['acceptance_file'], project)
    batch.fingerprint(acceptance, False)
    closeout = read(data['closeout_file'], project)
    require(isinstance(closeout, dict), 'draft_invalid_closeout')
    require(closeout.get('request_id') == data['request_id'] and closeout.get('coordinator_id') == root and
            closeout.get('status') == 'completed' and closeout.get('verification') == 'pass', 'draft_acceptance_required')
    batch.exact(closeout, {'schema_version', 'id', 'request_id', 'coordinator_id', 'time', 'status', 'summary', 'verification', 'assignments', 'artifacts', 'limitations'})
    require(type(closeout['schema_version']) is int and closeout['schema_version'] == 1, 'draft_closeout_version')
    identifier(closeout['id'])
    from trace_store import stamp
    require(stamp(closeout['time']) is not None, 'draft_closeout_time')
    assignments = closeout.get('assignments')
    require(isinstance(assignments, list), 'draft_assignments_required')
    require(all(isinstance(r, dict) for r in assignments), 'draft_invalid_assignment')
    ids = [r.get('actor_id') for r in assignments]
    require(len(set(ids)) == len(ids) and set(ids) == set(actors) - {root}, 'draft_assignment_coverage')
    artifacts = closeout['artifacts']
    require(isinstance(artifacts, list) and all(isinstance(row, dict) for row in artifacts),
            'draft_invalid_artifacts')
    detail = {k: closeout.get(k) for k in ('status', 'summary', 'verification', 'assignments', 'limitations')}
    detail.update(version=1, trace_sha256='0'*64, manifest_sha256='0'*64,
                  artifacts=[dict(r, sha256=r.get('sha256', '0'*64), bytes=r.get('bytes', 0)) for r in artifacts])
    validate_detail(detail)
    protected = [existing(input_file), existing(data['hook_config']), existing(data['closeout_file']), acceptance,
                 Path(cfg['ledger']), Path(cfg['trace_db']), Path(cfg['directory']), project]
    protected += [Path(str(cfg['trace_db']) + s) for s in ('-wal', '-shm', '-journal')]
    protected.append(Path(cfg['ledger']).with_suffix('.lock'))
    protected.extend(batch.path(r['path']) for r in closeout['artifacts'])
    protected.extend(source_paths)
    resource_paths = []; resources = []; directories = set(); count = 0
    for value in data['resource_directories']:
        directory = existing(value, project, True)
        require(directory not in directories, 'draft_duplicate_registry'); directories.add(directory); protected.append(directory)
        files = list(islice(directory.glob('*.resource.json'), 101))
        count += len(files); require(count <= 100, 'draft_resource_limit')
        for p in sorted(files):
            registration = read(p, project)
            require(isinstance(registration, dict), 'draft_invalid_resource_registration')
            rid = p.name.removesuffix('.resource.json')
            record = wr.load_record(wr.record_path(project, directory, rid), project, rid)
            b = record['binding']
            wr.scope(project, *(b[k] for k in ('source', 'allowed_root', 'archive')), p)
            require(record['phase'] in {'created', 'archived', 'prepared', 'removed'}, 'draft_resource_unsettled')
            identifier(b.get('request_id')); identifier(b.get('owner'))
            require(isinstance(b.get('purpose'), str) and bool(b['purpose'].strip()) and
                    isinstance(b.get('authorization'), str) and bool(b['authorization'].strip()) and
                    type(b.get('max_bytes')) is int and b['max_bytes'] > 0, 'draft_resource_binding_invalid')
            if b.get('request_id') != data['request_id']: continue
            require(b.get('owner') in actors, 'draft_resource_owner_unknown')
            require(bool(ops), 'draft_resource_requires_operations')
            protected.extend(Path(b[k]) for k in ('source', 'archive'))
            resource_paths.extend(Path(b[k]) for k in ('source', 'archive'))
            resources.append(dict(directory=str(directory), resource_id=rid, acceptance=str(acceptance), operation_ids=sorted(ops)))
    if version == 2:
        mutable = resource_paths + [Path(cfg['directory'])] + list(directories) + [Path(cfg['ledger']), Path(cfg['trace_db']), Path(data['hook_config'])]
        require(not any(a.is_relative_to(b) or b.is_relative_to(a) for a in source_paths for b in mutable),
                'draft_source_mutable_overlap')
    seen = set()
    for row in data['coordinator_turns']:
        batch.exact(row, ('session_file', 'session_id', 'binding_file', 'task_title'))
        identifier(row['session_id'])
        require(isinstance(row['task_title'], str) and 0 < len(row['task_title'].strip()) <= 200, 'draft_turn_title')
        session = existing(row['session_file']); binding = read(row['binding_file'], project)
        turns.validate_binding(binding)
        require(binding['request_id'] == data['request_id'] and binding['actor_id'] == root, 'draft_turn_scope')
        key = (row['session_id'], binding['native_turn_id']); require(key not in seen, 'draft_duplicate_turn'); seen.add(key)
        protected.extend((session, Path(row['binding_file'])))
    output = batch.path(str(output_file)); require(output.parent.is_dir(), 'draft_output_parent_missing')
    require(not any(output.is_relative_to(p) or p.is_relative_to(output) for p in protected), 'draft_output_overlap')
    private_file(output, project)
    if output.exists(): existing(output, project)
    batch.validate_artifact_locations(closeout, cfg, output, resources)
    manifest = dict(schema_version=version, id=data['id'], project=str(project), request_id=data['request_id'],
                    coordinator_id=root, hook_config=data['hook_config'], operations=operations,
                    coordinator_turns=data['coordinator_turns'], resources=sorted(resources, key=lambda r:(r['directory'], r['resource_id'])),
                    closeout_file=data['closeout_file'], retry_pending=data['retry_pending'])
    raw = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode()
    replay = output.exists()
    if replay:
        require(output.read_bytes() == raw, 'draft_output_conflict')
    else:
        fd, tmp = tempfile.mkstemp(prefix='.draft-', dir=output.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.link(tmp, output, follow_symlinks=False)
        finally: os.unlink(tmp)
    return dict(status='draft', requires_review=True, execution_authorized=False, replay=replay,
                source_counts=dict(operations=len(operations), resources=len(resources), coordinator_turns=len(seen)),
                profile_origins=origins)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True); parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try: result = build(args.input, args.output)
    except (ValueError, OSError, KeyError, TypeError, sqlite3.DatabaseError):
        print(json.dumps(dict(status='error', error='draft_validation_failed'))); return 2
    print(json.dumps(result)); return 0


if __name__ == '__main__':
    raise SystemExit(main())
