"""Opt-in binding of one accepted worker selection to one managed dispatch operation."""

import copy
from pathlib import Path

from lifecycle_io import digest, locked, private_file, read_json, save
from request_router import load_catalog, route
from routing_choices import CONTEXT_KEYS, MAX_BYTES, ROOT, validate


def prepare(state, reference, coordinator, request_id, plan, text):
    validate(state)
    if (state['project'] != plan['project'] or state['session_id'] != coordinator or
            state['request_id'] != request_id or state['revision'] != reference['revision']):
        raise ValueError('routing_choice_scope_changed')
    pid = reference['proposal_id']
    item = state['proposals'].get(pid)
    if (state['selection'] != pid or item is None or item['status'] != 'accepted' or
            item['version'] != reference['proposal_version'] or item['revision'] != state['revision']):
        raise ValueError('routing_choice_not_current_accepted_selection')
    target = item['target']
    if target['execution'] != 'worker':
        raise ValueError('routing_selection_does_not_authorize_worker')
    if plan['action'] != 'start' or plan['agent'] != 'codex':
        raise ValueError('routing_binding_supports_new_codex_worker_only')
    if plan['worktree'] != 'current':
        raise ValueError('routing_binding_requires_current_worktree')
    if any(target[key] is None or plan[key] != target[key] for key in ('model', 'effort')):
        raise ValueError('routing_worker_requires_exact_explicit_model_and_effort')
    check = route(dict({key: state[key] for key in CONTEXT_KEYS - {'project'}}, version=1,
                       analysis={'recommendation': dict(target, reason='선택한 위임의 실행 전 정책 검사')}),
                  lambda: load_catalog(Path(plan['project'])))
    if check['action'] != 'propose':
        raise ValueError('routing_choice_no_longer_valid')
    collection = plan.get('usage_collection')
    if collection and collection['role'] != target['role']:
        raise ValueError('routing_usage_role_mismatch')
    result = copy.deepcopy(plan)
    result['routing_choice'] = dict(reference, target=target)
    from workflow import load_config
    config = load_config(ROOT, Path(plan['project']))
    parts = ['# ai-workflow instructions',
             'Read these common instructions before the assignment, together with the target project and ancestor instructions. '
             'Report unavailable references instead of silently omitting them.']
    parts += ['- ' + str(path) for path in config['instructions']]
    if target['role'] is not None:
        parts.append('# Selected ai-workflow role: ' + target['role'])
        remaining = MAX_BYTES
        for path in config['roles'][target['role']]['instructions']:
            with path.open(encoding='utf-8') as stream:
                content = stream.read(remaining + 1)
            if len(content) > remaining:
                raise ValueError('routing_selected_instructions_too_large')
            remaining -= len(content)
            parts += ['Source: ' + str(path) + '\nResolve relative references against this source file.', content]
    parts += ['# Assignment', text]
    text = '\n\n'.join(parts)
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('routing_combined_spec_too_large')
    result['spec_digest'] = digest(text)
    return result, text


def launch_selected(args, ledger, plan, text, limits, cli, launch):
    values = (args.routing_choice_file, args.routing_revision, args.routing_proposal, args.routing_proposal_version)
    if any(value is None for value in values):
        raise ValueError('routing_binding_requires_file_revision_proposal_and_version')
    if any(type(value) is not int or value < 1 for value in (args.routing_revision, args.routing_proposal_version)):
        raise ValueError('routing_binding_requires_positive_versions')
    path = private_file(args.routing_choice_file, ledger.project)
    if path == ledger.path or path.with_suffix('.lock') == ledger.path.with_suffix('.lock'):
        raise ValueError('routing_choice_and_dispatch_ledger_must_be_separate')
    reference = dict(file=str(path), revision=args.routing_revision, proposal_id=args.routing_proposal,
                     proposal_version=args.routing_proposal_version)
    # A chooser cannot mutate this selection between its check and native submission.
    with locked(path.with_suffix('.lock'), ledger.project):
        state = validate(read_json(path))
        plan, text = prepare(state, reference, ledger.coordinator, args.request_id, plan, text)
        binding = dict(ledger=str(ledger.path), operation_id=args.operation_id, plan_digest=digest(plan))
        item = state['proposals'][reference['proposal_id']]
        if item.get('dispatch_binding') is not None and item['dispatch_binding'] != binding:
            raise ValueError('routing_choice_already_bound_to_another_operation')
        updated = copy.deepcopy(state)
        updated['proposals'][reference['proposal_id']]['dispatch_binding'] = binding
        validate(updated)

        def claim():
            if updated != state:
                save(path, updated, ledger.project)

        # The native invocation is still owned by dispatch_guard. Its reservation,
        # limits, replay and ambiguous-outcome handling remain in force.
        return launch(ledger, args.request_id, args.operation_id, plan, text, limits, cli, before_invoke=claim)


def completion_expectations(operation, expected, request_id=None, role=None):
    reference = operation['plan'].get('routing_choice')
    if reference is None:
        return expected
    target = reference['target']
    if request_id is not None and request_id != operation['request_id']:
        raise ValueError('routing_completion_request_mismatch')
    if role is not None and role != target['role']:
        raise ValueError('routing_completion_role_mismatch')
    for key in ('model', 'effort'):
        if key in expected and expected[key] != target[key]:
            raise ValueError('routing_completion_expectation_mismatch')
    return dict(expected, model=target['model'], effort=target['effort'])


def compare_profiles(operation, record):
    """Compare collected facts to the selection; never fill a missing actual value."""
    wanted = operation['plan']['routing_choice']['target']
    checks = {}
    for stage in ('requested', 'launch_effective', 'observed_contexts'):
        rows = record.get(stage)
        if stage != 'observed_contexts':
            rows = [rows] if isinstance(rows, dict) and rows.get('status') == 'observed' else []
        else:
            rows = [row.get('values', {}) for row in rows] if isinstance(rows, list) else []
        for key in ('model', 'effort'):
            values = [row.get(key) for row in rows]
            checks[stage + '.' + key] = ('fail' if any(v is not None and v != wanted[key] for v in values)
                                        else 'unverified' if not values or None in values else 'pass')
    status = 'fail' if 'fail' in checks.values() else 'unverified' if 'unverified' in checks.values() else 'pass'
    return dict(status=status, checks=checks, runtime_changed=False)
