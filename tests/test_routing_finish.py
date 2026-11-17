import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dispatch_guard as guard
import finish_worker
import lifecycle_io as storage
import routing_flow as flow
from trace_store import Store, load_bundle
import trace_usage
from test_dispatch_guard import receipt
from test_dispatch_usage import boundary, count
from test_lifecycle import snapshot
from test_native_record import assigned, context, event


class RoutingFinishTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.path = self.root / 'private' / 'choice.json'
        self.environ = {'ORCA_AGENT_SESSION_ID': 'coordinator_example'}
        live = dict(project=str(self.project), session_id='coordinator_example', request_id='sample-request',
                    revision=1, current={'model': 'gpt-6.1-sol', 'effort': 'medium'}, controls={})
        offered = self.call('request', dict(context=live, followup=False, analysis={
            'recommendation': {'role': 'tutor', 'execution': 'worker', 'reason': '가상 학습 준비'}}))
        self.ref = offered['selection']; self.call('choose', dict(self.ref, choice='accept'))
        self.spec = self.root / 'spec.txt'; self.spec.write_text('Read-only fictional assignment.'); self.spec.chmod(0o600)
        self.source = self.root / 'source' / 'session.jsonl'; self.source.parent.mkdir(mode=0o700)
        self.rows = [event('session_meta', dict(id='worker_session', cwd=str(self.project),
                     source='vscode', originator='orca_desktop')), boundary('task_started'),
                     context(model='gpt-6.1-sol', effort='medium'), assigned('task_1', 'ctx_1'),
                     count(), boundary('task_complete')]
        self.write_source()
        self.directory = self.root / 'completed'
        self.db = self.root / 'trace' / 'trace.sqlite3'
        store = Store(self.db)
        try:
            bundle = load_bundle(Path(__file__).parent / 'fixtures' / 'traces' / 'sample.json')
            bundle['actors'][1]['role'] = 'tutor'
            store.import_bundle(bundle)
        finally:
            store.close()

    def call(self, action, payload):
        return flow.handle(action, self.path, self.project, payload, environ=self.environ, cli='example')

    def write_source(self):
        self.source.write_text(''.join(json.dumps(row) + '\n' for row in self.rows)); self.source.chmod(0o600)

    def start(self, collection=True, native_receipt=None):
        payload = dict(selection=self.ref, directory=str(self.root / 'ledger'), operation_id='operation_example',
                       spec_file=str(self.spec), limits=dict(max_active_workers=1, max_dispatches_per_request=1))
        if collection:
            payload['collection'] = {'actor_id': 'sample-worker', 'task_title': '가상 학습 준비'}
        with patch.object(flow, 'startup', return_value={'environment': 'orca', 'connection': {'session_identity_confirmed': True}}), \
                patch.object(guard, 'invoke', return_value=native_receipt or (receipt(), 0)) as invoke:
            result = self.call('start', payload)
        return result, invoke

    def native(self, live=False, effective=True):
        value = snapshot()
        value['result']['dispatch'].update(id='ctx_1', taskId='task_1')
        value['result']['worker']['dispatchId'] = 'ctx_1'
        launch = value['result']['worker']['startOptions']['launch']
        launch['requested'].update(model='gpt-6.1-sol', effort='medium')
        if effective: launch['effective'] = dict(launch['requested'])
        if live: value['result']['observation']['status'] = 'running'
        return value

    def finish(self, *, source=True, trace=False, **changes):
        payload = dict(selection=self.ref, directory=str(self.directory))
        if source: payload['source'] = source if isinstance(source, dict) else {'session_file': str(self.source), 'session_id': 'worker_session'}
        if trace: payload['trace'] = {'db': str(self.db)}
        payload.update(changes)
        with patch.object(finish_worker, 'orca', return_value=self.native()), patch.object(guard, 'orca', return_value=self.native()):
            return self.call('finish', payload)

    def ledger(self):
        return guard.Ledger(self.root / 'ledger', self.project, 'coordinator_example')

    def test_start_collection_is_bound_to_plan_and_cannot_be_changed_on_replay(self):
        result, invoke = self.start()
        self.assertEqual(result['state'], 'bound')
        self.assertEqual(invoke.call_args.args[1]['usage_collection'], dict(role='tutor', actor_id='sample-worker', task_title='가상 학습 준비'))
        with self.assertRaisesRegex(ValueError, 'bound_to_another|binding_conflict'):
            self.start(collection=False)

    def test_collection_invalid_before_preflight_or_launch(self):
        payload = dict(selection=self.ref, directory=str(self.root / 'ledger'), operation_id='operation_example',
                       spec_file=str(self.spec), limits=dict(max_active_workers=1, max_dispatches_per_request=1),
                       collection={'actor_id': 'missing title'})
        with patch.object(flow, 'startup') as startup, patch.object(guard, 'invoke') as invoke, self.assertRaises(ValueError):
            self.call('start', payload)
        startup.assert_not_called(); invoke.assert_not_called()

    def test_finish_real_collector_and_trace_import_are_idempotent_without_rereading_source(self):
        self.start()
        result = self.finish(trace=True)
        self.assertEqual(result['collection_status'], 'collected')
        self.assertEqual(result['profile']['status'], 'pass')
        self.assertEqual(result['usage']['measurements']['total_tokens'], 120)
        self.assertFalse(result['usage']['request_total_measured'])
        self.assertFalse(result['quality_evaluated']); self.assertFalse(result['cleanup_performed'])
        self.assertEqual(result['trace']['status'], 'imported')
        self.assertEqual(result['worker']['state'], 'released')
        self.assertNotIn('receipt', result['worker'])
        record_path = Path(result['completion']['record_path'])
        self.assertEqual(result['comparison_reference'], dict(path=str(record_path), sha256=hashlib.sha256(record_path.read_bytes()).hexdigest()))
        before = record_path.read_bytes(), record_path.stat().st_mtime_ns
        self.source.unlink()
        with patch.object(finish_worker, 'orca') as native_read, patch.object(guard, 'orca') as refresh:
            replay = self.call('finish', dict(selection=self.ref, directory=str(self.directory),
                source={'session_file': str(self.source), 'session_id': 'worker_session'}, trace={'db': str(self.db)}))
        native_read.assert_not_called(); refresh.assert_not_called()
        self.assertTrue(replay['completion']['replay'])
        self.assertEqual(result['trace']['event_id'], replay['trace']['event_id'])
        self.assertEqual((record_path.read_bytes(), record_path.stat().st_mtime_ns), before)
        store = Store(self.db)
        try:
            usages = [e for e in store.request('sample-request')['events'] if e['kind'] == 'usage']
            self.assertEqual(len(usages), 1)
            self.assertEqual(usages[0]['actor_id'], 'sample-worker')
        finally: store.close()

    def test_missing_source_defers_collection_but_releases_confirmed_exit(self):
        self.start()
        with patch.object(finish_worker, 'finish') as collect:
            result = self.finish(source=False)
        collect.assert_not_called()
        self.assertEqual(result['action'], 'collection_deferred')
        self.assertEqual(result['reason'], 'official_session_source_required')
        self.assertEqual(result['worker']['state'], 'released')
        self.assertFalse(self.directory.exists())

    def test_live_worker_cannot_be_collected(self):
        self.start()
        payload = dict(selection=self.ref, directory=str(self.directory),
                       source={'session_file': str(self.source), 'session_id': 'worker_session'})
        with patch.object(finish_worker, 'orca', return_value=self.native(live=True)), self.assertRaisesRegex(ValueError, 'exit_unconfirmed'):
            self.call('finish', payload)
        self.assertFalse((self.directory / 'ctx_1.metadata.json').exists())
        self.assertEqual(self.ledger().read()['operations']['operation_example']['state'], 'bound')

    def test_missing_effective_profile_is_not_filled_from_selection(self):
        self.start()
        with patch.object(finish_worker, 'orca', return_value=self.native(effective=False)), patch.object(guard, 'orca', return_value=self.native()):
            result = self.call('finish', dict(selection=self.ref, directory=str(self.directory),
                source={'session_file': str(self.source), 'session_id': 'worker_session'}))
        self.assertEqual(result['collection_status'], 'partial')
        self.assertEqual(result['profile']['status'], 'unverified')
        record = storage.read_json(result['completion']['record_path'])
        self.assertIsNone(record['launch_effective']['model'])
        self.assertEqual(result['worker']['state'], 'released')

    def test_unmeasured_usage_stays_null_and_does_not_import(self):
        self.start(); self.rows.pop(4); self.write_source()
        with patch.object(trace_usage, 'import_usage') as importer:
            result = self.finish(trace=True)
        importer.assert_not_called()
        self.assertEqual(result['collection_status'], 'partial')
        self.assertIsNone(result['usage']['measurements'])
        self.assertEqual(result['trace']['status'], 'deferred')
        self.assertEqual(result['worker']['state'], 'released')

    def test_trace_failure_preserves_completion_for_replay(self):
        self.start()
        with patch.object(trace_usage, 'import_usage', side_effect=sqlite3.OperationalError('synthetic')):
            with self.assertRaises(sqlite3.OperationalError): self.finish(trace=True)
        self.assertTrue((self.directory / 'ctx_1.completion.json').exists())
        result = self.finish(trace=True)
        self.assertTrue(result['completion']['replay'])
        self.assertEqual(result['trace']['status'], 'imported')

    def test_trace_requires_launch_binding_and_existing_db_before_native_read(self):
        self.start(collection=False)
        payload = dict(selection=self.ref, directory=str(self.directory), source={
            'session_file': str(self.source), 'session_id': 'worker_session'}, trace={'db': str(self.db)})
        with patch.object(finish_worker, 'orca') as native_read, self.assertRaisesRegex(ValueError, 'launch_collection'):
            self.call('finish', payload)
        native_read.assert_not_called()

    def test_missing_trace_db_or_input_alias_rejected_before_collection(self):
        self.start()
        for path in (self.root / 'missing.sqlite3', self.source, self.path):
            payload = dict(selection=self.ref, directory=str(self.directory), source={
                'session_file': str(self.source), 'session_id': 'worker_session'}, trace={'db': str(path)})
            with self.subTest(path=path), patch.object(finish_worker, 'orca') as native_read, self.assertRaises(ValueError):
                self.call('finish', payload)
            native_read.assert_not_called()
        self.assertFalse((self.directory / 'ctx_1.metadata.json').exists())

    def test_foreign_trace_owner_cannot_receive_usage(self):
        self.start()
        other = self.root / 'other' / 'trace.sqlite3'
        store = Store(other); store.close()
        with patch.object(finish_worker, 'orca', return_value=self.native()), patch.object(guard, 'orca', return_value=self.native()), \
                self.assertRaises(ValueError):
            self.call('finish', dict(selection=self.ref, directory=str(self.directory),
                source={'session_file': str(self.source), 'session_id': 'worker_session'}, trace={'db': str(other)}))
        store = Store(other)
        try: self.assertEqual(store.requests(), [])
        finally: store.close()
        self.assertTrue((self.directory / 'ctx_1.completion.json').exists())

    def test_changed_observed_model_is_failure_and_keeps_collected_evidence(self):
        self.start(); self.rows[2]['payload']['model'] = 'different-model'; self.write_source()
        result = self.finish()
        self.assertEqual(result['profile']['status'], 'fail')
        self.assertEqual(result['completion']['verification'], 'fail')
        self.assertEqual(result['collection_status'], 'partial')
        self.assertTrue(Path(result['completion']['record_path']).exists())
        self.assertEqual(result['worker']['state'], 'released')

    def test_wrong_source_identity_cannot_produce_metadata(self):
        self.start()
        with self.assertRaises(ValueError):
            self.finish(source={'session_file': str(self.source), 'session_id': 'other_session'})
        self.assertFalse((self.directory / 'ctx_1.metadata.json').exists())

    def test_finish_allows_expired_selection_but_blocks_changed_operation(self):
        self.start()
        state = storage.read_json(self.path)
        live = {key: state[key] for key in flow.choices.CONTEXT_KEYS}; live['revision'] = 2
        self.call('request', dict(context=live, followup=False, analysis={'recommendation': None}))
        self.assertEqual(self.finish()['collection_status'], 'collected')
        ledger = self.ledger(); data = ledger.read(); data['operations']['operation_example']['plan']['model'] = 'changed'; ledger.write(data)
        with self.assertRaisesRegex(ValueError, 'binding_changed'): self.finish()

    def test_completion_outputs_cannot_overwrite_explicit_source_or_choice_state(self):
        self.start()
        collision = self.directory / 'ctx_1.worker.json'; collision.parent.mkdir(mode=0o700)
        collision.write_bytes(self.source.read_bytes()); collision.chmod(0o600)
        before = collision.read_bytes(), self.path.read_bytes()
        with patch.object(finish_worker, 'orca') as native_read, self.assertRaisesRegex(ValueError, 'collides'):
            self.call('finish', dict(selection=self.ref, directory=str(self.directory),
                source={'session_file': str(collision), 'session_id': 'worker_session'}))
        native_read.assert_not_called()
        self.assertEqual((collision.read_bytes(), self.path.read_bytes()), before)

    def test_existing_spec_or_unowned_artifact_is_never_overwritten(self):
        self.directory.mkdir(mode=0o700)
        self.spec = self.directory / 'ctx_1.worker.json'
        self.spec.write_text('Original fictional assignment.'); self.spec.chmod(0o600)
        self.start()
        for kind in ('worker', 'metadata', 'completion'):
            destination = self.directory / ('ctx_1.' + kind + '.json')
            destination.write_text('Original fictional assignment.'); destination.chmod(0o600)
            before = destination.read_bytes()
            with self.subTest(kind=kind), patch.object(finish_worker, 'orca') as native_read, self.assertRaisesRegex(ValueError, 'unowned_completion'):
                self.finish()
            native_read.assert_not_called()
            self.assertEqual(destination.read_bytes(), before)
            destination.unlink()
        self.assertFalse((self.directory / 'ctx_1.collection.json').exists())

    def test_crash_after_owned_snapshot_can_resume_without_adopting_foreign_files(self):
        self.start()
        with patch.object(finish_worker, 'inspect_session', side_effect=ValueError('synthetic interruption')):
            with self.assertRaisesRegex(ValueError, 'synthetic interruption'): self.finish()
        self.assertTrue((self.directory / 'ctx_1.collection.json').exists())
        self.assertTrue((self.directory / 'ctx_1.worker.json').exists())
        self.assertFalse((self.directory / 'ctx_1.completion.json').exists())
        self.assertEqual(self.finish()['collection_status'], 'collected')

    def test_collection_owner_change_blocks_replay_before_native_read(self):
        self.start(); self.finish()
        marker = self.directory / 'ctx_1.collection.json'
        data = storage.read_json(marker); data['plan_digest'] = '0' * 64; storage.save(marker, data, self.project)
        with patch.object(finish_worker, 'orca') as native_read, self.assertRaisesRegex(ValueError, 'owner_changed'):
            self.finish()
        native_read.assert_not_called()

    def test_trace_sidecar_cannot_alias_explicit_session_source(self):
        self.start()
        source = Path(str(self.db) + '-wal'); source.write_bytes(self.source.read_bytes()); source.chmod(0o600)
        before = source.read_bytes()
        with patch.object(finish_worker, 'orca') as native_read, self.assertRaisesRegex(ValueError, 'collides'):
            self.finish(source={'session_file': str(source), 'session_id': 'worker_session'}, trace=True)
        native_read.assert_not_called()
        self.assertEqual(source.read_bytes(), before)

    def test_finish_prestart_cancellation_never_requests_nonexistent_source(self):
        self.start(native_receipt=({'ok': False, 'error': {'code': 'runtime_access_denied'}}, 1))
        self.call('choose', dict(self.ref, choice='cancel'))
        with patch.object(guard, 'orca') as native_read:
            result = self.finish(source=False)
        native_read.assert_not_called()
        self.assertEqual(result['reason'], 'worker_not_started')

    def test_cli_signals_partial_and_deferred_collection(self):
        cases = [(flow.output('collection_deferred', reason='official_session_source_required'), 1),
                 (flow.output('completion', collection_status='partial'), 1),
                 (flow.output('completion', collection_status='collected'), 0)]
        for result, expected in cases:
            with patch.object(flow, 'handle', return_value=result), patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{}'))), \
                    patch.object(sys, 'stdout', io.StringIO()):
                code = flow.main(['finish', '--project', str(self.project), '--state', str(self.path)])
            self.assertEqual(code, expected)


if __name__ == '__main__': unittest.main()
