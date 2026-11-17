import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import feedback_exchange as feedback
from trace_store import Store, encoded, load_bundle
from trace_usage import import_usage

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'traces'


class FeedbackExchangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.root.chmod(0o700)
        self.trace = self.root / 'trace.sqlite3'
        self.store = Store(self.trace); self.addCleanup(self.store.close)
        self.source = load_bundle(FIXTURES / 'sample.json')
        self.source['requests'][0]['title'] = '/Users/private-person/secret-project Bearer secret-token'
        self.store.import_bundle(self.source)
        self.input = self.root / 'native.json'
        self.input.write_text((FIXTURES / 'native-usage.json').read_text())
        import_usage(self.store, self.input)
        self.key = bytes(range(32))
        self.db = self.root / 'combined.sqlite3'

    def bundle(self):
        return feedback.make_bundle(self.store.request('sample-request'), self.key, 'a' * 40, 'orca', project_id='example-project')

    def resign(self, b):
        b['sha256'] = hashlib.sha256(encoded(b['payload']).encode()).hexdigest()
        return b

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'scripts' / 'feedback_exchange.py'), *map(str, args)],
                              text=True, capture_output=True)

    def test_projection_excludes_raw_ids_paths_text_and_proof(self):
        b = self.bundle(); text = encoded(b)
        for secret in ('private-person', 'secret-project', 'secret-token', 'sample-request',
                       'sample-worker', 'session_example', 'turn_example', '/path/', 'task_title', 'increments'):
            self.assertNotIn(secret, text)
        self.assertFalse(b['payload']['full_usage_complete'])
        self.assertEqual(b['payload']['observations'][0]['tokens']['total_tokens'], 120)

    def test_two_pc_copy_import_reimport_and_readonly_source(self):
        before = self.trace.read_bytes()
        keypath = self.root / 'exchange.key'; feedback.write_private(keypath, self.key)
        out = self.root / 'result.json'
        result = self.cli('export', '--db', self.trace, '--request-id', 'sample-request', '--key', keypath,
                          '--project-id', 'example-project', '--workflow-commit', 'a' * 40, '--app', 'orca', '--out', out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        copied = self.root / 'received.json'; feedback.write_private(copied, out.read_bytes())
        for file, duplicate in ((out, False), (copied, True)):
            result = self.cli('import', '--db', self.db, '--file', file)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['duplicate'], duplicate)
        summary = json.loads(self.cli('summary', '--db', self.db).stdout)
        self.assertEqual((summary['requests'], summary['observations'], summary['measured_total_tokens']), (1, 1, 120))
        self.assertEqual(summary['actors_with_measurements'], 1)
        self.assertEqual(before, self.trace.read_bytes())

    def test_unknown_not_zero_and_legacy_not_promoted(self):
        data = self.store.request('sample-request'); data['events'] = []
        data['actors'][0]['usage'] = {'measurements': {'total_tokens': 500}}
        b = feedback.make_bundle(data, self.key, 'b' * 40, project_id='example-project')
        feedback.merge(self.db, b)
        s = feedback.summary(self.db)
        self.assertIsNone(s['measured_total_tokens'])
        self.assertEqual(s['actors_with_measurements'], 0)
        self.assertEqual(s['results'][0]['declared']['verification'], 'unknown')

    def test_genuine_zero_preserved(self):
        b = self.bundle()
        for k in b['payload']['observations'][0]['tokens']:
            b['payload']['observations'][0]['tokens'][k] = 0
        feedback.merge(self.db, self.resign(b))
        self.assertEqual(feedback.summary(self.db)['measured_total_tokens'], 0)

    def test_same_request_changed_conflicts_without_writes(self):
        b = self.bundle(); feedback.merge(self.db, b); before = self.db.read_bytes()
        b['payload']['declared']['verification'] = 'passed'
        with self.assertRaises(ValueError): feedback.merge(self.db, self.resign(b))
        self.assertEqual(before, self.db.read_bytes())

    def test_same_physical_measurement_under_other_app_or_request_rejected(self):
        b = self.bundle(); feedback.merge(self.db, b); before = self.db.read_bytes()
        b['payload'].update(request='b' * 64, app='paseo')
        with self.assertRaises(ValueError): feedback.merge(self.db, self.resign(b))
        self.assertEqual(before, self.db.read_bytes())

    def test_overlapping_session_different_turn_is_rejected(self):
        b = self.bundle(); feedback.merge(self.db, b)
        b['payload']['request'] = 'b' * 64
        o = b['payload']['observations'][0]; o.update(id='c' * 64, start=6, end=12)
        with self.assertRaises(ValueError): feedback.merge(self.db, self.resign(b))
        self.assertEqual(feedback.summary(self.db)['observations'], 1)

    def test_distinct_physical_turns_and_requests_combine(self):
        b = self.bundle(); feedback.merge(self.db, b)
        b['payload']['request'] = 'b' * 64
        b['payload']['observations'][0].update(id='c' * 64, start=10, end=15)
        feedback.merge(self.db, self.resign(b))
        s = feedback.summary(self.db)
        self.assertEqual((s['requests'], s['observations'], s['measured_total_tokens']), (2, 2, 240))

    def test_different_key_namespace_rejected(self):
        feedback.merge(self.db, self.bundle())
        b = feedback.make_bundle(self.store.request('sample-request'), b'x' * 32, 'a' * 40, project_id='example-project')
        with self.assertRaises(ValueError): feedback.merge(self.db, b)
        self.assertEqual(feedback.summary(self.db)['requests'], 1)

    def test_invalid_or_extra_fields_never_create_destination(self):
        base = self.bundle()
        for change in ('extra', 'digest', 'total', 'binding', 'bool_version', 'model'):
            with self.subTest(change=change):
                b = copy.deepcopy(base)
                if change == 'extra': b['payload']['raw_path'] = '/private/secret'
                if change == 'digest': b['sha256'] = 'f' * 64
                if change == 'total': b['payload']['observations'][0]['tokens']['total_tokens'] = 121
                if change == 'binding': b['payload']['observations'][0]['actor'] = 'd' * 64
                if change == 'bool_version': b['payload']['version'] = True
                if change == 'model': b['payload']['actors'][0]['model'] = '/secret/model'
                if change != 'digest': self.resign(b)
                with self.assertRaises(ValueError): feedback.merge(self.db, b)
                self.assertFalse(self.db.exists())

    def test_original_trace_db_cannot_be_used_as_destination(self):
        before = self.trace.read_bytes()
        with self.assertRaises(ValueError): feedback.merge(self.trace, self.bundle())
        self.assertEqual(before, self.trace.read_bytes())

    def test_project_scope_separates_local_ids_but_not_physical_usage(self):
        data = self.store.request('sample-request')
        b = feedback.make_bundle(data, self.key, 'a' * 40, project_id='other-project')
        a = self.bundle()
        self.assertNotEqual(a['payload']['request'], b['payload']['request'])
        self.assertEqual(a['payload']['observations'][0]['id'], b['payload']['observations'][0]['id'])
        feedback.merge(self.db, a)
        with self.assertRaises(ValueError): feedback.merge(self.db, b)
        # Unmeasured requests with reused local IDs in distinct projects remain distinct.
        data['events'] = []
        data['requests'][0]['id'] = 'another-request'
        for project in ('one', 'two'):
            feedback.merge(self.db, feedback.make_bundle(data, self.key, 'a' * 40, project_id=project))
        self.assertEqual(feedback.summary(self.db)['requests'], 3)

    def test_summary_profiles_and_null_details_preserved(self):
        data = self.store.request('sample-request')
        data['actors'][0]['observed'] = {'model': 'private-model-label', 'effort': 'high'}
        b = feedback.make_bundle(data, self.key, 'a' * 40, project_id='example-project')
        self.assertNotIn('private-model-label', encoded(b))
        b['payload']['observations'][0]['tokens']['reasoning_output_tokens'] = None
        feedback.merge(self.db, self.resign(b))
        profiles = feedback.summary(self.db)['profiles']
        observed = next(p for p in profiles if p['effort'] == 'high')
        self.assertEqual(observed['model'], feedback.token(self.key, 'model', 'private-model-label'))
        self.assertIsNone(observed['measured_total_tokens'])

    def test_output_no_overwrite_permissions_and_symlinks(self):
        out = self.root / 'file'; feedback.write_private(out, b'original')
        with self.assertRaises(OSError): feedback.write_private(out, b'changed')
        self.assertEqual(out.read_bytes(), b'original')
        out.chmod(0o644)
        with self.assertRaises(ValueError): feedback.read_private(out)
        link = self.root / 'link'; link.symlink_to(out)
        with self.assertRaises(ValueError): feedback.read_private(link)
        with self.assertRaises(ValueError): feedback.write_private(ROOT / 'leaked.json', b'secret')

    def test_duplicate_json_fields_oversize_and_bad_cli_are_private(self):
        path = self.root / 'bad.json'
        feedback.write_private(path, b'{"payload":{},"payload":{}}')
        with self.assertRaises(ValueError): feedback.load_feedback(path)
        path.write_bytes(b' ' * (feedback.MAX_BYTES + 1))
        with self.assertRaises(ValueError): feedback.load_feedback(path)
        result = self.cli('import', '--db', self.db, '--file', path)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(str(self.root), result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(self.db.exists())

    def test_key_creation_and_validation(self):
        key = self.root / 'key'
        self.assertEqual(self.cli('init-key', '--out', key).returncode, 0)
        self.assertEqual(len(feedback.read_key(key)), 32)
        before = key.read_bytes()
        self.assertEqual(self.cli('init-key', '--out', key).returncode, 2)
        self.assertEqual(before, key.read_bytes())

    def test_module_import_has_no_file_or_cli_side_effect(self):
        result = subprocess.run([sys.executable, '-c',
                                 'import sys; sys.path.insert(0, sys.argv[1]); import feedback_exchange',
                                 str(ROOT / 'scripts')], cwd=self.root, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'')
        self.assertFalse(self.db.exists())


if __name__ == '__main__':
    unittest.main()
