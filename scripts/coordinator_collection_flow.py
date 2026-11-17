#!/usr/bin/env python3
"""Review-gated collection of one explicitly selected closed coordinator turn."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

import draft_coordinator_binding as draft
import collect_request_usage as collector
from completion_hook import load_config
from lifecycle_io import private_file, locked
from native_turn_record import load_binding, inspect_turn

EXTRA = {'hook_config', 'task_title', 'collection_id', 'collection_directory'}
COVERAGE = dict(scope='selected_coordinator_turn_only', stop_delivery_inferred=False,
                full_usage_complete=False, unmeasured=['current_open_turn', 'unselected_turns',
                'worker_usage', 'tool_level_tokens'], excluded=['Stop_delivery', 'native_finish',
                'request_completion', 'semantic_work_classification'])


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_exact(file, raw, project):
    """Exclusive new file or exact replay; never replace an existing artifact."""
    private_file(file, project)
    if file.exists():
        draft.path(str(file))
        draft.require(load_binding(file)[1] == raw)
        return
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def prepare(input_file, directory):
    recipe = draft.path(str(input_file))
    _, raw, data = load_binding(recipe)
    draft.require(isinstance(data, dict) and set(data) == draft.FIELDS | EXTRA)
    draft.require(type(data['schema_version']) is int and data['schema_version'] == 1)
    for k in ('request_id', 'actor_id', 'task_id', 'session_id', 'native_turn_id', 'collection_id'):
        draft.identifier(data[k])
    draft.require(data['scope'] == 'single_work' and isinstance(data['task_title'], str) and
                  1 <= len(data['task_title'].strip()) <= 200)
    project = draft.path(data['project'], True)
    private_file(recipe, project)
    hook = draft.path(data['hook_config']); private_file(hook, project)
    config = load_config(hook)
    draft.require(config['project'] == data['project'] and config['trace_db'] == data['trace_db'])
    _, config_raw, _ = load_binding(hook)
    directory = collector.path(str(directory))
    target = collector.path(data['collection_directory'])
    inputs = [recipe, hook] + [draft.path(data[k]) for k in ('trace_db', 'session_file')]
    inputs.append(draft.path(config['ledger']))
    # Keep collector output outside every input root, including preparation artifacts.
    draft.require(not any(target == p.parent or target.is_relative_to(p.parent) for p in inputs))
    draft.require(not (target == directory or target.is_relative_to(directory) or directory.is_relative_to(target)))
    draft.require(not any(directory == p.parent or directory.is_relative_to(p.parent) for p in inputs))
    private_file(directory / 'review.json', project)
    private_file(target / 'probe.json', project)
    with locked(directory / 'prepare.lock', project):
        replay = (directory / 'review.json').exists()
        small = {k: data[k] for k in draft.FIELDS}
        write_exact(directory / 'draft-recipe.json', canonical(small), project)
        result, code = draft.build(str(directory / 'draft-recipe.json'), str(directory / 'binding.json'))
        if code:
            return result, code
        binding_path = directory / 'binding.json'
        _, binding_raw, binding = load_binding(binding_path)
        record = inspect_turn(project, data['session_file'], data['session_id'], binding)
        draft.require(record['turn_usage']['status'] == 'measured')
        manifest = dict(schema_version=1, id=data['collection_id'], hook_config=str(hook),
            request_id=data['request_id'], coordinator_id=data['actor_id'], worker_sources=[],
            coordinator_turns=[dict(session_file=data['session_file'], session_id=data['session_id'],
                                   binding_file=str(binding_path), task_title=data['task_title'])])
        manifest_raw = canonical(manifest)
        write_exact(directory / 'manifest.json', manifest_raw, project)
        review = dict(schema_version=1, status='prepared', requires_review=True,
            execution_authorized=False, recipe=data, recipe_sha256=sha(raw),
            config=config, config_sha256=sha(config_raw), binding=binding,
            binding_sha256=sha(binding_raw), manifest=manifest, manifest_sha256=sha(manifest_raw),
            source_sha256=record['source']['sha256'], turn_usage=record['turn_usage'], coverage=COVERAGE)
        # No digest field inside review: approval hashes the entire canonical JSON value.
        encoded = canonical(review)
        draft.require(load_binding(recipe)[1] == raw and load_binding(hook)[1] == config_raw)
        write_exact(directory / 'review.json', encoded, project)
        return dict(status='prepared', requires_review=True, execution_authorized=False,
                    approve_sha256=sha(encoded), replay=replay, coverage=COVERAGE), 0


def run_import(input_file, directory, approve_sha256, cli):
    draft.require(isinstance(approve_sha256, str) and re.fullmatch('[0-9a-f]{64}', approve_sha256))
    review_file = collector.path(str(directory)) / 'review.json'
    draft.path(str(review_file))
    _, _, reviewed = load_binding(review_file)
    draft.require(sha(canonical(reviewed)) == approve_sha256)
    # Rebuild against today's recipe, config, root and source. Changed artifacts are refused.
    result, code = prepare(input_file, directory)
    draft.require(code == 0 and result['approve_sha256'] == approve_sha256)
    data = reviewed['recipe']
    collected = collector.collect(str(Path(directory) / 'manifest.json'),
                                  data['collection_directory'], cli)
    return dict(**collected, coverage=COVERAGE)


def main(argv=None):
    parser = draft.Parser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'import'])
    parser.add_argument('--input', required=True)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--approve-sha256')
    parser.add_argument('--orca-cli')
    try:
        args = parser.parse_args(argv)
        if args.mode == 'prepare':
            draft.require(args.approve_sha256 is None and args.orca_cli is None)
            result, code = prepare(args.input, args.directory)
        else:
            draft.require(bool(args.orca_cli))
            result, code = run_import(args.input, args.directory, args.approve_sha256, args.orca_cli), 0
    except (ValueError, OSError, KeyError, TypeError, RecursionError, UnicodeError, sqlite3.Error):
        result, code = dict(status='error', error='coordinator_collection_flow_failed'), 2
    print(json.dumps(result, ensure_ascii=False)); return code


if __name__ == '__main__':
    raise SystemExit(main())
