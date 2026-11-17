"""Real collectors, SQLite and filesystem stages; only native/OS probes are mocked."""
import copy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import complete_request as batch
import completion_hook as hook
import finish_worker as fw
import workspace_cleanup as cleanup
import workspace_resources as resources
from lifecycle_io import digest, locked, read_json
import test_completion_hook as hook_fixture
from test_native_record import event, context, assigned
from test_dispatch_usage import boundary, count
from test_lifecycle import snapshot
from trace_store import Store


def hold_lock(filename, project, ready, release):
    with locked(Path(filename), Path(project)):
        ready.set(); release.wait(5)


class IntegratedBatchTests(unittest.TestCase):
    setUp_base = hook_fixture.CompletionHookTests.setUp
    save = hook_fixture.CompletionHookTests.save
    write_source = hook_fixture.CompletionHookTests.write_source
    write_ledger = hook_fixture.CompletionHookTests.write_ledger
    run_hook = hook_fixture.CompletionHookTests.run_hook
    data = hook_fixture.CompletionHookTests.data

    def setUp(self):
        self.setUp_base()
        # The original hook fixture deliberately permits a legacy actor label.
        # This wrapper requires the new explicit canonical role at registration.
        actor=json.loads(self.store.db.execute("SELECT body FROM records WHERE kind='actor' AND id='sample-worker'").fetchone()[0])
        actor['role']='backend'
        with self.store.db:
            self.store.db.execute("UPDATE records SET body=? WHERE kind='actor' AND id='sample-worker'",(json.dumps(actor),))
        self.env=patch.dict(os.environ,{'ORCA_AGENT_SESSION_ID':'coordinator_example'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.allowed=self.root/'scratch';self.allowed.mkdir()
        self.registry=self.private/'resources';self.work=self.allowed/'output'
        self.archive=self.private/'delivery'/'output';self.acceptance=self.private/'accepted.json'
        self.save(self.acceptance,dict(accepted=True))
        resources.create(self.project,self.registry,'output','sample-request','sample-worker',self.work,
                         self.allowed,self.archive,'synthetic integration','User authorized temporary test retirement')
        (self.work/'result.txt').write_text('verified output');(self.work/'empty').mkdir()
        self.coord=self.root/'coordinator.jsonl';self.coord_binding=self.private/'coordinator-binding.json'
        content=[dict(type='input_text',text='Synthetic single work request')]
        self.coord_rows=[event('session_meta',dict(id='coordinator_session',cwd=str(self.project),source='vscode',originator='orca_desktop')),
                         boundary('task_started','coordinator_turn'),context(),event('response_item',dict(type='message',role='user',content=content,
                         internal_chat_message_metadata_passthrough=dict(turn_id='coordinator_turn'))),count(),boundary('task_complete','coordinator_turn')]
        self.write_coord()
        self.save(self.coord_binding,dict(schema_version=1,request_id='sample-request',actor_id='sample-coordinator',task_id='coordinator_task',
                                        role='coordinator',native_turn_id='coordinator_turn',message_line=4,
                                        message_sha256=hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()))
        self.close_path=self.private/'closeout.json'
        self.close=dict(schema_version=1,id='batch-close',request_id='sample-request',coordinator_id='sample-coordinator',time='2026-12-01T00:00:00Z',
                        status='completed',summary='Explicit acceptance of synthetic result',verification='pass',assignments=[dict(actor_id='sample-worker')],
                        artifacts=[dict(kind='result',label='output',path=str(self.archive/'result.txt')),
                                   dict(kind='verification',label='acceptance',path=str(self.acceptance))],limitations=['synthetic only'])
        self.save(self.close_path,self.close)
        self.m=dict(schema_version=1,id='integrated-batch',project=str(self.project),request_id='sample-request',coordinator_id='sample-coordinator',
                    hook_config=str(self.config_path),operations=[dict(operation_id='example',expected=dict(model='example-model',effort='low',reviewer='auto_review'))],
                    coordinator_turns=[dict(session_file=str(self.coord),session_id='coordinator_session',binding_file=str(self.coord_binding),task_title='Coordination')],
                    resources=[dict(directory=str(self.registry),resource_id='output',acceptance=str(self.acceptance),operation_ids=['example'])],
                    closeout_file=str(self.close_path),retry_pending=False)
        self.manifest=self.private/'manifest.json';self.save(self.manifest,self.m);self.output=self.private/'batch'
        self.calls=[]
        for module in (fw,resources,cleanup):
            p=patch.object(module,'orca',self.fake_native);p.start();self.addCleanup(p.stop)
        p=patch.object(cleanup,'no_open_files');p.start();self.addCleanup(p.stop)
        p=patch('subprocess.run',side_effect=AssertionError('real native execution forbidden'));p.start();self.addCleanup(p.stop)

    def fake_native(self,cli,*args):
        self.calls.append(args)
        if args[:2]==('orchestration','worker-show'):return snapshot()
        if args[:2]==('project','setups'):return dict(result=dict(setups=[]))
        if args[:2]==('worktree','ps'):return dict(result=dict(truncated=False,hostScope=dict(omittedHostIds=[]),worktrees=[]))
        raise AssertionError(args)

    def write_coord(self):self.coord.write_text(''.join(json.dumps(r)+'\n' for r in self.coord_rows))
    def run_batch(self,auto_cleanup=None):return batch.run(self.manifest,self.output,'official-example',auto_cleanup=auto_cleanup)
    def usage_rows(self):return [e for e in self.data()['events'] if e['kind']=='usage']

    def test_real_collectors_database_archive_cleanup_and_immutable_replay(self):
        self.assertEqual(self.run_hook()['status'],'collected')
        self.assertEqual(self.run_batch()['status'],'complete')
        self.assertFalse(self.work.exists());self.assertEqual((self.archive/'result.txt').read_text(),'verified output')
        self.assertEqual(len(self.usage_rows()),2)
        self.assertEqual(self.data()['requests'][0]['status'],'completed')
        self.assertTrue(self.data()['closeout']['current'])
        journal=self.output/'sample-request.batch.json';before=journal.read_bytes();bundle=copy.deepcopy(self.data())
        self.assertTrue(self.run_batch()['replay']);self.assertEqual(journal.read_bytes(),before);self.assertEqual(self.data(),bundle)

    def test_n_retains_original_and_creates_no_cleanup_plan(self):
        self.run_hook();self.run_batch('N');self.assertTrue(self.work.exists())
        self.assertFalse((self.registry/'output.cleanup.json').exists());self.assertTrue(self.archive.is_dir())
        self.assertTrue(self.data()['closeout']['current'])

    def test_valid_saved_pending_is_retried_once_after_flush(self):
        terminal=self.rows.pop();self.write_source();self.assertEqual(self.run_hook()['status'],'pending')
        self.rows.append(terminal);self.write_source();self.m['retry_pending']=True;self.save(self.manifest,self.m)
        original=batch.recovery.retry
        with patch.object(batch.recovery,'retry',wraps=original) as retry:
            self.run_batch();self.assertEqual(retry.call_count,1)
        self.assertEqual(len(self.usage_rows()),2)

    def test_missing_event_and_late_arrival_keep_frozen_inputs(self):
        self.config['directory']=str(self.private/'hooks');Path(self.config['directory']).mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError,'receipt_attention'):self.run_batch()
        self.assertTrue(self.work.exists());self.assertFalse(self.archive.exists())
        self.run_hook()
        with self.assertRaisesRegex(ValueError,'input_binding_changed'):self.run_batch()
        self.assertTrue(self.work.exists());self.assertFalse(self.archive.exists())

    def test_unclosed_coordinator_keeps_all_sources(self):
        self.run_hook();self.coord_rows.pop();self.write_coord()
        with self.assertRaisesRegex(ValueError,'turn_unmeasured'):self.run_batch()
        self.assertTrue(self.work.exists());self.assertFalse(self.archive.exists())

    def test_markdown_acceptance_is_frozen_and_supported(self):
        self.run_hook();acceptance=self.private/'accepted.md';acceptance.write_text('Accepted synthetic output.\n');acceptance.chmod(0o600)
        self.m['resources'][0]['acceptance']=str(acceptance);self.save(self.manifest,self.m)
        self.run_batch();self.assertFalse(self.work.exists());self.assertTrue(self.data()['closeout']['current'])

    def test_unknown_coordinator_event_stays_visible_without_guessing_a_worker(self):
        self.run_hook();event_input=dict(session_id='coordinator_session',turn_id='coordinator_turn',cwd=str(self.project),transcript_path=str(self.coord))
        key=digest(dict(session=event_input['session_id'],turn=event_input['turn_id']))
        self.save(Path(self.config['directory'])/(key+'.receipt.json'),dict(version=1,status='pending',binding=dict(config=self.config,event=event_input)))
        self.assertEqual(self.run_batch()['unassociated_receipts'],1)
        diagnostic=batch.recovery.inspect(hook.load_config(self.config_path),'sample-request')
        self.assertEqual(diagnostic['request_status'],'observed_receipts_verified')
        self.assertEqual(diagnostic['coverage_summary']['recorded_coordinator_turns'],1)
        self.assertEqual(len(diagnostic['unassociated_receipts']),1)

    def test_actual_closeout_reference_error_stops_before_any_source_deletion(self):
        self.run_hook();self.close['assignments']*=2;self.save(self.close_path,self.close)
        with self.assertRaises(ValueError):self.run_batch()
        self.assertTrue(self.work.exists());self.assertTrue(self.archive.exists())
        self.assertFalse((self.registry/'output.cleanup.json').exists())
        self.assertEqual(self.data()['requests'][0]['status'],'running')

    def assert_artifact_rejected_before_writes(self, candidate, code='artifact_in_mutable_storage'):
        self.close['artifacts'][0]['path'] = str(candidate); self.save(self.close_path, self.close)
        with self.assertRaisesRegex(ValueError, code): self.run_batch()
        self.assertTrue(self.work.exists())
        self.assertFalse(self.archive.exists())
        self.assertFalse((self.registry/'output.cleanup.json').exists())
        self.assertFalse((self.output/'sample-request.batch.json').exists())
        self.assertEqual(self.data()['requests'][0]['status'], 'running')

    def test_live_database_artifact_is_rejected_before_output_archive_or_cleanup(self):
        self.run_hook(); self.assert_artifact_rejected_before_writes(self.db)
        self.assertFalse(self.output.exists())

    def test_sqlite_sidecars_and_mutable_lifecycle_paths_are_not_artifacts(self):
        self.run_hook()
        candidates = [Path(str(self.db)+suffix) for suffix in ('-wal', '-shm', '-journal')]
        candidates += [Path(self.config['ledger']), Path(self.config['ledger']).with_suffix('.lock'),
                       Path(self.config['directory'])/'future.metadata.json',
                       self.registry/'output.resource.json', self.registry/'output.cleanup.json',
                       self.output/'sample-request.batch.json', self.output/'ctx_example.metadata.json']
        for candidate in candidates:
            with self.subTest(candidate=candidate): self.assert_artifact_rejected_before_writes(candidate)

    def test_hardlink_aliases_outside_storage_are_not_immutable_artifacts(self):
        self.run_hook(); aliases = self.private/'delivery-aliases'; aliases.mkdir(mode=0o700)
        existing_output = self.output/'ctx_example.metadata.json'
        self.output.mkdir(mode=0o700); self.save(existing_output, dict(synthetic='mutable output'))
        for index, source in enumerate((self.db, self.registry/'output.resource.json', existing_output,
                                        next(Path(self.config['directory']).glob('*.receipt.json')))):
            alias = aliases/('alias-'+str(index)); os.link(source, alias)
            try:
                with self.subTest(source=source):
                    self.assert_artifact_rejected_before_writes(alias, 'artifact_shared_inode')
            finally: alias.unlink()

    def test_artifact_inode_alias_created_after_preflight_still_blocks_cleanup(self):
        self.run_hook(); original = batch.close_request
        def alias_after_trial(store, manifest):
            result = original(store, manifest)
            if store.path != self.db:
                os.link(self.acceptance, self.output/'acceptance-alias.md')
            return result
        with patch.object(batch, 'close_request', side_effect=alias_after_trial):
            with self.assertRaisesRegex(ValueError, 'artifact_shared_inode'): self.run_batch()
        self.assertTrue(self.work.exists()); self.assertTrue(self.archive.exists())
        self.assertFalse((self.registry/'output.cleanup.json').exists())
        self.assertEqual(self.data()['requests'][0]['status'], 'running')

    def test_existing_output_artifact_and_stable_sibling_report_are_distinguished(self):
        self.run_hook(); self.output.mkdir(mode=0o700)
        result = self.output/'future-report.md'; result.write_text('will be changed by output lifecycle')
        self.assert_artifact_rejected_before_writes(result)
        stable = self.private/'stable-delivery.md'; stable.write_text('accepted stable delivery')
        self.close['artifacts'][0]['path'] = str(stable); self.save(self.close_path, self.close)
        self.assertEqual(self.run_batch()['status'], 'complete')
        self.assertFalse(self.work.exists()); self.assertTrue(stable.exists())
        self.assertEqual(self.data()['requests'][0]['status'], 'completed')

    def test_source_counter_change_on_replay_keeps_success_receipt_and_database(self):
        self.run_hook();self.run_batch('N');journal=self.output/'sample-request.batch.json';before=journal.read_bytes();data=copy.deepcopy(self.data())
        self.rows[4]=count((101,20,70,5));self.write_source()
        with self.assertRaisesRegex(ValueError,'measurement_changed'):self.run_batch('N')
        self.assertEqual(journal.read_bytes(),before);self.assertEqual(self.data(),data);self.assertTrue(self.work.exists())

    def test_valid_later_turn_appends_do_not_rewrite_saved_measurements(self):
        self.run_hook();self.run_batch('N');before=copy.deepcopy(self.data())
        self.rows += [boundary('task_started','later'),context(),assigned('task_later','ctx_later'),count((200,40,140,10)),boundary('task_complete','later')];self.write_source()
        self.coord_rows += [boundary('task_started','later'),context(),count((200,40,140,10)),boundary('task_complete','later')];self.write_coord()
        self.assertTrue(self.run_batch('N')['replay']);self.assertEqual(self.data(),before)

    def test_two_resource_partial_cleanup_failure_resumes_without_recreating_first(self):
        self.run_hook();second=self.allowed/'second'
        resources.create(self.project,self.registry,'second','sample-request','sample-worker',second,self.allowed,self.private/'delivery'/'second','second fixture','User authorized temporary retirement')
        (second/'data').write_text('second output')
        self.m['resources'].append(dict(directory=str(self.registry),resource_id='second',acceptance=str(self.acceptance),operation_ids=['example']));self.save(self.manifest,self.m)
        original=resources.finalize
        def fail_second(*args,**kwargs):
            if args[2]=='second' and args[-1]=='Y':raise ValueError('synthetic_second_cleanup_failure')
            return original(*args,**kwargs)
        with patch.object(resources,'finalize',side_effect=fail_second):
            with self.assertRaisesRegex(ValueError,'second_cleanup_failure'):self.run_batch()
        self.assertFalse(self.work.exists());self.assertTrue(second.exists());self.assertIsNone(self.data()['closeout'])
        self.run_batch();self.assertFalse(second.exists());self.assertEqual(len(self.usage_rows()),2)

    def test_quarantine_interruption_preserves_files_and_requires_inspection(self):
        self.run_hook()
        with patch.object(cleanup,'remove_manifest',side_effect=OSError('synthetic removal interruption')):
            with self.assertRaises(OSError):self.run_batch()
        state=read_json((self.registry/'output.cleanup.json').with_suffix('.state.json'));quarantine=Path(state['quarantine'])
        self.assertTrue((quarantine/'result.txt').exists());self.assertTrue(self.archive.exists())
        with self.assertRaises((OSError,ValueError)):self.run_batch()
        self.assertTrue((quarantine/'result.txt').exists());self.assertEqual(self.data()['requests'][0]['status'],'running')

    def test_native_coordinator_mismatch_and_output_overlap_precede_writes(self):
        self.run_hook()
        with patch.dict(os.environ,{'ORCA_AGENT_SESSION_ID':'other_coordinator'}):
            with self.assertRaisesRegex(ValueError,'native_coordinator_mismatch'):self.run_batch()
        self.assertFalse(self.output.exists())
        self.output=self.private
        with self.assertRaisesRegex(ValueError,'output_input_overlap'):self.run_batch()
        self.assertTrue(self.work.exists())

    def test_real_process_holds_request_lock(self):
        self.run_hook();self.output.mkdir(mode=0o700);context_=multiprocessing.get_context('fork');ready=context_.Event();release=context_.Event()
        proc=context_.Process(target=hold_lock,args=(str(self.output/'sample-request.batch.lock'),str(self.project),ready,release));proc.start()
        try:
            self.assertTrue(ready.wait(3))
            with self.assertRaisesRegex(ValueError,'already_running'):self.run_batch()
            self.assertTrue(self.work.exists())
        finally:release.set();proc.join(5)
        self.assertEqual(proc.exitcode,0)


if __name__=='__main__':unittest.main()
