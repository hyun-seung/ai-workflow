import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import document_evidence as de


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'docs').mkdir()
        (self.root / 'src').mkdir()
        (self.root / 'docs/guide.md').write_text('Guide\n')
        (self.root / 'src/code.txt').write_text('204\n')
        self.m = dict(version=1, project=str(self.root), roots=['docs', 'src'],
                      files=['docs/guide.md', 'src/code.txt'], claims=[dict(id='response', documents=['docs/guide.md'],
                      sources=[dict(kind='code', file='src/code.txt', sha256=hashlib.sha256(b'204\n').hexdigest())])])

    def test_unchanged_changed_missing(self):
        self.assertEqual(de.check(self.root, self.m)['re_review_candidates'], [])
        (self.root / 'src/code.txt').write_text('200\n')
        report = de.check(self.root, self.m)
        self.assertEqual(report['re_review_candidates'], ['docs/guide.md'])
        self.assertEqual(report['evidence'][0]['status'], 'changed')
        (self.root / 'src/code.txt').unlink()
        report = de.check(self.root, self.m)
        self.assertEqual(report['evidence'][0]['status'], 'missing')
        self.assertEqual(report['re_review_candidates'], ['docs/guide.md'])

    def test_project_scope_hash_and_kind(self):
        for change in [dict(project='elsewhere'), dict(roots=['docs']), dict(files=['../escape']), dict(files=['src/unlisted'])]:
            with self.subTest(change=change), self.assertRaises((ValueError, OSError)):
                de.check(self.root, dict(self.m, **change))
        for key, value in [('sha256', 'invalid'), ('kind', 'approved'), ('file', 'src/unlisted')]:
            m = copy.deepcopy(self.m)
            m['claims'][0]['sources'][0][key] = value
            with self.assertRaises(ValueError):
                de.check(self.root, m)

    def test_symlink_and_duplicate_claim(self):
        source = self.root / 'src/code.txt'
        source.unlink()
        source.symlink_to(self.root / 'docs/guide.md')
        with self.assertRaises((ValueError, OSError)):
            de.check(self.root, self.m)
        source.unlink()
        source.write_text('204\n')
        self.m['claims'] *= 2
        with self.assertRaises(ValueError):
            de.check(self.root, self.m)

    def test_kinds_are_provenance_not_truth(self):
        for kind in de.KINDS:
            self.m['claims'][0]['sources'][0]['kind'] = kind
            report = de.check(self.root, self.m)
            self.assertFalse(report['approval'])
            self.assertEqual(report['evidence'][0]['kind'], kind)

    def test_only_listed_evidence_and_affected_documents(self):
        (self.root / 'src/unrelated.txt').write_text('not searched')
        (self.root / 'docs/other.md').write_text('Other')
        self.m['files'].append('docs/other.md')
        self.assertEqual(de.check(self.root, self.m)['re_review_candidates'], [])
        (self.root / 'src/code.txt').write_text('changed')
        self.assertEqual(de.check(self.root, self.m)['re_review_candidates'], ['docs/guide.md'])

    def test_parent_and_root_symlink(self):
        (self.root / 'src').rename(self.root / 'actual')
        (self.root / 'src').symlink_to(self.root / 'actual', target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            de.check(self.root, self.m)
        (self.root / 'src').unlink()
        (self.root / 'actual').rename(self.root / 'src')
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        self.assert_boundary_rejected(alias, dict(self.m, project=str(alias)))


    def assert_boundary_rejected(self, project, manifest):
        with self.assertRaises((ValueError, OSError)):
            de.check(project, manifest)
        manifest_path = self.root / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest))
        result = subprocess.run([sys.executable, str(Path(de.__file__)),
                                 '--project', str(project), '--manifest', str(manifest_path)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('error', json.loads(result.stdout))

    def test_project_ancestor_symlink(self):
        alias = self.root / 'alias-parent'
        alias.symlink_to(self.root, target_is_directory=True)
        nested = self.root / 'nested'
        nested.mkdir()
        (nested / 'docs').mkdir()
        (nested / 'src').mkdir()
        (nested / 'docs/guide.md').write_text('Guide\n')
        (nested / 'src/code.txt').write_text('204\n')
        project = alias / 'nested'
        self.assert_boundary_rejected(project, dict(self.m, project=str(project)))

    def test_affected_document_symlink(self):
        document = self.root / 'docs/guide.md'
        document.unlink()
        document.symlink_to(self.root / 'src/code.txt')
        self.assert_boundary_rejected(self.root, self.m)
        document.unlink()
        document.write_text('Guide\n')
        (self.root / 'docs').rename(self.root / 'actual-docs')
        (self.root / 'docs').symlink_to(self.root / 'actual-docs', target_is_directory=True)
        self.assert_boundary_rejected(self.root, self.m)
