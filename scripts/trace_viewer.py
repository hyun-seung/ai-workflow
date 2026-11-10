#!/usr/bin/env python3
"""Local read-only viewer over the exchange-format Store interface."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from trace_store import Store, load_bundle, load_file, orca_bundle, encoded

ASSETS = Path(__file__).with_name('trace_web')


def handler_for(db_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass  # private request/search contents never logged

        def reply(self, status, body, content_type='application/json; charset=utf-8'):
            if not isinstance(body, bytes): body = encoded(body).encode()
            self.send_response(status)
            for key,value in {'Content-Type':content_type, 'Content-Length':str(len(body)),
                'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff',
                'Referrer-Policy':'no-referrer', 'Cross-Origin-Resource-Policy':'same-origin',
                'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"}.items():
                self.send_header(key,value)
            self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            expected = '127.0.0.1:'+str(self.server.server_port)
            if self.headers.get('Host') != expected or self.headers.get('Origin', 'http://'+expected) != 'http://'+expected or self.headers.get('Sec-Fetch-Site') not in (None,'same-origin','none'):
                self.reply(403, {'error':'Local same-origin access only'}); return
            if len(self.path)>2048:
                self.reply(414, {'error':'URI too long'}); return
            path = urlsplit(self.path)
            assets = {'/':('index.html','text/html'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}
            if path.path in assets:
                name,kind = assets[path.path]
                self.reply(200,(ASSETS/name).read_bytes(),kind+'; charset=utf-8'); return
            store = None
            try:
                query = parse_qs(path.query, strict_parsing=True)
                if any(len(v)!=1 for v in query.values()): raise ValueError('Repeated query key')
                store = Store(db_path, readonly=True)
                if path.path == '/api/requests' and not set(query)-{'q','limit'}:
                    result = store.requests(query.get('q',[''])[0], int(query.get('limit',['100'])[0]))
                elif path.path == '/api/request' and set(query)=={'id'}:
                    result = store.request(query['id'][0])
                    if result is None:
                        self.reply(404,{'error':'Request not found'}); return
                else:
                    self.reply(404,{'error':'Not found'}); return
                self.reply(200,result)
            except (ValueError, KeyError): self.reply(400,{'error':'Invalid query'})
            except Exception: self.reply(500,{'error':'기록을 읽을 수 없습니다'})
            finally:
                if store: store.close()

        def do_POST(self): self.reply(405,{'error':'Read only'})
        do_PUT = do_DELETE = do_PATCH = do_POST
    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', required=True)
    sub = p.add_subparsers(dest='command',required=True)
    b = sub.add_parser('import'); b.add_argument('file')
    o = sub.add_parser('import-orca'); o.add_argument('file'); o.add_argument('--request-id',required=True); o.add_argument('--title',required=True); o.add_argument('--role'); o.add_argument('--parent-id')
    l = sub.add_parser('list'); l.add_argument('--q',default=''); l.add_argument('--limit',type=int,default=100)
    e = sub.add_parser('export'); e.add_argument('--request-id',required=True); e.add_argument('--format', choices=('json','jsonl'), default='json')
    s = sub.add_parser('serve'); s.add_argument('--port',type=int,default=8765)
    args = p.parse_args()
    try:
        if args.command == 'serve':
            store = Store(args.db,readonly=True); store.close()
            if not 1<=args.port<=65535: raise ValueError('Invalid port')
            server = HTTPServer(('127.0.0.1',args.port),handler_for(args.db))
            print(f'http://127.0.0.1:{server.server_port}',flush=True)
            try: server.serve_forever()
            except KeyboardInterrupt: pass
            finally: server.server_close()
            return
        store = Store(args.db,readonly=args.command in ('list','export'))
        try:
            if args.command == 'import': result = store.import_bundle(load_bundle(args.file))
            elif args.command == 'import-orca': result = store.import_bundle(orca_bundle(json.loads(load_file(args.file)),args.request_id,args.title,args.role,args.parent_id))
            elif args.command == 'list': result = store.requests(args.q,args.limit)
            else:
                result = store.request(args.request_id)
                if result is None: raise ValueError('Request not found')
                result['schema_version'] = 1
            if args.command == 'export' and args.format == 'jsonl':
                for kind,plural in [('request','requests'),('actor','actors'),('event','events'),('collection','collections')]:
                    for record in result[plural]:
                        print(encoded({'schema_version':1,'type':kind,'record':record}))
            else:
                print(encoded(result))
        finally: store.close()
    except (ValueError, OSError, KeyError, TypeError) as exc: p.exit(2, str(exc)+'\n')

if __name__ == '__main__': main()
