import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import draft_coordinator_binding as draft
import test_native_turn_record as fixtures
from trace_store import Store, load_bundle
from trace_usage import import_usage
from native_turn_record import inspect_turn
from test_dispatch_usage import boundary, count


class DraftBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NativeTurnTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.root
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.private = Path(self.tmp.name).resolve()
        self.db = self.private / 'trace.sqlite3'
        store = Store(self.db)
        store.import_bundle(load_bundle(Path(__file__).parent / 'fixtures/traces/sample.json'))
        store.close()
        self.recipe = self.private / 'recipe.json'; self.output = self.private / 'binding.json'
        self.data = dict(schema_version=1, project=str(self.project), trace_db=str(self.db),
                         request_id='sample-request', actor_id='sample-coordinator', task_id='logical',
                         session_file=str(self.fixture.source), session_id='session_example',
                         native_turn_id='turn_one', scope='single_work')
        self.save()

    def save(self):
        self.fixture.source.write_text(''.join(json.dumps(r)+'\n' for r in self.fixture.rows))
        self.recipe.write_text(json.dumps(self.data)); self.recipe.chmod(0o600)

    def run_cli(self):
        return subprocess.run([sys.executable, str(Path(draft.__file__)), '--input', str(self.recipe),
                               '--output', str(self.output)], capture_output=True, text=True)

    def test_binding_import_replay_and_private_content(self):
        self.fixture.multi_binding(); self.save()
        before = {p:p.read_bytes() for p in (self.db,self.recipe,self.fixture.source)}
        result = self.run_cli(); self.assertEqual(result.returncode,0,result.stdout)
        binding = json.loads(self.output.read_bytes())
        self.assertEqual(binding['messages'],self.fixture.binding['messages'])
        self.assertNotIn('PRIVATE',self.output.read_text())
        self.assertEqual(self.output.stat().st_mode & 0o777,0o600)
        identity = self.output.stat()
        self.assertTrue(draft.build(str(self.recipe),str(self.output))[0]['replay'])
        self.assertEqual((identity.st_ino,identity.st_mtime_ns),
                         (self.output.stat().st_ino,self.output.stat().st_mtime_ns))
        for p,raw in before.items(): self.assertEqual(p.read_bytes(),raw)
        record = inspect_turn(self.project,self.fixture.source,'session_example',binding)
        record_file = self.private/'record.json'; record_file.write_text(json.dumps(record))
        store=Store(self.db)
        try:
            import_usage(store,record_file); import_usage(store,record_file)
            self.assertEqual(len([e for e in store.request('sample-request')['events'] if e['kind']=='usage']),1)
        finally: store.close()

    def test_single_exact_selection_and_missing_foreign_inputs(self):
        self.assertEqual(self.run_cli().returncode,0)
        binding=json.loads(self.output.read_bytes())
        self.assertEqual(binding['messages'],[dict(line=4,sha256=self.fixture.binding['message_sha256'])])
        self.output.unlink()
        for changes in [dict(trace_db=str(self.private/'absent.sqlite3')),dict(session_id='foreign'),
                        dict(project=str(self.private/'missing')),dict(native_turn_id='absent')]:
            old=self.data.copy();self.data.update(changes);self.save()
            result=self.run_cli()
            self.assertEqual(result.returncode,1 if changes.get('native_turn_id') else 2)
            self.assertFalse(self.output.exists());self.data=old

    def test_ineligible_boundaries_routing_and_counters(self):
        original=copy.deepcopy(self.fixture.rows)
        cases=[original[:-1],original[:-1]+[boundary('turn_aborted')],
               original+[boundary('task_started')],
               original[:-1]+[boundary('task_started','nested'),original[-1]],
               original+[boundary('task_complete')]]
        bad=copy.deepcopy(original);bad[3]['payload']['internal_chat_message_metadata_passthrough']=None;cases.append(bad)
        bad=copy.deepcopy(original);bad[4]=count((200,40,140,10),(100,20,70,5));cases.append(bad)
        bad=copy.deepcopy(original);bad[0]['payload']['forked_from_id']='parent';cases.append(bad)
        bad=copy.deepcopy(original);bad[3]=fixtures.assigned();cases.append(bad)
        for rows in cases:
            self.fixture.rows=rows;self.save()
            result=self.run_cli()
            self.assertEqual(result.returncode,1,result.stdout)
            self.assertTrue(json.loads(result.stdout)['issues']);self.assertFalse(self.output.exists())

    def test_invalid_root_and_recipe(self):
        for changes in [dict(actor_id='sample-worker'),dict(actor_id='absent'),dict(request_id='missing'),
                        dict(scope='mixed'),dict(schema_version=True),dict(task_id='bad id'),dict(extra=1)]:
            old=self.data.copy();self.data.update(changes);self.save()
            self.assertEqual(self.run_cli().returncode,2);self.assertFalse(self.output.exists());self.data=old
        for raw in ['{"schema_version":1,"schema_version":1}', '{"x":NaN}', '['*2000+']'*2000, ' '*1048577]:
            self.recipe.write_text(raw)
            self.assertEqual(self.run_cli().returncode,2)

    def test_private_paths_collisions_links_and_conflict(self):
        for output in [self.recipe,self.db,self.fixture.source,self.project/'out.json',self.private/'missing/out.json',Path(str(self.db)+'-wal')]:
            with self.assertRaises((ValueError,OSError)):draft.build(str(self.recipe),str(output))
        self.output.symlink_to(self.recipe)
        self.assertEqual(self.run_cli().returncode,2);self.output.unlink()
        os.link(self.recipe,self.output)
        self.assertEqual(self.run_cli().returncode,2);self.output.unlink()
        self.output.write_bytes(b'other');self.output.chmod(0o600)
        self.assertEqual(self.run_cli().returncode,2);self.assertEqual(self.output.read_bytes(),b'other')

    def test_source_change_guard_and_append_replay(self):
        original=draft._SelectedSource.check
        def changed(selected):
            original(selected)
            if selected.data:
                with self.fixture.source.open('a') as stream:stream.write('\n')
                original(selected)
        with mock.patch.object(draft._SelectedSource,'check',changed):
            with self.assertRaises(ValueError):draft.build(str(self.recipe),str(self.output))
        self.assertFalse(self.output.exists());self.save()
        self.assertEqual(self.run_cli().returncode,0)
        self.fixture.rows += [boundary('task_started','later'),boundary('task_complete','later')]
        self.save();self.assertEqual(self.run_cli().returncode,0)

    def test_size_parser_and_no_native_actions(self):
        with mock.patch('native_record.MAX_BYTES',1):
            with self.assertRaises(ValueError):draft.build(str(self.recipe),str(self.output))
        self.fixture.source.write_text('invalid')
        result=self.run_cli();self.assertEqual(result.returncode,2)
        self.assertNotIn('Traceback',result.stderr)
        self.save()
        with mock.patch('subprocess.run',side_effect=AssertionError('native forbidden')), mock.patch('os.chmod',side_effect=AssertionError('chmod forbidden')):
            self.assertEqual(draft.build(str(self.recipe),str(self.output))[1],0)


if __name__=='__main__':unittest.main()
