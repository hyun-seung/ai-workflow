#!/usr/bin/env python3
"""Compare one explicit direct/routed pair from existing private measured records."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from dispatch_usage import FIELDS
from lifecycle_io import private_file
from request_router import identifier, obj, reject_constant, unique
from trace_usage import integer, reject_physical_conflict, validate_usage

MAX_BYTES = 8 * 1024 * 1024
PHASES = {'routing', 'selection', 'processing', 'handoff', 'integration', 'verification', 'rework', 'reporting'}


def read_private(path, project):
    path = Path(path).expanduser().absolute()
    if not path.exists():
        raise ValueError('missing_comparison_artifact')
    path = private_file(path, project)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('comparison_artifact_not_regular')
        raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    fingerprint = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if len(raw) > MAX_BYTES or fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(path.stat()):
        raise ValueError('comparison_artifact_changed_or_oversized')
    return raw


def reference(value, loader):
    obj(value, {'path', 'sha256'}, 'comparison_reference')
    if set(value) != {'path', 'sha256'} or not isinstance(value['path'], str) or not Path(value['path']).is_absolute():
        raise ValueError('invalid_comparison_reference')
    sha = value['sha256']
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('invalid_comparison_hash')
    raw = loader(value['path'])
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError('comparison_artifact_hash_changed')
    return raw


def parse(raw):
    return json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)


def compare(manifest, loader):
    required = {'version', 'case_id', 'criteria', 'direct', 'routed'}
    obj(manifest, required | {'rate_card'}, 'comparison')
    version = manifest.get('version')
    if (not required <= set(manifest) or type(version) is not int or version not in (1, 2) or
            version == 1 and set(manifest) != required):
        raise ValueError('invalid_comparison_version_or_fields')
    identifier(manifest['case_id'])
    if not reference(manifest['criteria'], loader).strip():
        raise ValueError('empty_comparison_criteria')
    from comparison_metrics import elapsed, money, observed_model, rate_card
    raw_ref = lambda ref: reference(ref, loader)
    json_ref = lambda ref: parse(raw_ref(ref))
    card = rate_card(json_ref(manifest['rate_card']), raw_ref) if 'rate_card' in manifest else None
    results, seen, records = {}, [], {}
    for name in ('direct', 'routed'):
        side_keys = {'request_id', 'measurements', 'coverage', 'quality'}
        side = obj(manifest[name], side_keys | ({'timing'} if version == 2 else set()), 'comparison_side')
        if not side_keys <= set(side):
            raise ValueError('incomplete_comparison_side')
        identifier(side['request_id'])
        coverage = obj(side['coverage'], PHASES, 'comparison_coverage')
        if set(coverage) != PHASES or any(v not in ('included', 'not_applicable', 'missing') for v in coverage.values()):
            raise ValueError('explicit_phase_coverage_required')
        if coverage['processing'] == 'not_applicable' or name == 'routed' and coverage['routing'] == 'not_applicable':
            raise ValueError('required_phase_not_applicable')
        quality = obj(side['quality'], {'status', 'reviewer', 'evidence'}, 'comparison_quality')
        if (set(quality) != {'status', 'reviewer', 'evidence'} or quality['status'] not in ('pass', 'fail', 'unverified') or
                quality['reviewer'] not in ('self', 'independent', 'user')):
            raise ValueError('explicit_quality_evaluation_required')
        if not reference(quality['evidence'], loader).strip():
            raise ValueError('empty_quality_evidence')
        refs = side['measurements']
        if not isinstance(refs, list) or len(refs) > 1000:
            raise ValueError('invalid_measurement_list')
        totals = dict.fromkeys(FIELDS, 0)
        own, duplicates = {}, 0
        records[name] = {}
        for ref in refs:
            record = parse(reference(ref, loader))
            if (not isinstance(record, dict) or type(record.get('schema_version')) is not int or record['schema_version'] != 1 or
                    record.get('kind') not in ('orca_codex_metadata', 'orca_codex_coordinator_turn') or
                    ('dispatch_usage' in record) == ('turn_usage' in record)):
                raise ValueError('unsupported_comparison_measurement')
            if ('dispatch_usage' in record) != (record['kind'] == 'orca_codex_metadata'):
                raise ValueError('comparison_measurement_kind_mismatch')
            usage = record.get('dispatch_usage', record.get('turn_usage'))
            validate_usage(usage)
            if usage['request_id'] != side['request_id']:
                raise ValueError('comparison_request_mismatch')
            pricing = (record.get('provider'), observed_model(record, usage)) if card is not None else None
            key = usage['measurement_key']
            if key in own:
                if own[key] != usage:
                    raise ValueError('conflicting_comparison_measurement')
                if card is not None and records[name][key][0][1] != pricing[1]:
                    raise ValueError('conflicting_billing_model_observations')
                if card is not None and records[name][key][0][0] != pricing[0]:
                    raise ValueError('conflicting_billing_provider_observations')
                duplicates += 1
                continue
            for old in seen:
                reject_physical_conflict(old, usage)
            own[key] = usage; seen.append(usage)
            if card is not None:
                # Keep only resolved billing facts, not every full metadata file.
                records[name][key] = (pricing, usage)
            for field in FIELDS:
                value = usage['measurements'][field]
                totals[field] = None if value is None or totals[field] is None else integer(totals[field] + value)
        missing = sorted(key for key, value in coverage.items() if value == 'missing')
        results[name] = dict(measured_tokens=totals if own else None, unique_measurements=len(own),
                             duplicate_references_skipped=duplicates, missing_phases=missing,
                             declared_coverage_complete=bool(own) and not missing,
                             quality_status=quality['status'], quality_reviewer=quality['reviewer'])
    complete = all(side['declared_coverage_complete'] for side in results.values())
    delta = (results['routed']['measured_tokens']['total_tokens'] - results['direct']['measured_tokens']['total_tokens']) if complete else None
    if any(side['quality_status'] == 'fail' for side in results.values()):
        decision = 'quality_not_met'
    elif not complete:
        decision = 'incomplete_measurement'
    elif any(side['quality_status'] != 'pass' or side['quality_reviewer'] == 'self' for side in results.values()):
        decision = 'needs_quality_review'
    else:
        decision = 'candidate_for_repeated_evaluation' if delta < 0 else 'no_token_saving'
    timing = elapsed({name: manifest[name] for name in ('direct', 'routed')}, json_ref, raw_ref) if version == 2 else None
    costs = money(records, results, card)
    return dict(version=version, case_id=manifest['case_id'], criteria_sha256=manifest['criteria']['sha256'],
                sides=results, routed_minus_direct_tokens=delta, decision=decision,
                automation_allowed=False, elapsed_time_comparison=timing, monetary_cost_comparison=costs,
                limitations=['single_pair', 'coverage_and_quality_are_caller_declarations',
                             'artifact_hashes_do_not_authenticate_collectors'] +
                            (['no_time_or_price_comparison'] if version == 1 else
                             ['decision_uses_tokens_only', 'timing_boundaries_and_rate_applicability_are_caller_declarations',
                              'token_rate_estimate_is_not_verified_billing', 'no_exchange_taxes_subscription_or_other_fees']),
                **({'decision_basis': 'tokens_with_quality_and_coverage'} if version == 2 else {}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        project = args.project.resolve(strict=True)
        if not project.is_dir():
            raise ValueError('comparison_project_not_directory')
        result = compare(parse(read_private(args.manifest, project)), lambda path: read_private(path, project))
        print(json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print('compare_routing: invalid input, changed artifact or incompatible measurement', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
