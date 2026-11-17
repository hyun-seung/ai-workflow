import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import coordinator_collection_flow as flow
import test_collect_request_usage as fixtures
from test_dispatch_usage import boundary


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.CollectionTests()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.directory = self.f.root / 'prepared'
        self.recipe = self.f.inputs / 'flow.json'
        self.data = dict(schema_version=1, project=str(self.f.project), trace_db=str(self.f.db),
            request_id='sample-request', actor_id='sample-coordinator', task_id='logical_task',
            session_file=str(self.f.inputs/'source.jsonl'), session_id='session_example',
            native_turn_id='turn_0', scope='single_work', hook_config=str(self.f.inputs/'config.json'),
            task_title='Synthetic selection', collection_id='selected', collection_directory=str(self.f.out))
        self.save()

    def save(self):
        self.recipe.write_text(json.dumps(self.data)); self.recipe.chmod(0o600)

    def cli(self, mode='prepare', *extra):
        return subprocess.run([sys.executable, flow.__file__, mode, '--input', str(self.recipe),
            '--directory', str(self.directory), *extra], capture_output=True, text=True)

    def prepare(self):
        result = self.cli(); self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)['approve_sha256']

    def collect(self, digest):
        return flow.run_import(str(self.recipe), str(self.directory), digest, 'forbidden-native')

    def test_cli_review_import_replay_and_coverage(self):
        before = self.f.data(); raw_db=self.f.db.read_bytes()
        digest = self.prepare(); self.assertEqual(before, self.f.data())
        self.assertEqual(raw_db,self.f.db.read_bytes())
        review = json.loads((self.directory/'review.json').read_bytes())
        self.assertEqual(digest, hashlib.sha256(flow.canonical(review)).hexdigest())
        self.assertTrue(review['requires_review']); self.assertFalse(review['execution_authorized'])
        identities = {p.name:(p.stat().st_ino,p.stat().st_mtime_ns) for p in self.directory.glob('*.json')}
        self.assertEqual(self.prepare(), digest)
        self.assertEqual(identities, {p.name:(p.stat().st_ino,p.stat().st_mtime_ns) for p in self.directory.glob('*.json')})
        with mock.patch.dict(os.environ, ORCA_AGENT_SESSION_ID='native-coordinator'):
            result = self.cli('import', '--approve-sha256', digest, '--orca-cli', 'forbidden-native')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            first = self.f.data(); self.collect(digest); self.assertEqual(first,self.f.data())
        self.assertEqual(len([e for e in first['events'] if e['kind']=='usage']),1)
        self.assertFalse(json.loads(result.stdout)['coverage']['full_usage_complete'])
        self.assertFalse(list((self.f.root/'hooks').glob('*.receipt.json')))
        for p in self.directory.glob('*.json'):
            self.assertNotIn('SYNTHETIC_PRIVATE_TEXT',p.read_text())
            self.assertEqual(p.stat().st_mode & 0o777,0o600)

    def test_no_approval_or_wrong_approval_db_unchanged(self):
        digest = self.prepare(); before = self.f.data()
        for args in [[], ['--approve-sha256','0'*64], ['--approve-sha256',digest+'x']]:
            result = self.cli('import', '--orca-cli', 'forbidden-native', *args)
            self.assertEqual(result.returncode,2); self.assertNotIn('Traceback',result.stderr)
        self.assertEqual(before,self.f.data()); self.assertFalse(list(self.f.out.glob('*.journal.json')))

    def test_changed_review_and_artifacts_refused(self):
        digest=self.prepare(); before=self.f.data()
        for name in ('review.json','binding.json','manifest.json','draft-recipe.json'):
            p=self.directory/name; raw=p.read_bytes(); value=json.loads(raw)
            value['unexpected']='change';p.write_text(json.dumps(value))
            with self.assertRaises((ValueError,KeyError)): self.collect(digest)
            p.write_bytes(raw)
        self.assertEqual(before,self.f.data())

    def test_changed_recipe_config_source_refused(self):
        digest=self.prepare();before=self.f.data()
        for key, value in [('request_id','other'),('actor_id','other'),('task_id','other'),
                           ('session_id','other'),('session_file',str(self.f.inputs/'missing.jsonl')),('task_title','changed'),('collection_id','other')]:
            old=self.data[key];self.data[key]=value;self.save()
            with self.assertRaises((ValueError,OSError)):self.collect(digest)
            self.data[key]=old;self.save()
        config=self.f.inputs/'config.json';raw=config.read_bytes()
        config.write_bytes(raw+b' ')
        with self.assertRaises(ValueError):self.collect(digest)
        config.write_bytes(raw)
        self.f.rows[3]['payload']['content'][0]['text']='CHANGED';self.f.write_source()
        with self.assertRaises(ValueError):self.collect(digest)
        self.assertEqual(before,self.f.data())

    def test_current_native_coordinator_required(self):
        digest=self.prepare(); before=self.f.data()
        with mock.patch.dict(os.environ, ORCA_AGENT_SESSION_ID='other'):
            with self.assertRaises(ValueError):self.collect(digest)
        self.assertEqual(before,self.f.data())

    def test_open_mixed_ids_ambiguous_and_over_limit(self):
        before=self.f.data()
        for index, changes in enumerate([dict(scope='mixed'),dict(task_id='bad id'),dict(schema_version=True),
                        dict(unknown=True),dict(actor_id='sample-worker'),dict(task_title='')]):
            self.directory=self.f.root/('invalid-'+str(index))
            old=self.data.copy();self.data.update(changes);self.save()
            self.assertEqual(self.cli().returncode,2);self.data=old;self.save()
        self.directory=self.f.root/'open'
        self.data['native_turn_id']='turn_1';self.save();self.f.rows.pop();self.f.write_source()
        self.assertEqual(self.cli().returncode,1)
        self.assertFalse((self.directory/'review.json').exists())
        self.assertEqual(before,self.f.data())

    def test_ambiguous_turn_rejected(self):
        before=self.f.data()
        self.f.rows.insert(5,boundary('task_started','nested'));self.f.write_source()
        self.assertEqual(self.cli().returncode,1)
        self.assertEqual(before,self.f.data())

    def test_message_limit_rejected(self):
        # More than the binding schema's finite message limit, within one explicit turn.
        message=self.f.rows[3]
        self.f.rows[4:4]=[message]*1000;self.f.write_source()
        before=self.f.data()
        self.assertEqual(self.cli().returncode,1)
        self.assertFalse((self.directory/'review.json').exists())
        self.assertEqual(before,self.f.data())

    def test_parser_limit_and_no_native_or_db_writes_prepare(self):
        before=self.f.data()
        with mock.patch('native_record.MAX_BYTES',1):
            with self.assertRaises(ValueError):flow.prepare(str(self.recipe),str(self.directory))
        with mock.patch('subprocess.run',side_effect=AssertionError('native forbidden')):
            result,code=flow.prepare(str(self.recipe),str(self.directory))
            self.assertEqual(code,0)
        self.assertEqual(before,self.f.data())

    def test_private_paths_output_collision_and_duplicate_keys(self):
        original=self.recipe.read_bytes()
        for raw in [b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}',b' '*1048577]:
            self.recipe.write_bytes(raw);self.assertEqual(self.cli().returncode,2)
        self.recipe.write_bytes(original)
        self.directory.mkdir(mode=0o700)
        p=self.directory/'binding.json';p.write_bytes(b'other');p.chmod(0o600)
        self.assertEqual(self.cli().returncode,2);self.assertEqual(p.read_bytes(),b'other')
        p.unlink();p.symlink_to(self.recipe)
        self.assertEqual(self.cli().returncode,2);p.unlink()
        os.link(self.recipe,p);self.assertEqual(self.cli().returncode,2);p.unlink()
        self.data['collection_directory']=str(self.f.inputs/'nested');self.save()
        self.assertEqual(self.cli().returncode,2)

    def test_physical_duplicate_rejected_atomically(self):
        digest=self.prepare();self.collect(digest)
        before=self.f.data()
        other=self.f.root/'other';self.data['collection_id']='other';self.data['task_id']='other'
        self.data['collection_directory']=str(self.f.root/'other-output');self.save()
        result,code=flow.prepare(str(self.recipe),str(other));self.assertEqual(code,0)
        with self.assertRaises(ValueError):flow.run_import(str(self.recipe),str(other),result['approve_sha256'],'unused')
        self.assertEqual(before,self.f.data())


if __name__=='__main__':unittest.main()
