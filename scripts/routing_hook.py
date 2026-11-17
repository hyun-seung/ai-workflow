#!/usr/bin/env python3
"""Read a scoped routing snapshot for Codex UserPromptSubmit; never classify or execute."""

import argparse
import json
import os
from pathlib import Path
import shlex
import stat
import sys

from request_router import controls, identifier, obj, reject_constant, unique
from run_records import validate_records_dir

ROOT = Path(__file__).resolve().parents[1]
MAX_INPUT = 1024 * 1024
MAX_STATE = 32768
MAX_CONTEXT = 512
PREFIX = 'ai-workflow 상태 힌트(실행 승인 아님). 현재 명시 지시 우선. 새 목표면 요청 힌트를 재사용하지 말고 재판단. '


def absolute(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError('absolute_path_required')
    return Path(value).resolve()


def validate_context(value):
    required = {'version', 'enabled', 'project', 'session_id', 'transcript_path',
                'conversation_controls', 'request_hint'}
    obj(value, required, 'context')
    if set(value) != required or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('invalid_context_version_or_fields')
    if type(value['enabled']) is not bool:
        raise ValueError('invalid_enabled')
    absolute(value['project'])
    absolute(value['transcript_path'])
    identifier(value['session_id'])
    controls(value['conversation_controls'])
    hint = value['request_hint']
    if hint is not None:
        obj(hint, {'id', 'revision', 'controls'}, 'request_hint')
        if set(hint) != {'id', 'revision', 'controls'}:
            raise ValueError('incomplete_request_hint')
        identifier(hint['id'])
        if type(hint['revision']) is not int or hint['revision'] < 1:
            raise ValueError('invalid_revision')
        controls(hint['controls'])
    return value


def load_context(path):
    path = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('context_symlink')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('context_not_regular')
        if os.name == 'posix' and (before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) & 0o077):
            raise ValueError('context_not_private')
        raw = stream.read(MAX_STATE + 1)
        after = os.fstat(stream.fileno())
    if len(raw) > MAX_STATE or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('context_changed_or_oversized')
    current = path.stat(follow_symlinks=False)
    if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
        raise ValueError('context_replaced')
    value = validate_context(json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant))
    project = absolute(value['project'])
    if not project.is_dir():
        raise ValueError('project_not_directory')
    validate_records_dir(path.parent, [ROOT, project])
    return value


def bridge(context, payload):
    context = validate_context(context)
    if not context['enabled']:
        return {}
    if not isinstance(payload, dict) or payload.get('hook_event_name') != 'UserPromptSubmit':
        return {}
    # Subagent hooks can share the parent's session id. Never attach its state to them.
    if payload.get('agent_id') is not None or payload.get('agent_type') is not None:
        return {}
    if payload.get('session_id') != context['session_id']:
        return {}
    if absolute(payload.get('cwd')) != absolute(context['project']):
        return {}
    if absolute(payload.get('transcript_path')) != absolute(context['transcript_path']):
        return {}
    identifier(payload.get('turn_id'))
    if not isinstance(payload.get('prompt'), str):
        raise ValueError('invalid_prompt_shape')
    # Only explicitly supplied metadata crosses into model context. No prompt/transcript read.
    state = {}
    if context['conversation_controls']:
        state['conversation'] = context['conversation_controls']
    if context['request_hint'] is not None:
        state['unconfirmed_request'] = context['request_hint']
    if not state:
        return {}
    text = PREFIX + json.dumps(state, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if len(text) > MAX_CONTEXT:
        raise ValueError('routing_context_too_large')
    return {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': text}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True)
    parser.add_argument('--print-hook', action='store_true', help='Print candidate only; never install or trust')
    args = parser.parse_args(argv)
    try:
        context = load_context(args.context)
        if args.print_hook:
            command = shlex.join([sys.executable, str(Path(__file__).resolve()),
                                  '--context', str(Path(args.context).resolve())])
            print(json.dumps({'description': 'Scoped routing hints; no classification or execution',
                              'hooks': {'UserPromptSubmit': [{'hooks': [
                                  {'type': 'command', 'command': command, 'timeout': 2}]}]}}, indent=2))
            return 0
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError('hook_input_too_large')
        result = bridge(context, json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant))
        if result:
            print(json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        # This advisory hook never blocks the original user request or echoes private values.
        if args.print_hook:
            print('routing_hook: invalid context', file=sys.stderr)
            return 2
        print(json.dumps({'systemMessage': 'ai-workflow routing hint unavailable; runtime settings unchanged.'}))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
