import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from trace_observation import build_event, import_observation, INPUT_SOURCE, SOURCE
from trace_store import Store, load_bundle

FIXTURES = Path(__file__).parent / 'fixtures/traces'


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.db = self.root/'private/trace.sqlite3'
        self.store = Store(self.db); self.addCleanup(self.store.close)
        self.sample = load_bundle(FIXTURES/'sample.json'); self.store.import_bundle(self.sample)
        self.payload = load_bundle(FIXTURES/'observation.json'); self.input = self.root/'observation.json'
        self.write()

    def write(self, payload=None, filename=None):
        path = self.root/filename if filename else self.input
        path.write_text(json.dumps(self.payload if payload is None else payload)); return path

    def data(self): return self.store.request('sample-request')

    def generic(self, event, **extra):
        return self.store.import_bundle(dict(schema_version=1, requests=[], actors=[], events=[event], **extra))

    def test_independent_command_actor_is_executor_not_product_owner(self):
        before=self.data(); receipt=import_observation(self.store,self.input)
        self.assertEqual(receipt['actor_id'],'sample-coordinator')
        event=next(e for e in self.data()['events'] if e['id']==self.payload['id'])
        self.assertEqual(event['actor_id'],'sample-coordinator')
        self.assertEqual(event['attribution']['product_owner_actor_id'],'sample-worker')
        self.assertEqual(event['attribution']['verification_subject_actor_id'],'sample-worker')
        self.assertEqual(self.data()['actors'],before['actors'])
        self.assertTrue(all(e in self.data()['events'] for e in before['events']))

    def test_self_test_and_unknown_owner_are_explicit_without_role_inference(self):
        self.payload['attribution'].update(executor_actor_id='sample-worker',verifier_actor_id='sample-worker',
                                          product_owner_actor_id=None,verification_subject_actor_id=None)
        self.write();import_observation(self.store,self.input)
        event=next(e for e in self.data()['events'] if e['id']==self.payload['id'])
        self.assertEqual(event['actor_id'],'sample-worker')
        self.assertIsNone(event['attribution']['product_owner_actor_id'])
        self.assertIsNone(event['attribution']['verification_subject_actor_id'])

    def test_verdict_belongs_to_verifier_and_cannot_pretend_to_execute_command(self):
        self.payload.update(kind='verification',detail=dict(verdict='pass',summary='가상 독립 검사'))
        self.payload['attribution']['executor_actor_id']=None
        self.write();import_observation(self.store,self.input)
        event=next(e for e in self.data()['events'] if e['id']==self.payload['id'])
        self.assertEqual(event['actor_id'],'sample-coordinator')
        for field in ['verifier_actor_id','verification_subject_actor_id']:
            bad=copy.deepcopy(self.payload);bad['attribution'][field]=None
            with self.assertRaises(ValueError):build_event(bad)
        self.payload['attribution']['executor_actor_id']='sample-worker'
        with self.assertRaises(ValueError):build_event(self.payload)

    def test_result_keeps_recorder_separate_from_actual_executor(self):
        self.payload.update(kind='tool_result',detail=dict(stdout='synthetic output',stderr='',exitCode=0))
        self.payload['attribution']['executor_actor_id']='sample-worker'
        event=build_event(self.payload);self.generic(event)
        self.assertEqual(event['actor_id'],'sample-worker')
        self.assertEqual(event['attribution']['recorded_by_actor_id'],'sample-coordinator')

    def test_generic_import_cannot_bypass_actor_binding_or_contract_namespace(self):
        original=self.data();event=build_event(self.payload)
        variants=[]
        bad=copy.deepcopy(event);bad['actor_id']='sample-worker';variants.append(bad)
        bad=copy.deepcopy(event);bad.pop('attribution');variants.append(bad)
        bad=copy.deepcopy(event);bad['source']='legacy';variants.append(bad)
        bad=copy.deepcopy(event);bad['source']='trace.attributed-observation.v2';variants.append(bad)
        bad=copy.deepcopy(event);bad['source_id']='different';variants.append(bad)
        for value in variants:
            with self.subTest(value=value),self.assertRaises(ValueError):self.generic(value)
            self.assertEqual(self.data(),original)

    def test_all_attribution_references_must_exist_in_same_request_and_rollback(self):
        request=copy.deepcopy(self.sample['requests'][0]);request['id']='other-request'
        actor=copy.deepcopy(self.sample['actors'][0]);actor.update(id='other-actor',request_id='other-request')
        self.store.import_bundle(dict(schema_version=1,requests=[request],actors=[actor],events=[]))
        before=self.data()
        for field in self.payload['attribution']:
            if field=='version':continue
            for value in ['missing-actor','other-actor']:
                bad=copy.deepcopy(self.payload);bad['attribution'][field]=value
                changed=copy.deepcopy(before['requests'][0]);changed['status']='completed'
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                    self.store.import_bundle(dict(schema_version=1,requests=[changed],actors=[],events=[build_event(bad)]))
                self.assertEqual(self.data(),before)

    def test_privacy_cleaning_cannot_hide_malformed_protected_metadata(self):
        before=self.data();event=build_event(self.payload)
        bad=copy.deepcopy(event);bad['attribution']['analysis']='must not be silently removed'
        with self.assertRaises(ValueError):self.generic(bad)
        bad=copy.deepcopy(event);bad.pop('attribution');bad['source']=SOURCE+'<analysis>invalid'
        with self.assertRaises(ValueError):self.generic(bad)
        self.assertEqual(self.data(),before)

    def test_replay_is_stable_and_new_input_path_adds_provenance_not_duplicate_event(self):
        import_observation(self.store,self.input);before=self.data()
        import_observation(self.store,self.input);self.assertEqual(self.data(),before)
        import_observation(self.store,self.write(filename='copy.json'));after=self.data()
        self.assertEqual(after['events'],before['events']);self.assertEqual(len(after['collections']),2)

    def test_finalized_observations_are_immutable_in_generic_and_file_import(self):
        import_observation(self.store,self.input);before=self.data()
        bad=copy.deepcopy(self.payload);bad['detail']['exitCode']=2
        with self.assertRaises(ValueError):import_observation(self.store,self.write(bad))
        with self.assertRaises(ValueError):self.generic(build_event(bad))
        self.assertEqual(self.data(),before)

    def test_old_records_remain_valid_and_legacy_tool_status_updates_still_work(self):
        legacy=copy.deepcopy(next(e for e in self.sample['events'] if e['kind']=='tool_call'))
        legacy['detail'].update(state='completed',exitCode=0);self.generic(legacy)
        saved=next(e for e in self.data()['events'] if e['id']==legacy['id'])
        self.assertNotIn('attribution',saved)
        self.assertEqual(saved['detail']['state'],'completed')

    def test_null_times_are_preserved_and_invalid_payloads_never_create_records(self):
        self.payload.update(time=None,recorded_at=None);self.write();import_observation(self.store,self.input)
        self.assertIsNone(next(e for e in self.data()['events'] if e['id']==self.payload['id'])['time'])
        self.assertIsNone(self.data()['collections'][0]['time'])
        variants=[]
        for field,value in [('schema_version',True),('kind','usage'),('kind',[]),('actor_id','sample-worker')]:
            bad=copy.deepcopy(self.payload);bad[field]=value;variants.append(bad)
        bad=copy.deepcopy(self.payload);bad['attribution']['executor_actor_id']=None;variants.append(bad)
        bad=copy.deepcopy(self.payload);bad['attribution']['version']=True;variants.append(bad)
        bad=copy.deepcopy(self.payload);bad['attribution']['extra']='guess';variants.append(bad)
        bad=copy.deepcopy(self.payload);bad['attribution'].pop('product_owner_actor_id');variants.append(bad)
        bad=copy.deepcopy(self.payload);bad['detail']['state']='running';variants.append(bad)
        bad=copy.deepcopy(self.payload);bad['detail']['exitCode']=True;variants.append(bad)
        for value in variants:
            with self.subTest(value=value),self.assertRaises(ValueError):build_event(value)

    def test_input_evidence_mismatch_rolls_back_even_generic_import(self):
        event=build_event(self.payload)
        collection=dict(request_id='sample-request',source=INPUT_SOURCE,source_id=event['id'],
                        actor_id='sample-worker',time=None,completeness='partial',input_path='/synthetic/input.json',input_sha256='0'*64)
        before=self.data()
        with self.assertRaises(ValueError):self.generic(event,collections=[collection])
        self.assertEqual(self.data(),before)

    def test_cli_import_and_jsonl_restore_work_after_input_disappears(self):
        script=str(Path(__file__).parents[1]/'scripts/trace_viewer.py')
        args=[sys.executable,script,'--db',str(self.db),'import-observation',str(self.input)]
        r=subprocess.run(args,capture_output=True,text=True,timeout=5);self.assertEqual(r.returncode,0,r.stderr)
        exported=subprocess.run([sys.executable,script,'--db',str(self.db),'export','--request-id','sample-request','--format','jsonl'],capture_output=True,text=True,timeout=5)
        self.assertEqual(exported.returncode,0,exported.stderr)
        file=self.root/'export.jsonl';file.write_text(exported.stdout);self.input.unlink()
        target=Store(self.root/'restore/trace.sqlite3');self.addCleanup(target.close)
        target.import_bundle(load_bundle(file));self.assertEqual(target.request('sample-request'),self.data())

    def test_duplicate_nonfinite_oversized_and_nonregular_inputs_are_rejected(self):
        before=self.data()
        for raw in ['{"id":"a","id":"b"}','{"value":NaN}',' '* (1024*1024+1)]:
            self.input.write_text(raw)
            with self.assertRaises(ValueError):import_observation(self.store,self.input)
            self.assertEqual(self.data(),before)
        fifo=self.root/'pipe';os.mkfifo(fifo)
        with self.assertRaises(ValueError):import_observation(self.store,fifo)

    def test_float_overflow_is_rejected_even_in_fields_privacy_clean_would_remove(self):
        before=self.data();payload=copy.deepcopy(self.payload)
        payload['detail']['input']['analysis']='OVERFLOW'
        for value in ['1e999','-1e999']:
            self.input.write_text(json.dumps(payload).replace('"OVERFLOW"',value))
            with self.subTest(value=value),self.assertRaises(ValueError):import_observation(self.store,self.input)
            self.assertEqual(self.data(),before)
        for value in [float('inf'),float('-inf'),float('nan')]:
            event=build_event(self.payload);event['detail']['input']['analysis']=value
            with self.subTest(value=value),self.assertRaises(ValueError):self.generic(event)
            self.assertEqual(self.data(),before)
        event=build_event(self.payload);event['detail']['input']['analysis']=(float('inf'),)
        with self.assertRaises(ValueError):self.generic(event)
        valid=copy.deepcopy(self.payload);valid['detail']['input']['threshold']=0.25
        import_observation(self.store,self.write(valid))

    def test_protected_input_namespace_is_checked_before_clean_and_rolls_back(self):
        before=self.data();event=build_event(self.payload)
        evidence=dict(request_id='sample-request',source=INPUT_SOURCE,source_id=event['id'],
                      actor_id='sample-worker',time=None,completeness='partial',
                      input_path='/synthetic/input.json',input_sha256='0'*64)
        for source in [INPUT_SOURCE,INPUT_SOURCE+'<analysis>invalid','trace.observation-input','trace.observation-input.v2']:
            bad=copy.deepcopy(evidence);bad['source']=source
            with self.subTest(source=source),self.assertRaises(ValueError):self.generic(event,collections=[bad])
            self.assertEqual(self.data(),before)
        evidence['actor_id']='sample-coordinator';evidence['analysis']=float('inf')
        with self.assertRaises(ValueError):self.generic(event,collections=[evidence])
        self.assertEqual(self.data(),before)
        # Opaque legacy evidence keeps its earlier contract.
        legacy=dict(request_id='sample-request',source='synthetic-legacy',source_id='example',time=None,completeness='unknown')
        self.store.import_bundle(dict(schema_version=1,requests=[],actors=[],events=[],collections=[legacy]))


if __name__=='__main__':unittest.main()
