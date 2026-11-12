#!/usr/bin/env python3
"""Reserve a bounded local Orca assignment before making one native call."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from harness_policy import load as load_policy
from lifecycle_io import digest, exited, locked, now, orca, private_file, read_json, save
from native_record import identifier, unique_object, reject_constant
from startup import executable_for

ACTIVE = {'launching', 'unknown', 'not_started', 'bound'}


class LimitReached(ValueError):
    pass


def denied_before_start(receipt, returncode):
    if (not isinstance(receipt, dict) or receipt.get('ok') is not False or
            type(returncode) is not int or returncode == 0 or
            set(receipt) - {'id', 'ok', 'error', '_meta'}):
        return False
    error = receipt.get('error')
    return isinstance(error, dict) and error.get('code') == 'runtime_access_denied'


def lineage(operations, operation_id):
    seen = set()
    while operation_id not in seen:
        seen.add(operation_id)
        previous = operations[operation_id]['plan'].get('previous')
        if not previous:
            return operation_id
        operation_id = previous
    raise ValueError('operation_lineage_cycle')


class Ledger:
    def __init__(self, directory, project, coordinator):
        identifier(coordinator)
        self.project = Path(project).resolve()
        try:
            self.path = private_file(Path(directory) / 'dispatches.json', self.project)
        except FileExistsError:
            # Concurrent first-use directory creation: revalidate the existing path.
            self.path = private_file(Path(directory) / 'dispatches.json', self.project)
        self.coordinator = coordinator

    def read(self):
        if not self.path.exists():
            return {'version': 1, 'coordinator': self.coordinator, 'requests': {}, 'operations': {}}
        data = read_json(self.path)
        if data.get('version') != 1 or data.get('coordinator') != self.coordinator:
            raise ValueError('delegation_ledger_scope_mismatch')
        return data

    def lock(self):
        return locked(self.path.with_suffix('.lock'), self.project)

    def write(self, data):
        save(self.path, data, self.project)

    def reserve(self, request_id, operation_id, plan, limits):
        identifier(request_id); identifier(operation_id)
        if set(limits) != {'max_active_workers', 'max_dispatches_per_request'} or any(type(v) is not int or v < 1 for v in limits.values()):
            raise ValueError('explicit_positive_delegation_limits_required')
        with self.lock():
            data = self.read()
            known = data['requests'].get(request_id)
            if known and known != limits:
                raise ValueError('request_limits_changed_replan_explicitly')
            old = data['operations'].get(operation_id)
            if old:
                if old['request_id'] != request_id or old['plan'] != plan:
                    raise ValueError('operation_binding_conflict')
                if old['state'] == 'not_started':
                    if not denied_before_start(old.get('receipt'), old.get('returncode')):
                        old['state'] = 'unknown'
                        self.write(data)
                        return old, False
                    old['state'] = 'launching'
                    old['updated_at'] = now()
                    self.write(data)
                    return old, True
                return old, False
            active = [o for o in data['operations'].values() if o['state'] in ACTIVE]
            # Concurrent requests share a coordinator ledger. The strictest live
            # request's cap remains in force; a new request cannot raise it.
            cap = min([limits['max_active_workers']] + [data['requests'][o['request_id']]['max_active_workers'] for o in active])
            if len(active) >= cap:
                raise LimitReached('max_active_workers_reached')
            used = sum(o['request_id'] == request_id for o in data['operations'].values())
            if used >= limits['max_dispatches_per_request']:
                raise LimitReached('max_dispatches_per_request_reached')
            for dependency in plan['depends_on']:
                dep = data['operations'].get(dependency)
                if not dep or dep['state'] != 'released' or dep.get('outcome') != 'succeeded':
                    raise LimitReached('dependency_not_successfully_released')
            for current in active:
                if any(Path(a).is_relative_to(Path(b)) or Path(b).is_relative_to(Path(a))
                       for a in plan['writes'] for b in current['plan']['writes']):
                    raise LimitReached('write_scope_owned_by_active_worker')
            if plan['action'] in ('followup', 'retry'):
                previous = data['operations'].get(plan['previous'])
                if not previous or previous['request_id'] != request_id or previous['state'] != 'released':
                    raise LimitReached('predecessor_exit_not_confirmed')
                if plan['action'] == 'retry' and previous.get('outcome') != 'failed':
                    raise ValueError('retry_requires_failed_predecessor')
                if previous.get('dispatch_id') != plan['previous_dispatch']:
                    raise ValueError('predecessor_binding_changed')
                origin = lineage(data['operations'], plan['previous'])
                if any(o['state'] in ACTIVE and lineage(data['operations'], key) == origin
                       for key,o in data['operations'].items()):
                    raise LimitReached('predecessor_lineage_already_owned')
            data['requests'][request_id] = dict(limits)
            operation = dict(request_id=request_id, plan=plan, state='launching', created_at=now(), updated_at=now(),
                             task_id=None, dispatch_id=None, run_id=None, outcome=None, receipt=None)
            data['operations'][operation_id] = operation
            self.write(data)
            return operation, True

    def capture(self, operation_id, receipt, returncode):
        with self.lock():
            data = self.read(); operation = data['operations'][operation_id]
            if operation['state'] != 'launching':
                raise ValueError('operation_not_awaiting_receipt')
            operation.update(receipt=receipt, returncode=returncode, updated_at=now(), state='unknown')
            if denied_before_start(receipt, returncode):
                operation['state'] = 'not_started'
            else:
                binding = receipt_binding(receipt)
                if binding:
                    if any(o is not operation and o.get('dispatch_id') == binding['dispatch_id'] for o in data['operations'].values()):
                        self.write(data)
                        raise ValueError('native_dispatch_already_accounted')
                    operation.update(binding, state='bound')
            self.write(data)
            return operation

    def refresh(self, operation_id, cli):
        with self.lock():
            data = self.read(); operation = data['operations'][operation_id]
            if operation['state'] == 'released':
                return operation
            if not operation.get('dispatch_id'):
                raise ValueError('unknown_start_requires_native_receipt_inspection')
            binding = (operation['task_id'], operation['dispatch_id'])
        snapshot = orca(cli, 'orchestration', 'worker-show', '--dispatch', binding[1], '--full')
        try:
            exited(snapshot, *binding)
        except ValueError as error:
            if str(error) != 'worker_exit_unconfirmed':
                raise
            # Approval waits, completed-but-live and unknown liveness retain a slot.
            return dict(operation, release='unconfirmed')
        with self.lock():
            data = self.read(); current = data['operations'][operation_id]
            if (current['task_id'], current['dispatch_id']) != binding:
                raise ValueError('operation_changed_during_refresh')
            current.update(state='released', outcome=snapshot['result']['worker']['state'],
                           exit_evidence={'observed_at': now(), 'dispatch_id': binding[1], 'exact_worker': True}, updated_at=now())
            self.write(data)
            return current


def receipt_binding(receipt):
    if (not isinstance(receipt, dict) or receipt.get('ok') is not True or
            receipt.get('error') is not None or not isinstance(receipt.get('result'), dict)):
        return None
    result = receipt['result']
    # Known concise worker-start and reopened send receipts. Unknown shapes retain the slot.
    if not all(isinstance(result.get(k), str) for k in ('taskId', 'dispatchId', 'runId')):
        return None
    return {'task_id': identifier(result['taskId']), 'dispatch_id': identifier(result['dispatchId']),
            'run_id': identifier(result['runId'])}


def invoke(cli, plan, text):
    if plan['action'] == 'followup':
        args = ['orchestration', 'send', '--to', 'dispatch:' + plan['previous_dispatch'],
                '--subject', plan['subject'], '--body', text]
        stdin = None
    else:
        args = ['orchestration', 'worker-start', '--worktree', plan['worktree'], '--agent', plan['agent']]
        if plan['action'] == 'retry':
            args += ['--task', plan['previous_task'], '--retry-of', plan['previous_dispatch'], '--run', plan['previous_run']]
            stdin = None
        else:
            args += ['--spec-file', '-']
            stdin = text
        for key in ('model', 'effort'):
            if plan[key]: args += ['--' + key, plan[key]]
    result = subprocess.run([str(cli), *args, '--environment', 'local', '--json'], input=stdin,
                            cwd=plan['project'], capture_output=True, text=True, timeout=90)
    try:
        if len(result.stdout) > 8*1024*1024: raise ValueError('oversized_native_receipt')
        receipt = json.loads(result.stdout, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except ValueError:
        receipt = {'error': {'code': 'unparseable_native_receipt'}}
    return receipt, result.returncode


def launch(ledger, request_id, operation_id, plan, text, limits, cli):
    operation, fresh = ledger.reserve(request_id, operation_id, plan, limits)
    if not fresh:
        return dict(operation, replay=True)
    try:
        receipt, code = invoke(cli, plan, text)
    except (OSError, subprocess.TimeoutExpired):
        receipt, code = {'error': {'code': 'transport_outcome_unknown'}}, None
    return dict(ledger.capture(operation_id, receipt, code), replay=False)


def make_plan(args, ledger):
    project = Path(args.project).resolve(strict=True)
    if not project.is_dir(): raise ValueError('project_not_directory')
    text = ''
    if args.command != 'retry':
        with Path(args.spec_file).open('rb') as stream: raw = stream.read(65537)
        if not raw.strip() or len(raw) > 65536: raise ValueError('spec_must_be_nonempty_at_most_64KiB')
        text = raw.decode('utf-8')
    writes = []
    for name in args.writes:
        p = (project / name).resolve()
        if not p.is_relative_to(project): raise ValueError('write_scope_outside_project')
        writes.append(str(p))
    if args.effort and not args.model: raise ValueError('effort_requires_explicit_model')
    if args.worktree in ('new-child','new-top-level'):
        raise ValueError('guard_requires_existing_worktree')
    plan = dict(action=args.command, project=str(project), worktree=args.worktree, agent=args.agent,
                model=args.model, effort=args.effort, spec_digest=digest(text), writes=sorted(set(writes)),
                depends_on=sorted(set(args.depends_on)), previous=None, subject=args.subject)
    usage_fields = tuple(getattr(args, key, None) for key in ('usage_role','usage_actor_id','usage_task_title'))
    if any(value is not None for value in usage_fields):
        from completion_hook import validate_collection
        collection = validate_collection(dict(zip(('role','actor_id','task_title'),usage_fields)))
        if args.agent != 'codex': raise ValueError('usage_hook_requires_codex')
        plan['usage_collection'] = collection
    if args.command in ('followup','retry'):
        old=ledger.read()['operations'].get(args.previous)
        if not old or not old.get('dispatch_id'): raise ValueError('predecessor_unknown')
        plan.update(previous=args.previous, previous_dispatch=old['dispatch_id'], previous_task=old['task_id'], previous_run=old['run_id'])
        if args.command == 'followup' and (args.model or args.effort):
            raise ValueError('followup_preserves_native_profile')
    return plan, text


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',type=Path,required=True)
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--config')
    p.add_argument('--orca-cli')
    sub=p.add_subparsers(dest='command',required=True)
    for command in ('start','followup','retry'):
        q=sub.add_parser(command)
        for key in ('request-id','operation-id'):q.add_argument('--'+key,required=True)
        q.add_argument('--max-active-workers',type=int)
        q.add_argument('--max-dispatches-per-request',type=int)
        q.add_argument('--spec-file',required=command!='retry')
        q.add_argument('--previous',required=command!='start')
        q.add_argument('--worktree',default='current')
        q.add_argument('--agent',default='codex')
        q.add_argument('--model');q.add_argument('--effort')
        q.add_argument('--usage-role');q.add_argument('--usage-actor-id');q.add_argument('--usage-task-title')
        q.add_argument('--subject',default='Follow-up assignment')
        q.add_argument('--writes',action='append',default=[])
        q.add_argument('--depends-on',action='append',default=[])
    q=sub.add_parser('refresh');q.add_argument('--operation-id',required=True)
    sub.add_parser('status')
    args=p.parse_args(argv)
    try:
        coordinator=os.environ.get('ORCA_AGENT_SESSION_ID')
        if not coordinator:raise ValueError('orca_coordinator_session_required')
        ledger=Ledger(args.directory,args.project,coordinator)
        cli=args.orca_cli or executable_for('orca',os.environ)
        if args.command=='status':
            data=ledger.read()
            print(json.dumps({'requests':data['requests'],'active':sum(o['state'] in ACTIVE for o in data['operations'].values()),
                              'operations':{k:{f:o.get(f) for f in ('request_id','state','task_id','dispatch_id','run_id','outcome')} for k,o in data['operations'].items()}}))
            return 0
        if args.command=='refresh':
            op=ledger.refresh(args.operation_id,cli)
        else:
            limits={k:getattr(args,k) for k in ('max_active_workers','max_dispatches_per_request') if getattr(args,k) is not None}
            policy=load_policy(args.project,args.config,limits=limits)
            remembered=ledger.read()['requests'].get(args.request_id,{})
            policy['limits']={k:(v if v is not None else remembered.get(k)) for k,v in policy['limits'].items()}
            plan,text=make_plan(args,ledger)
            op=launch(ledger,args.request_id,args.operation_id,plan,text,policy['limits'],cli)
        print(json.dumps({k:op.get(k) for k in ('state','task_id','dispatch_id','run_id','outcome','replay','release')},ensure_ascii=False))
        if op.get('receipt') and not op.get('replay'):
            # Preserve native next/recovery instructions; no hidden retries.
            print(json.dumps(op['receipt'],ensure_ascii=False))
        return 0 if op['state']=='released' or (op['state']=='bound' and op.get('returncode')==0) else 2
    except LimitReached as error:
        print('Delegation deferred: '+str(error),file=sys.stderr);return 3
    except (OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired) as error:
        print('Delegation stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__),file=sys.stderr);return 2


if __name__=='__main__':
    raise SystemExit(main())
