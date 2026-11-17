import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import routing_probe as probe


class ProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.receipt = self.root / 'private' / 'receipt.json'
        self.payload = dict(hook_event_name='UserPromptSubmit', session_id='session_example', turn_id='turn_example',
                            cwd=str(self.project), transcript_path=str(self.root / 'native.jsonl'), prompt='PRIVATE_REQUEST')

    def capture(self, **changes):
        return probe.capture(self.project, 'session_example', self.receipt, dict(self.payload, **changes))

    def test_one_receipt_captures_only_explicit_metadata(self):
        result = self.capture()
        saved = json.loads(self.receipt.read_text())
        self.assertEqual(saved['transcript_path'], self.payload['transcript_path'])
        self.assertNotIn('prompt', saved)
        self.assertNotIn('PRIVATE_REQUEST', self.receipt.read_text() + json.dumps(result))
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)

    def test_followup_does_not_rewrite_or_reinject_marker(self):
        self.capture(); before = self.receipt.read_bytes(), self.receipt.stat().st_mtime_ns
        self.assertEqual(self.capture(turn_id='turn_two'), {})
        self.assertEqual((self.receipt.read_bytes(), self.receipt.stat().st_mtime_ns), before)

    def test_other_session_project_event_and_subagent_do_not_capture(self):
        for changes in ({'session_id': 'other'}, {'cwd': str(self.root)}, {'hook_event_name': 'Stop'},
                        {'agent_id': 'worker'}, {'agent_type': 'worker'}):
            with self.subTest(changes=changes):self.assertEqual(self.capture(**changes), {})
        self.assertFalse(self.receipt.exists())

    def test_changed_transcript_is_not_silently_rebound(self):
        self.capture(); before = self.receipt.read_bytes()
        with self.assertRaisesRegex(ValueError, 'binding_changed'):
            self.capture(transcript_path=str(self.root / 'other.jsonl'))
        self.assertEqual(self.receipt.read_bytes(), before)

    def test_corrupt_prior_receipt_is_not_accepted_as_a_real_capture(self):
        self.capture()
        for change in ({'version': True}, {'turn_id': None}):
            previous = json.loads(self.receipt.read_text()); previous.update(change)
            self.receipt.write_text(json.dumps(previous))
            with self.assertRaises(ValueError):self.capture()
            self.receipt.unlink(); self.capture()

    def test_no_transcript_prompt_network_or_runtime_read(self):
        class UnreadablePrompt:
            def __str__(self):raise AssertionError('prompt read')
        with patch('socket.socket', side_effect=AssertionError('network')), patch('subprocess.run', side_effect=AssertionError('runtime')):
            self.capture(prompt=UnreadablePrompt())
        self.assertFalse(Path(self.payload['transcript_path']).exists())

    def test_cli_definition_is_only_a_candidate_and_failure_never_blocks(self):
        args = [sys.executable, str(ROOT / 'scripts/routing_probe.py'), '--project', str(self.project),
                '--session-id', 'session_example', '--receipt', str(self.receipt)]
        result = subprocess.run([*args, '--print-hook'], capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('UserPromptSubmit', json.loads(result.stdout)['hooks'])
        self.assertFalse(self.receipt.parent.exists())
        self.assertFalse((self.project / '.codex').exists())
        result = subprocess.run(args, input=b'PRIVATE_INVALID_JSON', capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(b'PRIVATE_INVALID_JSON', result.stdout + result.stderr)
        self.assertNotIn('decision', json.loads(result.stdout))

    def test_receipt_inside_project_and_symlinks_are_rejected(self):
        with self.assertRaises(ValueError):probe.capture(self.project, 'session_example', self.project / 'receipt.json', self.payload)
        self.capture()
        linked = self.root / 'link.json'; linked.symlink_to(self.receipt)
        with self.assertRaises(ValueError):probe.capture(self.project, 'session_example', linked, self.payload)


if __name__ == '__main__':
    unittest.main()
