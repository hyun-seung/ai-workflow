import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import completion_hook as hook
import dispatch_guard as guard
from trace_store import Store, load_bundle
from test_native_record import event, context, assigned
from test_dispatch_usage import count, boundary


class CompletionHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.project=self.root/'project';self.project.mkdir()
        self.private=self.root/'private';self.private.mkdir(mode=0o700)
        self.db=self.private/'trace.sqlite3';self.store=Store(self.db);self.addCleanup(self.store.close)
        self.store.import_bundle(load_bundle(Path(__file__).parent/'fixtures/traces/sample.json'))
        self.source=self.root/'session.jsonl'
        self.rows=[event('session_meta',dict(id='session_example',cwd=str(self.project),source='vscode',originator='orca_desktop')),
                   boundary('task_started'),context(),assigned(),count(),boundary('task_complete')]
        self.write_source()
        self.plan=dict(project=str(self.project),agent='codex',usage_collection=dict(role='backend',actor_id='sample-worker',task_title='가상 hook 검증'))
        self.operation=dict(plan=self.plan,task_id='task_example',dispatch_id='ctx_example',run_id='run_example',request_id='sample-request',state='bound')
        self.ledger=dict(version=1,coordinator='coordinator_example',requests={},operations=dict(example=self.operation))
        self.config=dict(version=1,project=str(self.project),ledger=str(self.private/'dispatches.json'),
                         directory=str(self.private/'hooks'),trace_db=str(self.db),coordinator='coordinator_example')
        self.config_path=self.private/'config.json';self.save(self.config_path,self.config);self.write_ledger()
        self.payload=dict(hook_event_name='Stop',session_id='session_example',turn_id='turn_one',cwd=str(self.project),
                          transcript_path=str(self.source),stop_hook_active=False,last_assistant_message='PRIVATE_NOT_TO_STORE')

    def save(self,path,value):path.write_text(json.dumps(value));path.chmod(0o600)
    def write_source(self):self.source.write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
    def write_ledger(self):self.save(Path(self.config['ledger']),self.ledger)
    def run_hook(self,**args):return hook.collect(hook.load_config(self.config_path),self.payload,attempts=2,delay=0,**args)
    def data(self):return self.store.request('sample-request')

    def test_closed_registered_stop_imports_once_and_preserves_actors(self):
        before=self.data();result=self.run_hook();after=self.data()
        usage=[e for e in after['events'] if e['kind']=='usage']
        self.assertEqual(result['status'],'collected');self.assertEqual(len(usage),1)
        self.assertEqual(usage[0]['detail']['usage']['measurements']['total_tokens'],120)
        self.assertEqual(before['actors'],after['actors']);self.assertFalse(result['worker_exit_verified'])
        self.assertTrue(self.run_hook()['replay']);self.assertEqual(after,self.data())
        text=''.join(p.read_text() for p in Path(self.config['directory']).glob('*.json'))
        self.assertNotIn('PRIVATE_NOT_TO_STORE',text);self.assertNotIn('PRIVATE_TASK_BODY',text)

    def test_stop_before_source_closes_remains_pending_and_later_replay_collects(self):
        final=self.rows.pop();self.write_source();before=self.data()
        self.assertEqual(self.run_hook()['status'],'pending');self.assertEqual(before,self.data())
        self.rows.append(final);self.write_source();self.assertEqual(self.run_hook()['status'],'collected')

    def test_bounded_async_flush_race_and_registration_race(self):
        final=self.rows.pop();self.write_source()
        def close(_):
            self.rows.append(final);self.write_source()
        with patch.object(hook.time,'sleep',side_effect=close) as sleeps:
            self.assertEqual(self.run_hook()['status'],'collected');self.assertEqual(sleeps.call_count,1)

    def test_unknown_operation_or_plain_coordinator_turn_is_ignored_without_per_session_files(self):
        self.operation['dispatch_id']='different';self.write_ledger();before=self.data()
        self.assertEqual(self.run_hook()['status'],'ignored');self.assertEqual(before,self.data())
        self.assertEqual(list(Path(self.config['directory']).glob('*')),[])
        self.payload['cwd']=str(self.root);self.assertEqual(self.run_hook()['reason'],'different_project')

    def test_bad_identity_aborted_missing_turn_wrong_request_and_actor_cannot_write_usage(self):
        before=self.data()
        self.payload['session_id']='wrong'
        with self.assertRaises(ValueError):self.run_hook()
        self.payload['session_id']='session_example';self.payload['turn_id']='different'
        self.assertEqual(self.run_hook()['status'],'pending')
        self.payload['turn_id']='turn_one';self.rows[-1]=boundary('turn_aborted');self.write_source()
        self.assertEqual(self.run_hook()['status'],'pending')
        self.assertEqual(self.data(),before)
        self.rows[-1]=boundary('task_complete');self.write_source();self.operation['request_id']='other';self.write_ledger()
        with self.assertRaises(ValueError):self.run_hook()
        self.assertEqual(before,self.data())

    def test_malformed_stop_and_nonfinite_payload_are_not_accepted(self):
        for key,value in [('hook_event_name','SessionEnd'),('transcript_path',None),('stop_hook_active',None),
                          ('turn_id','bad turn'),('analysis',float('inf'))]:
            p=dict(self.payload,**{key:value})
            with self.subTest(key=key),self.assertRaises((ValueError,TypeError)):hook.event_input(p)

    def test_private_config_scope_and_existing_db_are_required(self):
        original=copy.deepcopy(self.config)
        for key,value in [('coordinator','wrong'),('trace_db',str(self.private/'missing.sqlite3')),
                          ('project',str(self.root/'missing')),('ledger','relative.json')]:
            self.config=dict(original,**{key:value});self.save(self.config_path,self.config)
            with self.subTest(key=key),self.assertRaises(ValueError):self.run_hook()
        self.assertFalse((self.private/'missing.sqlite3').exists())

    def test_replay_checks_saved_hash_and_operation_binding(self):
        result=self.run_hook();record=Path(result['record_path']);old=record.read_bytes()
        record.write_text('{}')
        with self.assertRaisesRegex(ValueError,'hook_record_changed'):self.run_hook()
        record.write_bytes(old);self.operation['plan']['usage_collection']['task_title']='changed';self.write_ledger()
        with self.assertRaisesRegex(ValueError,'hook_operation_binding_changed'):self.run_hook()

    def test_failed_db_append_can_retry_without_losing_record_or_adding_twice(self):
        with patch.object(hook,'append_usage',side_effect=ValueError('simulated_db_failure')):
            with self.assertRaises(ValueError):self.run_hook()
        self.assertEqual(self.run_hook()['status'],'collected')
        self.assertTrue(self.run_hook()['replay'])
        self.assertEqual(len([e for e in self.data()['events'] if e['kind']=='usage']),1)

    def test_failed_append_pins_run_and_plan_before_any_retry(self):
        before=self.data()
        with patch.object(hook,'append_usage',side_effect=ValueError('simulated_db_failure')):
            with self.assertRaises(ValueError):self.run_hook()
        self.operation['run_id']='another_run'
        self.operation['plan']['usage_collection']['task_title']='Changed after failed import';self.write_ledger()
        with self.assertRaisesRegex(ValueError,'hook_operation_binding_changed'):self.run_hook()
        self.assertEqual(before,self.data())

    def test_pending_unclosed_turn_already_freezes_its_operation(self):
        self.rows.pop();self.write_source();self.assertEqual(self.run_hook()['status'],'pending')
        self.operation['plan']['usage_collection']['task_title']='changed pending title';self.write_ledger()
        with self.assertRaisesRegex(ValueError,'hook_operation_binding_changed'):self.run_hook()
        self.assertFalse(any(e['kind']=='usage' for e in self.data()['events']))

    def test_intent_recovers_crash_before_metadata_file_but_rejects_snapshot_tamper(self):
        real_save=hook.save
        def fail_metadata(path,*args):
            if str(path).endswith('.metadata.json'):raise OSError('simulated_crash')
            return real_save(path,*args)
        with patch.object(hook,'save',side_effect=fail_metadata):
            with self.assertRaises(OSError):self.run_hook()
        intent=next(Path(self.config['directory']).glob('*.receipt.json'))
        original=intent.read_bytes();value=json.loads(original)
        self.assertIn('snapshot',value);self.assertFalse(Path(value['record_path']).exists())
        value['snapshot']['recorded_at']='tampered';self.save(intent,value)
        with self.assertRaisesRegex(ValueError,'hook_record_changed'):self.run_hook()
        intent.write_bytes(original)
        self.assertEqual(self.run_hook()['status'],'collected')
        self.assertEqual(len([e for e in self.data()['events'] if e['kind']=='usage']),1)

    def test_malformed_ledger_and_deep_json_always_return_json_warning_without_traceback(self):
        cmd=[sys.executable,str(Path(hook.__file__)),'--config',str(self.config_path)]
        for ledger in [[],dict(self.ledger,operations=[]),dict(self.ledger,operations={'bad':None}),
                       dict(self.ledger,operations={'bad':{'plan':[]}})]:
            self.save(Path(self.config['ledger']),ledger)
            result=subprocess.run(cmd,input=json.dumps(self.payload),capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('systemMessage',json.loads(result.stdout));self.assertNotIn('Traceback',result.stderr)
        self.write_ledger()
        for depth in (65,2000):
            raw=json.dumps(self.payload)[:-1]+',"unused":'+'['*depth+'0'+']'*depth+'}'
            result=subprocess.run(cmd,input=raw,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            out=json.loads(result.stdout);self.assertIn('systemMessage',out)
            self.assertFalse({'decision','continue'}&set(out));self.assertNotIn('Traceback',result.stderr)
        self.assertFalse(any(e['kind']=='usage' for e in self.data()['events']))

    def test_lock_prevents_concurrent_collection(self):
        from lifecycle_io import locked,digest
        key=digest(dict(session=self.payload['session_id'],turn=self.payload['turn_id']))
        path=Path(self.config['directory'])/(key+'.receipt.lock')
        with locked(path,self.project):
            with self.assertRaisesRegex(ValueError,'lifecycle_already_running'):self.run_hook()

    def test_same_dispatch_in_two_opt_in_operations_is_not_selected_arbitrarily(self):
        self.ledger['operations']['duplicate']=copy.deepcopy(self.operation);self.write_ledger()
        before=self.data()
        with self.assertRaisesRegex(ValueError,'ambiguous_hook_operation'):self.run_hook()
        self.assertEqual(self.data(),before)

    def test_launch_receipt_race_is_bounded_and_can_become_registered(self):
        self.operation['task_id']=self.operation['dispatch_id']=None;self.operation['state']='launching';self.write_ledger()
        def captured(_):
            self.operation.update(task_id='task_example',dispatch_id='ctx_example',state='bound');self.write_ledger()
        with patch.object(hook.time,'sleep',side_effect=captured) as sleeps:
            self.assertEqual(self.run_hook()['status'],'collected');self.assertEqual(sleeps.call_count,1)

    def test_success_replay_can_reimport_a_restored_database_without_adding_twice(self):
        self.run_hook()
        # Simulate restoring a DB snapshot from before the usage event existed.
        with self.store.db:
            self.store.db.execute("DELETE FROM records WHERE kind='event' AND json_extract(body,'$.kind')='usage'")
        self.assertTrue(self.run_hook()['replay'])
        self.assertEqual(len([e for e in self.data()['events'] if e['kind']=='usage']),1)
        self.assertTrue(self.run_hook()['replay'])

    def test_different_provider_or_changed_raw_source_is_not_promoted_to_usage(self):
        before=self.data();self.rows[0]['payload']['originator']='other_app';self.write_source()
        with self.assertRaises(ValueError):self.run_hook()
        self.rows[0]['payload']['originator']='orca_desktop';self.write_source()
        self.operation['plan']['agent']='claude';self.write_ledger()
        with self.assertRaises(ValueError):self.run_hook()
        self.assertEqual(before,self.data())

    def test_cli_definition_is_async_bounded_and_errors_never_request_continuation(self):
        script=Path(hook.__file__)
        cmd=[sys.executable,str(script),'--config',str(self.config_path)]
        r=subprocess.run(cmd+['--print-hook'],capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr);definition=json.loads(r.stdout)
        handler=definition['hooks']['Stop'][0]['hooks'][0]
        self.assertTrue(handler['async']);self.assertEqual(handler['timeout'],30)
        self.assertFalse((self.project/'.codex/hooks.json').exists())
        for payload in [self.payload,dict(self.payload,session_id='wrong')]:
            r=subprocess.run(cmd,input=json.dumps(payload),capture_output=True,text=True,timeout=10)
            self.assertEqual(r.returncode,0);self.assertNotIn('decision',json.loads(r.stdout));self.assertNotIn('continue',json.loads(r.stdout))

    def test_usage_metadata_is_reserved_in_guard_plan_and_requires_all_fields(self):
        spec=self.root/'task.txt';spec.write_text('synthetic')
        import argparse
        args=argparse.Namespace(project=self.project,command='start',spec_file=spec,writes=[],effort=None,model=None,
            worktree='current',agent='codex',depends_on=[],subject='test',usage_role='backend',usage_actor_id='sample-worker',usage_task_title='가상 작업')
        plan,_=guard.make_plan(args,None);self.assertEqual(plan['usage_collection']['actor_id'],'sample-worker')
        for key,value in [('usage_role',None),('usage_actor_id',None),('usage_task_title',None),('agent','claude')]:
            changed=copy.copy(args);setattr(changed,key,value)
            with self.assertRaises((ValueError,TypeError)):guard.make_plan(changed,None)
