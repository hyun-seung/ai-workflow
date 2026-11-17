import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import routing_hook as hook


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.private = self.root / 'private'
        self.private.mkdir(mode=0o700)
        self.path = self.private / 'context.json'
        self.context = dict(version=1, enabled=True, project=str(self.project), session_id='session_example',
                            transcript_path=str(self.private / 'native.jsonl'), conversation_controls={}, request_hint=None)
        self.payload = dict(hook_event_name='UserPromptSubmit', cwd=str(self.project), session_id='session_example',
                            transcript_path=self.context['transcript_path'], turn_id='turn_example',
                            prompt='PRIVATE_RAW_PROMPT', model='unverified-model')

    def write(self):
        self.path.write_text(json.dumps(self.context))
        self.path.chmod(0o600)

    def run_cli(self, payload=None, *args):
        self.write()
        return subprocess.run([sys.executable, str(ROOT / 'scripts/routing_hook.py'), '--context', str(self.path), *args],
                              input=json.dumps(payload or self.payload).encode(), capture_output=True)

    def hint(self):
        self.context['request_hint'] = dict(id='request_example', revision=1, controls={'model_locked': True})

    def test_empty_or_disabled_state_adds_no_model_context(self):
        self.assertEqual(hook.bridge(self.context, self.payload), {})
        self.hint()
        self.context['enabled'] = False
        self.assertEqual(hook.bridge(self.context, self.payload), {})

    def test_only_explicit_controls_and_unconfirmed_hint_are_transmitted(self):
        self.hint()
        self.context['conversation_controls'] = {'delegation': 'forbid'}
        before = copy.deepcopy(self.context)
        result = hook.bridge(self.context, self.payload)
        text = result['hookSpecificOutput']['additionalContext']
        self.assertIn('unconfirmed_request', text)
        self.assertIn('model_locked', text)
        self.assertNotIn('PRIVATE_RAW_PROMPT', text)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn('unverified-model', text)
        self.assertNotIn('session_example', text)
        self.assertLessEqual(len(text), hook.MAX_CONTEXT)
        self.assertEqual(self.context, before)

    def test_wrong_project_session_transcript_event_and_subagent_are_skipped(self):
        self.hint()
        changes = [{'session_id': 'another'}, {'cwd': str(self.root)},
                   {'transcript_path': str(self.private / 'other.jsonl')},
                   {'hook_event_name': 'Stop'}, {'agent_id': 'worker'}, {'agent_type': 'worker'}, {'agent_id': ''}]
        for change in changes:
            with self.subTest(change=change):
                self.assertEqual(hook.bridge(self.context, dict(self.payload, **change)), {})

    def test_turn_and_prompt_shapes_must_be_valid(self):
        self.hint()
        for change in [{'turn_id': None}, {'prompt': 1}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                hook.bridge(self.context, dict(self.payload, **change))

    def test_snapshot_typos_and_fake_approval_fields_are_rejected(self):
        self.hint()
        bad = [dict(self.context, approved=True), dict(self.context, version=True),
               dict(self.context, conversation_controls={'model_loked': True}),
               dict(self.context, request_hint={'id': 'request_example', 'revision': True, 'controls': {}}),
               dict(self.context, request_hint={'id': 'request_example', 'revision': 1, 'controls': {}, 'selected': True})]
        for context in bad:
            with self.subTest(context=context), self.assertRaises(ValueError):
                hook.bridge(context, self.payload)

    def test_large_state_is_not_truncated_into_a_different_control(self):
        self.hint()
        with patch.object(hook, 'MAX_CONTEXT', 10), self.assertRaises(ValueError):
            hook.bridge(self.context, self.payload)

    def test_bridge_does_not_spawn_read_transcript_or_use_network(self):
        self.hint()
        with patch('subprocess.run', side_effect=AssertionError('spawn')), \
             patch('socket.socket', side_effect=AssertionError('network')), \
             patch.object(Path, 'read_text', side_effect=AssertionError('transcript read')):
            self.assertIn('hookSpecificOutput', hook.bridge(self.context, self.payload))

    def test_snapshot_requires_private_owned_regular_file_outside_git(self):
        self.write()
        self.assertEqual(hook.load_context(self.path), self.context)
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):hook.load_context(self.path)
        self.path.chmod(0o600)
        link = self.private / 'link.json'
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):hook.load_context(link)
        inside = self.project / 'state.json'
        inside.write_bytes(self.path.read_bytes()); inside.chmod(0o600)
        with self.assertRaises(ValueError):hook.load_context(inside)
        (self.private / '.git').mkdir()
        with self.assertRaises(ValueError):hook.load_context(self.path)

    def test_duplicate_and_nonfinite_snapshot_json_are_rejected(self):
        for raw in ['{"version":1,"version":1}', '{"version":NaN}', 'x' * (hook.MAX_STATE + 1)]:
            self.path.write_text(raw); self.path.chmod(0o600)
            with self.assertRaises(ValueError):hook.load_context(self.path)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'FIFO requires POSIX')
    def test_nonregular_context_does_not_wait_for_a_writer(self):
        fifo = self.private / 'context.pipe'
        os.mkfifo(fifo, 0o600)
        with self.assertRaisesRegex(ValueError, 'context_not_regular'):
            hook.load_context(fifo)

    def test_no_hint_cli_is_silent_and_does_not_write(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'')
        self.assertEqual(sorted(p.name for p in self.private.iterdir()), ['context.json'])

    def test_failure_is_advisory_and_never_leaks_or_blocks_original_prompt(self):
        result = self.run_cli(dict(self.payload, cwd='PRIVATE_BAD_PATH'))
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(set(output), {'systemMessage'})
        self.assertNotIn(b'PRIVATE', result.stdout + result.stderr)
        self.assertNotIn('decision', output)
        self.assertNotIn('continue', output)

    def test_print_hook_is_a_candidate_not_install_or_trust(self):
        result = self.run_cli(None, '--print-hook')
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        entry = output['hooks']['UserPromptSubmit'][0]['hooks'][0]
        self.assertEqual(entry['timeout'], 2)
        self.assertNotIn('--dangerously', entry['command'])
        self.assertFalse((self.project / '.codex').exists())


if __name__ == '__main__':
    unittest.main()
