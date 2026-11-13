#!/usr/bin/env python3
"""Stateless recommendation gate. No model calls, dispatch, settings writes or approval."""

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MAX_INPUT = 32768
EFFORTS = {'low', 'medium', 'high', 'xhigh', 'max'}
DEFAULT_CONTROLS = dict(mode='propose', frequency='changes', role_locked=False,
                        model_locked=False, effort_locked=False, delegation='propose')
PROFILE_KEYS = {'role', 'model', 'effort', 'execution'}


def obj(value, keys, label):
    if not isinstance(value, dict) or set(value) - keys:
        raise ValueError('invalid_' + label)
    return value


def short(value, label, limit=240):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError('invalid_' + label)
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
        raise ValueError('invalid_identifier')
    return value


def controls(value):
    obj(value, set(DEFAULT_CONTROLS), 'controls')
    result = dict(DEFAULT_CONTROLS, **value)
    for key in ('role_locked', 'model_locked', 'effort_locked'):
        if type(result[key]) is not bool:
            raise ValueError('invalid_control_boolean')
    for key, allowed in [('mode', ('direct', 'propose', 'auto')),
                         ('frequency', ('changes', 'always')), ('delegation', ('forbid', 'propose'))]:
        if result[key] not in allowed:
            raise ValueError('invalid_' + key)
    return result


def profile(value):
    obj(value, PROFILE_KEYS, 'profile')
    result = dict(role=None, model=None, effort=None, execution='current')
    result.update(value)
    if result['role'] is not None:
        identifier(result['role'])
    if result['model'] is not None and (not isinstance(result['model'], str) or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}', result['model'])):
        raise ValueError('invalid_model')
    if result['effort'] is not None and (not isinstance(result['effort'], str) or result['effort'] not in EFFORTS):
        raise ValueError('invalid_effort')
    if result['execution'] not in ('current', 'worker'):
        raise ValueError('invalid_execution')
    return result


def load_catalog(project=ROOT, local_config=None):
    # Configuration validation checks instruction paths; it never reads their contents.
    from workflow import load_config
    config = load_config(ROOT, project, local_config)
    descriptions = json.loads((ROOT / 'routing-catalog.json').read_text(), object_pairs_hook=unique)
    if descriptions.get('version') != 1 or set(descriptions.get('roles', {})) != set(config['roles']):
        raise ValueError('catalog_role_mismatch')
    return {
        name: dict(summary=short(descriptions['roles'][name], 'role_summary', 100),
                   model=settings['model'], effort=settings['effort'])
        for name, settings in config['roles'].items()
    }


def validate_target(target, current, settings, catalog):
    if target['role'] != current['role'] and target['role'] is not None and target['role'] not in catalog:
        raise ValueError('unknown_role')
    # Unchanged current models may be external; new recommendations stay in the configured set.
    if target['model'] != current['model'] and target['model'] not in {v['model'] for v in catalog.values()}:
        raise ValueError('unconfigured_model')
    violations = [key + '_locked' for key in ('role', 'model', 'effort')
                  if settings[key + '_locked'] and target[key] != current[key]]
    if settings['delegation'] == 'forbid' and target['execution'] == 'worker':
        violations.append('delegation_forbidden')
    return violations


def route(value, catalog_loader=load_catalog):
    obj(value, {'version', 'session_id', 'request_id', 'revision', 'controls', 'current',
                'followup', 'previous', 'analysis'}, 'input')
    if type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('invalid_version')
    session, request = identifier(value.get('session_id')), identifier(value.get('request_id'))
    revision = value.get('revision')
    if type(revision) is not int or revision < 1:
        raise ValueError('invalid_revision')
    settings = controls(value.get('controls', {}))
    current = profile(value.get('current', {}))
    if type(value.get('followup', False)) is not bool:
        raise ValueError('invalid_followup')

    def result(action, reason, target=current, message='', **extra):
        return dict(version=1, action=action, reason=reason, profile=target, message=message,
                    scope='request', executable=False, runtime_support_verified=False,
                    router_model_calls=0, **extra)

    if settings['mode'] == 'direct':
        return result('direct', 'explicit_direct')
    if settings['mode'] == 'auto':
        return result('unsupported', 'automatic_execution_not_implemented')

    previous = value.get('previous')
    if previous is not None:
        obj(previous, {'session_id', 'request_id', 'revision', 'controls', 'selection'}, 'previous')
        matches = (value.get('followup', False) and previous.get('session_id') == session
                   and previous.get('request_id') == request and previous.get('revision') == revision
                   and controls(previous.get('controls', {})) == settings)
        if matches and 'selection' in previous:
            if not isinstance(previous['selection'], dict) or set(previous['selection']) != PROFILE_KEYS:
                raise ValueError('incomplete_previous_selection')
            selected = profile(previous['selection'])
            violations = validate_target(selected, current, settings, catalog_loader())
            if not violations:
                return result('reuse', 'same_request_selection', selected)

    if settings['role_locked'] and settings['model_locked'] and settings['effort_locked'] and settings['delegation'] == 'forbid':
        message = '현재 AI·설정 유지 제안입니다. 이번 요청에만 적용합니다.' if settings['frequency'] == 'always' else ''
        return result('direct', 'all_changes_disabled', message=message)
    if 'analysis' not in value:
        return result('needs_analysis', 'current_agent_judgment_required')
    analysis = obj(value['analysis'], {'recommendation'}, 'analysis')
    if 'recommendation' not in analysis:
        raise ValueError('recommendation_required')
    candidate = analysis['recommendation']
    if candidate is None:
        message = '현재 AI·설정으로 직접 처리하는 방식을 제안합니다. 이번 요청에만 적용합니다.' if settings['frequency'] == 'always' else ''
        return result('direct', 'no_material_benefit', message=message)
    obj(candidate, PROFILE_KEYS | {'reason'}, 'recommendation')
    reason = short(candidate.get('reason'), 'reason')
    target = profile(dict(current, **{key: val for key, val in candidate.items() if key in PROFILE_KEYS}))
    violations = validate_target(target, current, settings, catalog_loader())
    if violations:
        return result('revise', 'control_conflict', violations=violations)
    changes = [key for key in sorted(PROFILE_KEYS) if target[key] != current[key]]
    if not changes:
        message = '현재 AI·설정 유지 제안: ' + reason if settings['frequency'] == 'always' else ''
        return result('direct', 'unchanged', message=message)
    parts = []
    for key, label in [('role', '역할'), ('model', '모델'), ('effort', 'effort'), ('execution', '처리')]:
        if key in changes:
            display = {'current': '현재 AI', 'worker': '별도 담당자'}[target[key]] if key == 'execution' else str(target[key] or '현재 방식')
            parts.append(label + ': ' + display)
    message = ' / '.join(parts) + '. ' + reason + ' 이번 요청에만 적용하는 제안입니다. 현재 방식 유지 / 제안 수용 / 수정.'
    return result('propose', 'material_change', target, message, changes=changes)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def reject_constant(_):
    raise ValueError('nonfinite_json')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('catalog', 'route'))
    parser.add_argument('--project', type=Path, default=ROOT)
    parser.add_argument('--local-config', type=Path)
    args = parser.parse_args(argv)
    try:
        loader = lambda: load_catalog(args.project, args.local_config)
        if args.command == 'catalog':
            output = dict(version=1, profiles='configured_not_runtime_verified', roles=loader())
        else:
            raw = sys.stdin.buffer.read(MAX_INPUT + 1)
            if len(raw) > MAX_INPUT:
                raise ValueError('input_too_large')
            value = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
            output = route(value, loader)
        print(json.dumps(output, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
        return 0
    except (ValueError, TypeError, OSError, RecursionError):
        # Never echo input values, request text, local paths or parser excerpts.
        print('request_router: invalid input or configuration', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
