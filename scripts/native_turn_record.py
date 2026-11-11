#!/usr/bin/env python3
"""Measure one explicitly bound, closed coordinator turn; never invent a dispatch."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from dispatch_usage import measure_window
from native_record import read_session, identifier, unique_object, reject_constant, write_record


def validate_binding(binding):
    expected = {'schema_version', 'request_id', 'actor_id', 'task_id', 'role',
                'native_turn_id', 'message_line', 'message_sha256'}
    if not isinstance(binding, dict) or set(binding) != expected or type(binding['schema_version']) is not int or binding['schema_version'] != 1:
        raise ValueError('invalid_turn_binding')
    for key in ('request_id', 'actor_id', 'task_id', 'native_turn_id'):
        identifier(binding[key])
    if (binding['role'] != 'coordinator' or type(binding['message_line']) is not int or
            not 1 <= binding['message_line'] <= 2**53-1 or
            not isinstance(binding['message_sha256'], str) or not re.fullmatch('[a-f0-9]{64}', binding['message_sha256'])):
        raise ValueError('invalid_turn_binding')


def collect_turn(data, binding):
    validate_binding(binding)
    result = dict(version=2, status='unavailable', scope='native_turn', task_attributed=False,
                  request_id=binding['request_id'], role='coordinator', task_id=binding['task_id'],
                  dispatch_id=None, actor_id=binding['actor_id'], session_id=data['meta']['id'],
                  source='codex.token_count.cumulative_delta.v1', measurement_key=None,
                  measurements={}, issues=[], interval=None, baseline=None, final=None, increments=[],
                  request_binding='explicit_native_turn', message_sha256=binding['message_sha256'])

    def stop(reason):
        result['issues'] = [reason]
        return result

    boundaries = data['boundaries']; turn_id = binding['native_turn_id']
    starts = [b for b in boundaries if b['kind'] == 'task_started' and b['turn_id'] == turn_id]
    if len(starts) != 1:
        return stop('missing_or_duplicate_native_turn')
    start = starts[0]
    after = [b for b in boundaries if b['line'] > start['line']]
    terminal = next((b for b in after if b['kind'] in {'task_complete', 'turn_aborted'}), None)
    if terminal is None:
        return stop('native_turn_not_closed')
    if terminal['kind'] != 'task_complete' or terminal['turn_id'] != turn_id:
        return stop('native_turn_terminal_mismatch')
    if any(b['kind'] == 'task_started' and b['line'] < terminal['line'] for b in after):
        return stop('overlapping_native_turns')
    if sum(b['kind'] in {'task_complete', 'turn_aborted'} and b['turn_id'] == turn_id for b in boundaries) != 1:
        return stop('ambiguous_native_turn_terminal')
    users = [u for u in data['user_lines'] if start['line'] < u['line'] < terminal['line']]
    if len(users) != 1:
        return stop('ambiguous_turn_user_messages')
    message = users[0]
    if message.get('routing_status') == 'invalid':
        return stop('invalid_user_message_routing')
    if (message['line'] != binding['message_line'] or message['content_sha256'] != binding['message_sha256'] or
            message['turn_id'] not in (None, turn_id)):
        return stop('turn_message_binding_mismatch')
    if any(start['line'] < a['line'] < terminal['line'] for a in data['assignments']):
        return stop('worker_assignment_in_coordinator_turn')
    next_start = next((b['line'] for b in after if b['kind'] == 'task_started' and b['line'] > terminal['line']), float('inf'))
    if any(terminal['line'] < m['line'] < next_start for m in data['measurements']):
        return stop('usage_outside_native_turn')
    result = measure_window(result, data['meta']['id'], start, terminal, message['line'], boundaries,
                            data['measurements'], data['inherited'], True, 'explicit_native_turn')
    if result['interval'] is not None:
        result['interval']['binding_line'] = result['interval'].pop('assignment_line')
    return result


def inspect_turn(project, session_file, session_id, binding):
    data = read_session(project, session_file, session_id)
    usage = collect_turn(data, binding)
    return dict(schema_version=1, kind='orca_codex_coordinator_turn', environment='orca', provider='codex',
                recorded_at=datetime.now(timezone.utc).isoformat(), project=str(data['project']), session=data['meta'],
                source=dict(path=str(data['path']), sha256=data['sha256'], bytes=data['size'], lines=data['lines']),
                turn_binding=binding, turn_usage=usage, live_state_verified=False, task_acceptance_evaluated=False,
                limitations=['single_closed_native_turn_only', 'explicit_logical_binding_not_source_identity',
                             'not_whole_request_usage', 'no_tool_token_allocation', 'not_a_trace_bundle'])


def load_binding(filename):
    path = Path(filename).resolve(strict=True)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
            raise ValueError('invalid_turn_binding_file')
        raw = stream.read(1024 * 1024 + 1); after = os.fstat(stream.fileno())
    fingerprint = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if len(raw) > 1024 * 1024 or fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(path.stat()):
        raise ValueError('turn_binding_file_changed_or_oversized')
    return path, raw, json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('project', 'session-file', 'session-id', 'binding', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    try:
        binding_path, raw, binding = load_binding(args.binding)
        record = inspect_turn(args.project, args.session_file, args.session_id, binding)
        record['binding_source'] = dict(path=str(binding_path), sha256=hashlib.sha256(raw).hexdigest())
        output = write_record(record, args.output, args.project)
    except (OSError, ValueError) as error:
        print('Turn record failed: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        return 2
    print(json.dumps(dict(output=str(output), usage_status=record['turn_usage']['status'],
                          issues=record['turn_usage']['issues'])))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
