"""Protocol fakes are not native binary evidence; see separate checker-trial."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import document_checks as d


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'docs').mkdir()
        (self.root / 'docs/a.md').write_text('[ok](b.md)\n')
        (self.root / 'docs/b.md').write_text('하늘단추\n')
        self.config = self.root / 'checks.json'
        template = Path(__file__).resolve().parents[1] / 'templates/document-checks.json'
        self.cfg = json.loads(template.read_text())
        self.cfg.update(project=str(self.root), files=['docs/a.md', 'docs/b.md'], targets=['docs/b.md', 'docs/missing.md'])

    def run_checks(self):
        self.config.write_text(json.dumps(self.cfg))
        return d.run(str(self.root), str(self.config), timeout=2)

    def fake(self, name='lychee', body=None):
        version = d.VERSIONS[name]
        path = self.root / ('fake-' + name)
        code = body or ("print(json.dumps({'total':1,'successful':1,'errors':0,'unknown':0,'unsupported':0,'timeouts':0,'excludes':0,'cached':0,'error_map':{}}))" if name == 'lychee' else "print('{}')")
        script = '#!' + sys.executable + '\nimport sys,json,os,time\nif sys.argv[1:]==["--version"]:\n print(' + repr(version) + ')\nelse:\n ' + code.replace('\n','\n ') + '\n'
        path.write_text(script)
        path.chmod(0o700)
        self.cfg['tools'][name].update(enabled=True, executable=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        return path

    def test_disabled_never_executes(self):
        with patch.object(d, 'process', side_effect=AssertionError):
            self.assertEqual({v['status'] for v in self.run_checks()['tools'].values()}, {'disabled'})

    def test_missing_is_unavailable(self):
        self.fake().unlink()
        self.assertEqual(self.run_checks()['tools']['lychee']['status'], 'unavailable')

    def test_identity_mismatch(self):
        self.fake().write_text('changed')
        self.assertEqual(self.run_checks()['tools']['lychee']['status'], 'error')

    def test_real_subprocess_protocol(self):
        self.fake()
        self.assertEqual(self.run_checks()['tools']['lychee']['status'], 'pass')

    def test_vale_zero_with_alert(self):
        self.fake('vale', "print(json.dumps({'input0.md':[{'Check':'Project.Term0','Line':1,'Severity':'warning'}]}))")
        result = self.run_checks()['tools']['vale']
        self.assertEqual(result['status'], 'findings')
        self.assertEqual(result['details']['alerts'][0]['file'], 'docs/a.md')

    def test_timeout(self):
        for body in ['time.sleep(5)', "__import__('subprocess').Popen([sys.executable,'-c','import time;time.sleep(5)']);sys.exit(0)"]:
            with self.subTest(body=body):
                self.fake(body=body)
                self.assertIn('process_timeout', self.run_checks()['tools']['lychee']['reason'])

    def test_output_bound(self):
        self.fake(body="print('x'*3000000)")
        self.assertIn('process_output_limit', self.run_checks()['tools']['lychee']['reason'])

    def test_malformed_and_unknown_exit(self):
        for body in ["print('bad')", "print('{}')", "print('{}');sys.exit(42)"]:
            with self.subTest(body=body):
                self.fake(body=body)
                self.assertEqual(self.run_checks()['tools']['lychee']['status'], 'error')

    def test_ambient_config_env_and_argv(self):
        (self.root / 'lychee.toml').write_text('preprocess = "touch PWNED"')
        self.fake(body="assert 'GITHUB_TOKEN' not in os.environ; assert os.environ['PATH']==''; assert '--offline' in sys.argv; assert '--preprocess' not in sys.argv; assert 'preprocess' not in open(sys.argv[sys.argv.index('--config')+1]).read(); " + "print(json.dumps({'total':1,'successful':1,'errors':0,'unknown':0,'unsupported':0,'timeouts':0,'excludes':0,'cached':0,'error_map':{}}))")
        with patch.dict(os.environ, {'GITHUB_TOKEN':'fictional-secret','LYCHEE_PREPROCESS':'touch PWNED','VALE_CONFIG_PATH':'evil'}):
            self.assertEqual(self.run_checks()['tools']['lychee']['status'], 'pass')
        self.assertFalse((self.root / 'PWNED').exists())

    def test_vale_generated_rules_only(self):
        self.fake('vale', "assert '--no-global' in sys.argv; assert 'Packages' not in open('.vale.ini').read(); assert os.path.exists('styles/Project/Term0.yml'); print('{}')")
        self.assertEqual(self.run_checks()['tools']['vale']['status'], 'pass')

    def test_bad_settings(self):
        for field, val in [('files',['docs/../a.md']), ('roots',['.']), ('files',['/etc/passwd']), ('files',['docs']), ('targets',['../secret'])]:
            with self.subTest(val=val):
                original=copy.deepcopy(self.cfg)
                self.cfg[field]=val
                with self.assertRaises(ValueError):self.run_checks()
                self.cfg=original
        self.cfg['tools']['vale']['extra_flags']='sync'
        with self.assertRaises(ValueError):self.run_checks()

    def test_duplicate_json_keys(self):
        with self.assertRaises(ValueError):d.load_json(b'{"version":1,"version":2}')
        with self.assertRaises(ValueError):d.load_json(b' '*(d.MAX_BYTES+1))

    def test_symlinks_all_ancestors(self):
        alias=self.root/'alias';alias.symlink_to(self.root/'docs',target_is_directory=True)
        with self.assertRaises(ValueError):d.canonical(alias/'a.md')
        (self.root/'docs/a.md').unlink();(self.root/'docs/a.md').symlink_to(self.root/'docs/b.md')
        with self.assertRaises(ValueError):self.run_checks()

    def test_binary_symlink(self):
        binary=self.fake();alias=self.root/'alias';alias.symlink_to(binary)
        self.cfg['tools']['lychee']['executable']=str(alias)
        with self.assertRaises(ValueError):self.run_checks()

    def test_only_remote_not_run(self):
        self.fake();(self.root/'docs/a.md').write_text('[remote](https://example.invalid)')
        r=self.run_checks()['tools']['lychee']
        self.assertEqual(r['status'],'not_run');self.assertTrue(r['skipped'])

    def test_unsafe_links_never_staged(self):
        self.fake();(self.root/'docs/a.md').write_text('[x](../escape) [y](/etc/passwd) [z](file:///etc/passwd) [q](b.md#anchor) [u](unlisted.md)')
        r=self.run_checks()['tools']['lychee']
        self.assertEqual(r['status'],'not_run');self.assertGreaterEqual(len(r['skipped']),4)

    def test_changed_midcheck(self):
        self.fake()
        orig=d.process
        def change(*args,**kw):
            r=orig(*args,**kw)
            (self.root/'docs/b.md').write_text('changed')
            return r
        with patch.object(d,'process',side_effect=change):
            self.assertEqual(self.run_checks()['tools']['lychee']['reason'],'inputs_changed')

    def test_rules_required_and_literal_only(self):
        self.fake('vale')
        for terms in [[],[{'disallowed':'.*','preferred':'하늘단추'}], [{'disallowed':'구름단추','preferred':'구름단추'}]]:
            self.cfg['tools']['vale']['terms']=terms
            with self.assertRaises(ValueError):self.run_checks()

    def test_cli_disabled_and_bad_config(self):
        self.run_checks()
        argv=[sys.executable,str(Path(d.__file__)), '--project',str(self.root),'--config',str(self.config)]
        p=subprocess.run(argv,capture_output=True,text=True)
        self.assertEqual(p.returncode,0)
        self.config.write_text('{}')
        p=subprocess.run(argv,capture_output=True,text=True)
        self.assertEqual(p.returncode,2)
        self.assertEqual(json.loads(p.stdout)['tools']['lychee']['status'],'not_run')

    def test_vale_malformed_and_exit_status(self):
        for body in ["print('[]')", "print('{}');sys.exit(2)", "print(json.dumps({'input0.md':[{'Check':'Project.Term999','Line':1,'Severity':'warning'}]}))"]:
            with self.subTest(body=body):
                self.fake('vale', body)
                self.assertEqual(self.run_checks()['tools']['vale']['status'], 'error')

    def malformed_vale(self, check, sibling):
        payload = {'input0.md': [{'Check': check, 'Line': 1, 'Severity': 'warning'}]}
        self.fake('vale', 'print(' + repr(json.dumps(payload)) + ')')
        self.cfg['tools']['lychee']['enabled'] = False
        if sibling != 'disabled':
            errors = 1 if sibling == 'findings' else 0
            payload = {'total': 1, 'successful': 1 - errors, 'errors': errors,
                       'unknown': 0, 'unsupported': 0, 'timeouts': 0,
                       'excludes': 0, 'cached': 0,
                       'error_map': {'links.md': [{'url': 'file:///missing', 'status': {}}]} if errors else {}}
            self.fake('lychee', 'print(' + repr(json.dumps(payload)) + ');sys.exit(' + str(2 if errors else 0) + ')')

    def assert_vale_protocol_report(self, report, sibling):
        self.assertEqual(report['version'], 1)
        self.assertNotIn('error', report)
        self.assertEqual(report['config_sha256'], d.digest(self.config.read_bytes()))
        self.assertEqual(report['inputs'], {name: d.digest((self.root / name).read_bytes())
                                           for name in self.cfg['files']})
        self.assertEqual(report['targets'], {'docs/b.md': d.digest((self.root / 'docs/b.md').read_bytes()),
                                            'docs/missing.md': None})
        self.assertEqual(report['tools']['vale']['status'], 'error')
        self.assertEqual(report['tools']['vale']['reason'], 'ValueError:malformed_vale_alert')
        self.assertEqual(report['tools']['lychee']['status'], sibling)
        if sibling != 'disabled':
            self.assertEqual(report['tools']['lychee']['details'],
                             {'errors': 1 if sibling == 'findings' else 0, 'checked': 1})

    def test_vale_check_type_normalizer(self):
        for check in (None, 7, [], {}, True):
            with self.subTest(check=check):
                payload = {'input0.md': [{'Check': check, 'Line': 1, 'Severity': 'warning'}]}
                with self.assertRaisesRegex(ValueError, '^malformed_vale_alert$'):
                    d.normalize_vale(0, json.dumps(payload).encode(), ['input0.md'], 1)

    def test_vale_check_type_api_preserves_report(self):
        for check in (None, 7, [], {}, True):
            for sibling in ('disabled', 'pass', 'findings'):
                with self.subTest(check=check, sibling=sibling):
                    self.malformed_vale(check, sibling)
                    self.assert_vale_protocol_report(self.run_checks(), sibling)

    def test_vale_check_type_cli_preserves_report(self):
        for check in (None, 7, [], {}, True):
            for sibling in ('disabled', 'pass', 'findings'):
                with self.subTest(check=check, sibling=sibling):
                    self.malformed_vale(check, sibling)
                    self.config.write_text(json.dumps(self.cfg))
                    argv = [sys.executable, str(Path(d.__file__)), '--project', str(self.root),
                            '--config', str(self.config)]
                    result = subprocess.run(argv, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2)
                    self.assert_vale_protocol_report(json.loads(result.stdout), sibling)

    def test_version_mismatch(self):
        self.fake()
        self.cfg['tools']['lychee']['version'] = 'lychee 0.0.0'
        with self.assertRaises(ValueError): self.run_checks()
        self.cfg['tools']['lychee']['version'] = d.VERSIONS['lychee']
        original = d.process
        def changed_version(argv, *args, **kwargs):
            if '--version' in argv: return 0, b'lychee wrong', b''
            return original(argv, *args, **kwargs)
        with patch.object(d, 'process', side_effect=changed_version):
            self.assertEqual(self.run_checks()['tools']['lychee']['status'], 'error')

    def test_cli_findings_and_unavailable(self):
        self.fake('vale', "print(json.dumps({'input0.md':[{'Check':'Project.Term0','Line':1,'Severity':'warning'}]}))")
        self.run_checks()
        argv=[sys.executable,str(Path(d.__file__)), '--project',str(self.root),'--config',str(self.config)]
        self.assertEqual(subprocess.run(argv,capture_output=True).returncode,1)
        Path(self.cfg['tools']['vale']['executable']).unlink()
        self.assertEqual(subprocess.run(argv,capture_output=True).returncode,2)

    def test_lychee_actual_exit_semantics(self):
        obj={'total':1,'successful':0,'errors':1,'unknown':0,'unsupported':0,'timeouts':0,'excludes':0,'cached':0,'error_map':{'links.md':[{'url':'file:///missing','status':{}}]}}
        self.assertEqual(d.normalize_lychee(2,json.dumps(obj).encode(),1)[0],'findings')
        with self.assertRaises(ValueError):d.normalize_lychee(1,json.dumps(obj).encode(),1)
        obj.update(errors=0,excludes=1,error_map={})
        with self.assertRaises(ValueError):d.normalize_lychee(0,json.dumps(obj).encode(),1)


if __name__ == '__main__':unittest.main()
