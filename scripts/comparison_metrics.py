"""Optional declared wall-clock measurements and token-rate estimates; no IO/runtime."""

from datetime import datetime
from decimal import Decimal, localcontext
import re
from urllib.parse import urlsplit

from request_router import identifier, obj, profile, short

PHASES = {'routing', 'selection', 'processing', 'handoff', 'integration', 'verification', 'rework', 'reporting'}


def exact(value, fields, label):
    obj(value, fields, label)
    if set(value) != set(fields):
        raise ValueError('incomplete_' + label)
    return value


def utc(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)', value):
        raise ValueError('explicit_utc_timestamp_required')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def number(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,12})?', value):
        raise ValueError('nonnegative_decimal_rate_string_required')
    return Decimal(value)


def display(value):
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def elapsed(sides, load_json, load_raw):
    if not any('timing' in side for side in sides.values()):
        return None
    rows = {}
    for name, side in sides.items():
        if 'timing' not in side:
            rows[name] = dict(elapsed_seconds=None, missing_phases=[], status='unavailable', reason='timing_not_provided')
            continue
        record = exact(load_json(side['timing']), {'version', 'request_id', 'measurement_id', 'clock_id',
            'scope', 'start_event', 'end_event', 'started_at', 'finished_at', 'coverage', 'evidence'}, 'request_timing')
        if (type(record['version']) is not int or record['version'] != 1 or record['request_id'] != side['request_id'] or
                record['scope'] != 'request_wall_clock' or record['start_event'] != 'request_received' or
                record['end_event'] != 'final_response_delivered'):
            raise ValueError('request_timing_scope_mismatch')
        identifier(record['measurement_id']); identifier(record['clock_id'])
        coverage = exact(record['coverage'], PHASES, 'timing_coverage')
        if (any(v not in ('included', 'not_applicable', 'missing') for v in coverage.values()) or
                coverage['processing'] == 'not_applicable' or name == 'routed' and coverage['routing'] == 'not_applicable'):
            raise ValueError('invalid_timing_coverage')
        if not load_raw(record['evidence']).strip():
            raise ValueError('empty_timing_evidence')
        started, finished = utc(record['started_at']), utc(record['finished_at'])
        if finished < started:
            raise ValueError('negative_request_elapsed_time')
        delta = finished - started
        with localcontext() as context:
            context.prec = 80
            seconds = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000
        missing = sorted(key for key, value in coverage.items() if value == 'missing')
        rows[name] = dict(elapsed_seconds=display(seconds), missing_phases=missing,
                          status='partial' if missing else 'declared_complete', clock_id=record['clock_id'],
                          measurement_id=record['measurement_id'])
    measured = [r for r in rows.values() if r.get('measurement_id')]
    if len(measured) == 2 and (measured[0]['clock_id'], measured[0]['measurement_id']) == (measured[1]['clock_id'], measured[1]['measurement_id']):
        raise ValueError('same_request_timing_reused_across_sides')
    complete = all(row['status'] == 'declared_complete' for row in rows.values())
    same_clock = len(measured) == 2 and measured[0]['clock_id'] == measured[1]['clock_id']
    with localcontext() as context:
        context.prec = 80
        difference = Decimal(rows['routed']['elapsed_seconds']) - Decimal(rows['direct']['elapsed_seconds']) if complete and same_clock else None
    return dict(status='declared_comparable' if difference is not None else 'unavailable',
                reason=None if difference is not None else 'missing_timing_or_scope_or_matching_clock', sides=rows,
                routed_minus_direct_seconds=display(difference) if difference is not None else None,
                boundaries_and_clock_are_caller_declarations=True)


def rate_card(value, load_raw):
    exact(value, {'version', 'provider', 'currency', 'unit', 'billing_mode', 'valid_from', 'valid_until', 'source', 'models'}, 'rate_card')
    if (type(value['version']) is not int or value['version'] != 1 or value['unit'] != 'per_million_tokens' or
            not isinstance(value['currency'], str) or not re.fullmatch('[A-Z]{3}', value['currency'])):
        raise ValueError('invalid_rate_card')
    short(value['billing_mode'], 'billing_mode', 100)
    identifier(value['provider'])
    start, end = utc(value['valid_from']), utc(value['valid_until'])
    if end <= start:
        raise ValueError('invalid_rate_validity_window')
    source = exact(value['source'], {'url', 'retrieved_at', 'evidence'}, 'rate_source')
    if not isinstance(source['url'], str) or len(source['url']) > 2048:
        raise ValueError('invalid_rate_source_url')
    url = urlsplit(source['url'])
    if url.scheme != 'https' or not url.hostname or url.username is not None or url.password is not None:
        raise ValueError('https_rate_source_required')
    utc(source['retrieved_at'])
    if not load_raw(source['evidence']).strip():
        raise ValueError('empty_rate_evidence')
    if not isinstance(value['models'], dict) or not 1 <= len(value['models']) <= 1000:
        raise ValueError('invalid_rate_models')
    models = {}
    for model, prices in value['models'].items():
        profile({'model': model})
        exact(prices, {'uncached_input', 'cached_input', 'output'}, 'model_rates')
        models[model] = {key: number(rate) for key, rate in prices.items()}
    return dict(provider=value['provider'], currency=value['currency'], billing_mode=value['billing_mode'], start=start, end=end, models=models)


def observed_model(record, usage):
    # Never price requested/default profiles. The current coordinator collector
    # does not publish model contexts, so it remains unpriced here.
    if record['kind'] != 'orca_codex_metadata':
        return None, 'observed_model_unavailable'
    rows = record.get('observed_contexts')
    if not isinstance(rows, list) or not rows:
        return None, 'observed_model_unavailable'
    session, binding = record.get('session'), record.get('binding')
    if (not isinstance(session, dict) or session.get('id') != usage['session_id'] or
            session.get('source') != 'vscode' or session.get('originator') != 'orca_desktop' or
            not isinstance(binding, dict) or binding.get('task_id') != usage['task_id'] or
            binding.get('dispatch_id') != usage['dispatch_id']):
        return None, 'profile_binding_mismatch_or_unavailable'
    interval = usage['interval']
    context_line, assignment_line = binding.get('context_line'), binding.get('line')
    if (type(context_line) is not int or type(assignment_line) is not int or
            assignment_line != interval['assignment_line'] or
            not interval['start_line'] < context_line < assignment_line):
        return None, 'model_at_assignment_unavailable'
    models, lines = [], []
    for row in rows:
        if not isinstance(row, dict) or type(row.get('line')) is not int or not isinstance(row.get('values'), dict):
            return None, 'invalid_model_observation'
        if interval['start_line'] < row['line'] < interval['end_line']:
            if lines and row['line'] <= lines[-1]:
                return None, 'invalid_model_observation_order'
            model = row['values'].get('model')
            if model is None:
                return None, 'observed_model_unavailable'
            profile({'model': model})
            models.append(model)
            lines.append(row['line'])
    if not lines or lines[0] != context_line:
        return None, 'model_at_assignment_unavailable'
    if len(set(models)) != 1:
        return None, 'mixed_models_in_one_measurement'
    return models[0], None


def money(records, results, card):
    if card is None:
        return None
    sides = {}
    with localcontext() as context:
        context.prec = 80
        for name, rows in records.items():
            total, count, issues = Decimal(0), 0, []
            for key, (observation, usage) in rows.items():
                provider, (model, reason) = observation
                if provider != card['provider']:
                    reason = 'provider_rate_mismatch_or_unavailable'
                if reason is None and model not in card['models']:
                    reason = 'model_rate_unavailable'
                if reason is None and usage['measurements']['cached_input_tokens'] is None:
                    reason = 'cached_input_unavailable'
                interval = usage['interval']
                if reason is None and (interval['started_at'] is None or interval['finished_at'] is None):
                    reason = 'billing_interval_unavailable'
                if reason is None:
                    started, finished = utc(interval['started_at']), utc(interval['finished_at'])
                    if not card['start'] <= started <= finished < card['end']:
                        reason = 'measurement_outside_rate_window'
                if reason is not None:
                    issues.append(dict(measurement_key=key, reason=reason))
                    continue
                values, rates = usage['measurements'], card['models'][model]
                cached = values['cached_input_tokens']
                # Cached input is a subset of input, reasoning a subset of output.
                total += ((values['input_tokens'] - cached) * rates['uncached_input'] +
                          cached * rates['cached_input'] + values['output_tokens'] * rates['output']) / 1000000
                count += 1
            sides[name] = dict(measured_token_cost=display(total) if count else None,
                               priced_measurements=count, unpriced_measurements=issues,
                               declared_complete=bool(rows) and not issues and results[name]['declared_coverage_complete'])
        complete = all(row['declared_complete'] for row in sides.values())
        delta = Decimal(sides['routed']['measured_token_cost']) - Decimal(sides['direct']['measured_token_cost']) if complete else None
    return dict(status='declared_rate_estimate' if complete else 'partial' if any(row['priced_measurements'] for row in sides.values()) else 'unavailable', currency=card['currency'],
                billing_mode=card['billing_mode'], sides=sides,
                routed_minus_direct_estimated_cost=display(delta) if delta is not None else None,
                billed_cost_verified=False, rate_applicability_is_caller_declared=True)
