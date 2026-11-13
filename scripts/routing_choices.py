#!/usr/bin/env python3
"""Record explicit request-scoped choices. No natural-language parsing or runtime execution."""

import argparse
import copy
import json
from pathlib import Path
import sys

from lifecycle_io import locked, private_file, read_json, save
from request_router import ROOT, controls, identifier, load_catalog, obj, profile, route, unique, reject_constant

MAX_BYTES = 65536
MAX_PROPOSALS = 64
CONTEXT_KEYS = {'project', 'session_id', 'request_id', 'revision', 'current', 'controls'}
ROUTE_KEYS = CONTEXT_KEYS - {'project'}


def revision(value):
    if type(value) is not int or value < 1:
        raise ValueError('invalid_revision')
    return value


def context(value):
    obj(value, CONTEXT_KEYS, 'choice_context')
    if set(value) != CONTEXT_KEYS:
        raise ValueError('incomplete_context')
    if not isinstance(value['project'], str) or not Path(value['project']).is_absolute():
        raise ValueError('absolute_project_required')
    return dict(project=str(Path(value['project']).resolve()), session_id=identifier(value['session_id']), request_id=identifier(value['request_id']),
                revision=revision(value['revision']), current=profile(value['current']), controls=controls(value['controls']))


def validate(state):
    obj(state, CONTEXT_KEYS | {'version', 'sequence', 'proposals', 'selection'}, 'choice_state')
    if set(state) != CONTEXT_KEYS | {'version', 'sequence', 'proposals', 'selection'} or type(state['version']) is not int or state['version'] != 1:
        raise ValueError('invalid_choice_version')
    normalized = context({key: state[key] for key in CONTEXT_KEYS})
    if any(normalized[key] != state[key] for key in CONTEXT_KEYS):
        raise ValueError('noncanonical_choice_context')
    if type(state['sequence']) is not int or not 0 <= state['sequence'] <= MAX_PROPOSALS:
        raise ValueError('invalid_choice_sequence')
    proposals = state['proposals']
    if not isinstance(proposals, dict) or len(proposals) != state['sequence']:
        raise ValueError('invalid_proposal_history')
    versions = set()
    for key, item in proposals.items():
        identifier(key)
        required = {'version', 'revision', 'target', 'status'}
        obj(item, required | {'dispatch_binding'}, 'stored_proposal')
        if not required <= set(item):
            raise ValueError('incomplete_proposal')
        if 'dispatch_binding' in item:
            binding = obj(item['dispatch_binding'], {'ledger', 'operation_id', 'plan_digest'}, 'dispatch_binding')
            if set(binding) != {'ledger', 'operation_id', 'plan_digest'}:
                raise ValueError('incomplete_dispatch_binding')
            if not isinstance(binding['ledger'], str) or not Path(binding['ledger']).is_absolute():
                raise ValueError('invalid_dispatch_ledger')
            identifier(binding['operation_id'])
            if not isinstance(binding['plan_digest'], str) or len(binding['plan_digest']) != 64 or any(c not in '0123456789abcdef' for c in binding['plan_digest']):
                raise ValueError('invalid_dispatch_digest')
            if item['status'] not in ('accepted', 'cancelled', 'expired'):
                raise ValueError('unaccepted_dispatch_binding')
        versions.add(revision(item['version']))
        if revision(item['revision']) > state['revision']:
            raise ValueError('future_proposal')
        if profile(item['target']) != item['target']:
            raise ValueError('incomplete_target')
        if item['status'] not in ('pending', 'accepted', 'rejected', 'cancelled', 'expired'):
            raise ValueError('invalid_choice_status')
    if versions != set(range(1, state['sequence'] + 1)):
        raise ValueError('invalid_proposal_versions')
    if sum(p['status'] == 'pending' for p in proposals.values()) > 1:
        raise ValueError('ambiguous_pending_proposal')
    if any(p['status'] in ('pending', 'accepted') and p['revision'] != state['revision'] for p in proposals.values()):
        raise ValueError('stale_live_proposal')
    accepted = [key for key, item in proposals.items() if item['status'] == 'accepted']
    if accepted != ([] if state['selection'] is None else [state['selection']]):
        raise ValueError('inconsistent_accepted_selection')
    if state['selection'] is not None:
        selected = proposals.get(state['selection'])
        if not selected or selected['status'] != 'accepted' or selected['revision'] != state['revision']:
            raise ValueError('invalid_selection')
    if len(json.dumps(state).encode()) > MAX_BYTES:
        raise ValueError('choice_state_too_large')
    return state


def operate(existing, action, payload, catalog_loader=load_catalog):
    state = copy.deepcopy(existing)
    if action == 'init':
        if state is not None:
            raise ValueError('choice_state_already_exists')
        return validate(dict(context(payload), version=1, sequence=0, proposals={}, selection=None)), {'replay': False}
    if state is None:
        raise ValueError('choice_state_missing')
    validate(state)
    if action == 'advance':
        updated = context(payload)
        if any(updated[key] != state[key] for key in ('project', 'session_id', 'request_id')):
            raise ValueError('new_request_requires_new_state_file')
        if all(updated[key] == state[key] for key in CONTEXT_KEYS):
            return state, {'replay': True}
        if updated['revision'] <= state['revision']:
            raise ValueError('context_change_requires_new_revision')
        state.update(updated)
        for item in state['proposals'].values():
            if item['status'] in ('pending', 'accepted'):
                item['status'] = 'expired'
        state['selection'] = None
    elif action == 'offer':
        obj(payload, {'proposal_id', 'analysis'}, 'offer')
        pid = identifier(payload.get('proposal_id'))
        result = route(dict({key: state[key] for key in ROUTE_KEYS}, version=1,
                            analysis=payload.get('analysis')), catalog_loader)
        if result['action'] != 'propose':
            return state, {'routing': result, 'replay': False}
        previous = state['proposals'].get(pid)
        if previous is not None:
            if previous['target'] != result['profile'] or previous['revision'] != state['revision']:
                raise ValueError('proposal_id_conflict')
            return state, {'routing': result, 'replay': True}
        if state['sequence'] >= MAX_PROPOSALS:
            raise ValueError('proposal_limit_reached')
        for item in state['proposals'].values():
            if item['status'] == 'pending':
                item['status'] = 'expired'
        state['sequence'] += 1
        state['proposals'][pid] = dict(version=state['sequence'], revision=state['revision'],
                                       target=result['profile'], status='pending')
        return validate(state), {'routing': result, 'replay': False}
    elif action == 'choose':
        required = {'session_id', 'request_id', 'revision', 'proposal_id', 'proposal_version', 'choice'}
        obj(payload, required, 'choice')
        if set(payload) != required:
            raise ValueError('incomplete_choice')
        if any(payload[key] != state[key] for key in ('session_id', 'request_id')) or revision(payload['revision']) != state['revision']:
            raise ValueError('stale_choice_context')
        pid = identifier(payload['proposal_id'])
        item = state['proposals'].get(pid)
        if not item or revision(payload['proposal_version']) != item['version'] or item['revision'] != state['revision']:
            raise ValueError('stale_choice_proposal')
        desired = {'accept': 'accepted', 'reject': 'rejected', 'cancel': 'cancelled'}.get(payload['choice'])
        if desired is None:
            raise ValueError('explicit_choice_required')
        if item['status'] == desired:
            return state, {'replay': True}
        if item['status'] != 'pending' and not (item['status'] == 'accepted' and desired == 'cancelled'):
            raise ValueError('proposal_is_terminal')
        if desired == 'accepted':
            check = route(dict({key: state[key] for key in ROUTE_KEYS}, version=1,
                               analysis={'recommendation': dict(item['target'], reason='명시 선택의 정책 재검사')}), catalog_loader)
            if check['action'] != 'propose':
                raise ValueError('choice_no_longer_valid')
            if state['selection'] is not None:
                state['proposals'][state['selection']]['status'] = 'expired'
            state['selection'] = pid
        elif state['selection'] == pid:
            state['selection'] = None
        item['status'] = desired
    else:
        raise ValueError('invalid_choice_action')
    return validate(state), {'replay': False}


def role_context(state, project):
    validate(state)
    if state['project'] != str(Path(project).resolve()):
        raise ValueError('choice_project_mismatch')
    if state['selection'] is None:
        raise ValueError('no_accepted_selection')
    target = state['proposals'][state['selection']]['target']
    check = route(dict({key: state[key] for key in ROUTE_KEYS}, version=1,
                       analysis={'recommendation': dict(target, reason='선택 지침 준비 전 정책 재검사')}),
                  lambda: load_catalog(project))
    if check['action'] != 'propose':
        raise ValueError('choice_no_longer_valid')
    if target['execution'] != 'current' or any(target[key] != state['current'][key] for key in ('model', 'effort')):
        raise ValueError('requires_separate_runtime_connection')
    from workflow import load_config
    config = load_config(ROOT, project)
    if target['role'] is None:
        return {'role': None, 'instructions': [], 'runtime_applied': False}
    if target['role'] not in config['roles']:
        raise ValueError('unknown_selected_role')
    instructions = []
    remaining = MAX_BYTES
    for path in config['roles'][target['role']]['instructions']:
        with path.open('r', encoding='utf-8') as stream:
            text = stream.read(remaining + 1)
        if len(text) > remaining:
            raise ValueError('selected_instructions_too_large')
        remaining -= len(text)
        instructions.append({'path': str(path), 'text': text})
    return {'role': target['role'], 'instructions': instructions, 'runtime_applied': False}


def reuse_selection(state, payload, catalog_loader=load_catalog):
    """Check a caller-confirmed followup; never load instructions or apply a profile."""
    validate(state)
    required = CONTEXT_KEYS | {'followup'}
    obj(payload, required, 'followup_context')
    if set(payload) != required or type(payload['followup']) is not bool:
        raise ValueError('explicit_followup_context_required')
    live = context({key: payload[key] for key in CONTEXT_KEYS})
    value = dict({key: live[key] for key in ROUTE_KEYS}, version=1)

    def fresh(reason):
        result = route(value, catalog_loader)
        if result['action'] == 'needs_analysis':
            result['reason'] = reason
        return dict(result, runtime_applied=False)

    # Latest explicit direct/auto controls take precedence over a stored selection.
    if live['controls']['mode'] in ('direct', 'auto'):
        return fresh('current_controls')
    if not payload['followup']:
        return fresh('followup_not_confirmed')
    if any(live[key] != state[key] for key in CONTEXT_KEYS):
        return fresh('choice_context_changed')
    if state['selection'] is None:
        return fresh('no_accepted_selection')
    target = state['proposals'][state['selection']]['target']
    if target['execution'] != 'current' or any(target[key] != live['current'][key] for key in ('model', 'effort')):
        return fresh('requires_separate_runtime_connection')
    previous = {key: state[key] for key in ('session_id', 'request_id', 'revision', 'controls')}
    previous['selection'] = target
    return dict(route(dict(value, followup=True, previous=previous), catalog_loader), runtime_applied=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('init', 'offer', 'choose', 'advance', 'show', 'role-context', 'reuse'))
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--state', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        project = args.project.resolve(strict=True)
        if not project.is_dir():
            raise ValueError('project_not_directory')
        path = private_file(args.state, project)
        with locked(path.with_suffix('.lock'), project):
            state = validate(read_json(path)) if path.exists() else None
            if state is not None and state['project'] != str(project):
                raise ValueError('choice_project_mismatch')
            if args.action in ('show', 'role-context'):
                if state is None:
                    raise ValueError('choice_state_missing')
                output = state if args.action == 'show' else role_context(state, project)
            else:
                raw = sys.stdin.buffer.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError('choice_input_too_large')
                payload = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
                if args.action == 'reuse':
                    if state is None:
                        raise ValueError('choice_state_missing')
                    output = reuse_selection(state, payload, lambda: load_catalog(project))
                else:
                    updated, result = operate(state, args.action, payload, lambda: load_catalog(project))
                    if updated['project'] != str(project):
                        raise ValueError('choice_project_mismatch')
                    if updated != state:
                        save(path, updated, project)
                    output = dict(result, selection=updated['selection'], sequence=updated['sequence'],
                                  executable=False, runtime_applied=False)
        print(json.dumps(output, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        print('routing_choices: invalid input, stale choice or unavailable state', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
