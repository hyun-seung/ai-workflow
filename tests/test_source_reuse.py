from pathlib import Path
from unittest.mock import patch
import json
import unittest
import sys

sys.path.insert(0, str(Path(__file__).parent))
import test_collect_request_usage as fixtures
import native_record
from native_turn_record import inspect_turn


class ReuseTests(unittest.TestCase):
    # Reuse fixture methods explicitly; inherited test methods are not discovered twice.
    setUp = fixtures.CollectionTests.setUp
    save = fixtures.CollectionTests.save
    write_source = fixtures.CollectionTests.write_source
    save_manifest = fixtures.CollectionTests.save_manifest
    run_collect = fixtures.CollectionTests.run_collect
    data = fixtures.CollectionTests.data

    def test_one_parse_and_fresh_invocation(self):
        original = native_record._read_session_snapshot
        with patch.object(native_record, '_read_session_snapshot', wraps=original) as read:
            self.run_collect(); self.assertEqual(read.call_count, 1)
            self.run_collect(); self.assertEqual(read.call_count, 2)
        for i, entry in enumerate(self.manifest['coordinator_turns']):
            binding = json.loads(Path(entry['binding_file']).read_text())
            fresh = inspect_turn(self.project, entry['session_file'], entry['session_id'], binding)
            saved = json.loads((self.out / ('batch.turn.' + str(i) + '.json')).read_text())
            self.assertEqual(fresh['turn_usage'], saved['turn_usage'])

    def test_mutations_at_reuse_and_before_import(self):
        for late in (False, True):
            for mutation in ('append', 'replace', 'content'):
                with self.subTest(late=late, mutation=mutation):
                    self.write_source(); before = self.data()
                    original = native_record._SelectedSource.check
                    calls = 0
                    def check(source):
                        nonlocal calls
                        calls += 1
                        if calls == (5 if late else 3):
                            p = self.inputs/'source.jsonl'
                            if mutation == 'replace':
                                replacement = self.inputs/'replacement'; replacement.write_bytes(p.read_bytes()); replacement.replace(p)
                            elif mutation == 'append':
                                with p.open('a') as stream: stream.write('\n')
                            else: p.write_text(p.read_text().replace('example-model', 'changed-model'))
                        return original(source)
                    with patch.object(native_record._SelectedSource, 'check', check):
                        with self.assertRaises(ValueError): self.run_collect()
                    self.assertEqual(before, self.data())

    def test_mutation_inside_transaction_rolls_back(self):
        import sqlite3
        import collect_request_usage as collector
        original = collector.Store.import_bundle
        before = self.data()
        def changed(store, bundle):
            if store.path == self.db:
                source = self.inputs/'source.jsonl'
                with source.open('a') as stream: stream.write('\n')
            return original(store, bundle)
        with patch.object(collector.Store, 'import_bundle', changed):
            with self.assertRaises(sqlite3.DatabaseError): self.run_collect()
        self.assertEqual(before, self.data())

    def test_malformed_and_oversized_no_import(self):
        before = self.data()
        (self.inputs/'source.jsonl').write_bytes(b'{broken\n')
        with self.assertRaises(ValueError): self.run_collect()
        self.write_source()
        with patch.object(native_record, 'MAX_BYTES', 1):
            with self.assertRaises(ValueError): self.run_collect()
        self.assertEqual(before, self.data())

    def test_actual_source_open_reduction(self):
        original = Path.open
        opened = []
        def counted(path, *args, **kwargs):
            if path == self.inputs/'source.jsonl': opened.append(path)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', counted): self.run_collect()
        self.assertEqual(len(opened), 1)
