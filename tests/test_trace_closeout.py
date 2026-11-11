import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from trace_store import Store, load_bundle
from trace_closeout import close_request


class CloseoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = Store(self.root / 'private' / 'trace.sqlite3')
        self.addCleanup(self.store.close)
        bundle = load_bundle(ROOT / 'tests/fixtures/traces/sample.json')
        for actor in bundle['actors']:
            actor['status'] = 'completed'
        followup = dict(bundle['actors'][1], id='sample-followup', role='verifier')
        bundle['actors'].append(followup)
        bundle['collections'] = [{'request_id':'sample-request', 'source':'synthetic',
                                  'source_id':'page-1','time':None,'completeness':'partial'}]
        self.store.import_bundle(bundle)
        result = self.root / 'result.md'; result.write_text('Synthetic result only')
        verification = self.root / 'verification.json'; verification.write_text('{"status":"pass"}')
        self.manifest = dict(schema_version=1, id='close-1', request_id='sample-request',
                             coordinator_id='sample-coordinator', time='2026-01-02T00:00:00Z',
                             status='completed', summary='Synthetic final result', verification='pass',
                             assignments=[{'actor_id':'sample-worker'},
                                          {'actor_id':'sample-followup','previous_actor_id':'sample-worker'}],
                             artifacts=[{'kind':'result','label':'Result','path':str(result)},
                                        {'kind':'verification','label':'Assessment','path':str(verification)}],
                             limitations=['Partial transcript; no claim of full collection'])

    def test_closeout_preserves_raw_records_and_replays(self):
        before = self.store.request('sample-request')
        first = close_request(self.store, self.manifest)
        self.assertFalse(first['replay'])
        self.assertTrue(close_request(self.store, self.manifest)['replay'])
        after = self.store.request('sample-request')
        self.assertEqual(len(after['events']), len(before['events']) + 1)
        self.assertEqual([e for e in after['events'] if e['kind'] != 'completion'], before['events'])
        self.assertEqual(after['requests'], before['requests'])
        self.assertEqual(after['actors'], before['actors'])
        self.assertTrue(after['closeout']['current'])
        detail = after['closeout']['detail']
        self.assertEqual(detail['recording_status'], 'partial')
        self.assertEqual(detail['process_exit'], 'not_evaluated')
        self.assertIsNone(detail['last_collection_time'])
        self.assertEqual(detail['assignments'][1]['previous_actor_id'], 'sample-worker')
        self.assertEqual(after['actors'][1]['usage']['status'], 'unavailable')

    def test_validation_and_conflicts_leave_database_unchanged(self):
        before = self.store.request('sample-request')
        for change in ({'verification':'unverified'}, {'assignments':[]}, {'coordinator_id':'sample-worker'},
                       {'artifacts':[]}, {'time':None}, {'schema_version':True}, {'request_id':'absent'},
                       {'assignments':[{'actor_id':'sample-worker','previous_actor_id':'sample-followup'},
                                       {'actor_id':'sample-followup','previous_actor_id':'sample-worker'}]}):
            value = dict(self.manifest, **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                close_request(self.store, value)
            self.assertEqual(self.store.request('sample-request'), before)
        close_request(self.store, self.manifest)
        after = self.store.request('sample-request')
        with self.assertRaises(ValueError):
            close_request(self.store, dict(self.manifest, summary='changed same revision'))
        self.assertEqual(self.store.request('sample-request'), after)

    def test_unknown_actor_report_prevents_success_but_allows_followup(self):
        raw = self.store.request('sample-request')
        # This is a deliberately new actor, not a stale update of an ended actor.
        actor = dict(raw['actors'][1], id='new-worker', status='running')
        self.store.import_bundle({'schema_version':1,'requests':[],'actors':[actor],'events':[]})
        value = copy.deepcopy(self.manifest)
        value['assignments'].append({'actor_id':'new-worker'})
        with self.assertRaises(ValueError):close_request(self.store, value)
        value.update(status='needs_followup', verification='unverified')
        close_request(self.store, value)
        self.assertEqual(self.store.request('sample-request')['closeout']['detail']['status'], 'needs_followup')

    def test_changed_artifact_requires_new_review_and_revision(self):
        close_request(self.store, self.manifest)
        Path(self.manifest['artifacts'][0]['path']).write_text('Changed result')
        before = self.store.request('sample-request')
        with self.assertRaises(ValueError):close_request(self.store, self.manifest)
        self.assertEqual(self.store.request('sample-request'), before)
        value = dict(self.manifest, id='close-2', time='2026-01-02T01:00:00Z')
        close_request(self.store, value)
        self.assertEqual(len([e for e in self.store.request('sample-request')['events'] if e['kind']=='completion']), 2)

    def test_new_source_record_marks_closeout_stale_until_explicit_revision(self):
        close_request(self.store, self.manifest)
        old = self.store.request('sample-request')['events'][0]
        new = dict(old, id='later-evidence', detail='New explicit evidence')
        self.store.import_bundle({'schema_version':1,'requests':[],'actors':[],'events':[new]})
        self.assertFalse(self.store.request('sample-request')['closeout']['current'])
        with self.assertRaises(ValueError):close_request(self.store, self.manifest)
        with self.assertRaises(ValueError):close_request(self.store, dict(self.manifest, id='too-old'))
        close_request(self.store, dict(self.manifest, id='close-2', time='2026-01-02T01:00:00Z'))
        self.assertTrue(self.store.request('sample-request')['closeout']['current'])

    def test_cli_jsonl_export_restore_and_forged_reference_rollback(self):
        manifest = self.root / 'close.json';manifest.write_text(json.dumps(self.manifest))
        base = [sys.executable, str(ROOT/'scripts/trace_viewer.py'), '--db', str(self.store.path)]
        r = subprocess.run(base+['close',str(manifest)],capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr)
        exported = subprocess.run(base+['export','--request-id','sample-request','--format','jsonl'],capture_output=True,text=True,timeout=10)
        self.assertEqual(exported.returncode,0,exported.stderr)
        path=self.root/'export.jsonl';path.write_text(exported.stdout)
        restored=Store(self.root/'copy'/'trace.sqlite3')
        try:
            bundle=load_bundle(path);restored.import_bundle(bundle)
            self.assertEqual(restored.request('sample-request'),self.store.request('sample-request'))
            bad=copy.deepcopy(bundle)
            completion=next(e for e in bad['events'] if e['kind']=='completion')
            completion['id']='bad-closeout'
            completion['detail']['assignments'][0]['actor_id']='foreign-worker'
            with self.assertRaises(ValueError):restored.import_bundle(bad)
            self.assertEqual(restored.request('sample-request'),self.store.request('sample-request'))
        finally:restored.close()

    def test_artifact_missing_symlink_hash_mismatch_and_nonfile(self):
        link=self.root/'link';link.symlink_to(self.manifest['artifacts'][0]['path'])
        for patch in ({'path':str(self.root/'missing')},{'path':str(link)},
                      {'sha256':'0'*64},{'path':str(self.root)}):
            value=copy.deepcopy(self.manifest);value['artifacts'][0].update(patch)
            with self.subTest(patch=patch),self.assertRaises((ValueError,OSError)):
                close_request(self.store,value)
            self.assertIsNone(self.store.request('sample-request')['closeout'])

    def test_parent_replacement_before_or_after_directory_open_rejects(self):
        for stage in ('directory', 'file', 'after-read'):
            with self.subTest(stage=stage):
                parent = self.root / stage; parent.mkdir()
                target = parent / 'result.md'; target.write_text('Original result')
                redirected = self.root / (stage + '-redirected'); redirected.mkdir()
                (redirected / target.name).write_text('Different result')
                value = copy.deepcopy(self.manifest)
                value['artifacts'][0]['path'] = str(target)
                before = self.store.request('sample-request')
                real_open, real_fdopen = os.open, os.fdopen
                switched = False

                def replace_parent():
                    nonlocal switched
                    switched = True
                    parent.rename(self.root / (stage + '-saved'))
                    parent.symlink_to(redirected, target_is_directory=True)

                def open_hook(path, *args, **kwargs):
                    trigger = parent.name if stage == 'directory' else target.name
                    if stage != 'after-read' and not switched and str(path) == trigger:
                        replace_parent()
                    return real_open(path, *args, **kwargs)

                class ReadHook:
                    def __init__(self, stream): self.stream = stream
                    def __enter__(self): return self
                    def __exit__(self, *args): return self.stream.__exit__(*args)
                    def fileno(self): return self.stream.fileno()
                    def read(self, size):
                        data = self.stream.read(size)
                        if stage == 'after-read' and data and not switched: replace_parent()
                        return data

                with patch('trace_closeout.os.open', side_effect=open_hook), \
                     patch('trace_closeout.os.fdopen', side_effect=lambda *args: ReadHook(real_fdopen(*args))):
                    with self.assertRaises((ValueError, OSError)):
                        close_request(self.store, value)
                self.assertTrue(switched, 'The race must actually be injected')
                self.assertEqual(self.store.request('sample-request'), before)

    def test_import_rejects_nonroot_coordinator_and_coordinator_overlap(self):
        close_request(self.store, self.manifest)
        before = self.store.request('sample-request')
        original = next(e for e in before['events'] if e['kind'] == 'completion')
        for case in ('nonroot', 'nonroot-overlap', 'root-overlap'):
            with self.subTest(case=case):
                event = copy.deepcopy(original)
                event.update(id='invalid-closeout', time='2026-01-03T00:00:00Z')
                if case.startswith('nonroot'):
                    event['actor_id'] = 'sample-worker'
                    if case == 'nonroot': event['detail']['assignments'] = [{'actor_id':'sample-followup'}]
                else:
                    event['detail']['assignments'].append({'actor_id':'sample-coordinator'})
                new_request = dict(before['requests'][0], id='must-roll-back')
                changed_actor = dict(before['actors'][0], status='failed')
                with self.assertRaises(ValueError):
                    self.store.import_bundle(dict(schema_version=1, requests=[new_request],
                                                  actors=[changed_actor], events=[event]))
                self.assertIsNone(self.store.request('must-roll-back'))
                self.assertEqual(self.store.request('sample-request'), before)

    def test_stale_historical_closeout_import_without_artifact_files(self):
        close_request(self.store, self.manifest)
        original = self.store.request('sample-request')
        later = dict(original['actors'][1], id='later-worker', status='running')
        self.store.import_bundle(dict(schema_version=1, requests=[], actors=[later], events=[]))
        stale = self.store.request('sample-request')
        self.assertFalse(stale['closeout']['current'])
        Path(self.manifest['artifacts'][0]['path']).unlink()
        restored = Store(self.root / 'restored' / 'trace.sqlite3')
        try:
            restored.import_bundle(dict(stale, schema_version=1))
            self.assertEqual(restored.request('sample-request'), stale)
        finally:
            restored.close()


if __name__=='__main__':unittest.main()
