import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from trace_store import Store, load_bundle, digest, encoded
from trace_observation import build_event
from trace_usage import import_usage
from trace_amendments import SOURCES, canonical_target, event_state, import_amendment

FIXTURES = Path(__file__).parent / 'fixtures/traces'


class AmendmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.db = self.root/'private/trace.sqlite3'
        self.store = Store(self.db); self.addCleanup(self.store.close)
        self.store.import_bundle(load_bundle(FIXTURES/'sample.json'))
        self.observation = build_event(load_bundle(FIXTURES/'observation.json'))
        self.add(self.observation)
        record = load_bundle(FIXTURES/'native-usage.json')
        for index in range(2):
            record = copy.deepcopy(record); u = record['dispatch_usage']
            u['session_id'] = 'session_' + str(index)
            u['dispatch_id'] = u['actor_id'] = 'dispatch_' + str(index)
            u['measurement_key'] = 'codex.session:' + u['session_id'] + ':turn:turn_example'
            path = self.root/f'usage-{index}.json'; path.write_text(json.dumps(record))
            import_usage(self.store, path, actor_id='sample-worker')
        self.members = [e['id'] for e in self.data()['events'] if e['kind'] == 'usage']

    def add(self, *events):
        return self.store.import_bundle(dict(schema_version=1, requests=[], actors=[], events=list(events)))

    def data(self): return self.store.request('sample-request')

    def group(self, name='group-one', members=None):
        return dict(id=name, request_id='sample-request', actor_id='sample-worker', time=None,
                    kind='usage_group', source=SOURCES['usage_group'], source_id=name, completeness='partial',
                    detail=dict(version=1, recorded_by_actor_id='sample-coordinator', task_id=name, title='가상 구현',
                                role='backend', measurement_event_ids=self.members if members is None else members))

    def revision(self, target, name='revision-one', **changes):
        state = event_state(self.store, 'sample-request', target)
        replacement = copy.deepcopy(state['current']); replacement.update(changes)
        replacement = canonical_target(replacement)
        return dict(id=name, request_id='sample-request', actor_id='sample-coordinator', time=None,
                    kind='revision', source=SOURCES['revision'], source_id=name, completeness='partial',
                    detail=dict(version=1, recorded_by_actor_id='sample-coordinator', target_event_id=target,
                                previous_revision_id=state['previous_revision_id'], expected_event_sha256=state['expected_event_sha256'],
                                reason='가상 귀속 정정 근거', replacement=replacement))

    def rejected(self, row):
        before = self.data()
        with self.assertRaises((ValueError, TypeError)): self.add(row)
        self.assertEqual(self.data(), before)

    def test_groups_sum_atomic_measurements_without_new_usage_or_actor_mutation(self):
        before = self.data(); self.add(self.group()); after = self.data()
        self.assertEqual(after['usage_groups'][0]['measurements']['total_tokens'], 240)
        self.assertEqual(len([e for e in after['events'] if e['kind']=='usage']), 2)
        self.assertEqual(before['actors'], after['actors'])
        self.add(self.group()); self.assertEqual(self.data(), after)
        self.assertEqual(sum(e['detail']['usage']['measurements']['total_tokens'] for e in after['events'] if e['kind']=='usage'), 240)

    def test_groups_reject_missing_cross_owner_role_duplicate_and_claimed_members(self):
        for field, value in [('measurement_event_ids', []), ('measurement_event_ids', ['missing']),
                             ('measurement_event_ids', self.members*2), ('role', 'frontend')]:
            row=self.group(); row['detail'][field]=value; self.rejected(row)
        row=self.group();row['actor_id']='sample-coordinator';self.rejected(row)
        self.add(self.group()); self.rejected(self.group('second'))

    def test_unknown_subset_stays_unknown_in_group(self):
        # Reuse validated measurements with unavailable optional subsets in a fresh DB.
        store=Store(self.root/'other/trace.sqlite3');self.addCleanup(store.close)
        bundle=self.data(); bundle['schema_version']=1
        for row in bundle['events']:
            if row['kind']=='usage':
                u=row['detail']['usage']
                u['measurements']['cached_input_tokens']=None
                for inc in u['increments']: inc['values']['cached_input_tokens']=None
                u['final']['values']['cached_input_tokens']=None
                row.pop('detail_sha256',None)
        store.import_bundle(bundle);store.import_bundle(dict(schema_version=1,requests=[],actors=[],events=[self.group()]))
        self.assertIsNone(store.request('sample-request')['usage_groups'][0]['measurements']['cached_input_tokens'])

    def test_correction_preserves_original_and_changes_only_effective_executor(self):
        before=self.data(); attribution=copy.deepcopy(self.observation['attribution'])
        attribution['executor_actor_id']='sample-worker'
        row=self.revision(self.observation['id'], actor_id='sample-worker', attribution=attribution)
        self.add(row); data=self.data()
        self.assertTrue(all(e in data['events'] for e in before['events']))
        current=next(e for e in data['effective_events'] if e['id']==self.observation['id'])
        self.assertEqual(current['actor_id'],'sample-worker')
        self.assertEqual(data['event_revisions'][current['id']][0]['detail']['reason'],'가상 귀속 정정 근거')
        self.assertFalse(any(e['kind']=='revision' for e in data['effective_events']))
        self.add(row);self.assertEqual(self.data(),data)

    def test_chain_hash_predecessor_fork_and_cycle_are_rejected_atomically(self):
        first=self.revision(self.observation['id']);stale=copy.deepcopy(first)
        self.add(first); second=self.revision(self.observation['id'],'revision-two');self.add(second)
        stale.update(id='stale',source_id='stale');self.rejected(stale)
        for key,value in [('expected_event_sha256','0'*64),('previous_revision_id','missing'),('reason','')]:
            bad=self.revision(self.observation['id'],'bad');bad['detail'][key]=value;self.rejected(bad)
        cyclic=self.revision(self.observation['id'],'cycle');cyclic['detail']['previous_revision_id']='cycle';self.rejected(cyclic)
        conflict=copy.deepcopy(first);conflict['detail']['reason']='different';self.rejected(conflict)

    def test_raw_usage_identity_cross_request_and_non_root_recorder_are_not_correctable(self):
        with self.assertRaises(ValueError): self.revision(self.members[0])
        for key,value in [('request_id','different'),('kind','tool_result'),('source','legacy')]:
            bad=self.revision(self.observation['id']);bad['detail']['replacement'][key]=value;self.rejected(bad)
        bad=self.revision(self.observation['id']);bad['actor_id']=bad['detail']['recorded_by_actor_id']='sample-worker';self.rejected(bad)
        bad=self.revision(self.observation['id']);bad['detail']['replacement']['attribution']['product_owner_actor_id']='missing';self.rejected(bad)

    def test_group_revision_can_change_members_but_cannot_double_claim(self):
        self.add(self.group(members=self.members[:1]),self.group('group-two',self.members[1:]))
        detail=copy.deepcopy(event_state(self.store,'sample-request','group-one')['current']['detail'])
        detail['measurement_event_ids']=self.members
        self.rejected(self.revision('group-one',detail=detail))
        detail['measurement_event_ids']=self.members[:1];detail['title']='정정한 작업명'
        self.add(self.revision('group-one',detail=detail))
        self.assertEqual(self.data()['usage_groups'][0]['title'],'정정한 작업명')

    def test_order_independent_restore_and_original_immutability(self):
        self.add(self.group());self.add(self.revision(self.observation['id']))
        self.add(self.revision(self.observation['id'],'revision-two'))
        bundle=self.data();bundle['schema_version']=1;bundle['events'].reverse()
        store=Store(self.root/'restored/trace.sqlite3');self.addCleanup(store.close);store.import_bundle(bundle)
        expected=self.data();self.assertEqual(store.request('sample-request'),expected)
        bad=copy.deepcopy(self.observation);bad['detail']['exitCode']=1;self.rejected(bad)

    def test_cli_group_state_revision_jsonl_roundtrip_and_provenance(self):
        script=Path(__file__).parents[1]/'scripts/trace_viewer.py'
        def cli(*args):
            return subprocess.run([sys.executable,str(script),'--db',str(self.db),*args],capture_output=True,text=True,timeout=10)
        def manifest(row):
            return dict(schema_version=1,**{k:row[k] for k in ('id','request_id','actor_id','time','detail')},recorded_at=None)
        for kind, row in [('group-usage',self.group()), ('revise-event',self.revision(self.observation['id']))]:
            path=self.root/(kind+'.json');path.write_text(encoded(manifest(row)))
            result=cli(kind,str(path));self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(cli(kind,str(path)).stdout,result.stdout)
        state=cli('event-state','--request-id','sample-request','--event-id',self.observation['id'])
        self.assertEqual(json.loads(state.stdout)['previous_revision_id'],'revision-one')
        exported=cli('export','--request-id','sample-request','--format','jsonl')
        path=self.root/'export.jsonl';path.write_text(exported.stdout)
        restored=Store(self.root/'jsonl/trace.sqlite3');self.addCleanup(restored.close)
        restored.import_bundle(load_bundle(path));self.assertEqual(restored.request('sample-request'),self.data())
        collection=next(c for c in self.data()['collections'] if c['source']=='trace.amendment-input.v1')
        collection=copy.deepcopy(collection);collection['actor_id']='sample-worker'
        with self.assertRaises(ValueError):self.store.import_bundle(dict(schema_version=1,requests=[],actors=[],events=[],collections=[collection]))

    def test_nonfinite_hidden_numbers_and_reserved_namespaces_cannot_bypass_preflight(self):
        for row in [self.group(),self.revision(self.observation['id'])]:
            bad=copy.deepcopy(row);bad['detail']['analysis']=float('inf');self.rejected(bad)
            bad=copy.deepcopy(row);bad['source']='trace.event-revision.v999';self.rejected(bad)
            bad=copy.deepcopy(row);bad['source']='legacy';self.rejected(bad)
        bad=self.revision(self.observation['id']);bad['detail']['replacement']['detail']['analysis']=float('inf');self.rejected(bad)
        path=self.root/'overflow.json';payload=dict(schema_version=1,**{k:self.group()[k] for k in ('id','request_id','actor_id','time','detail')},recorded_at=None)
        path.write_text(json.dumps(payload)[:-1]+',"analysis":1e999}')
        with self.assertRaises(ValueError):import_amendment(self.store,path,'usage_group')
