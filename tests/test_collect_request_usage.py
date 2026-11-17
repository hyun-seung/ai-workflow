import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import collect_request_usage as collector
from trace_store import Store, load_bundle
from test_native_record import event, context, assigned
from test_dispatch_usage import boundary, count
from test_lifecycle import snapshot as exit_snapshot


def snapshot():
    value = exit_snapshot()
    value['result']['dispatch']['runId'] = 'run_example'
    return value


class CollectionTests(unittest.TestCase):
    def setUp(self):
        base = os.environ.get('AI_WORKFLOW_TEST_TMP')
        if base is not None:
            base = Path(base).resolve(strict=True)
            if not base.is_dir() or base.stat().st_uid != os.getuid() or base.stat().st_mode & 0o077:
                raise ValueError('test_fixture_parent_must_be_private')
        self.tmp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.project = self.root/'project'; self.project.mkdir()
        self.inputs = self.root/'inputs'; self.inputs.mkdir(mode=0o700)
        self.out = self.root/'output'
        self.db = self.inputs/'trace.sqlite3'
        self.store = Store(self.db); self.addCleanup(self.store.close)
        self.store.import_bundle(load_bundle(Path(__file__).parent/'fixtures/traces/sample.json'))
        self.config = dict(version=1, project=str(self.project), ledger=str(self.inputs/'ledger.json'),
            directory=str(self.root/'hooks'), trace_db=str(self.db), coordinator='native-coordinator')
        self.save('config.json', self.config)
        self.ledger = dict(version=1, coordinator='native-coordinator', operations={})
        self.save('ledger.json', self.ledger)
        self.manifest = dict(schema_version=1, id='batch', hook_config=str(self.inputs/'config.json'),
            request_id='sample-request', coordinator_id='sample-coordinator', coordinator_turns=[], worker_sources=[])
        self.rows = [event('session_meta',dict(id='session_example',cwd=str(self.project),source='vscode',originator='orca_desktop'))]
        for n in range(2):
            turn = 'turn_' + str(n)
            content = [dict(type='input_text',text='SYNTHETIC_PRIVATE_TEXT')]
            self.rows += [boundary('task_started', turn), context(), event('response_item',dict(type='message',role='user',content=content,
                internal_chat_message_metadata_passthrough=dict(turn_id=turn))), count((100*(n+1),20*(n+1),70*(n+1),5*(n+1)),(100,20,70,5)), boundary('task_complete',turn)]
            binding = dict(schema_version=1,request_id='sample-request',actor_id='sample-coordinator',task_id='task_'+str(n),role='coordinator',native_turn_id=turn,message_line=4+5*n,
                message_sha256=hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest())
            self.save('binding'+str(n)+'.json',binding)
            self.manifest['coordinator_turns'].append(dict(session_file=str(self.inputs/'source.jsonl'),session_id='session_example',binding_file=str(self.inputs/('binding'+str(n)+'.json')),task_title='Synthetic turn'))
        self.write_source(); self.save_manifest()
        self.env = patch.dict(os.environ, ORCA_AGENT_SESSION_ID='native-coordinator'); self.env.start(); self.addCleanup(self.env.stop)
        self.native_patch = patch.object(collector, 'orca', return_value=snapshot())
        self.native = self.native_patch.start()
        self.addCleanup(self.native_patch.stop)

    def save(self,name,obj):
        p=self.inputs/name;p.write_text(json.dumps(obj));p.chmod(0o600);return p
    def write_source(self): (self.inputs/'source.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
    def save_manifest(self):self.save('manifest.json',self.manifest)
    def run_collect(self):return collector.collect(self.inputs/'manifest.json',self.out,'mock-orca')
    def data(self):return self.store.request('sample-request')
    def test_two_turns_replay_no_duplicate_or_stop_forgery(self):
        before=self.data()['actors'];self.run_collect();first=self.data();self.run_collect()
        self.assertEqual(first,self.data());self.assertEqual(before,first['actors'])
        self.assertEqual(len([e for e in first['events'] if e['kind']=='usage']),2)
        e=next(e for e in first['events'] if e['source']=='trace.manual-source-recovery')
        self.assertFalse(e['detail']['stop_delivery_inferred']);self.assertFalse(e['detail']['full_usage_complete'])
        self.assertEqual(list((self.root/'hooks').glob('*.receipt.json')),[])
        self.assertNotIn('SYNTHETIC_PRIVATE_TEXT',''.join(p.read_text() for p in self.out.glob('*.json')))
    def test_unavailable_is_atomic(self):
        before=self.data();self.rows.pop();self.write_source()
        with self.assertRaises(ValueError):self.run_collect()
        self.assertEqual(before,self.data())
    def test_fresh_counter_change_rejected(self):
        self.run_collect();before=self.data();self.rows[5]=count((101,20,70,5));self.write_source()
        with self.assertRaises(ValueError):self.run_collect()
        self.assertEqual(before,self.data())
    def test_fresh_context_change_rejected(self):
        self.run_collect();self.rows[2]=context(model='changed');self.write_source()
        with self.assertRaises(ValueError):self.run_collect()
    def test_missing_source_rejected_after_restore(self):
        self.run_collect();self.store.db.execute("DELETE FROM records WHERE kind='event' AND json_extract(body, '$.kind') IN ('usage','profile')");self.store.db.commit()
        (self.inputs/'source.jsonl').unlink()
        with self.assertRaises(OSError):self.run_collect()
    def test_restore_missing_events(self):
        self.run_collect();before=self.data()
        self.store.db.execute("DELETE FROM records WHERE kind='event' AND json_extract(body, '$.kind') IN ('usage','profile')");self.store.db.commit()
        self.run_collect();self.assertEqual(before,self.data())
    def test_valid_appended_turn_allowed(self):
        self.run_collect();before=self.data()
        self.rows += [boundary('task_started','extra'),context(),boundary('task_complete','extra')];self.write_source()
        self.run_collect();self.assertEqual(before,self.data())
    def test_manifest_rebind_refused(self):
        self.run_collect();self.manifest['coordinator_turns'][0]['task_title']='changed';self.save_manifest()
        with self.assertRaises(ValueError):self.run_collect()
    def test_binding_rebind_refused(self):
        self.run_collect();p=self.inputs/'binding0.json';b=json.loads(p.read_text());b['task_id']='changed';self.save(p.name,b)
        with self.assertRaises(ValueError):self.run_collect()
    def test_duplicate_physical_turn_refused(self):
        t=copy.deepcopy(self.manifest['coordinator_turns'][0]);self.save('duplicate.json',json.loads((self.inputs/'binding0.json').read_text()));t['binding_file']=str(self.inputs/'duplicate.json');self.manifest['coordinator_turns'].append(t);self.save_manifest()
        with self.assertRaises(ValueError):self.run_collect()
    def test_wrong_request_or_root_refused(self):
        for k in ('request_id','coordinator_id'):
            with self.subTest(k=k):
                m=copy.deepcopy(self.manifest);self.manifest[k]='wrong';self.save_manifest()
                with self.assertRaises(ValueError):self.run_collect()
                self.manifest=m
    def test_malformed_json_config_binding_sqlite_short_errors(self):
        for filename in ('manifest.json','config.json','binding0.json','trace.sqlite3'):
            with self.subTest(filename=filename):
                p=self.inputs/filename;old=p.read_bytes();p.write_bytes(b'{broken')
                from contextlib import redirect_stderr
                import io
                out=io.StringIO()
                with redirect_stderr(out):self.assertEqual(collector.main(['--manifest',str(self.inputs/'manifest.json'),'--directory',str(self.out),'--orca-cli','mock']),2)
                self.assertEqual(json.loads(out.getvalue())['status'],'error');p.write_bytes(old)
    def test_nonfinite_duplicate_keys_and_bounds(self):
        p=self.inputs/'manifest.json'
        for raw in ('{"id":1,"id":2}', '{"x":NaN}', '{"x":1e999}'):
            p.write_text(raw)
            with self.assertRaises(ValueError):collector.load_manifest(p)
        self.manifest['coordinator_turns']*=51;self.save_manifest()
        with self.assertRaises(ValueError):self.run_collect()
    def test_late_conflict_rolls_back_entire_batch(self):
        original = collector.Store.import_bundle
        injected = False
        def concurrent(store, bundle):
            nonlocal injected
            if store.path == self.db and not injected:
                injected = True
                conflict = copy.deepcopy(bundle['events'][-1])
                conflict['detail'] = dict(concurrent=True)
                original(store,dict(schema_version=1,requests=[],actors=[],events=[conflict],collections=[]))
            return original(store,bundle)
        with patch.object(collector.Store,'import_bundle',concurrent):
            with self.assertRaises(ValueError):self.run_collect()
        self.assertEqual(len([e for e in self.data()['events'] if e['kind']=='usage']),0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM collections').fetchone()[0],0)

    def test_existing_physical_measurement_other_role_rejected(self):
        self.run_collect()
        self.manifest['id']='second_batch'
        binding=json.loads((self.inputs/'binding0.json').read_text());binding['task_id']='another_task'
        self.save('binding0.json',binding);self.save_manifest();before=self.data()
        with self.assertRaises(ValueError):self.run_collect()
        self.assertEqual(before,self.data())

    def test_missing_source_before_commit_and_replay(self):
        before=self.data();(self.inputs/'source.jsonl').unlink()
        with self.assertRaises(OSError):self.run_collect()
        self.assertEqual(before,self.data())

    def test_cross_request_physical_overlap_refused(self):
        self.run_collect();sample=load_bundle(Path(__file__).parent/'fixtures/traces/sample.json')
        sample['requests'][0]['id']='another-request'
        root=sample['actors'][0];root['id']='another-root';root['request_id']='another-request'
        self.store.import_bundle(dict(schema_version=1,requests=sample['requests'],actors=[root],events=[],collections=[]))
        self.manifest.update(id='other_batch',request_id='another-request',coordinator_id='another-root')
        for n in range(2):
            b=json.loads((self.inputs/('binding'+str(n)+'.json')).read_text())
            b.update(request_id='another-request',actor_id='another-root');self.save('binding'+str(n)+'.json',b)
        self.save_manifest()
        with self.assertRaises(ValueError):self.run_collect()
        self.assertEqual(len([e for e in self.store.request('another-request')['events'] if e['kind']=='usage']),0)

    def test_wrong_or_missing_native_run_precedes_finish(self):
        self.worker_fixture();before=self.data()
        for run in (None, 'wrong_run'):
            native=snapshot()
            if run is None:del native['result']['dispatch']['runId']
            else:native['result']['dispatch']['runId']=run
            self.native.return_value=native
            with patch.object(collector,'finish') as finish:
                with self.assertRaises(ValueError):self.run_collect()
                finish.assert_not_called()
            self.assertEqual(before,self.data())

    def test_optional_worker_run_and_fresh_finish_snapshot_validation(self):
        self.worker_fixture()
        for bad in (None, [], 'wrong_run'):
            native=snapshot();native['result']['worker']['runId']=bad;self.native.return_value=native
            with self.assertRaises(ValueError):self.run_collect()
        self.native.return_value=snapshot()
        wrong=snapshot();wrong['result']['dispatch']['runId']='wrong_run'
        with patch('finish_worker.orca',return_value=wrong):
            with self.assertRaises(ValueError):self.run_collect()
        self.assertEqual(len([e for e in self.data()['events'] if e['kind']=='usage']),0)

    def test_replay_and_restore_require_current_exact_exit_and_mapping(self):
        self.worker_fixture()
        with patch('finish_worker.orca',return_value=snapshot()):self.run_collect()
        originals={p:p.read_bytes() for p in self.out.rglob('*.json') if not p.name.startswith('native-check-')}
        bad_values=[]
        running=snapshot();running['result']['observation']['status']='running';bad_values.append(running)
        uncertain=snapshot();uncertain['result']['observation']['exactWorker']=False;bad_values.append(uncertain)
        for field in ('runId','taskId','id'):
            wrong=snapshot();wrong['result']['dispatch'][field]='wrong';bad_values.append(wrong)
        missing=snapshot();del missing['result']['dispatch']['runId'];bad_values.append(missing)
        remote=snapshot();remote['result']['dispatch']['hostScope']['kind']='remote';bad_values.append(remote)
        for restore in (False,True):
            if restore:
                self.store.db.execute("DELETE FROM records WHERE kind='event' AND json_extract(body,'$.kind') IN ('usage','profile')")
                self.store.db.execute('DELETE FROM collections');self.store.db.commit()
            before=self.data()
            for native in bad_values:
                self.native.return_value=native;self.native.reset_mock()
                with self.assertRaises(ValueError):self.run_collect()
                self.native.assert_called_once();self.assertEqual(before,self.data())
            self.assertEqual(originals,{p:p.read_bytes() for p in originals})
        self.native.side_effect=ValueError('native_unavailable')
        with self.assertRaises(ValueError):self.run_collect()
        self.assertEqual(before,self.data())

    def test_malformed_ledger_and_plan_cli_errors(self):
        self.worker_fixture()
        valid=copy.deepcopy(self.ledger)
        import subprocess
        for value in ([],None,1,dict(valid,version=True),dict(valid,operations=[]),dict(valid,operations=None)):
            self.save('ledger.json',value)
            result=subprocess.run([sys.executable,str(Path(collector.__file__)), '--manifest',str(self.inputs/'manifest.json'),
                '--directory',str(self.out),'--orca-cli','unused'],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,2);self.assertEqual(json.loads(result.stderr)['status'],'error')
            self.assertNotIn('Traceback',result.stderr)
        for value in ([],None,1):
            ledger=copy.deepcopy(valid);ledger['operations']['operation']['plan']=value;self.save('ledger.json',ledger)
            result=subprocess.run([sys.executable,str(Path(collector.__file__)), '--manifest',str(self.inputs/'manifest.json'),
                '--directory',str(self.out),'--orca-cli','unused'],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,2);self.assertEqual(json.loads(result.stderr)['status'],'error')
        self.assertEqual(len([e for e in self.data()['events'] if e['kind']=='usage']),0)

    def test_default_fixture_uses_generic_tempfile(self):
        with patch.dict(os.environ):
            os.environ.pop('AI_WORKFLOW_TEST_TMP',None)
            nested=CollectionTests('test_unavailable_is_atomic')
            try:
                nested.setUp()
                self.assertEqual(nested.root.parent,Path(tempfile.gettempdir()).resolve())
            finally:nested.doCleanups()

    def worker_fixture(self):
        worker=self.inputs/'worker.jsonl';worker.write_text(''.join(json.dumps(r)+'\n' for r in [self.rows[0],boundary('task_started'),context(),assigned(),count(),boundary('task_complete')]))
        self.ledger['operations']['operation']=dict(request_id='sample-request',task_id='task_example',dispatch_id='ctx_example',run_id='run_example',state='bound',plan=dict(project=str(self.project),agent='codex',usage_collection=dict(role='backend',actor_id='sample-worker',task_title='Synthetic worker')))
        self.save('ledger.json',self.ledger)
        self.manifest['worker_sources']=[dict(operation_id='operation',session_file=str(worker),session_id='session_example',expected=dict(model='example-model',effort='low',reviewer='auto_review'))]
        # Distinct physical session identity is mandatory.
        rows=worker.read_text().replace('session_example','worker_session');worker.write_text(rows);self.manifest['worker_sources'][0]['session_id']='worker_session';self.save_manifest()
        return worker

    def test_mixed_worker_no_receipt(self):
        worker=self.worker_fixture()
        with patch('finish_worker.orca',return_value=snapshot()):self.run_collect();self.run_collect()
        self.assertEqual(len([e for e in self.data()['events'] if e['kind']=='usage']),3)
        self.assertEqual(list((self.root/'hooks').glob('*.receipt.json')),[])
        self.manifest['id']='overlap_batch'
        self.manifest['worker_sources'][0]['session_id']='session_example'
        worker.write_text(worker.read_text().replace('worker_session','session_example').replace('turn_one','turn_0'))
        self.save_manifest();before=self.data()
        with patch('finish_worker.orca',return_value=snapshot()):
            with self.assertRaises(ValueError):self.run_collect()
        self.assertEqual(before,self.data())


if __name__=='__main__':unittest.main()
