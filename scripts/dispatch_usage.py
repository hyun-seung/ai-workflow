"""Attribute recorded counter increments to one closed native worker turn.

No runtime calls, source text, token estimates or request-level aggregation.
"""

ROLE_IDS = {'coordinator', 'architect', 'designer', 'backend', 'frontend', 'verifier',
            'tutor', 'learning-concepts', 'learning-visuals', 'learning-connections'}
FIELDS = ('input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_output_tokens', 'total_tokens')
PRIMARY = ('input_tokens', 'output_tokens')


def collect(session_id, target, assignments, boundaries, measurements, user_lines,
            request_id=None, role=None, inherited=False):
    result = dict(version=1, status='unavailable', scope='dispatch', task_attributed=False,
                  request_id=request_id, role=role, task_id=target['task_id'],
                  dispatch_id=target['dispatch_id'], actor_id=target['dispatch_id'], session_id=session_id,
                  source='codex.token_count.cumulative_delta.v1', measurement_key=None,
                  measurements={}, issues=[], interval=None, baseline=None, final=None, increments=[])

    def stop(reason):
        result['issues'] = [reason]
        return result

    starts = [b for b in boundaries if b['kind'] == 'task_started' and b['line'] < target['line']]
    if not starts or not starts[-1]['turn_id']:
        return stop('native_turn_start_unknown')
    start = starts[-1]
    if sum(b['kind'] == 'task_started' and b['turn_id'] == start['turn_id'] for b in boundaries) != 1:
        return stop('duplicate_native_turn_id')
    after = [b for b in boundaries if b['line'] > start['line']]
    terminal = next((b for b in after if b['kind'] in {'task_complete', 'turn_aborted'}), None)
    if terminal is None:
        return stop('native_turn_not_closed')
    if terminal['turn_id'] != start['turn_id'] or terminal['line'] <= target['line']:
        return stop('native_turn_identity_mismatch')
    if any(b['kind'] == 'task_started' and b['line'] < terminal['line'] for b in after):
        return stop('overlapping_native_turns')
    if terminal['kind'] != 'task_complete':
        return stop('native_turn_aborted')
    if sum(b['kind'] in {'task_complete', 'turn_aborted'} and b['turn_id'] == start['turn_id']
           for b in boundaries) != 1:
        return stop('ambiguous_native_turn_terminal')
    following_start = next((b for b in after if b['kind'] == 'task_started'
                            and b['line'] > terminal['line']), None)
    gap_end = following_start['line'] if following_start else float('inf')
    if any(terminal['line'] < m['line'] < gap_end for m in measurements):
        return stop('usage_outside_native_turn')
    inside = [a for a in assignments if start['line'] < a['line'] < terminal['line']]
    if len(inside) != 1 or inside[0] != target:
        return stop('ambiguous_turn_assignment')
    if any(start['line'] < message['line'] < terminal['line'] and message.get('routing_status') == 'invalid'
           for message in user_lines):
        return stop('invalid_user_message_routing')
    if any(target['line'] <= message['line'] < terminal['line']
           and message['turn_id'] != start['turn_id']
           and (message['line'] != target['line'] or message['turn_id'] is not None)
           for message in user_lines):
        return stop('unbound_user_message')
    next_assignment = next((a for a in assignments if a['line'] > target['line']), None)
    for b in boundaries:
        if b['kind'] == 'task_started' and b['line'] > terminal['line']:
            if next_assignment is None:
                return stop('unbound_followup_turn')
            if b['line'] < next_assignment['line']:
                later_starts = [n for n in boundaries if n['kind'] == 'task_started'
                                and b['line'] < n['line'] < next_assignment['line']]
                if later_starts:
                    return stop('unbound_followup_turn')
            break
    return measure_window(result, session_id, start, terminal, target['line'], boundaries,
                          measurements, inherited, request_id is not None,
                          'explicit' if request_id is not None else 'unlinked')


def measure_window(result, session_id, start, terminal, binding_line, boundaries, measurements,
                   inherited, attributed, binding_method):
    """Reconcile counter deltas after the caller validates its binding and boundaries."""
    def stop(reason):
        result['issues'] = [reason]
        return result
    starts = [b for b in boundaries if b['kind'] == 'task_started' and b['line'] <= start['line']]
    result['measurement_key'] = 'codex.session:' + session_id + ':turn:' + start['turn_id']
    result['interval'] = dict(native_turn_id=start['turn_id'], start_line=start['line'],
                              assignment_line=binding_line, end_line=terminal['line'],
                              started_at=start['time'], finished_at=terminal['time'])
    current = [m for m in measurements if start['line'] < m['line'] < terminal['line']]
    if any(m['line'] < binding_line for m in current):
        return stop('usage_before_assignment')
    if not current:
        return stop('missing_dispatch_usage')
    before = [m for m in measurements if m['line'] < start['line']]
    if before:
        baseline = before[-1]
        if baseline['issue'] or baseline['values'] is None:
            return stop('invalid_baseline')
        previous_end = next((b for b in reversed(boundaries) if b['line'] < start['line']
                             and b['kind'] in {'task_complete', 'turn_aborted'}), None)
        previous_start = next((b for b in reversed(boundaries) if b['line'] < start['line']
                               and b['kind'] == 'task_started'), None)
        if (previous_end is None or previous_start is None or
                previous_start['turn_id'] != previous_end['turn_id'] or
                not previous_start['line'] < baseline['line'] < previous_end['line'] < start['line']):
            return stop('baseline_turn_boundary_unknown')
        previous = dict(baseline['values'])
        result['baseline'] = dict(line=baseline['line'], time=baseline['time'], values=previous, method='prior_turn_counter')
    else:
        if inherited or len(starts) != 1:
            return stop('initial_baseline_unknown')
        previous = dict.fromkeys(FIELDS, 0)
        result['baseline'] = dict(line=None, time=None, values=previous, method='zero_reconciled_with_first_usage')
    totals = dict.fromkeys(FIELDS, 0)
    seen_new = False
    for sample in current:
        if sample['issue'] or sample['values'] is None:
            return stop('invalid_dispatch_counter')
        value, last = sample['values'], sample['last_values']
        if sample['last_issue'] or last is None:
            return stop('last_usage_unavailable')
        delta = {key: value[key] - previous[key] if value[key] is not None and previous[key] is not None
                 else None for key in FIELDS}
        if any(v is not None and v < 0 for v in delta.values()):
            return stop('counter_decreased')
        if all(delta[key] == 0 for key in PRIMARY):
            if any(delta[key] not in (0, None) for key in FIELDS):
                return stop('detail_changed_without_usage')
            if all(last[key] == 0 for key in PRIMARY):
                seen_new = True  # An explicit measured zero, never a missing value.
                for key in FIELDS:
                    if delta[key] is None or last[key] is None:
                        totals[key] = None
        else:
            if any(delta[key] != last[key] for key in PRIMARY):
                return stop('counter_increment_mismatch')
            for key in FIELDS:
                if delta[key] is not None and last[key] is not None and delta[key] != last[key]:
                    return stop('counter_detail_mismatch')
                if delta[key] is None or last[key] is None:
                    totals[key] = None
                elif totals[key] is not None:
                    totals[key] += delta[key]
            seen_new = True
            result['increments'].append(dict(line=sample['line'], time=sample['time'], values=delta))
        previous = dict(value)
    if not seen_new:
        return stop('no_new_usage_measurement')
    for detail, primary in [('cached_input_tokens', 'input_tokens'), ('reasoning_output_tokens', 'output_tokens')]:
        if totals[detail] is not None and totals[detail] > totals[primary]:
            return stop('invalid_dispatch_subset')
    totals['total_tokens'] = totals['input_tokens'] + totals['output_tokens']
    result.update(status='measured', task_attributed=attributed, measurements=totals,
                  final=dict(line=current[-1]['line'], time=current[-1]['time'], values=previous),
                  request_binding=binding_method)
    return result
