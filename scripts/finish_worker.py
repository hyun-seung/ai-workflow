#!/usr/bin/env python3
"""Handle one completion notification once; no watcher, agent launch or approval."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

from lifecycle_io import digest, exited, locked, now, orca, private_file, read_json, save
from native_record import inspect_session, identifier
from startup import executable_for
from harness_policy import load as load_policy


def finish(cli, project, session_file, session_id, task_id, dispatch_id, directory, expected):
    identifier(dispatch_id)
    project = Path(project).resolve()
    # Persist exact input mapping. A second notification cannot silently rebind it.
    binding = dict(project=str(project), session_file=str(Path(session_file).resolve()),
                   session_id=session_id, task_id=task_id, dispatch_id=dispatch_id, expected=expected)
    state_path = private_file(Path(directory) / (dispatch_id + '.completion.json'), project)
    with locked(state_path.with_suffix('.lock'), project):
        if state_path.exists():
            previous = read_json(state_path)
            if previous.get('binding') != binding:
                raise ValueError('completion_binding_conflict')
            record_path = Path(previous['record_path'])
            private_file(record_path, project)
            if digest(read_json(record_path)) != previous['record_digest']:
                raise ValueError('completion_record_changed')
            return dict(previous, replay=True)
        snapshot = orca(cli, 'orchestration', 'worker-show', '--dispatch', dispatch_id, '--full')
        exited(snapshot, task_id, dispatch_id)
        snapshot_path = state_path.with_name(dispatch_id + '.worker.json')
        record_path = state_path.with_name(dispatch_id + '.metadata.json')
        # A crash before the completion receipt is safe to retry under the same lock.
        save(snapshot_path, snapshot, project)
        record = inspect_session(project, session_file, session_id, task_id, dispatch_id,
                                 expected, snapshot_path)
        save(record_path, record, project)
        state = dict(version=1, binding=binding, completed_at=now(), record_path=str(record_path),
                     record_digest=digest(record), verification=record['verification']['status'],
                     usage_status=record['usage']['status'], worker_exited=True,
                     task_acceptance_evaluated=False)
        save(state_path, state, project)
        return dict(state, replay=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('project', 'session-file', 'session-id', 'task-id', 'dispatch-id', 'directory'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--orca-cli')
    parser.add_argument('--expect-reviewer')
    parser.add_argument('--expect-model')
    parser.add_argument('--expect-effort')
    parser.add_argument('--cleanup-plan', help='Optional previously authorized, archive-verified cleanup plan')
    parser.add_argument('--config', help='Explicit harness policy override file')
    parser.add_argument('--policy-project', help='Existing policy project; defaults to --project. Use a retained coordinator project for temporary sources.')
    parser.add_argument('--auto-cleanup', choices=('Y', 'N'), help='Override automatic cleanup (default policy: Y)')
    parser.add_argument('--dispatch-directory', help='Managed delegation ledger to release after confirmed worker exit')
    parser.add_argument('--operation-id')
    args = parser.parse_args(argv)
    try:
        policy = load_policy(args.policy_project or args.project, args.config, args.auto_cleanup)
        if bool(args.dispatch_directory) != bool(args.operation_id):
            raise ValueError('dispatch_directory_and_operation_id_required_together')
        cli = args.orca_cli or executable_for('orca', os.environ)
        expected = {k: getattr(args, 'expect_' + k) for k in ('reviewer', 'model', 'effort')
                    if getattr(args, 'expect_' + k) is not None}
        result = finish(cli, args.project, args.session_file, args.session_id, args.task_id,
                        args.dispatch_id, args.directory, expected)
        print('completion=' + ('replay' if result['replay'] else 'recorded') +
              ' verification=' + result['verification'])
        if args.dispatch_directory:
            from dispatch_guard import Ledger
            ledger=Ledger(args.dispatch_directory,args.project,os.environ.get('ORCA_AGENT_SESSION_ID'))
            operation=ledger.read()['operations'][args.operation_id]
            if (operation.get('task_id'),operation.get('dispatch_id')) != (args.task_id,args.dispatch_id):
                raise ValueError('completion_operation_binding_mismatch')
            print('delegation=' + ledger.refresh(args.operation_id,cli)['state'])
        if result['verification'] in ('fail', 'unverified'):
            return 1
        if args.cleanup_plan and policy['auto_cleanup'] == 'N':
            print('cleanup=skipped_disabled')
        elif args.cleanup_plan:
            from workspace_cleanup import apply
            plan = read_json(args.cleanup_plan)
            if {'task_id': args.task_id, 'dispatch_id': args.dispatch_id} not in plan['workers']:
                raise ValueError('cleanup_plan_not_for_this_dispatch')
            print('cleanup=' + apply(args.cleanup_plan, cli)['phase'])
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        print('Completion failed: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
