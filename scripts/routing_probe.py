#!/usr/bin/env python3
"""Capture one scoped native input receipt; never read prompt or transcript contents."""

import argparse
import json
from pathlib import Path
import shlex
import sys

from lifecycle_io import locked, private_file, read_json, save
from request_router import identifier, reject_constant, unique
from routing_hook import absolute, MAX_INPUT


def capture(project, session_id, receipt, payload):
    project = Path(project).resolve(strict=True)
    if not project.is_dir():
        raise ValueError('probe_project_not_directory')
    identifier(session_id)
    if not isinstance(payload, dict) or payload.get('hook_event_name') != 'UserPromptSubmit':
        return {}
    if payload.get('session_id') != session_id or payload.get('agent_id') is not None or payload.get('agent_type') is not None:
        return {}
    if absolute(payload.get('cwd')) != project:
        return {}
    turn = identifier(payload.get('turn_id'))
    transcript = absolute(payload.get('transcript_path'))
    record = dict(version=1, event='UserPromptSubmit', project=str(project), session_id=session_id,
                  turn_id=turn, transcript_path=str(transcript))
    path = private_file(receipt, project)
    with locked(path.with_suffix('.lock'), project):
        if path.exists():
            previous = read_json(path)
            if (not isinstance(previous, dict) or set(previous) != set(record) or
                    type(previous.get('version')) is not int or
                    any(previous[key] != value for key, value in record.items() if key != 'turn_id')):
                raise ValueError('probe_receipt_binding_changed')
            identifier(previous['turn_id'])
            return {}
        save(path, record, project)
    return {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext':
            'ai-workflow: 현재 세션의 실제 입력 hook 수신을 확인했습니다. 역할·모델·effort·권한은 변경하지 않았습니다.'}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--print-hook', action='store_true')
    args = parser.parse_args(argv)
    try:
        project = args.project.resolve(strict=True)
        identifier(args.session_id)
        if not project.is_dir() or not args.receipt.is_absolute():
            raise ValueError('explicit_probe_scope_required')
        if args.print_hook:
            command = shlex.join([sys.executable, str(Path(__file__).resolve()), '--project', str(project),
                                  '--session-id', args.session_id, '--receipt', str(args.receipt)])
            print(json.dumps({'description': 'One scoped input receipt; no prompt logging or execution',
                              'hooks': {'UserPromptSubmit': [{'hooks': [
                                  {'type': 'command', 'command': command, 'timeout': 2}]}]}}, indent=2))
            return 0
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError('probe_input_too_large')
        result = capture(project, args.session_id, args.receipt,
                         json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant))
        if result:
            print(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        if args.print_hook:
            print('routing_probe: invalid scope', file=sys.stderr)
            return 2
        print(json.dumps({'systemMessage': 'ai-workflow input probe unavailable; original request continues.'}))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
