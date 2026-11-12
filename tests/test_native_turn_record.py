import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from native_turn_record import inspect_turn
from native_record import inspect_session, write_record
from trace_store import Store, load_bundle
from trace_usage import import_usage
from test_native_record import event, context, assigned
from test_dispatch_usage import boundary, count


class NativeTurnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.source = self.root / 'session.jsonl'
        content = [dict(type='input_text', text='PRIVATE_REQUEST_BODY')]
        self.message = event('response_item', dict(type='message', role='user', content=content,
            internal_chat_message_metadata_passthrough=dict(turn_id='turn_one')))
        self.rows = [event('session_meta', dict(id='session_example', cwd=str(self.root),
                    source='vscode', originator='orca_desktop')), boundary('task_started'), context(),
                    self.message, count(), boundary('task_complete')]
        self.binding = dict(schema_version=1, request_id='sample-request', actor_id='sample-coordinator',
            task_id='coord_task', role='coordinator', native_turn_id='turn_one', message_line=4,
            message_sha256=hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest())

    def inspect(self):
        self.source.write_text(''.join(json.dumps(r) + '\n' for r in self.rows))
        return inspect_turn(self.root, self.source, 'session_example', self.binding)

    def test_closed_turn_uses_native_key_without_inventing_a_dispatch(self):
        record = self.inspect(); u = record['turn_usage']
        self.assertEqual(u['measurements']['total_tokens'], 120)
        self.assertIsNone(u['dispatch_id']); self.assertEqual(u['scope'], 'native_turn')
        self.assertEqual(u['actor_id'], 'sample-coordinator'); self.assertEqual(u['interval']['binding_line'], 4)
        self.assertNotIn('assignment_line', u['interval'])
        self.assertNotIn('PRIVATE_REQUEST_BODY', json.dumps(record))

    def multi_binding(self):
        second = copy.deepcopy(self.message)
        second['payload']['content'] = [dict(type='input_text', text='ADDITIONAL_PRIVATE_REQUEST')]
        self.rows.insert(4, second)
        self.binding = {k:v for k,v in self.binding.items() if k not in ('message_line','message_sha256')}
        self.binding.update(schema_version=2, scope='single_work', messages=[
            dict(line=index+1, sha256=hashlib.sha256(json.dumps(row['payload']['content'],ensure_ascii=False,
                 sort_keys=True,separators=(',',':')).encode()).hexdigest())
            for index,row in enumerate(self.rows) if row['type']=='response_item'])

    def test_explicit_multi_message_binding_is_one_physical_measurement(self):
        self.multi_binding();record=self.inspect();u=record['turn_usage']
        self.assertEqual(u['version'],3);self.assertEqual(u['measurements']['total_tokens'],120)
        self.assertEqual(u['interval']['binding_line'],4)
        self.assertEqual(len(u['message_bindings']),2)
        self.assertNotIn('ADDITIONAL_PRIVATE_REQUEST',json.dumps(record))
        from trace_usage import validate_usage
        validate_usage(u)
        sample=load_bundle(Path(__file__).parent/'fixtures/traces/sample.json')
        store=Store(self.root/'private/multi.sqlite3');self.addCleanup(store.close);store.import_bundle(sample)
        path=self.root/'record.json';path.write_text(json.dumps(record));import_usage(store,path)
        import_usage(store,path)
        usage=[e for e in store.request('sample-request')['events'] if e['kind']=='usage']
        self.assertEqual(len(usage),1);self.assertEqual(usage[0]['detail']['usage'],u)

    def test_multi_message_binding_requires_all_messages_and_valid_additional_routing(self):
        self.multi_binding();original=copy.deepcopy(self.binding)
        self.binding['messages'].pop()
        self.assertEqual(self.inspect()['turn_usage']['status'],'unavailable')
        self.binding=original
        for metadata in [None,{},dict(turn_id='other')]:
            self.rows[4]['payload']['internal_chat_message_metadata_passthrough']=metadata
            self.assertEqual(self.inspect()['turn_usage']['status'],'unavailable')
        self.rows[4]['payload']['internal_chat_message_metadata_passthrough']=dict(turn_id='turn_one')
        self.rows[4]['payload']['content'][0]['text']='changed'
        self.assertEqual(self.inspect()['turn_usage']['status'],'unavailable')

    def test_multi_binding_order_scope_and_duplicates_fail_closed(self):
        self.multi_binding();original=copy.deepcopy(self.binding)
        for change in [dict(scope='mixed_requests'),dict(messages=[]),dict(messages=self.binding['messages'][::-1]),
                       dict(messages=self.binding['messages']*2),dict(messages=[dict(line=True,sha256='0'*64)])]:
            self.binding=dict(original,**change)
            with self.assertRaises(ValueError):self.inspect()

    def test_version_three_cannot_overlap_version_two_or_mutate_existing_usage(self):
        from trace_usage import validate_usage
        self.multi_binding();u=self.inspect()['turn_usage']
        for members in [[],u['message_bindings'][::-1],[dict(line=6,sha256='0'*64)],
                        [dict(line=4,sha256='bad')],u['message_bindings']*2]:
            bad=copy.deepcopy(u);bad['message_bindings']=members
            with self.assertRaises(ValueError):validate_usage(bad)
        # Identical physical key cannot acquire a different logical binding on replay.
        store=Store(self.root/'private/conflict.sqlite3');self.addCleanup(store.close)
        store.import_bundle(load_bundle(Path(__file__).parent/'fixtures/traces/sample.json'))
        path=self.root/'v3.json';record=self.inspect();path.write_text(json.dumps(record));import_usage(store,path)
        record['turn_usage'].pop('message_bindings');record['turn_usage'].update(version=2,message_sha256='0'*64)
        path.write_text(json.dumps(record))
        with self.assertRaises(ValueError):import_usage(store,path)

    def test_separate_later_turn_does_not_change_a_closed_turn_measurement(self):
        first=self.inspect()['turn_usage']
        self.rows += [boundary('task_started','turn_two'), context(), event('response_item',dict(role='user',content=[])),
                      count((150,30,100,7),(50,10,30,2)),boundary('task_complete','turn_two')]
        self.assertEqual(self.inspect()['turn_usage'],first)
        self.binding.update(native_turn_id='turn_two', message_line=9,
                            message_sha256=hashlib.sha256(b'[]').hexdigest())
        u=self.inspect()['turn_usage'];self.assertEqual(u['measurements']['total_tokens'],60)
        self.assertEqual(u['baseline']['method'],'prior_turn_counter')

    def test_wrong_hash_line_routing_and_multiple_messages_are_not_attributed(self):
        for key,value in [('message_line',3),('message_sha256','0'*64)]:
            original=copy.deepcopy(self.binding);self.binding[key]=value
            self.assertEqual(self.inspect()['turn_usage']['issues'],['turn_message_binding_mismatch'])
            self.binding=original
        self.message['payload']['internal_chat_message_metadata_passthrough']['turn_id']='other'
        self.assertEqual(self.inspect()['turn_usage']['status'],'unavailable')
        self.message['payload']['internal_chat_message_metadata_passthrough']['turn_id']='turn_one'
        self.rows.insert(-1,copy.deepcopy(self.message))
        self.assertEqual(self.inspect()['turn_usage']['issues'],['ambiguous_turn_user_messages'])

    def test_malformed_explicit_routing_is_rejected_but_missing_remains_supported(self):
        key='internal_chat_message_metadata_passthrough'
        for routing in [None,False,123,'routing',[],{'turn_id':None},{'turn_id':123},
                        {'turn_id':False},{'turn_id':''},{'turn_id':'invalid turn'},
                        {'turn_id':[]},{'turn_id':'invalid/turn'}]:
            self.message['payload'][key]=routing
            with self.subTest(routing=routing):
                u=self.inspect()['turn_usage']
                self.assertEqual(u['issues'],['invalid_user_message_routing'])
                self.assertFalse(u['task_attributed']);self.assertEqual(u['measurements'],{})
        for routing in [{},{'other':'metadata'},{'turn_id':'turn_one'}]:
            self.message['payload'][key]=routing
            self.assertEqual(self.inspect()['turn_usage']['status'],'measured')
        self.message['payload'].pop(key)
        self.assertEqual(self.inspect()['turn_usage']['status'],'measured')

    def test_open_aborted_duplicate_nested_and_worker_turns_are_not_attributed(self):
        original=copy.deepcopy(self.rows)
        cases=[original[:-1],original[:-1]+[boundary('turn_aborted')],
               original+[boundary('task_started')],original[:-1]+[boundary('task_started','nested'),original[-1]]]
        for rows in cases:
            self.rows=rows
            with self.subTest(rows=rows):self.assertEqual(self.inspect()['turn_usage']['status'],'unavailable')
        self.rows=copy.deepcopy(original);self.rows[3]=assigned()
        content=self.rows[3]['payload']['content']
        self.binding['message_sha256']=hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(self.inspect()['turn_usage']['issues'],['worker_assignment_in_coordinator_turn'])

    def test_bad_counters_inherited_and_counter_outside_turn_are_not_zero(self):
        original=copy.deepcopy(self.rows)
        for sample in [count((200,40,140,10),(100,20,70,5)),event('event_msg',dict(type='token_count',info=None))]:
            self.rows=copy.deepcopy(original);self.rows[4]=sample
            self.assertEqual(self.inspect()['turn_usage']['status'],'unavailable')
        self.rows=copy.deepcopy(original);self.rows[0]['payload']['forked_from_id']='parent'
        self.assertEqual(self.inspect()['turn_usage']['issues'],['initial_baseline_unknown'])
        self.rows=copy.deepcopy(original);self.rows.append(count())
        self.assertEqual(self.inspect()['turn_usage']['issues'],['usage_outside_native_turn'])

    def test_unavailable_source_and_invalid_binding_fail_explicitly(self):
        for key,value in [('role','verifier'),('message_line',True),('message_sha256','bad')]:
            original=copy.deepcopy(self.binding);self.binding[key]=value
            with self.assertRaises(ValueError):self.inspect()
            self.binding=original
        self.rows[0]['payload']['source']='guardian'
        with self.assertRaises(ValueError):self.inspect()

    def test_explicit_archive_mode_reads_former_project_without_recreating_it(self):
        former=self.root/'removed-project'
        self.rows[0]['payload']['cwd']=str(former)
        self.rows[3]=assigned()
        self.source.write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
        args=dict(project=former,session_file=self.source,session_id='session_example',task_id='task_example',
                  dispatch_id='ctx_example',request_id='sample-request',role='verifier')
        with self.assertRaises(OSError):inspect_session(**args)
        record=inspect_session(**args,archived_project=True)
        self.assertEqual(record['dispatch_usage']['measurements']['total_tokens'],120)
        self.assertFalse(record['live_state_verified']);self.assertTrue(record['archived_project'])
        write_record(record,self.root/'private/archive.json',former,True)
        self.assertFalse(former.exists())
        with self.assertRaises(ValueError):inspect_session(**{**args,'project':self.root},archived_project=True)
        self.rows=[]
        with self.assertRaises(ValueError):self.inspect()

    def test_db_replay_roundtrip_and_legacy_cross_source_conflict(self):
        record=self.inspect();inp=self.root/'input.json';inp.write_text(json.dumps(record))
        store=Store(self.root/'private/trace.sqlite3');self.addCleanup(store.close)
        sample=load_bundle(Path(__file__).parent/'fixtures/traces/sample.json');store.import_bundle(sample)
        before=store.request('sample-request')
        import_usage(store,inp,task_title='요청 분석');after=store.request('sample-request')
        self.assertEqual(before['actors'],after['actors'])
        import_usage(store,inp,task_title='요청 분석');self.assertEqual(store.request('sample-request'),after)
        restored=Store(self.root/'restore/trace.sqlite3');self.addCleanup(restored.close)
        restored.import_bundle(dict(schema_version=1,**{k:after[k] for k in ('requests','actors','events','collections')}))
        self.assertEqual(restored.request('sample-request'),after)
        legacy=copy.deepcopy(sample['actors'][1]);legacy['id']='other-worker'
        legacy['usage']=copy.deepcopy(load_bundle(Path(__file__).parent/'fixtures/traces/native-usage.json')['dispatch_usage'])
        legacy['usage']['actor_id']='other-worker'
        with self.assertRaises(ValueError):store.import_bundle(dict(schema_version=1,requests=[],actors=[legacy],events=[]))
        self.assertEqual(store.request('sample-request'),after)

    def test_turn_cannot_bind_child_remap_actor_or_mix_input_formats(self):
        store=Store(self.root/'private/trace.sqlite3');self.addCleanup(store.close)
        store.import_bundle(load_bundle(Path(__file__).parent/'fixtures/traces/sample.json'))
        before=store.request('sample-request')
        original=self.inspect()
        for mutate in ['child','remap','both','spoof_dispatch']:
            r=copy.deepcopy(original)
            if mutate=='child':r['turn_usage']['actor_id']='sample-worker'
            if mutate=='both':r['dispatch_usage']=r['turn_usage']
            if mutate=='spoof_dispatch':r['turn_usage']['dispatch_id']='fake'
            path=self.root/'input.json';path.write_text(json.dumps(r))
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):
                import_usage(store,path,actor_id='sample-worker' if mutate=='remap' else None)
            self.assertEqual(store.request('sample-request'),before)

    def test_cli_saves_private_record_and_refuses_overwrite(self):
        self.inspect();binding=self.root/'binding.json';binding.write_text(json.dumps(self.binding))
        output=self.root/'private/record.json'
        args=[sys.executable,str(Path(__file__).parents[1]/'scripts/native_turn_record.py'),
              '--project',str(self.root),'--session-file',str(self.source),'--session-id','session_example',
              '--binding',str(binding),'--output',str(output)]
        # Output must be outside the target project.
        result=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertFalse(output.exists())
        other=tempfile.TemporaryDirectory();self.addCleanup(other.cleanup)
        output=Path(other.name).resolve()/'private/record.json';args[-1]=str(output)
        result=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(output.stat().st_mode & 0o777,0o600)
        before=output.read_bytes()
        self.assertEqual(subprocess.run(args,capture_output=True).returncode,2)
        self.assertEqual(output.read_bytes(),before)


if __name__=='__main__':unittest.main()
