import copy
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import native_record as nr


def event(kind, payload):
    return {'type': kind, 'timestamp': '2026-01-01T00:00:00Z', 'payload': payload}


def context(**changes):
    p = {'model': 'example-model', 'effort': 'low', 'approvals_reviewer': 'auto_review',
         'approval_policy': 'on-request', 'sandbox_policy': {'type': 'workspace-write', 'network_access': False}}
    p.update(changes)
    return event('turn_context', p)


def assigned(task='task_example', dispatch='ctx_example', body='PRIVATE_TASK_BODY'):
    text = (nr.PREAMBLE + '\nYour task ID is: ' + task +
            '\nYour Orca session ID is: orca_session_id:example\n'
            '=== CLI COMMANDS ===\n'
            'orca orchestration send --task-id ' + task + ' --dispatch-id ' + dispatch +
            '\n=== TASK ===\n' + body)
    return event('response_item', {'type': 'message', 'role': 'user',
                                  'content': [{'type': 'input_text', 'text': text}]})


def usage(inputs=100, outputs=20, **changes):
    values = {'input_tokens': inputs, 'output_tokens': outputs, 'cached_input_tokens': 70,
              'reasoning_output_tokens': 5, 'total_tokens': inputs + outputs}
    values.update(changes)
    return event('event_msg', {'type': 'token_count', 'info': {'total_token_usage': values}})


class NativeRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.source = self.root / 'session.jsonl'
        self.output = self.root / 'private' / 'metadata.json'
        self.meta = event('session_meta', {'id': 'session_example', 'cwd': str(self.project),
                                         'originator': 'orca_desktop', 'source': 'vscode', 'cli_version': '0.0.0'})
        self.rows = [self.meta, context(), assigned(), usage()]
        self.save()

    def save(self):
        self.source.write_text(''.join(json.dumps(r) + '\n' for r in self.rows), encoding='utf-8')

    def inspect(self, **changes):
        args = dict(project=self.project, session_file=self.source, session_id='session_example',
                    task_id='task_example', dispatch_id='ctx_example', expected={'reviewer': 'auto_review'})
        args.update(changes)
        return nr.inspect_session(**args)

    def cli(self, *extra):
        return subprocess.run([sys.executable, str(Path(nr.__file__)), '--project', str(self.project),
                               '--session-file', str(self.source), '--session-id', 'session_example',
                               '--task-id', 'task_example', '--dispatch-id', 'ctx_example',
                               '--output', str(self.output), *extra], text=True, capture_output=True, timeout=10)

    def test_metadata_binding_and_source_hash(self):
        result = self.inspect()
        self.assertEqual(result['binding']['line'], 3)
        self.assertEqual(result['verification']['status'], 'pass')
        self.assertEqual(result['source']['sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(result['observed_contexts'][0]['values']['model'], 'example-model')
        self.assertFalse(result['live_state_verified'])
        self.assertFalse(result['task_acceptance_evaluated'])

    def test_rejects_wrong_identity_and_project(self):
        for change in ({'session_id': 'different'}, {'task_id': 'different'}, {'dispatch_id': 'different'},
                       {'project': self.root}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.inspect(**change)

    def test_excludes_guardian_and_other_origins(self):
        for source, origin in (({'subagent': {'other': 'guardian'}}, 'orca_desktop'),
                               ('cli', 'orca_desktop'), ('vscode', 'other')):
            self.rows[0]['payload'].update(source=source, originator=origin)
            self.save()
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, 'not_supported'):
                self.inspect()

    def test_reference_in_task_body_is_not_assignment(self):
        self.rows[2] = assigned('task_other', 'ctx_other', body='Your task ID is: task_example\n--dispatch-id ctx_example')
        self.save()
        with self.assertRaisesRegex(ValueError, 'missing_or_ambiguous'):
            self.inspect()

    def test_quoted_preamble_not_a_real_assignment(self):
        self.rows[2]['payload']['content'][0]['text'] = 'Quoted example:\n' + self.rows[2]['payload']['content'][0]['text']
        self.save()
        with self.assertRaises(ValueError):
            self.inspect()

    def test_ambiguous_header_or_duplicate_assignment_rejected(self):
        original = copy.deepcopy(self.rows)
        self.rows[2]['payload']['content'][0]['text'] = self.rows[2]['payload']['content'][0]['text'].replace(
            '=== TASK ===', '--dispatch-id ctx_other\n=== TASK ===')
        self.save()
        with self.assertRaises(ValueError):
            self.inspect()
        self.rows = original + [context(), assigned()]
        self.save()
        with self.assertRaises(ValueError):
            self.inspect()

    def test_raw_body_tools_and_hidden_reasoning_are_not_saved(self):
        self.rows.extend([event('response_item', {'type': 'reasoning', 'summary': 'PRIVATE_REASONING'}),
                          event('response_item', {'role': 'assistant', 'channel': 'analysis',
                                                 'content': [{'type': 'text', 'text': 'PRIVATE_ANALYSIS'}]}),
                          event('response_item', {'type': 'function_call_output', 'output': 'PRIVATE_TOOL_OUTPUT'})])
        self.rows[1]['payload']['extra_secret'] = 'PRIVATE_CONTEXT_EXTRA'
        self.rows[-1]['payload']['credentials'] = 'PRIVATE_CREDENTIAL'
        self.save()
        text = json.dumps(self.inspect())
        self.assertNotIn('PRIVATE_', text)

    def test_all_contexts_checked_when_mode_changes(self):
        self.rows.extend([context(approvals_reviewer='user'), usage(200, 30)])
        self.save()
        result = self.inspect()
        self.assertEqual(len(result['observed_contexts']), 2)
        self.assertEqual(result['verification']['checks']['reviewer']['status'], 'fail')

    def test_missing_expected_field_is_unverified(self):
        del self.rows[1]['payload']['approvals_reviewer']
        self.save()
        self.assertEqual(self.inspect()['verification']['status'], 'unverified')

    def test_no_expectations_is_not_a_verification_pass(self):
        self.assertEqual(self.inspect(expected={})['verification']['status'], 'not_requested')

    def test_unknown_nested_policy_and_network_not_inferred(self):
        self.rows[1] = context(approval_policy={'granular': {}}, sandbox_policy={'type': 'workspace-write'})
        self.save()
        result = self.inspect(expected={'approval_policy': 'on-request'})
        self.assertEqual(result['verification']['status'], 'unverified')
        self.assertIsNone(result['observed_contexts'][0]['values']['network_access'])

    def test_cumulative_usage_uses_last_snapshot_once(self):
        self.rows.extend([usage(), usage(200, 40), usage(200, 40)])
        self.save()
        value = self.inspect()['usage']
        self.assertEqual(value['tokens']['total_tokens'], 240)
        self.assertEqual(value['status'], 'measured')
        self.assertEqual(value['scope'], 'session_cumulative')
        self.assertFalse(value['task_attributed'])

    def test_later_assignment_does_not_change_earlier_profile_or_usage(self):
        self.rows.extend([context(model='another-model', approvals_reviewer='user'),
                          assigned('task_second', 'ctx_second'), usage(300, 50)])
        self.save()
        first = self.inspect()
        second = self.inspect(task_id='task_second', dispatch_id='ctx_second', expected={'reviewer': 'user'})
        self.assertEqual(first['verification']['status'], 'pass')
        self.assertEqual(first['usage']['tokens']['total_tokens'], 120)
        self.assertEqual(second['usage']['tokens']['total_tokens'], 350)
        self.assertEqual(first['usage']['measurement_key'], second['usage']['measurement_key'])
        self.assertFalse(second['usage']['task_attributed'])
        self.assertEqual(second['observed_contexts'][0]['values']['model'], 'another-model')

    def test_missing_followup_context_rejects_both_attributions(self):
        self.rows.extend([assigned('task_second', 'ctx_second'), usage(300, 50)])
        self.save()
        for args in ({}, {'task_id': 'task_second', 'dispatch_id': 'ctx_second'}):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, 'boundary_unknown'):
                self.inspect(**args)

    def test_missing_or_empty_usage_is_not_zero(self):
        variants = [None, {}, {'total_token_usage': {}}, {'total_token_usage': {'input_tokens': None}}]
        for info in variants:
            self.rows[3]['payload']['info'] = info
            self.save()
            result = self.inspect()['usage']
            self.assertEqual(result['status'], 'unavailable')
            self.assertIsNone(result['tokens']['total_tokens'])

    def test_no_usage_event(self):
        self.rows.pop()
        self.save()
        self.assertEqual(self.inspect()['usage']['status'], 'unavailable')

    def test_invalid_token_values_mark_unavailable(self):
        for change in ({'input_tokens': True}, {'input_tokens': -1}, {'input_tokens': '100'},
                       {'cached_input_tokens': 101}, {'reasoning_output_tokens': 21}, {'total_tokens': 999}):
            self.rows[3] = usage(**change)
            self.save()
            with self.subTest(change=change):
                self.assertEqual(self.inspect()['usage']['status'], 'unavailable')

    def test_decreasing_usage_and_late_missing_measurement_are_partial(self):
        self.rows.extend([usage(80, 15), event('event_msg', {'type': 'token_count', 'info': None})])
        self.save()
        value = self.inspect()['usage']
        self.assertEqual(value['status'], 'partial')
        self.assertIn('non_monotonic_session_usage', value['issues'])

    def test_optional_token_fields_missing_and_exact_total_derived(self):
        self.rows[3]['payload']['info']['total_token_usage'] = {'input_tokens': 100, 'output_tokens': 20}
        self.save()
        value = self.inspect()['usage']
        self.assertEqual(value['status'], 'measured')
        self.assertEqual(value['tokens']['total_tokens'], 120)
        self.assertIsNone(value['tokens']['cached_input_tokens'])

    def test_json_errors_do_not_echo_private_content(self):
        for raw in ('{"PRIVATE_MALFORMED":', '{"type":"a","type":"b","payload":{}}',
                    '{"type":"x","payload":{"n":NaN}}', '[]'):
            self.source.write_text(raw)
            result = self.cli()
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('PRIVATE_', result.stderr)
            self.assertFalse(self.output.parent.exists())

    def test_file_and_line_limits_fail_without_output(self):
        for limit in ('MAX_BYTES', 'MAX_LINE_BYTES'):
            with patch.object(nr, limit, 8), self.assertRaisesRegex(ValueError, 'limit'):
                self.inspect()

    def test_read_changes_are_detected(self):
        original = nr.observed
        def modify(payload):
            with self.source.open('a') as f:
                f.write('\n')
            return original(payload)
        with patch.object(nr, 'observed', modify), self.assertRaisesRegex(ValueError, 'changed_during_read'):
            self.inspect()

    def test_private_record_permissions_and_no_overwrite(self):
        record = self.inspect()
        path = nr.write_record(record, self.output, self.project)
        original = path.read_bytes()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        with self.assertRaises(FileExistsError):
            nr.write_record(record, self.output, self.project)
        self.assertEqual(path.read_bytes(), original)

    def test_public_git_and_symlink_outputs_rejected(self):
        gitdir = self.root / 'gitrepo'
        gitdir.mkdir(); (gitdir / '.git').mkdir()
        public = self.root / 'public'; public.mkdir(mode=0o755)
        private = self.root / 'safe'; private.mkdir(mode=0o700)
        alias = self.root / 'alias'; alias.symlink_to(private, target_is_directory=True)
        for out in (self.project / 'out.json', gitdir / 'out' / 'out.json', public / 'out.json', alias / 'out.json'):
            with self.subTest(output=out), self.assertRaises(ValueError):
                nr.write_record(self.inspect(), out, self.project)
            self.assertFalse(out.exists())

    def test_cli_success_and_failure_codes_preserve_mismatch_record(self):
        result = self.cli('--expect-reviewer', 'user')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(self.output.read_text())['verification']['status'], 'fail')
        self.output.unlink()
        result = self.cli('--expect-reviewer', 'auto_review', '--expect-model', 'example-model')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['verification'], 'pass')
        self.assertNotIn('input_tokens', result.stdout)

    def test_cli_unknown_and_bad_input_codes(self):
        self.rows[1]['payload'].pop('approvals_reviewer'); self.save()
        result = self.cli('--expect-reviewer', 'auto_review')
        self.assertEqual(result.returncode, 1)
        self.output.unlink()
        self.rows[0]['payload']['id'] = 'wrong'; self.save()
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.output.exists())

    def test_cli_request_context_collects_dispatch_usage_separately(self):
        self.rows.insert(1,event('event_msg',{'type':'task_started','turn_id':'native_one'}))
        self.rows[-1]['payload']['info']['last_token_usage']=dict(self.rows[-1]['payload']['info']['total_token_usage'])
        self.rows.append(event('event_msg',{'type':'task_complete','turn_id':'native_one'}));self.save()
        result=self.cli('--request-id','request_example','--role','backend')
        self.assertEqual(result.returncode,0,result.stderr)
        saved=json.loads(self.output.read_text())
        self.assertTrue(saved['dispatch_usage']['task_attributed'])
        self.assertEqual(saved['dispatch_usage']['measurements']['total_tokens'],120)
        self.assertFalse(saved['usage']['task_attributed'])
        self.assertNotIn('input_tokens',result.stdout)

    def test_duplicate_meta_and_missing_context_rejected(self):
        original = copy.deepcopy(self.rows)
        for rows in ([self.meta] + original, [self.meta, assigned(), usage()]):
            self.rows = rows; self.save()
            with self.assertRaises(ValueError):
                self.inspect()

    def snapshot(self, model=None, effort=None):
        value = {'ok': True, 'result': {
            'dispatch': {'id': 'ctx_example', 'taskId': 'task_example', 'processIncarnation': 'structured:example'},
            'worker': {'dispatchId': 'ctx_example', 'startOptions': {'launch': {'requested': {
                'agent': 'codex', 'model': model, 'effort': effort}}}}}}
        path = self.root / 'worker.json'
        path.write_text(json.dumps(value))
        return path

    def test_expectations_are_not_fabricated_launch_settings(self):
        result = self.inspect(expected={'model': 'example-model'})
        self.assertEqual(result['expected']['model'], 'example-model')
        self.assertIsNone(result['requested']['model'])
        self.assertEqual(result['requested']['status'], 'unavailable')

    def test_native_defaults_differ_from_unknown_launch(self):
        result = self.inspect(worker_snapshot=self.snapshot())
        self.assertEqual(result['requested']['status'], 'observed')
        self.assertIsNone(result['requested']['model'])
        self.assertEqual(result['observed_contexts'][0]['values']['model'], 'example-model')

    def test_native_model_mismatch_is_checked_separately(self):
        result = self.inspect(worker_snapshot=self.snapshot('other-model', 'low'))
        self.assertEqual(result['verification']['checks']['launch_model']['status'], 'fail')
        self.assertEqual(result['verification']['checks']['launch_effort']['status'], 'pass')
        self.assertEqual(result['verification']['status'], 'fail')

    def test_snapshot_must_match_dispatch_task_and_orca_session(self):
        for field in ('id', 'taskId', 'processIncarnation'):
            path = self.snapshot()
            value = json.loads(path.read_text())
            value['result']['dispatch'][field] = 'different'
            path.write_text(json.dumps(value))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'mismatched_launch'):
                self.inspect(worker_snapshot=path)


if __name__ == '__main__':
    unittest.main()
