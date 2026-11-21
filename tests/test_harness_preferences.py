import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import harness_policy as hp


class DecisionMakerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.common = self.root / 'common'
        self.common.mkdir()
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.common / 'harness.json').write_text('{"version": 1}')

    def test_legacy_config_defaults_to_coordinator_without_writes(self):
        before = sorted(self.root.rglob('*'))
        with patch.object(hp, 'ROOT', self.common):
            value = hp.load(self.project)
        self.assertEqual(value['delegation_decision_maker'], 'coordinator')
        self.assertEqual(before, sorted(self.root.rglob('*')))
        self.assertFalse((self.project / '.ai-development-policy.json').exists())

    def test_common_project_explicit_and_argument_precedence(self):
        (self.common / 'harness.json').write_text('{"delegation_decision_maker":"user"}')
        override = self.project / '.ai-workflow-harness.json'
        explicit = self.root / 'explicit.json'
        with patch.object(hp, 'ROOT', self.common):
            self.assertEqual(hp.load(self.project)['delegation_decision_maker'], 'user')
            override.write_text('{"delegation_decision_maker":"coordinator"}')
            self.assertEqual(hp.load(self.project)['delegation_decision_maker'], 'coordinator')
            explicit.write_text('{"delegation_decision_maker":"user"}')
            self.assertEqual(hp.load(self.project, explicit)['delegation_decision_maker'], 'user')
            self.assertEqual(hp.load(self.project, explicit, delegation_decision_maker='coordinator')
                             ['delegation_decision_maker'], 'coordinator')

    def test_unrelated_override_keeps_common_user_choice(self):
        (self.common / 'harness.json').write_text('{"delegation_decision_maker":"user"}')
        (self.project / '.ai-workflow-harness.json').write_text('{"auto_cleanup":"N"}')
        with patch.object(hp, 'ROOT', self.common):
            self.assertEqual(hp.load(self.project)['delegation_decision_maker'], 'user')

    def test_invalid_values_and_misspelled_key_refused(self):
        for value in (True, False, None, 0, [], {}, 'auto', '', 'Coordinator'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                hp.validate({'delegation_decision_maker': value})
        with self.assertRaises(ValueError):
            hp.validate({'delegation_decider': 'user'})
        with self.assertRaises(ValueError):
            hp.load(self.project, delegation_decision_maker=False)

    def test_effective_cli_user_choice_is_readonly_and_preserves_existing_policy(self):
        config = self.project / '.ai-workflow-harness.json'
        config.write_text('{"delegation_decision_maker":"user"}')
        policy = self.project / '.ai-development-policy.json'
        policy.write_text('{"version":1,"enabled":false,"max_dispatches":2,"max_fix_rounds":0}')
        before = {p.name: p.read_bytes() for p in self.project.iterdir()}
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['delegation_decision_maker'], 'user')
        self.assertIn(str(config.resolve()), output['sources'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.project.iterdir()})
        self.assertFalse((self.project / 'state').exists())

    def test_cli_invalid_project_config_and_duplicate_keys_fail_without_traceback(self):
        path = self.project / '.ai-workflow-harness.json'
        for raw in ('{"delegation_decision_maker":null}',
                    '{"delegation_decision_maker":"user","delegation_decision_maker":"coordinator"}',
                    '{"delegation_decision_maker":"other"}'):
            with self.subTest(raw=raw):
                path.write_text(raw)
                result = self.cli()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr), {'error': 'invalid_harness_policy'})
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual(path.read_text(), raw)

    def test_cli_default_and_user_argument(self):
        self.assertEqual(json.loads(self.cli().stdout)['delegation_decision_maker'], 'coordinator')
        result = self.cli('--delegation-decision-maker', 'user')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['delegation_decision_maker'], 'user')

    def cli(self, *extra):
        return subprocess.run([sys.executable, str(hp.ROOT / 'scripts/harness_policy.py'),
                               '--project', str(self.project), *extra],
                              capture_output=True, text=True, timeout=10)
