"""HTTP client faults and source failures have distinct, private error contracts."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import HTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from trace_store import Store, load_bundle
from trace_viewer import handler_for


class TraceApiContractTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.path = self.root / 'private/trace.sqlite3'
        store = Store(self.path)
        store.import_bundle(load_bundle(Path(__file__).parent / 'fixtures/traces/sample.json'))
        store.close()

    def serve(self, db=None, skills_root=None):
        server = HTTPServer(('127.0.0.1', 0), handler_for(db or self.path, skills_root))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def cleanup():
            server.shutdown(); thread.join(); server.server_close()
        self.addCleanup(cleanup)
        def call(path, method='GET', headers=None):
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                connection.request(method, path, headers=headers or {})
                response = connection.getresponse()
                body = response.read()
                return response.status, dict(response.getheaders()), json.loads(body)
            finally: connection.close()
        return call

    def test_valid_query_with_missing_database_is_private_server_error(self):
        call = self.serve(self.root / 'private/missing.sqlite3')
        for path in ['/api/requests', '/api/request?id=sample-request']:
            with self.subTest(path=path):
                status, headers, body = call(path)
                self.assertEqual(status, 500)
                self.assertEqual(body, {'error':'기록을 읽을 수 없습니다'})
                self.assertNotIn(str(self.root), json.dumps(body))
                self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_valid_skill_query_with_broken_catalog_is_server_error(self):
        root = self.root / 'managed'; (root / 'skills').mkdir(parents=True)
        (root / 'skills/catalog.json').write_text('{"SECRET_INTERNAL_CONFIG":')
        call = self.serve(skills_root=root)
        status, _, body = call('/api/skills')
        self.assertEqual(status, 500)
        self.assertEqual(body, {'error':'기록을 읽을 수 없습니다'})
        self.assertNotIn('SECRET_INTERNAL_CONFIG', json.dumps(body))
        with patch('trace_viewer.library_snapshot', side_effect=KeyError('private/config/path')):
            self.assertEqual(call('/api/skills')[0], 500)

    def test_invalid_query_is_400_without_reading_sources(self):
        call = self.serve()
        paths = ['/api/requests?limit=nan', '/api/requests?limit=0', '/api/requests?limit=201',
                 '/api/requests?limit=', '/api/request?id=',
                 '/api/requests?q=' + 'x' * 201, '/api/request?id=bad!id',
                 '/api/requests?limit=1&limit=2', '/api/requests?limit=&limit=1',
                 '/api/request?id=sample-request&id=', '/api/requests?broken']
        with patch('trace_viewer.Store', side_effect=AssertionError('must reject before source access')):
            for path in paths:
                with self.subTest(path=path):
                    status, _, body = call(path)
                    self.assertEqual(status, 400)
                    self.assertEqual(body, {'error':'Invalid query'})

    def test_unknown_route_or_unsupported_query_is_404_without_sources(self):
        call = self.serve()
        with patch('trace_viewer.Store', side_effect=AssertionError('unrelated source must not matter')):
            for path in ['/unknown', '/api/requests?unknown=1', '/api/skills?root=/private/other',
                         '/api/skills?root=', '/api/requests?unknown=']:
                with self.subTest(path=path): self.assertEqual(call(path)[0], 404)

    def test_success_and_existing_readonly_security_contract_stay_compatible(self):
        call = self.serve()
        status, headers, body = call('/api/request?id=sample-request')
        self.assertEqual(status, 200); self.assertEqual(len(body['events']), 4)
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertEqual(call('/api/request?id=missing')[0], 404)
        self.assertEqual(call('/api/requests?q=')[0], 200)
        self.assertEqual(call('/api/skills')[2]['status'], 'unconnected')
        self.assertEqual(call('/api/requests', method='POST')[0], 405)
        self.assertEqual(call('/api/requests', headers={'Origin':'https://example.com'})[0], 403)
        self.assertEqual(call('/api/requests', headers={'Sec-Fetch-Site':'cross-site'})[0], 403)
