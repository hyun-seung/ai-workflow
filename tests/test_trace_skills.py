import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from trace_store import Store, load_bundle
from trace_skills import validate_event, validate_review, library_snapshot
from trace_viewer import handler_for
import skill_catalog

FIXTURE = Path(__file__).parent / 'fixtures/traces/skill-flow.json'


class SkillTraceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = Store(self.root / 'private/trace.sqlite3')
        self.addCleanup(self.store.close)
        self.bundle = load_bundle(FIXTURE)

    def test_roundtrip_and_immutable_use_without_inventing_old_actor_records(self):
        self.store.import_bundle(self.bundle)
        self.store.import_bundle(self.bundle)
        result = self.store.request('skill-demo')
        self.assertEqual(len([e for e in result['events'] if e['kind'] == 'skill_use']), 3)
        self.assertFalse(any(e['kind'] == 'skill_use' and e['actor_id'] == 'skill-coordinator' for e in result['events']))
        changed = copy.deepcopy(self.bundle)
        changed['events'][-1]['detail']['purpose'] = 'different use'
        with self.assertRaises(ValueError): self.store.import_bundle(changed)
        result['schema_version'] = 1
        self.store.import_bundle(result)

    def test_reject_spoofed_source_reference_execution_and_unsupported_pass(self):
        original = self.bundle['events'][-1]
        for patch in [lambda e: e.update(source='synthetic'),
                      lambda e: e['detail'].update(stage='executed'),
                      lambda e: e['detail']['skill'].update(source_ref='main'),
                      lambda e: e['detail']['skill'].update(source_url='https://user:secret@example.com/skills'),
                      lambda e: e['detail']['result'].update(status='pass'),
                      lambda e: e['detail'].update(analysis='hidden invalid field')]:
            with self.subTest(patch=patch):
                event = copy.deepcopy(original); patch(event)
                with self.assertRaises(ValueError): validate_event(event)

    def test_evidence_must_resolve_in_request_and_failure_rolls_back(self):
        for evidence in ['missing', 'skill-applied', 'skill-demo-instruction']:
            bad = copy.deepcopy(self.bundle)
            bad['events'][3]['detail']['evidence_event_ids'] = [evidence]
            with self.assertRaises(ValueError): self.store.import_bundle(bad)
            self.assertEqual(self.store.requests(), [])
        other = copy.deepcopy(self.bundle)
        other['requests'][0]['id'] = 'other'
        for row in other['actors'] + other['events']: row['request_id'] = 'other'
        self.store.import_bundle(other)
        bad = copy.deepcopy(self.bundle)
        for actor in bad['actors']:
            actor['id'] = 'new-' + actor['id']
            if actor.get('parent_id'): actor['parent_id'] = 'new-' + actor['parent_id']
        bad['events'] = [dict(e, id='new-' + e['id'], source_id='new-' + e['id']) for e in bad['events'] if e['kind'] == 'skill_use']
        for event in bad['events']: event['actor_id'] = 'new-' + event['actor_id']
        # Existing evidence ID belongs to another request; new actors cannot borrow it.
        with self.assertRaisesRegex(ValueError, 'same request'): self.store.import_bundle(bad)
        self.assertIsNone(self.store.request('skill-demo'))

    def managed_root(self):
        root = self.root / 'managed'
        (root / 'skills').mkdir(parents=True)
        spec = {'id':'example-skill', 'repo':'example/skills', 'path':'skills/example-skill', 'ref':'a'*40,
                'roles':['frontend'], 'tags':['web'], 'mode':'reference-only', 'license':'MIT', 'license_paths':[], 'review':'synthetic review'}
        (root / 'skills/catalog.json').write_text(json.dumps({'version':1,'skills':[spec]}))
        path = root / skill_catalog.location(spec, 'pilot'); path.mkdir(parents=True)
        (path / 'SKILL.md').write_text('---\nname: example\ndescription: Synthetic guidance\n---\nExample')
        state = {'version':1,'skills':{'example-skill':{'spec':spec,'status':'pilot','location':str(path.relative_to(root)), 'files':skill_catalog.inventory(path)}},'history':[]}
        (root / 'local/role-skills/registry.json').write_text(json.dumps(state))
        return root, path

    def test_overview_is_read_only_redacts_paths_and_marks_integrity_failures(self):
        root, path = self.managed_root()
        before = {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
        snapshot = library_snapshot(root)
        self.assertEqual(snapshot['skills'][0]['integrity'], 'verified')
        self.assertNotIn(str(root), json.dumps(snapshot))
        self.assertNotIn('location', snapshot['skills'][0])
        self.assertEqual(before, {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()})
        (path / 'SKILL.md').write_text('USER EDIT')
        self.assertEqual(library_snapshot(root)['skills'][0]['integrity'], 'failed')
        self.assertEqual((path / 'SKILL.md').read_text(), 'USER EDIT')

    def test_reviews_do_not_infer_success_from_missing_or_malformed_reports(self):
        root, _ = self.managed_root()
        review = {'schema_version':1,'run_id':'example-run','reviewed_at':'2026-01-01T00:00:00Z','status':'partial',
                  'summary':'Source lookup incomplete','candidates':[{'id':'example-next','decision':'hold','source_ref':None,'reason':'No compatibility evidence'}]}
        validate_review(review)
        run = root / 'local/role-skills/runs/example-run'; run.mkdir(parents=True)
        (run / 'review-summary.json').write_text(json.dumps(review))
        malformed = root / 'local/role-skills/runs/bad'; malformed.mkdir()
        (malformed / 'review-summary.json').write_text('{invalid')
        snapshot = library_snapshot(root)
        self.assertEqual(snapshot['review']['status'], 'partial')
        self.assertEqual(snapshot['invalid_reviews'], 1)
        self.assertIsNone(library_snapshot(None)['review'])
        self.assertEqual(library_snapshot(None)['status'], 'unconnected')

    def test_latest_review_uses_time_across_all_folders_including_failure(self):
        root, _ = self.managed_root()
        for i in range(201):
            key = f'z-old-{i}' if i else 'a-newest'
            run = root / 'local/role-skills/runs' / key; run.mkdir(parents=True)
            review = {'schema_version':1,'run_id':key,'reviewed_at':'2026-01-01T00:00:00Z' if i else '2026-11-15T00:00:00Z',
                      'status':'complete' if i else 'failed','summary':'Synthetic report','candidates':[]}
            (run/'review-summary.json').write_text(json.dumps(review))
        snapshot = library_snapshot(root)
        self.assertEqual(snapshot['review']['run_id'], 'a-newest')
        self.assertEqual(snapshot['review']['status'], 'failed')

    def test_skills_endpoint_keeps_same_origin_and_readonly_boundaries(self):
        self.store.import_bundle(self.bundle)
        server = HTTPServer(('127.0.0.1',0),handler_for(self.store.path))
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection(*server.server_address)
        self.addCleanup(conn.close)
        for method,path,headers,status in [('GET','/api/skills',{},200),('GET','/api/skills?root=/private/other',{},404),
                                           ('GET','/api/skills',{'Origin':'https://example.com'},403),('POST','/api/skills',{},405)]:
            conn.request(method,path,headers=headers);response=conn.getresponse()
            self.assertEqual(response.status,status);response.read()
