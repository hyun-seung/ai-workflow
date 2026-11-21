"""Validated harness policy; never changes runtime permissions or model settings."""

import argparse
import json
import sys
from pathlib import Path
from lifecycle_io import read_json

ROOT = Path(__file__).resolve().parents[1]
LIMIT_KEYS = {'max_active_workers', 'max_dispatches_per_request'}
DECISION_MAKERS = ('coordinator', 'user')


def validate(value):
    if not isinstance(value, dict) or set(value) - {'$schema', 'version', 'auto_cleanup', 'limits', 'delegation_decision_maker'}:
        raise ValueError('invalid_harness_policy_keys')
    if 'version' in value and (type(value['version']) is not int or value['version'] != 1):
        raise ValueError('invalid_harness_policy_version')
    if 'auto_cleanup' in value and value['auto_cleanup'] not in ('Y', 'N'):
        raise ValueError('auto_cleanup_must_be_Y_or_N')
    if 'delegation_decision_maker' in value and value['delegation_decision_maker'] not in DECISION_MAKERS:
        raise ValueError('delegation_decision_maker_must_be_coordinator_or_user')
    limits = value.get('limits', {})
    if not isinstance(limits, dict) or set(limits) - LIMIT_KEYS:
        raise ValueError('invalid_harness_limit_keys')
    if any(v is not None and (type(v) is not int or v < 1) for v in limits.values()):
        raise ValueError('harness_limits_must_be_positive_integers')
    return value


def load(project, config=None, auto_cleanup=None, limits=None, delegation_decision_maker=None):
    project = Path(project).expanduser().resolve()
    if not project.is_dir():
        raise ValueError('harness_policy_project_not_directory')
    policy = {'version': 1, 'auto_cleanup': 'Y', 'delegation_decision_maker': 'coordinator',
              'limits': dict.fromkeys(LIMIT_KEYS)}
    sources = []
    project_file = project / '.ai-workflow-harness.json'
    paths = [(ROOT / 'harness.json', True), (project_file, False)]
    if config is not None:
        paths.append((Path(config).expanduser().absolute(), True))
    for path, required in paths:
        if not path.exists() and not path.is_symlink() and not required:
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError('harness_policy_file_missing_or_symlink')
        value = validate(read_json(path))
        if 'auto_cleanup' in value:
            policy['auto_cleanup'] = value['auto_cleanup']
        if 'delegation_decision_maker' in value:
            policy['delegation_decision_maker'] = value['delegation_decision_maker']
        policy['limits'].update(value.get('limits', {}))
        sources.append(str(path.resolve()))
    override = {}
    if auto_cleanup is not None:
        override['auto_cleanup'] = auto_cleanup
    if limits:
        override['limits'] = limits
    if delegation_decision_maker is not None:
        override['delegation_decision_maker'] = delegation_decision_maker
    validate(override)
    policy.update({k: v for k, v in override.items() if k != 'limits'})
    policy['limits'].update(override.get('limits', {}))
    return dict(policy, sources=sources)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--config', help='explicit local harness override')
    parser.add_argument('--delegation-decision-maker', choices=DECISION_MAKERS)
    args = parser.parse_args(argv)
    try:
        policy = load(args.project, args.config,
                      delegation_decision_maker=args.delegation_decision_maker)
    except (OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({'error': 'invalid_harness_policy'}), file=sys.stderr)
        return 2
    print(json.dumps(policy, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
