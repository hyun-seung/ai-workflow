"""Synthetic v2 orchestration checks; no native lifecycle calls."""
import copy
from unittest.mock import patch
import test_completion_sources as component
import complete_request as batch
import workspace_resources as resources

class ConsumerTests(component.SourceFixture):
    def setUp(self):
        super().setUp()
        self.f.m['schema_version']=2
        self.f.m['operations'][0]['source']=self.source
        self.f.save(self.f.manifest,self.f.m)
    def test_missing_stop_replay_and_append(self):
        f=self.f
        result=f.run_batch('N'); self.assertEqual(result['explicit_source_operations'],1)
        self.assertEqual(batch.recovery.inspect(self.cfg,'sample-request')['operations'][0]['status'],'missing')
        raw=(f.output/'sample-request.batch.json').read_bytes(); data=copy.deepcopy(f.data())
        f.rows += [component.fixture.boundary('task_started','later'),component.fixture.context(),component.fixture.assigned('task_later','ctx_later'),component.fixture.count((200,40,140,10)),component.fixture.boundary('task_complete','later')]; f.write_source()
        self.assertTrue(f.run_batch('N')['replay']);self.assertEqual(f.data(),data)
        self.assertEqual(raw,(f.output/'sample-request.batch.json').read_bytes())
    def test_cleanup_and_mutation_after_archive(self):
        f=self.f; original=resources.finalize
        def mutate(*args,**kw):
            result=original(*args,**kw)
            if args[-1]=='N': self.record.write_text('{}')
            return result
        with patch.object(resources,'finalize',side_effect=mutate):
            with self.assertRaises(ValueError): f.run_batch('Y')
        self.assertTrue(f.work.exists());self.assertTrue(f.archive.exists())
    def test_y_and_late_stop_conflict(self):
        self.assertEqual(self.f.run_batch('Y')['status'],'complete');self.assertFalse(self.f.work.exists())
    def test_cleanup_overlap_and_invalid_source(self):
        f=self.f
        target=f.work/'metadata.json';target.write_bytes(self.record.read_bytes());target.chmod(0o600)
        self.source['record_file']=str(target);f.save(f.manifest,f.m)
        with self.assertRaises(ValueError):f.run_batch('Y')
        self.assertFalse(f.archive.exists())
    def test_global_bad_receipt(self):
        f=self.f;f.save(component.Path(f.config['directory'])/'bad.receipt.json',{})
        with self.assertRaises(ValueError):f.run_batch('Y')
        self.assertFalse(f.archive.exists())
    def test_v2_stop_and_v1_default(self):
        f=self.f;f.run_hook();f.m['operations'][0]['source']=dict(kind='stop');f.save(f.manifest,f.m)
        result=f.run_batch('N');self.assertEqual(result['stop_source_operations'],1)
        self.assertTrue(result['source_provenance']['example']['receipt_key'])
    def test_late_stop_and_pending_rejected(self):
        f=self.f;f.run_batch('N');raw=(f.output/'sample-request.batch.json').read_bytes()
        f.run_hook()
        with self.assertRaises(ValueError):f.run_batch('N')
        self.assertEqual(raw,(f.output/'sample-request.batch.json').read_bytes())
    def test_matching_received_stop(self):
        self.f.run_hook();result=self.f.run_batch('N')
        self.assertEqual(result['explicit_source_operations'],1)
        self.assertEqual(len(self.f.usage_rows()),2)
    def test_database_restore(self):
        f=self.f;before=copy.deepcopy(f.data());f.run_batch('N');after=copy.deepcopy(f.data())
        raw=(f.output/'sample-request.batch.json').read_bytes();f.store.close()
        for suffix in ('','-wal','-shm'):component.Path(str(f.db)+suffix).unlink(missing_ok=True)
        f.store=component.fixture.Store(f.db);f.addCleanup(f.store.close)
        f.store.import_bundle(dict(schema_version=1,requests=before['requests'],actors=before['actors'],events=before['events'],collections=before['collections']))
        f.run_batch('N')
        self.assertEqual({e['id'] for e in f.data()['events']},{e['id'] for e in after['events']})
        self.assertEqual(raw,(f.output/'sample-request.batch.json').read_bytes())
    def test_legacy_manifest_has_no_new_result_or_event(self):
        f=self.f;f.m['schema_version']=1;f.m['operations'][0].pop('source');f.save(f.manifest,f.m);f.run_hook()
        result=f.run_batch('N');self.assertNotIn('source_provenance',result)
        self.assertFalse(any(e.get('source')=='trace.explicit-completion-sources' for e in f.data()['events']))
    def test_received_pending_blocks_explicit_without_retry(self):
        f=self.f;f.run_hook()
        receipt=next(component.Path(f.config['directory']).glob('*.receipt.json'))
        value=component.fixture.read_json(receipt);value['status']='pending';f.save(receipt,value)
        with self.assertRaises(ValueError):f.run_batch('N')
        self.assertFalse(f.archive.exists())
    def test_invalid_selection_precedes_output(self):
        for source in ({},dict(kind='guess'),dict(kind='stop',record_file=str(self.record))):
            self.f.m['operations'][0]['source']=source;self.f.save(self.f.manifest,self.f.m)
            with self.assertRaises(ValueError):self.f.run_batch('N')
            self.assertFalse(self.f.output.exists())
