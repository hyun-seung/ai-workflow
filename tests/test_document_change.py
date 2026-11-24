import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import document_change as dc


class DocumentChangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'docs').mkdir()
        (self.root / 'backups').mkdir()
        (self.root / 'docs/a.md').write_text('# Before\n')
        (self.root / 'docs/b.md').write_text('# Guide\n')
        self.cfg = self.root / 'settings.json'
        template = Path(__file__).resolve().parents[1] / 'templates/documentation-project.json'
        self.c = json.loads(template.read_text())
        self.c.update(permitted_files=['docs/a.md', 'docs/b.md'], backup_directory='backups', synthetic_trial=True)
        self.save_config()
        self.p = dc.prepare(self.root, self.cfg, 1, 'synthetic-author', 'meaning',
                            'Fictional trial only', {'docs/a.md': '# After\n', 'docs/b.md': '[A](a.md)\n'})
        self.reviews = [dict(role=role, reviewer=who, conclusion='approved', version=1,
                             proposal_sha256=dc.digest(self.p), evidence='SYNTHETIC fixture trial, no human approval', synthetic=True)
                        for role, who in [('independent', 'document-reviewer'), ('policy', 'policy-owner')]]

    def save_config(self):
        self.cfg.write_text(json.dumps(self.c))

    def apply(self):
        return dc.apply(self.root, self.cfg, self.p, self.reviews, 'document-applier')

    def test_normal_apply_and_backup(self):
        r = self.apply()
        self.assertTrue(r['complete'])
        self.assertEqual((self.root / 'docs/a.md').read_text(), '# After\n')
        self.assertEqual((self.root / r['backups']['docs/a.md']).read_text(), '# Before\n')
        self.assertEqual(len(r['applied']), 2)
        with self.assertRaises(ValueError):
            self.apply()  # approved snapshot cannot replay after apply

    def test_unapproved_has_no_writes(self):
        for reviews in [[], self.reviews[:1], [dict(r, conclusion='pending') for r in self.reviews]]:
            with self.subTest(reviews=reviews), self.assertRaises(ValueError):
                dc.apply(self.root, self.cfg, self.p, reviews, 'document-applier')
        self.assertEqual(list((self.root / 'backups').iterdir()), [])
        self.assertEqual((self.root / 'docs/a.md').read_text(), '# Before\n')

    def test_stale_proposal_version_content_scope(self):
        for change in [dict(version=2), dict(rationale='changed'), dict(changes={'docs/a.md': '# Other\n'}),
                       dict(diff={'docs/a.md': 'concealed'})]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                dc.apply(self.root, self.cfg, dict(self.p, **change), self.reviews, 'document-applier')

    def test_whitespace_config_change_and_project_binding(self):
        self.cfg.write_text(self.cfg.read_text() + '\n')
        with self.assertRaises(ValueError):
            self.apply()
        self.save_config()
        other = self.root / 'other'
        (other / 'docs').mkdir(parents=True)
        (other / 'backups').mkdir()
        for name in ['a.md', 'b.md']:
            (other / 'docs' / name).write_bytes((self.root / 'docs' / name).read_bytes())
        with self.assertRaises(ValueError):
            dc.apply(other, self.cfg, self.p, self.reviews, 'document-applier')

    def test_stale_target(self):
        (self.root / 'docs/b.md').write_text('User edit\n')
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual((self.root / 'docs/a.md').read_text(), '# Before\n')

    def test_stale_config_and_allowlist(self):
        for field, value in [('review_criteria', ['changed']), ('permitted_files', ['docs/a.md']),
                             ('document_roots', ['elsewhere']), ('appliers', ['other'])]:
            old = self.c[field]
            self.c[field] = value
            self.save_config()
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.apply()
            self.c[field] = old
        self.save_config()

    def test_escapes_and_unlisted_files(self):
        for name in ['../outside.md', '/tmp/escape.md', 'docs/../../escape.md', 'docs/unlisted.md']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                dc.prepare(self.root, self.cfg, 1, 'a', 'minor', 'test', {name: 'text\n'})
        self.c['permitted_files'] = ['other/a.md']
        self.save_config()
        with self.assertRaises(ValueError):
            self.apply()

    def test_symlink_leaf_parent_root_and_backup(self):
        a = self.root / 'docs/a.md'
        a.unlink()
        a.symlink_to(self.root / 'docs/b.md')
        with self.assertRaises((ValueError, OSError)):
            self.apply()
        a.unlink()
        a.write_text('# Before\n')
        (self.root / 'docs').rename(self.root / 'real')
        (self.root / 'docs').symlink_to(self.root / 'real', target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.apply()
        (self.root / 'docs').unlink()
        (self.root / 'real').rename(self.root / 'docs')
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            dc.apply(alias, self.cfg, self.p, self.reviews, 'document-applier')
        (self.root / 'backups').rmdir()
        (self.root / 'backups').symlink_to(self.root / 'docs', target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.apply()

    def test_identity_and_synthetic_boundary(self):
        with self.assertRaises(ValueError):
            dc.apply(self.root, self.cfg, self.p, self.reviews, 'stranger')
        p = dict(self.p, author='document-reviewer')
        reviews = [dict(r, proposal_sha256=dc.digest(p)) for r in self.reviews]
        with self.assertRaises(ValueError):
            dc.apply(self.root, self.cfg, p, reviews, 'document-applier')
        self.c['synthetic_trial'] = False
        self.save_config()
        p = dc.prepare(self.root, self.cfg, 1, 'a', 'meaning', 'test', self.p['changes'])
        reviews = [dict(r, proposal_sha256=dc.digest(p)) for r in self.reviews]
        with self.assertRaises(ValueError):
            dc.apply(self.root, self.cfg, p, reviews, 'document-applier')

    def test_lint_and_skipped(self):
        r = dc.lint(self.root, {'docs/a.md': '[bad](missing.md)\n[[wiki]]\n```mermaid\nx\n```\n[remote](https://example.invalid)\n'})
        self.assertTrue(r['errors'])
        self.assertEqual(len(r['skipped']), 3)
        self.assertTrue(dc.lint(self.root, {'docs/a.md': 'space \n```\n'})['errors'])
        self.assertFalse(dc.lint(self.root, self.p['changes'])['errors'])

    def test_parser_representative_links(self):
        (self.root / 'docs/image(1).png').write_bytes(b'image fixture')
        text = ('`[inline](absent.md)`\n\n~~~text\n[code](absent.md)\n~~~\n'
                '    [indented](absent.md)\n\n'
                '[ref][r] ![image](image\\(1\\).png) [paren](image(1).png)\n\n[r]: b.md "title"\n')
        self.assertFalse(dc.lint(self.root, {'docs/a.md': text})['errors'])
        self.assertTrue(dc.lint(self.root, {'docs/a.md': '![missing](absent.png)\n'})['errors'])
        self.assertTrue(dc.lint(self.root, {'docs/a.md': '[escape](../../outside)\n'})['errors'])
        (self.root / 'docs/link').symlink_to(self.root / 'docs/b.md')
        self.assertTrue(dc.lint(self.root, {'docs/a.md': '[alias](link)\n'})['errors'])
        self.assertEqual(len(dc.lint(self.root, {'docs/a.md': '[web](https://example.invalid) [anchor](b.md#x)\n'})['skipped']), 2)

    def test_parser_unavailable_fails_clearly(self):
        with patch.dict(sys.modules, {'markdown_it': None}):
            with self.assertRaisesRegex(ValueError, 'parser unavailable'):
                dc.lint(self.root, self.p['changes'])

    def test_incomplete_backup_write_flush_fsync(self):
        original = dc.os.fdopen
        for method in ('write', 'flush', 'fsync'):
            class BrokenStream:
                def __init__(self, fd, mode):
                    self.stream = original(fd, mode)
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    self.stream.close()
                def write(self, raw):
                    if method == 'write':
                        self.stream.write(raw[:1])
                        raise OSError('write failed')
                    return self.stream.write(raw)
                def flush(self):
                    if method == 'flush':
                        raise OSError('flush failed')
                    return self.stream.flush()
                def fileno(self):
                    return self.stream.fileno()
            with self.subTest(method=method):
                with patch.object(dc.os, 'fdopen', side_effect=lambda fd, mode: BrokenStream(fd, mode) if mode == 'wb' else original(fd, mode)):
                    if method == 'fsync':
                        with patch.object(dc.os, 'fsync', side_effect=OSError('fsync failed')):
                            result = self.apply()
                    else:
                        result = self.apply()
                self.assertFalse(result['complete'])
                self.assertEqual(result['backups'], {})
                self.assertEqual(result['applied'], [])
                self.assertEqual(len(result['incomplete_backups']), 1)
                self.assertTrue((self.root / next(iter(result['incomplete_backups'].values()))).exists())
                self.assertEqual((self.root / 'docs/a.md').read_text(), '# Before\n')

    def test_backup_failure_leaves_documents_intact(self):
        with patch.object(dc.os, 'fsync', side_effect=OSError('disk full')):
            r = self.apply()
        self.assertFalse(r['complete'])
        self.assertEqual(r['applied'], [])
        self.assertEqual((self.root / 'docs/a.md').read_text(), '# Before\n')

    def test_partial_failure_reports_no_rollback(self):
        original = dc.os.replace
        def fail_second(src, dst, **kwargs):
            if dst == 'b.md':
                raise OSError('synthetic replace failure')
            return original(src, dst, **kwargs)
        with patch.object(dc.os, 'replace', side_effect=fail_second):
            r = self.apply()
        self.assertFalse(r['complete'])
        self.assertEqual(r['applied'], ['docs/a.md'])
        self.assertEqual(len(r['backups']), 2)
        self.assertEqual((self.root / 'docs/b.md').read_text(), '# Guide\n')
        self.assertEqual(list((self.root / 'docs').glob('.document-*')), [])

    def test_config_changed_after_backups(self):
        original = dc.config
        calls = []
        def change(*args):
            calls.append(1)
            if len(calls) == 3:
                self.c['environment'] = {'changed': True}
                self.save_config()
            return original(*args)
        with patch.object(dc, 'config', side_effect=change):
            r = self.apply()
        self.assertFalse(r['complete'])
        self.assertEqual(r['applied'], [])

    def test_duplicate_json_and_special_file(self):
        self.cfg.write_text('{"x":1,"x":2}')
        with self.assertRaises(ValueError):
            dc.load(self.cfg)
        fifo = self.root / 'pipe'
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            dc.read(self.root, 'pipe')

    def test_minor_reuses_bound_existing_authorization(self):
        p = dc.prepare(self.root, self.cfg, 1, 'synthetic-author', 'minor',
                       'Fictional link correction under existing authority', self.p['changes'])
        review = dict(self.reviews[0], proposal_sha256=dc.digest(p), evidence='SYNTHETIC existing project link authority')
        self.assertTrue(dc.apply(self.root, self.cfg, p, [review], 'document-applier')['complete'])


if __name__ == '__main__':
    unittest.main()
