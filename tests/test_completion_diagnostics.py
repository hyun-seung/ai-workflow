"""Ordinary synthetic regression cases for v2 descriptor append persistence.

Old name-publication/race/fault-injection cases are archived unchanged privately,
not executed here; prefix/framing/legacy/schema tests cover the new contract.
"""
import ast
import inspect
import concurrent.futures
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import unittest

import test_completion_hook as fixture
import completion_hook as hook
import completion_diagnostics as diagnostic
from lifecycle_io import digest, locked


class CompletionDiagnosticsTests(unittest.TestCase):
    # Existing isolated synthetic fixture; no user config, source, ledger or DB.
    setUp = fixture.CompletionHookTests.setUp
    save = fixture.CompletionHookTests.save
    write_source = fixture.CompletionHookTests.write_source
    write_ledger = fixture.CompletionHookTests.write_ledger
    data = fixture.CompletionHookTests.data

    @property
    def journal(self):
        return Path(self.config['directory']) / 'invocations' / diagnostic.EVENTS_NAME

    @property
    def legacy(self):
        return self.journal.parent / diagnostic.LEGACY_NAME

    def stored(self):
        with diagnostic.directory_fd(self.journal.parent) as fd:
            return diagnostic.read(fd)

    def write_events(self, raw):
        self.journal.parent.parent.mkdir(mode=0o700, exist_ok=True)
        self.journal.parent.mkdir(mode=0o700, exist_ok=True)
        self.journal.write_bytes(raw); self.journal.chmod(0o600)

    def legacy_model(self, count=1, final=False):
        records = [dict(phase='entry', outcome='started', category='none', reason='none')]
        if final:
            records += [dict(phase='payload', outcome='started', category='none', reason='none'),
                        dict(phase='payload', outcome='ignored', category='none', reason='different_project', event_key='a' * 64)]
        return dict(version=1, invocations=[dict(id=f'{i:032x}', records=copy.deepcopy(records)) for i in range(count)])

    def create_legacy(self, count=1, final=False):
        self.journal.parent.parent.mkdir(mode=0o700, exist_ok=True)
        self.journal.parent.mkdir(mode=0o700, exist_ok=True)
        self.save(self.legacy, self.legacy_model(count, final))
        return self.legacy.read_bytes()

    def cli(self, raw=None, extra=()):
        result = subprocess.run([sys.executable, '-B', hook.__file__, '--config', str(self.config_path), *extra],
                                input=json.dumps(self.payload) if raw is None else raw,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        value = json.loads(result.stdout)
        self.assertFalse({'decision', 'continue', 'permissionDecision'} & set(value))
        return value

    def inspect(self):
        return diagnostic.inspect(self.config)

    def terminal(self):
        return self.inspect()['invocations'][-1]['records'][-1]

    def no_receipt(self):
        self.assertEqual(list(Path(self.config['directory']).glob('*.receipt.json')), [])

    def test_cli_success_and_replay_are_separate_assertions_with_immutable_prior_history(self):
        self.assertEqual(self.cli(), {})
        first = copy.deepcopy(self.inspect()['invocations'])
        terminal = self.terminal()
        self.assertEqual(terminal['phase'], 'collection')
        self.assertEqual(terminal['outcome'], 'collected')
        self.assertEqual(terminal['event_key'], digest(dict(session='session_example', turn='turn_one')))
        self.assertEqual([r['phase'] for r in first[0]['records'][:-1]], list(diagnostic.PHASES))
        self.cli()
        inspected = self.inspect()
        self.assertEqual(inspected['invocations'][:1], first)
        self.assertEqual(len(inspected['invocations']), 2)
        self.assertFalse(inspected['stop_receipt_verified'])
        self.assertFalse(inspected['usage_verified'])
        self.assertEqual(len([e for e in self.data()['events'] if e['kind'] == 'usage']), 1)
        self.assertEqual(stat.S_IMODE(self.journal.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.journal.parent.stat().st_mode), 0o700)

    def test_different_project_and_unregistered_have_no_receipt(self):
        self.payload['cwd'] = str(self.root)
        self.assertEqual(self.cli(), {})
        self.assertEqual(self.terminal()['reason'], 'different_project')
        self.payload['cwd'] = str(self.project)
        self.operation['dispatch_id'] = 'unregistered'; self.write_ledger()
        self.assertEqual(self.cli(), {})
        self.assertEqual(self.terminal()['reason'], 'no_registered_dispatch')
        self.assertEqual(self.terminal()['phase'], 'registration')
        self.no_receipt()

    def test_malformed_deep_oversize_and_invalid_event_are_observed_before_receipt(self):
        cases = [('PRIVATE_RAW{', 'malformed_json'),
                 (json.dumps(self.payload)[:-1] + ',"nested":' + '[' * 65 + '0' + ']' * 65 + '}', 'hook_json_too_deep'),
                 ('[' * 2000 + '0' + ']' * 2000, 'hook_json_too_deep'),
                 ('x' * (hook.MAX_INPUT + 1), 'hook_input_oversized'),
                 (json.dumps(dict(self.payload, session_id='invalid id')), 'invalid_identifier')]
        for raw, reason in cases:
            with self.subTest(reason=reason):
                self.assertIn('systemMessage', self.cli(raw))
                row = self.terminal()
                self.assertEqual((row['phase'], row['category'], row['outcome'], row['reason']),
                                 ('payload', 'input', 'error', reason))
        self.no_receipt()

    def test_source_identity_and_registration_failures_before_receipt_are_distinct(self):
        self.payload['session_id'] = 'wrong'
        self.cli()
        row = self.terminal()
        self.assertEqual((row['phase'], row['reason']), ('source', 'session_identity_or_project_mismatch'))
        self.payload['session_id'] = 'session_example'
        self.save(Path(self.config['ledger']), [])
        self.cli()
        self.assertEqual((self.terminal()['phase'], self.terminal()['reason']),
                         ('registration', 'hook_ledger_scope_mismatch'))
        self.write_ledger(); self.source.unlink()
        self.cli()
        self.assertEqual((self.terminal()['phase'], self.terminal()['reason']), ('source', 'source_unavailable'))
        with self.source.open('wb') as stream:
            stream.truncate(128 * 1024 * 1024 + 1)
        self.cli()
        self.assertEqual((self.terminal()['phase'], self.terminal()['reason']), ('source', 'session_file_limit'))
        self.no_receipt()

    def test_pending_is_final_invocation_but_not_verified_collection(self):
        self.rows.pop(); self.write_source()
        self.assertIn('pending', self.cli()['systemMessage'])
        self.assertEqual(self.terminal()['outcome'], 'pending')
        self.assertEqual(self.inspect()['invocations'][0]['state'], 'final')
        self.assertEqual(self.inspect()['status'], 'attention_required')
        self.assertFalse(any(e['kind'] == 'usage' for e in self.data()['events']))

    def test_storage_failure_does_not_fail_successful_cli_collection(self):
        self.journal.parent.parent.mkdir(mode=0o700)
        self.journal.parent.write_text('not a directory')
        self.assertEqual(self.cli(), {})
        self.assertEqual(len([e for e in self.data()['events'] if e['kind'] == 'usage']), 1)
        self.assertEqual(self.inspect()['status'], 'attention_required')

    def test_lock_contention_drops_observation_without_waiting_or_changing_collection(self):
        with locked(self.journal.parent / 'writer.lock', self.project):
            start = time.monotonic()
            self.assertEqual(self.cli(), {})
            self.assertLess(time.monotonic() - start, 5)
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.inspect()['status'], 'unknown')

    def test_cap_preserves_every_old_invocation_and_never_prevents_collection(self):
        for _ in range(diagnostic.MAX_INVOCATIONS): diagnostic.Observation(self.config)
        prior = self.journal.read_bytes()
        self.assertEqual(self.cli(), {})
        self.assertEqual(self.journal.read_bytes(), prior)
        self.assertTrue(self.inspect()['capacity_reached'])
        self.assertLessEqual(len(prior), diagnostic.MAX_APPEND_BYTES)
        self.assertLessEqual(len(list(self.journal.parent.iterdir())), 2)

    def test_concurrent_cli_invocations_preserve_valid_history_and_bounded_count(self):
        self.payload['cwd'] = str(self.root)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda _: self.cli(), range(6)))
        values = self.stored()['invocations']
        self.assertTrue(1 <= len(values) <= 6)
        self.assertEqual(len({i['id'] for i in values}), len(values))
        prior = copy.deepcopy(values)
        self.cli()
        self.assertEqual(self.stored()['invocations'][:-1], prior)
        self.no_receipt()


    def test_corrupt_oversize_foreign_field_and_orphan_are_never_adopted_or_printed(self):
        self.journal.parent.parent.mkdir(mode=0o700)
        self.journal.parent.mkdir(mode=0o700)
        for raw in ['PRIVATE_SECRET', 'x' * (diagnostic.MAX_BYTES + 1),
                    json.dumps(dict(version=1, invocations=[], raw_stdin='PRIVATE_SECRET'))]:
            self.journal.write_text(raw); self.journal.chmod(0o600)
            self.assertEqual(self.cli(), {})
            result = self.inspect()
            self.assertEqual(result['status'], 'attention_required')
            self.assertNotIn('PRIVATE_SECRET', json.dumps(result))
            self.assertEqual(self.journal.read_text(), raw)
        self.journal.unlink()
        orphan = self.journal.parent / '.lifecycle-interrupted'
        orphan.write_text('PRIVATE_SECRET'); orphan.chmod(0o600)
        self.cli()
        self.assertFalse(self.journal.exists()); self.assertTrue(orphan.exists())

    def test_symlink_and_nonprivate_diagnostic_storage_are_not_followed_or_modified(self):
        target = self.private / 'untouched'; target.mkdir(mode=0o700)
        self.journal.parent.parent.mkdir(mode=0o700)
        self.journal.parent.symlink_to(target, target_is_directory=True)
        self.cli()
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(self.inspect()['status'], 'attention_required')
        self.journal.parent.unlink(); self.journal.parent.mkdir(mode=0o700)
        secret = target / 'secret'; secret.write_text('PRIVATE_SECRET'); secret.chmod(0o600)
        self.journal.symlink_to(secret)
        self.cli()
        self.assertEqual(secret.read_text(), 'PRIVATE_SECRET')
        self.journal.unlink(); self.journal.parent.chmod(0o755)
        self.cli()
        self.assertFalse(self.journal.exists())

    def test_inspection_is_read_only_source_free_and_absence_is_unknown(self):
        self.assertEqual(self.inspect()['status'], 'unknown')
        self.assertFalse(Path(self.config['directory']).exists())
        self.cli(); self.source.unlink()
        paths = [self.config_path, Path(self.config['ledger']), self.db,
                 *Path(self.config['directory']).rglob('*')]
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
        self.assertEqual(self.inspect()['status'], 'observed')
        result = subprocess.run([sys.executable, '-B', diagnostic.__file__, '--config', str(self.config_path), 'inspect'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()})

    def test_privacy_and_unknown_reason_use_only_fixed_codes_and_hashed_event_reference(self):
        self.cli()
        observer = diagnostic.Observation(self.config); observer.phase('payload')
        observer.finish(error=ValueError('PRIVATE_ERROR /secret/transcript env=PRIVATE_ENV'))
        text = self.journal.read_text() + json.dumps(self.inspect())
        for value in ['PRIVATE_ERROR', 'PRIVATE_ENV', 'PRIVATE_NOT_TO_STORE', 'PRIVATE_TASK_BODY',
                      str(self.root), 'session_example', 'turn_one', 'total_tokens', 'task_title']:
            self.assertNotIn(value, text)
        self.assertEqual(self.terminal()['reason'], 'unknown')


    def test_strict_schema_rejects_forged_final_bad_hash_and_excess_records(self):
        self.cli()
        original = self.stored()
        for mutate in [lambda data: data['invocations'][0]['records'][-1].update(event_key='../secret'),
                       lambda data: data['invocations'][0]['records'][0].update(outcome='collected'),
                       lambda data: data['invocations'][0]['records'].append(data['invocations'][0]['records'][-1]),
                       lambda data: data['invocations'][0]['records'][-1].update(reason='private_error_code'),
                       lambda data: data['invocations'][0]['records'][-1].update(category='private_category')]:
            data = copy.deepcopy(original); mutate(data)
            with self.assertRaises(ValueError): diagnostic.validate(data)
            self.save(self.legacy, data)
            self.assertEqual(self.inspect()['status'], 'attention_required')
            self.assertEqual(self.inspect()['invocations'], [])

    def test_inspection_invalid_config_and_missing_evidence_have_fixed_nonzero_results(self):
        cmd = [sys.executable, '-B', diagnostic.__file__, '--config', str(self.config_path), 'inspect']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['status'], 'unknown')
        self.assertFalse(Path(self.config['directory']).exists())
        self.save(self.config_path, dict(self.config, directory=str(self.private / 'UNVALIDATED'), version=2))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(str(self.private), result.stdout + result.stderr)
        self.assertFalse((self.private / 'UNVALIDATED').exists())

    def test_invalid_config_help_print_hook_and_direct_api_create_no_observations(self):
        self.cli(extra=['--print-hook'])
        self.assertFalse(Path(self.config['directory']).exists())
        for script in (hook.__file__, diagnostic.__file__):
            result = subprocess.run([sys.executable, '-B', script, '--help'], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0)
        config = hook.load_config(self.config_path)
        self.assertFalse(Path(self.config['directory']).exists())
        self.assertEqual(hook.collect(config, self.payload, attempts=1, delay=0)['status'], 'collected')
        self.assertFalse(self.journal.parent.exists())
        self.save(self.config_path, dict(self.config, version=2))
        self.assertIn('systemMessage', self.cli())
        self.assertFalse(self.journal.parent.exists())










    def test_source_failure_classification_is_fixed_private_and_phase_scoped(self):
        cases = [
            (FileNotFoundError(2, 'PRIVATE_MESSAGE', '/PRIVATE_PATH'), 'source_unavailable'),
            (NotADirectoryError(20, 'PRIVATE_MESSAGE', '/PRIVATE_PATH'), 'source_unavailable'),
            (PermissionError(13, 'PRIVATE_MESSAGE', '/PRIVATE_PATH'), 'source_access_denied'),
            (IsADirectoryError(21, 'PRIVATE_MESSAGE', '/PRIVATE_PATH'), 'source_not_regular'),
            (ValueError('session_not_regular_file'), 'source_not_regular'),
            (ValueError('unsupported_assignment_header'), 'source_assignment_rejected'),
            (ValueError('ambiguous_assignment_header'), 'source_assignment_rejected'),
            (ValueError('multiple_session_meta'), 'source_parser_rejected'),
            (ValueError('duplicate_json_key'), 'source_parser_rejected'),
            (ValueError('nonfinite_json'), 'source_parser_rejected'),
            (ValueError('invalid_json_line_123'), 'unknown'),
            (ValueError('unsupported_assignment_header PRIVATE_MESSAGE'), 'unknown'),
            (ValueError('source_unavailable'), 'unknown'),
            (OSError('PRIVATE_MESSAGE'), 'unknown'),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__, expected=expected):
                observer = diagnostic.Observation(self.config); observer.phase('payload')
                observer.bind_event({'session_id': 'synthetic', 'turn_id': 'turn'})
                observer.phase('source'); observer.finish(error=error)
                self.assertEqual(self.terminal()['reason'], expected)
                self.assertEqual(self.terminal()['category'], 'source')
        text = self.journal.read_text() + json.dumps(self.inspect())
        for secret in ('PRIVATE_MESSAGE', 'PRIVATE_PATH', 'invalid_json_line', 'synthetic'):
            self.assertNotIn(secret, text)
        self.assertEqual(diagnostic.reason_code(PermissionError('PRIVATE_MESSAGE'), 'registration'), 'unknown')

    def test_actual_source_parser_and_assignment_rejections_use_fixed_codes(self):
        original = copy.deepcopy(self.rows)
        self.rows.append(copy.deepcopy(self.rows[0])); self.write_source()
        self.assertIn('systemMessage', self.cli())
        self.assertEqual(self.terminal()['reason'], 'source_parser_rejected')
        self.rows = copy.deepcopy(original)
        self.rows[3]['payload']['content'][0]['text'] = self.rows[3]['payload']['content'][0]['text'].replace('=== TASK ===', 'PRIVATE_INVALID_HEADER')
        self.write_source()
        self.assertIn('systemMessage', self.cli())
        self.assertEqual(self.terminal()['reason'], 'source_assignment_rejected')
        self.assertNotIn('PRIVATE_INVALID_HEADER', self.journal.read_text())
        self.no_receipt()

    def test_each_append_preserves_bytes_inode_and_complete_event_reconstruction(self):
        observer = diagnostic.Observation(self.config)
        before = self.journal.read_bytes(); inode = self.journal.stat().st_ino
        observer.phase('payload')
        self.assertTrue(self.journal.read_bytes().startswith(before))
        before = self.journal.read_bytes()
        observer.bind_event(dict(session_id='synthetic_session', turn_id='synthetic_turn'))
        observer.finish(result=dict(status='ignored', reason='different_project'))
        self.assertTrue(self.journal.read_bytes().startswith(before))
        self.assertEqual(self.journal.stat().st_ino, inode)
        rows = [json.loads(line) for line in self.journal.read_bytes().splitlines()]
        self.assertEqual([row['index'] for row in rows], [0, 1, 2])
        self.assertTrue(all(row['version'] == 2 for row in rows))
        self.assertEqual(self.inspect()['version'], 1)
        self.assertEqual(self.inspect()['status'], 'observed')
        self.assertEqual(self.terminal()['outcome'], 'ignored')
        self.assertFalse(self.legacy.exists())

    def test_legacy_only_read_preserves_bytes_file_set_and_public_meaning(self):
        before = self.create_legacy(final=True)
        names = set(self.journal.parent.iterdir())
        self.assertEqual(self.inspect()['status'], 'observed')
        self.assertEqual(self.inspect()['invocations'][0]['state'], 'final')
        self.assertEqual(self.legacy.read_bytes(), before)
        self.assertEqual(set(self.journal.parent.iterdir()), names)
        self.assertFalse(self.journal.exists())

    def test_combined_read_preserves_incomplete_legacy_history_without_promotion(self):
        before = self.create_legacy()
        self.assertEqual(self.cli(), {})
        result = self.inspect()
        self.assertEqual(len(result['invocations']), 2)
        self.assertEqual(result['invocations'][0]['state'], 'incomplete')
        self.assertEqual(result['invocations'][1]['state'], 'final')
        self.assertEqual(result['status'], 'attention_required')
        self.assertEqual(self.legacy.read_bytes(), before)
        self.assertEqual({p.name for p in self.journal.parent.iterdir()},
                         {diagnostic.LEGACY_NAME, diagnostic.EVENTS_NAME, 'writer.lock'})

    def test_cross_format_duplicate_id_is_rejected_and_neither_store_is_changed(self):
        legacy = self.create_legacy()
        item = self.legacy_model()['invocations'][0]
        raw = diagnostic.encode_event(item['id'], 0, item['records'][0])
        self.write_events(raw)
        self.assertEqual(self.inspect()['status'], 'attention_required')
        self.assertEqual(self.inspect()['invocations'], [])
        self.assertEqual(self.cli(), {})
        self.assertEqual(self.legacy.read_bytes(), legacy)
        self.assertEqual(self.journal.read_bytes(), raw)

    def test_every_proper_frame_prefix_is_incomplete_and_committed_prefix_is_intact(self):
        observer = diagnostic.Observation(self.config)
        committed = self.journal.read_bytes()
        record = dict(phase='payload', outcome='started', category='none', reason='none')
        frame = diagnostic.encode_event(observer.id, 1, record)
        # Static pre-existing input fixtures, not injected short writes or races.
        for cut in range(1, len(frame)):
            with self.subTest(cut=cut):
                raw = committed + frame[:cut]
                self.write_events(raw)
                self.assertEqual(self.inspect()['status'], 'attention_required')
                self.assertEqual(self.inspect()['invocations'], [])
                self.assertEqual(self.journal.read_bytes(), raw)
                self.assertEqual(raw[:len(committed)], committed)
        self.assertEqual(self.cli(), {})
        self.assertEqual(self.journal.read_bytes(), committed + frame[:-1])

    def test_invalid_v2_sequence_checksum_version_and_schema_are_not_adopted(self):
        diagnostic.Observation(self.config)
        initial = self.journal.read_bytes(); original = json.loads(initial)
        changed = copy.deepcopy(original); changed['record']['reason'] = 'unknown'
        variants = [initial + initial,
                    diagnostic.encode_event(original['id'], 1, original['record']),
                    json.dumps(changed).encode() + b'\n',
                    json.dumps(dict(original, version=True)).encode() + b'\n',
                    json.dumps(dict(original, index=False)).encode() + b'\n',
                    json.dumps(dict(original, extra='PRIVATE_TEXT')).encode() + b'\n',
                    b'{"version":2,"version":2}\n', b'NaN\n']
        for raw in variants:
            self.write_events(raw)
            self.assertEqual(self.inspect()['status'], 'attention_required')
            self.assertEqual(self.inspect()['invocations'], [])
            self.assertEqual(self.journal.read_bytes(), raw)
            self.assertNotIn('PRIVATE_TEXT', json.dumps(self.inspect()))

    def test_v2_frame_byte_event_count_and_file_byte_limits(self):
        entry = self.legacy_model()['invocations'][0]['records'][0]
        frame = diagnostic.encode_event('f' * 32, 0, entry)
        variants = [b' ' * diagnostic.MAX_EVENT_BYTES + frame,
                    frame * (diagnostic.MAX_EVENTS + 1),
                    b'x' * (diagnostic.MAX_APPEND_BYTES + 1),
                    diagnostic.encode_event('f' * 32, 6, entry)]
        for raw in variants:
            self.write_events(raw)
            self.assertEqual(self.inspect()['status'], 'attention_required')
            self.assertEqual(self.journal.read_bytes(), raw)
        with self.assertRaises(ValueError):
            diagnostic.encode_event('f' * 32, 0, dict(entry, reason='x' * diagnostic.MAX_EVENT_BYTES))

    def test_legacy_size_mode_and_schema_limits_preserve_legacy_bytes(self):
        self.create_legacy()
        for raw in [b'PRIVATE_LEGACY_TEXT', b'x' * (diagnostic.MAX_BYTES + 1),
                    json.dumps(dict(version=True, invocations=[])).encode()]:
            self.legacy.write_bytes(raw)
            self.assertEqual(self.inspect()['status'], 'attention_required')
            self.assertEqual(self.cli(), {})
            self.assertEqual(self.legacy.read_bytes(), raw)
            self.assertNotIn('PRIVATE_LEGACY_TEXT', json.dumps(self.inspect()))
        self.save(self.legacy, self.legacy_model()); self.legacy.chmod(0o644)
        self.assertEqual(self.inspect()['status'], 'attention_required')

    def test_global_capacity_includes_legacy_and_allows_admitted_v2_to_finish(self):
        before = self.create_legacy(diagnostic.MAX_INVOCATIONS - 1)
        active = diagnostic.Observation(self.config); prefix = self.journal.read_bytes()
        self.assertTrue(self.inspect()['capacity_reached'])
        diagnostic.Observation(self.config)
        self.assertEqual(self.journal.read_bytes(), prefix)
        active.phase('payload'); active.bind_event(dict(session_id='example', turn_id='example_turn'))
        active.finish(result=dict(status='ignored', reason='different_project'))
        self.assertTrue(self.journal.read_bytes().startswith(prefix))
        self.assertEqual(self.legacy.read_bytes(), before)
        self.assertEqual(len(self.inspect()['invocations']), diagnostic.MAX_INVOCATIONS)
        full = self.journal.read_bytes()
        self.assertEqual(self.cli(), {})
        self.assertEqual(self.journal.read_bytes(), full)

    def test_combined_over_capacity_and_old_residue_require_attention(self):
        before = self.create_legacy(diagnostic.MAX_INVOCATIONS)
        raw = diagnostic.encode_event('f' * 32, 0, self.legacy_model()['invocations'][0]['records'][0])
        self.write_events(raw)
        self.assertEqual(self.inspect()['status'], 'attention_required')
        self.assertEqual(self.journal.read_bytes(), raw); self.assertEqual(self.legacy.read_bytes(), before)
        self.journal.unlink()
        residue = self.journal.parent / '.lifecycle-pending'
        residue.write_bytes(b'OLD_RESIDUE'); residue.chmod(0o600)
        self.assertEqual(self.cli(), {})
        self.assertEqual(residue.read_bytes(), b'OLD_RESIDUE')
        self.assertFalse(self.journal.exists())

    def test_empty_store_and_unfinished_invocation_are_not_success(self):
        self.write_events(b'')
        self.assertEqual(self.inspect()['status'], 'unknown')
        diagnostic.Observation(self.config)
        first = copy.deepcopy(self.inspect()['invocations'])
        self.assertEqual(first[0]['state'], 'incomplete')
        self.cli()
        self.assertEqual(self.inspect()['invocations'][:1], first)
        self.assertEqual(self.inspect()['status'], 'attention_required')
        self.assertEqual(self.inspect()['root_cause'], 'undetermined')

    def test_static_hardlink_and_nonregular_v2_file_are_rejected_without_blocking(self):
        diagnostic.Observation(self.config); raw = self.journal.read_bytes()
        link = self.private / 'linked'; os.link(self.journal, link)
        self.assertEqual(self.inspect()['status'], 'attention_required')
        self.assertEqual(self.cli(), {}); self.assertEqual(link.read_bytes(), raw)
        link.unlink(); self.journal.unlink(); os.mkfifo(self.journal, 0o600)
        self.assertEqual(self.inspect()['status'], 'attention_required')
        self.assertEqual(self.cli(), {})
        self.assertTrue(stat.S_ISFIFO(self.journal.stat().st_mode))

    def test_source_inspection_confirms_no_name_publication_or_destructive_calls(self):
        tree = ast.parse(Path(diagnostic.__file__).read_text())
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(calls & {'replace', 'rename', 'unlink', 'truncate', 'ftruncate', 'mkstemp'})
        flags = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertNotIn('O_TRUNC', flags)
        append_tree = ast.parse(inspect.getsource(diagnostic.append_event))
        writes = [node for node in ast.walk(append_tree) if isinstance(node, ast.Call) and
                  isinstance(node.func, ast.Attribute) and node.func.attr == 'write']
        self.assertEqual(len(writes), 1)
        self.assertIsInstance(writes[0].args[0], ast.Name)
        self.assertEqual(writes[0].args[0].id, 'fd')





if __name__ == '__main__':
    unittest.main()
