"""Validated harness policy; never changes runtime permissions or model settings."""

from pathlib import Path
from lifecycle_io import read_json

ROOT = Path(__file__).resolve().parents[1]
LIMIT_KEYS = {'max_active_workers', 'max_dispatches_per_request'}


def validate(value):
    if not isinstance(value, dict) or set(value) - {'$schema', 'version', 'auto_cleanup', 'limits'}:
        raise ValueError('invalid_harness_policy_keys')
    if 'version' in value and (type(value['version']) is not int or value['version'] != 1):
        raise ValueError('invalid_harness_policy_version')
    if 'auto_cleanup' in value and value['auto_cleanup'] not in ('Y', 'N'):
        raise ValueError('auto_cleanup_must_be_Y_or_N')
    limits = value.get('limits', {})
    if not isinstance(limits, dict) or set(limits) - LIMIT_KEYS:
        raise ValueError('invalid_harness_limit_keys')
    if any(v is not None and (type(v) is not int or v < 1) for v in limits.values()):
        raise ValueError('harness_limits_must_be_positive_integers')
    return value


def load(project, config=None, auto_cleanup=None, limits=None):
    project = Path(project).expanduser().resolve()
    if not project.is_dir():
        raise ValueError('harness_policy_project_not_directory')
    policy = {'version': 1, 'auto_cleanup': 'Y', 'limits': dict.fromkeys(LIMIT_KEYS)}
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
        policy['limits'].update(value.get('limits', {}))
        sources.append(str(path.resolve()))
    override = {}
    if auto_cleanup is not None:
        override['auto_cleanup'] = auto_cleanup
    if limits:
        override['limits'] = limits
    validate(override)
    policy.update({k: v for k, v in override.items() if k != 'limits'})
    policy['limits'].update(override.get('limits', {}))
    return dict(policy, sources=sources)
