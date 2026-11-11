import copy
import json
import os
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from trace_store import Store, load_bundle
from trace_usage import import_usage, event_id

FIXTURES = Path(__file__).parent / 'fixtures' / 'traces'


class TraceUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.path = self.root / 'private' / 'trace.sqlite3'
        self.store = Store(self.path); self.addCleanup(lambda: self.store.close())
        self.bundle = load_bundle(FIXTURES / 'sample.json'); self.store.import_bundle(self.bundle)
        self.record = load_bundle(FIXTURES / 'native-usage.json'); self.input = self.root / 'usage.json'; self.write()

    def write(self, record=None, name=None):
        path = self.root / name if name else self.input
        path.write_text(json.dumps(record or self.record)); return path

    def data(self): return self.store.request('sample-request')

    def observe(self, **args): return import_usage(self.store, self.input, **args)

    def second(self):
        record = copy.deepcopy(self.record); u = record['dispatch_usage']
        u.update(task_id='task_two', dispatch_id='ctx_two', actor_id='ctx_two', measurement_key='codex.session:session_example:turn:turn_two')
        u['interval'].update(native_turn_id='turn_two', start_line=10, assignment_line=12, end_line=15)
        u['baseline'] = dict(line=6, time=None, method='prior_turn_counter', values=copy.deepcopy(self.record['dispatch_usage']['final']['values']))
        values = dict(input_tokens=50, output_tokens=10, cached_input_tokens=30, reasoning_output_tokens=2, total_tokens=60)
        u['measurements'] = values
        u['final'].update(line=14, values={k: v + values[k] for k, v in u['baseline']['values'].items()})
        u['increments'] = [dict(line=14, time=None, values=copy.deepcopy(values))]
        return record

    def test_replay_and_new_snapshot_preserve_original_actors_and_single_measurement(self):
        before = self.data(); one = self.observe(task_title='가상 구현'); two = self.observe(task_title='가상 구현')
        self.assertEqual(one, two)
        data = self.data(); self.assertEqual(data['actors'], before['actors'])
        self.assertEqual(len(data['events']), len(before['events']) + 1); self.assertEqual(len(data['collections']), 1)
        for event in before['events']: self.assertIn(event, data['events'])
        later = copy.deepcopy(self.record); later['recorded_at'] = '2026-01-02T00:00:00Z'; later['source']['sha256'] = '1' * 64
        import_usage(self.store, self.write(later, 'later.json'), task_title='가상 구현')
        self.assertEqual(len(self.data()['events']), len(data['events'])); self.assertEqual(len(self.data()['collections']), 2)

    def test_two_nonoverlapping_dispatches_map_explicitly_to_one_imported_actor(self):
        self.observe(task_title='첫 구현')
        import_usage(self.store, self.write(self.second(), 'second.json'), actor_id='sample-worker', task_title='수정')
        events = [e for e in self.data()['events'] if e['kind'] == 'usage']
        self.assertEqual([e['actor_id'] for e in events], ['sample-worker', 'sample-worker'])
        self.assertEqual(sum(e['detail']['usage']['measurements']['total_tokens'] for e in events), 180)
        self.assertEqual(len(self.data()['actors']), 2)

    def test_cross_request_duplicate_physical_interval_is_rejected(self):
        self.observe()
        other = copy.deepcopy(self.bundle); other['requests'][0]['id'] = 'other'; other['actors'] = [dict(other['actors'][0], id='other-owner', request_id='other')]; other['events'] = []
        self.store.import_bundle(other)
        bad = copy.deepcopy(self.record); bad['dispatch_usage']['request_id'] = 'other'
        with self.assertRaises(ValueError): import_usage(self.store, self.write(bad, 'other.json'), actor_id='other-owner')
        self.assertEqual(len(self.store.request('other')['events']), 0)
        self.assertEqual(len(self.data()['collections']), 1)

    def test_overlap_and_second_physical_interval_for_same_dispatch_rejected(self):
        self.observe()
        bad = copy.deepcopy(self.record); u = bad['dispatch_usage']; u['interval']['native_turn_id'] = 'different'; u['measurement_key'] = 'codex.session:session_example:turn:different'
        with self.assertRaisesRegex(ValueError, 'Dispatch already'): import_usage(self.store, self.write(bad, 'bad.json'))
        u.update(task_id='another', dispatch_id='another', actor_id='another')
        with self.assertRaisesRegex(ValueError, 'overlap'): import_usage(self.store, self.write(bad, 'overlap.json'), actor_id='sample-worker')
        self.assertEqual(len([e for e in self.data()['events'] if e['kind'] == 'usage']), 1)

    def test_generic_bundle_cannot_bypass_identity_or_overlap_and_rolls_back(self):
        self.observe(); event = copy.deepcopy(next(e for e in self.data()['events'] if e['kind'] == 'usage'))
        event['id'] = 'forged-id'
        with self.assertRaises(ValueError): self.store.import_bundle(dict(schema_version=1, requests=[], actors=[], events=[event]))
        u = event['detail']['usage']; u.update(task_id='two', dispatch_id='two', actor_id='two', measurement_key='codex.session:session_example:turn:two'); u['interval']['native_turn_id'] = 'two'
        event['id'] = event_id(u['measurement_key'])
        extra = dict(self.bundle['events'][0], id='unrelated')
        with self.assertRaises(ValueError): self.store.import_bundle(dict(schema_version=1, requests=[], actors=[], events=[extra, event]))
        self.assertFalse(any(e['id'] == 'unrelated' for e in self.data()['events']))

    def test_unmeasured_invalid_counter_proof_and_unknown_owner_not_written(self):
        original = self.data()
        patches = [lambda u: u.update(status='unavailable'), lambda u: u.update(task_attributed=False),
                   lambda u: u['measurements'].update(total_tokens=999), lambda u: u['measurements'].update(input_tokens=True),
                   lambda u: u['final']['values'].update(input_tokens=101, total_tokens=121),
                   lambda u: u['increments'].append(copy.deepcopy(u['increments'][0])),
                   lambda u: u['increments'][0]['values'].update(cached_input_tokens=71),
                   lambda u: u.update(measurement_key='made-up'), lambda u: u.update(role='arbitrary')]
        for mutate in patches:
            bad = copy.deepcopy(self.record); mutate(bad['dispatch_usage'])
            with self.subTest(bad=bad), self.assertRaises(ValueError): import_usage(self.store, self.write(bad, 'invalid.json'))
            self.assertEqual(self.data(), original)
        with self.assertRaises(ValueError): self.observe(actor_id='missing')
        self.assertEqual(self.data(), original)

    def test_jsonl_cli_roundtrip_does_not_need_original_input_file(self):
        viewer = Path(__file__).parents[1] / 'scripts' / 'trace_viewer.py'
        def cli(db, *args):
            result = subprocess.run([sys.executable, str(viewer), '--db', str(db), *args], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr); return result.stdout
        cli(self.path, 'import-usage', str(self.input), '--task-title', '가상 작업')
        exported = self.root / 'export.jsonl'; exported.write_text(cli(self.path, 'export', '--request-id', 'sample-request', '--format', 'jsonl'))
        self.input.unlink(); new_path = self.root / 'restored' / 'trace.sqlite3'; cli(new_path, 'import', str(exported))
        restored = Store(new_path, readonly=True)
        try: self.assertEqual(restored.request('sample-request'), self.data())
        finally: restored.close()

    def test_evidence_cannot_be_rebound_or_reference_absent_observation(self):
        self.observe(); evidence = copy.deepcopy(self.data()['collections'][0]); evidence['actor_id'] = 'sample-coordinator'
        with self.assertRaises(ValueError): self.store.import_bundle(dict(schema_version=1, requests=[], actors=[], events=[], collections=[evidence]))
        evidence['actor_id'] = 'sample-worker'; evidence['measurement_key'] = evidence['source_id'] = 'missing'
        with self.assertRaises(ValueError): self.store.import_bundle(dict(schema_version=1, requests=[], actors=[], events=[], collections=[evidence]))

    def test_private_input_body_not_copied_and_fifo_rejected(self):
        self.record['analysis'] = 'PRIVATE_REASONING'; self.record['tool_output'] = 'PRIVATE_OUTPUT'; self.write(); self.observe()
        self.assertNotIn('PRIVATE_', json.dumps(self.data()))
        fifo = self.root / 'fifo'; os.mkfifo(fifo)
        with self.assertRaises(ValueError): import_usage(self.store, fifo)

    def test_legacy_attributed_actor_value_requires_exact_reconciliation(self):
        self.store.close(); self.store = Store(self.root / 'other' / 'trace.sqlite3')
        self.bundle['actors'][1]['usage'] = copy.deepcopy(self.record['dispatch_usage'])
        self.bundle['actors'][1]['usage']['measurement_key'] = 'other-measurement'
        self.store.import_bundle(self.bundle)
        with self.assertRaisesRegex(ValueError, 'reconciliation'): self.observe()
        self.assertFalse(any(e['kind'] == 'usage' for e in self.data()['events']))

    def test_generic_actor_import_cannot_double_count_existing_native_key(self):
        self.observe()
        actor = copy.deepcopy(self.data()['actors'][1]); actor['id'] = 'duplicate-owner'
        actor['usage'] = copy.deepcopy(self.record['dispatch_usage']); actor['usage']['actor_id'] = actor['id']; actor['usage']['source'] = 'other-source'
        with self.assertRaises(ValueError): self.store.import_bundle(dict(schema_version=1, requests=[], actors=[actor], events=[]))
        self.assertEqual(len(self.data()['actors']), 2)

    def test_explicit_zero_with_missing_optional_subcounts_stays_distinct(self):
        u=self.record['dispatch_usage']
        values=dict(input_tokens=0,output_tokens=0,cached_input_tokens=None,reasoning_output_tokens=None,total_tokens=0)
        u['measurements']=values;u['final']['values']=copy.deepcopy(values);u['increments']=[]
        self.write();self.observe()
        saved=next(e for e in self.data()['events'] if e['kind']=='usage')['detail']['usage']['measurements']
        self.assertEqual(saved['total_tokens'],0);self.assertIsNone(saved['cached_input_tokens'])

    def test_duplicate_and_nonfinite_json_fail_without_partial_writes(self):
        for raw in ['{"dispatch_usage":{},"dispatch_usage":{}}','{"dispatch_usage":NaN}']:
            self.input.write_text(raw)
            with self.assertRaises(ValueError):self.observe()
        self.assertEqual(len(self.data()['events']),4)

    def finish_args(self):
        import finish_worker as fw
        from test_native_record import event,context,assigned
        from test_dispatch_usage import count,boundary
        project=self.root/'project';project.mkdir(exist_ok=True)
        source=self.root/'session.jsonl'
        rows=[event('session_meta',dict(id='session_example',cwd=str(project),source='vscode',originator='orca_desktop')),
              boundary('task_started','turn_example'),context(),assigned(dispatch='sample-worker'),count(),boundary('task_complete','turn_example')]
        source.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        return fw,['--project',str(project),'--session-file',str(source),'--session-id','session_example',
                   '--task-id','task_example','--dispatch-id','sample-worker','--request-id','sample-request','--role','backend',
                   '--directory',str(self.root/'completions'),'--trace-db',str(self.path),'--trace-task-title','가상 작업','--auto-cleanup','N']

    def test_finish_cli_imports_and_replays_without_native_reexecution(self):
        from test_lifecycle import snapshot
        fw,args=self.finish_args();response=snapshot();response['result']['dispatch']['id']='sample-worker';response['result']['worker']['dispatchId']='sample-worker'
        with patch.object(fw,'orca',return_value=response) as call:
            self.assertEqual(fw.main(args),0);before=self.data()
            self.assertEqual(fw.main(args),0);self.assertEqual(self.data(),before);self.assertEqual(call.call_count,1)
        self.assertEqual(len([e for e in before['events'] if e['kind']=='usage']),1)

    def test_failed_trace_import_preserves_completion_for_safe_retry(self):
        from test_lifecycle import snapshot
        fw,args=self.finish_args();response=snapshot();response['result']['dispatch']['id']='sample-worker';response['result']['worker']['dispatchId']='sample-worker'
        with patch.object(fw,'orca',return_value=response) as call:
            for failure in [ValueError('simulated_import_failure'),sqlite3.OperationalError('simulated_db_failure')]:
                with patch('trace_usage.import_usage',side_effect=failure):
                    self.assertEqual(fw.main(args),2)
            receipt=self.root/'completions'/'sample-worker.completion.json';self.assertTrue(receipt.exists())
            self.assertEqual(fw.main(args),0);self.assertEqual(call.call_count,1)

    def legacy_actor(self, usage=None):
        actor=copy.deepcopy(self.bundle['actors'][1]);actor.update(id='legacy-worker',role='backend')
        actor['usage']=copy.deepcopy(usage or self.record['dispatch_usage'])
        u=actor['usage'];u['actor_id']=u['dispatch_id']='legacy-worker'
        u['interval']['native_turn_id']='legacy-turn'
        u['measurement_key']='codex.session:session_example:turn:legacy-turn'
        return actor

    def test_legacy_native_overlap_in_both_import_orders_rolls_back(self):
        for shape in ['full','key_only_session','missing_interval']:
            for order in ['legacy_first','native_first']:
                with self.subTest(shape=shape,order=order):
                    db=Store(self.root/(shape+'-'+order)/'trace.sqlite3')
                    try:
                        db.import_bundle(copy.deepcopy(self.bundle));legacy=self.legacy_actor()
                        if shape=='key_only_session':legacy['usage'].pop('session_id')
                        if shape=='missing_interval':legacy['usage'].pop('interval')
                        addition=dict(schema_version=1,requests=[],actors=[legacy],events=[])
                        if order=='legacy_first':db.import_bundle(addition)
                        else:import_usage(db,self.input)
                        before=db.request('sample-request')
                        with self.assertRaises(ValueError):
                            if order=='legacy_first':import_usage(db,self.input)
                            else:db.import_bundle(addition)
                        self.assertEqual(db.request('sample-request'),before)
                    finally:db.close()

    def test_disjoint_legacy_and_native_same_session_allowed_in_both_orders(self):
        for order in ['legacy_first','native_first']:
            with self.subTest(order=order):
                db=Store(self.root/('disjoint-'+order)/'trace.sqlite3')
                try:
                    db.import_bundle(copy.deepcopy(self.bundle));legacy=self.legacy_actor(self.second()['dispatch_usage'])
                    addition=dict(schema_version=1,requests=[],actors=[legacy],events=[])
                    if order=='legacy_first':db.import_bundle(addition);import_usage(db,self.input)
                    else:import_usage(db,self.input);db.import_bundle(addition)
                    data=db.request('sample-request');self.assertEqual(len(data['actors']),3)
                    self.assertEqual(sum(e['detail']['usage']['measurements']['total_tokens'] for e in data['events'] if e['kind']=='usage')+
                                     next(a['usage']['measurements']['total_tokens'] for a in data['actors'] if a['id']=='legacy-worker'),180)
                finally:db.close()

    def test_overlapping_legacy_pair_cannot_enter_via_generic_bundle(self):
        first=self.legacy_actor();second=copy.deepcopy(first);second['id']='legacy-two'
        second['usage'].update(actor_id='legacy-two',dispatch_id='legacy-two',measurement_key='codex.session:session_example:turn:legacy-two')
        second['usage']['interval']['native_turn_id']='legacy-two'
        before=self.data()
        with self.assertRaisesRegex(ValueError,'overlap'):
            self.store.import_bundle(dict(schema_version=1,requests=[],actors=[first,second],events=[]))
        self.assertEqual(self.data(),before)
        ambiguous=self.legacy_actor();ambiguous['usage'].pop('session_id')
        ambiguous['usage']['measurement_key']='codex.session:session:turn:nested:turn:legacy'
        with self.assertRaisesRegex(ValueError,'reconciliation'):
            self.store.import_bundle(dict(schema_version=1,requests=[],actors=[ambiguous],events=[]))
        self.assertEqual(self.data(),before)


if __name__ == '__main__': unittest.main()
