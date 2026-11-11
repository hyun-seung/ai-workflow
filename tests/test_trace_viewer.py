import copy
import http.client
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from trace_store import Store, orca_bundle, load_bundle, MAX_BYTES, load_file
from trace_viewer import handler_for

FIXTURES=Path(__file__).parent/'fixtures'/'traces'

class TraceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name).resolve(); self.path=self.root/'private'/'trace.sqlite3'
        self.s=Store(self.path); self.bundle=load_bundle(FIXTURES/'sample.json')
    def tearDown(self):
        self.s.close(); self.tmp.cleanup()
    def test_persistence_idempotency_export(self):
        self.s.import_bundle(self.bundle); self.s.import_bundle(self.bundle)
        data=self.s.request('sample-request'); self.assertEqual(len(data['events']),4)
        self.assertEqual(data['actors'][1]['parent_id'],'sample-coordinator')
        self.assertIsNone(data['actors'][1]['observed']['model'])
        self.assertEqual(data['actors'][1]['usage']['status'],'unavailable')
        self.s.close(); self.s=Store(self.path)
        self.assertEqual(self.s.request('sample-request'),data)
        data['schema_version']=1; self.s.import_bundle(data)
        self.assertEqual(len(self.s.requests()),1)
    def test_transaction_and_conflicts(self):
        self.s.import_bundle(self.bundle)
        bad=copy.deepcopy(self.bundle);bad['requests'].append(dict(bad['requests'][0],id='other'));bad['events'][0]['detail']='different'
        with self.assertRaises(ValueError):self.s.import_bundle(bad)
        self.assertIsNone(self.s.request('other'))
        bad=copy.deepcopy(self.bundle);bad['events'][0]['actor_id']='absent'
        with self.assertRaises(ValueError):self.s.import_bundle(bad)
        self.assertEqual(len(self.s.request('sample-request')['events']),4)
    def test_orca_partial_state_and_internal_exclusion(self):
        snap=json.loads((FIXTURES/'orca-snapshot.json').read_text())
        bundle=orca_bundle(snap,'native-sample','가상 snapshot',role='developer')
        self.s.import_bundle(bundle)
        data=self.s.request('native-sample');self.assertEqual(data['collections'][0]['completeness'],'partial')
        self.assertEqual(len(data['events']),4)
        self.assertEqual(data['actors'][0]['usage']['status'],'unavailable')
        self.assertNotIn('SECRET_REASONING',json.dumps(data))
        self.assertTrue(any(e['kind']=='approval_request' for e in data['events']))
        self.assertFalse(any(e['kind']=='approval_resolution' for e in data['events']))
        snap['result']['status']['worker']='completed';snap['result']['transcript']['messages'][1]['blocks'][0]['state']='resolved'
        self.s.import_bundle(orca_bundle(snap,'native-sample','가상 snapshot',role='developer'))
        self.assertEqual(self.s.request('native-sample')['actors'][0]['status'],'completed')
        self.s.import_bundle(bundle) # stale running snapshot must not regress completion
        self.assertEqual(self.s.request('native-sample')['actors'][0]['status'],'completed')
    def test_permissions_git_symlinks_and_usage(self):
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode),0o700)
        (self.root/'git').mkdir();(self.root/'git'/'.git').mkdir()
        with self.assertRaises(ValueError):Store(self.root/'git'/'db')
        (self.root/'link').symlink_to(self.path.parent,target_is_directory=True)
        with self.assertRaises(ValueError):Store(self.root/'link'/'db')
        os.chmod(self.path,0o644)
        with self.assertRaises(ValueError):Store(self.path)
        os.chmod(self.path,0o600)
        self.bundle['actors'][0]['usage']={'source':'native','scope':'turn','measurements':{'input_tokens':5}}
        self.s.import_bundle(self.bundle)
        self.assertEqual(self.s.request('sample-request')['actors'][0]['usage']['measurements']['input_tokens'],5)
    def test_jsonl_and_size(self):
        p=self.root/'input.jsonl'
        p.write_text('\n'.join(json.dumps({'schema_version':1,'type':kind,'record':r}) for kind,plural in [('request','requests'),('actor','actors'),('event','events')] for r in self.bundle[plural]))
        self.s.import_bundle(load_bundle(p));self.assertEqual(len(self.s.requests("' OR 1=1 --")),0)
        with self.assertRaises(ValueError):self.s.requests('',201)
        p.write_bytes(b'x'*(MAX_BYTES+1))
        with self.assertRaises(ValueError):load_file(p)
    def test_multiple_dispatches_with_coordinator_and_jsonl_cli(self):
        viewer=Path(__file__).resolve().parents[1]/'scripts'/'trace_viewer.py'
        def cli(*args, expected=0):
            result=subprocess.run([sys.executable,str(viewer),'--db',str(self.path),*args],
                                  capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,expected,result.stderr)
            return result.stdout
        def write(name,value):
            path=self.root/name;path.write_text(json.dumps(value));return str(path)
        snapshot=json.loads((FIXTURES/'orca-snapshot.json').read_text())
        rid='shared-request';title='가상 다중 담당 요청'
        metadata={'id':rid,'title':title,'status':'unknown','source':'trace.explicit-request',
                  'source_id':rid,'time':None,'completeness':'unknown'}
        base={'source':'synthetic.coordinator','source_id':'explicit-coordinator',
              'time':'2026-01-01T00:00:00Z','completeness':'complete'}
        coordinator=dict(base,id='parent-coordinator',request_id=rid,role='coordinator',environment='orca')
        initial={'schema_version':1,'requests':[metadata],'actors':[coordinator],'events':[]}
        first=write('coordinator.json',initial);cli('import',first)
        for dispatch,role in [('worker-a','developer'),('worker-b','verifier')]:
            snapshot['result']['dispatchId']=dispatch
            snapshot['result']['sourceIdentity']='synthetic-'+dispatch
            snapshot_path=write(dispatch+'.json',snapshot)
            for _ in range(2):
                cli('import-orca',snapshot_path,'--request-id',rid,'--title',title,
                    '--role',role,'--parent-id','parent-coordinator')
        events=[dict(base,id='coordinator-'+kind,request_id=rid,actor_id='parent-coordinator',
                     kind=kind,detail={'text':'가상 '+kind,'basis':rid})
                for kind in ('decision','assignment','instruction')]
        extra=write('coordinator-events.json',dict(initial,requests=[],actors=[],events=events))
        cli('import',extra);cli('import',extra)
        rows=json.loads(cli('list'))
        self.assertEqual([r['id'] for r in rows],[rid])
        data=self.s.request(rid)
        self.assertEqual(data['requests'],[metadata])
        self.assertEqual(len(data['actors']),3);self.assertEqual(len(data['events']),11)
        self.assertEqual(len(data['collections']),2)
        for aid in ('worker-a','worker-b'):
            actor=next(a for a in data['actors'] if a['id']==aid)
            self.assertEqual(actor['parent_id'],'parent-coordinator')
            self.assertEqual(actor['source_id'],aid)
            self.assertEqual(actor['source'],'orca.worker-read')
            self.assertEqual(len([e for e in data['events'] if e['actor_id']==aid]),4)
        self.assertEqual({c['source_id'] for c in data['collections']},{'worker-a','worker-b'})
        export=cli('export','--request-id',rid,'--format','jsonl')
        exported=self.root/'export.jsonl';exported.write_text(export)
        self.assertEqual(len(export.splitlines()),17)
        cli('import',str(exported))
        self.assertEqual(self.s.request(rid),data)
        # Explicit metadata disagreement must fail without inserting a third actor.
        snapshot['result']['dispatchId']='worker-c'
        bad=write('conflict.json',snapshot)
        output=cli('import-orca',bad,'--request-id',rid,'--title','different title',expected=2)
        self.assertEqual(output,'');self.assertEqual(self.s.request(rid),data)
        other=self.root/'roundtrip'/'copy.sqlite3'
        result=subprocess.run([sys.executable,str(viewer),'--db',str(other),'import',str(exported)],
                              capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        copy_store=Store(other,readonly=True)
        try:self.assertEqual(copy_store.request(rid),data)
        finally:copy_store.close()
    def test_empty_usage_measurements_are_unavailable(self):
        self.bundle['actors'][0]['usage']={'source':'native','scope':'single_turn','measurements':{}}
        self.s.import_bundle(self.bundle)
        usage=self.s.request('sample-request')['actors'][0]['usage']
        self.assertEqual(usage['status'],'unavailable')
        self.assertNotIn('total_tokens',usage)
    def test_additive_role_labels_keep_source_actor_and_validate_references(self):
        self.s.import_bundle(self.bundle)
        before=self.s.request('sample-request')['actors']
        event=dict(self.bundle['events'][0],id='role-labels',kind='assignment',detail={
            'actor_roles':[{'actor_id':'sample-worker','role_id':'backend','task_title':'가상 작업'}]})
        bundle=dict(schema_version=1,requests=[],actors=[],events=[event])
        self.s.import_bundle(bundle);self.s.import_bundle(bundle)
        self.assertEqual(self.s.request('sample-request')['actors'],before)
        for patch in [dict(actor_id='absent'),dict(role_id='arbitrary'),dict(task_title=' ')]:
            bad=copy.deepcopy(bundle);bad['events'][0]['id']='bad-label'
            bad['events'][0]['detail']['actor_roles'][0].update(patch)
            with self.assertRaises(ValueError):self.s.import_bundle(bad)
        self.assertEqual(len(self.s.request('sample-request')['events']),5)
    def test_newest_requests_before_limit_unknowns_use_registration_order(self):
        base = self.bundle['requests'][0]
        rows = [dict(base, id='z-older', time='2026-01-01T00:00:00Z'),
                dict(base, id='a-newer', time='2026-01-02T00:00:00Z'),
                dict(base, id='unknown-first', time=None), dict(base, id='unknown-last', time=None)]
        self.s.import_bundle(dict(schema_version=1, requests=rows, actors=[], events=[]))
        self.assertEqual([r['id'] for r in self.s.requests('', 2)], ['a-newer','z-older'])
        self.assertEqual([r['id'] for r in self.s.requests()][2:], ['unknown-last','unknown-first'])
        # Re-importing identical records must not make an old request newer.
        self.s.import_bundle(dict(schema_version=1, requests=[rows[2]], actors=[], events=[]))
        self.assertEqual([r['id'] for r in self.s.requests()][2:], ['unknown-last','unknown-first'])
        self.s.import_bundle(dict(schema_version=1, requests=[dict(rows[2],status='completed')], actors=[], events=[]))
        self.assertEqual([r['id'] for r in self.s.requests()][2:], ['unknown-last','unknown-first'])
    def test_http(self):
        self.s.import_bundle(self.bundle)
        server=HTTPServer(('127.0.0.1',0),handler_for(self.path));thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            def call(path,headers=None,method='GET'):
                c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request(method,path,headers=headers or {});r=c.getresponse();body=r.read();c.close();return r.status,dict(r.getheaders()),body
            status,headers,body=call('/api/request?id=sample-request');self.assertEqual(status,200);self.assertEqual(len(json.loads(body)['events']),4)
            self.assertIn("default-src 'none'",headers['Content-Security-Policy'])
            self.assertEqual(call('/api/requests',{'Host':'evil.example'})[0],403)
            self.assertEqual(call('/api/requests',{'Origin':'https://evil.example'})[0],403)
            self.assertEqual(call('/api/requests',{'Sec-Fetch-Site':'cross-site'})[0],403)
            self.assertEqual(call('/../../etc/passwd')[0],404)
            self.assertEqual(call('/api/requests?limit=999')[0],400)
            self.assertEqual(call('/api/request?id=missing')[0],404)
            self.assertEqual(call('/api/requests',method='POST')[0],405)
            self.assertEqual(call('/')[0],200)
            self.assertEqual(call('/app.js')[0],200)
        finally:server.shutdown();thread.join();server.server_close()

if __name__=='__main__':unittest.main()
