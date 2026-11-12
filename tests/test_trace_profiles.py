import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from trace_store import Store, load_bundle
from trace_usage import import_usage
from trace_profiles import SOURCE, EVIDENCE, profile_id

FIXTURES=Path(__file__).parent/'fixtures/traces'


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.store=Store(self.root/'private/trace.sqlite3');self.addCleanup(self.store.close)
        self.store.import_bundle(load_bundle(FIXTURES/'sample.json'))
        self.record=load_bundle(FIXTURES/'native-usage.json');u=self.record['dispatch_usage']
        self.record.update(session=dict(id=u['session_id'],source='vscode',originator='orca_desktop'),
            binding=dict(task_id=u['task_id'],dispatch_id=u['dispatch_id']),
            observed_contexts=[dict(line=3,time=None,values=dict(model='example-model',effort='low'))])
        self.path=self.root/'metadata.json'

    def data(self):return self.store.request('sample-request')
    def observe(self):
        self.path.write_text(json.dumps(self.record));return import_usage(self.store,self.path,task_title='가상 프로필')
    def profiles(self):return [e for e in self.data()['events'] if e['kind']=='profile']
    def generic(self,event,collections=None):
        return self.store.import_bundle(dict(schema_version=1,requests=[],actors=[],events=[event],collections=collections or []))
    def stage(self,name):
        return dict(model='example-model',effort='low',status='observed',source=dict(kind='orca.worker-show.launch.'+name,path='/private/path/worker.json',sha256='0'*64))

    def test_auto_append_does_not_change_actors_or_duplicate_tokens_and_replays(self):
        before=self.data();self.observe();after=self.data();self.observe();self.assertEqual(self.data(),after)
        self.assertEqual(before['actors'],after['actors']);self.assertEqual(len(self.profiles()),1)
        usage=[e for e in after['events'] if e['kind']=='usage'];self.assertEqual(len(usage),1)
        self.assertEqual(usage[0]['detail']['usage']['measurements']['total_tokens'],120)
        d=self.profiles()[0]['detail'];self.assertEqual(d['contexts'][0]['model'],'example-model')
        self.assertEqual(d['requested']['status'],'unavailable');self.assertEqual(d['effective']['status'],'unavailable')
        self.assertEqual(len([c for c in after['collections'] if c['source']==EVIDENCE]),1)

    def test_new_launch_evidence_adds_observation_without_replacing_earlier_unknown(self):
        self.observe();first=copy.deepcopy(self.profiles()[0])
        self.record.update(requested=self.stage('requested'),launch_effective=self.stage('effective'));self.observe()
        self.assertEqual(len(self.profiles()),2);self.assertIn(first,self.profiles())
        self.assertEqual(len([e for e in self.data()['events'] if e['kind']=='usage']),1)

    def test_only_contexts_inside_physical_turn_are_attributed(self):
        self.record['observed_contexts']=[dict(line=1,time=None,values=dict(model='other',effort='high')),
            *self.record['observed_contexts'],dict(line=8,time=None,values=dict(model='later',effort='high'))]
        self.observe();self.assertEqual([c['line'] for c in self.profiles()[0]['detail']['contexts']],[3])

    def test_multiple_context_profiles_and_missing_fields_are_preserved(self):
        self.record['observed_contexts'].append(dict(line=5,time=None,values=dict(model='other-model',effort=None)))
        self.observe();contexts=self.profiles()[0]['detail']['contexts']
        self.assertEqual(len(contexts),2);self.assertIsNone(contexts[1]['effort'])
        self.assertEqual(contexts[1]['model'],'other-model')

    def test_bad_source_identity_and_stages_roll_back_usage_too(self):
        original=copy.deepcopy(self.record);before=self.data()
        changes=[dict(session=dict(id='wrong',source='vscode',originator='orca_desktop')),
                 dict(binding=dict(task_id='other',dispatch_id='sample-worker')),
                 dict(kind='other'),dict(requested=dict(model='invented',effort='low',source=None,status='unavailable')),
                 dict(launch_effective=self.stage('requested'))]
        for change in changes:
            self.record=dict(original,**change)
            with self.subTest(change=change),self.assertRaises(ValueError):self.observe()
            self.assertEqual(self.data(),before)

    def test_generic_profile_cannot_cross_owner_request_or_interval(self):
        self.observe();before=self.data();row=copy.deepcopy(self.profiles()[0])
        for change in [dict(actor_id='sample-coordinator'),dict(request_id='other-request')]:
            bad=dict(row,**change)
            # Separate ID would still fail interval owner/request references.
            with self.assertRaises(ValueError):self.generic(bad)
            self.assertEqual(self.data(),before)
        bad=copy.deepcopy(row);bad['detail']['interval']['end_line']=9;bad['id']=profile_id(bad['detail'])
        with self.assertRaises(ValueError):self.generic(bad)
        bad=copy.deepcopy(row);bad['detail']['contexts'][0]['line']=7;bad['id']=profile_id(bad['detail'])
        with self.assertRaises(ValueError):self.generic(bad)

    def test_evidence_cannot_reference_wrong_owner_or_source_version(self):
        self.observe();before=self.data();evidence=next(c for c in before['collections'] if c['source']==EVIDENCE)
        for change in [dict(actor_id='sample-coordinator'),dict(source='trace.native-profile-evidence.v99')]:
            with self.assertRaises(ValueError):self.store.import_bundle(dict(schema_version=1,requests=[],actors=[],events=[],collections=[dict(evidence,**change)]))
            self.assertEqual(self.data(),before)

    def test_profile_contract_and_nonfinite_values_survive_privacy_preflight(self):
        self.observe();before=self.data();row=copy.deepcopy(self.profiles()[0])
        for change in [dict(source='trace.native-profile.v99'),dict(source='legacy'),dict(kind='explanation')]:
            with self.assertRaises(ValueError):self.generic(dict(row,**change))
        bad=copy.deepcopy(row);bad['detail']['analysis']=float('inf')
        with self.assertRaises(ValueError):self.generic(bad)
        self.record['observed_contexts'][0]['values']['analysis']=float('inf')
        with self.assertRaises(ValueError):self.observe()
        self.assertEqual(self.data(),before)

    def test_restoring_profile_before_usage_is_order_independent(self):
        self.observe();bundle=dict(self.data(),schema_version=1);bundle['events'].reverse()
        store=Store(self.root/'restore/trace.sqlite3');self.addCleanup(store.close);store.import_bundle(bundle)
        self.assertEqual(store.request('sample-request'),self.data())
