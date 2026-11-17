import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import compare_routing as comparison
from lifecycle_io import save


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.files = {}
        self.criteria = self.artifact('criteria', b'Same goal, source requirements and acceptance checks.')
        self.evaluation = self.artifact('evaluation', b'Fictional independent review; all eight phase declarations checked.')
        self.base = json.loads((ROOT / 'tests/fixtures/traces/native-usage.json').read_text())
        self.manifest = dict(version=1, case_id='case_example', criteria=self.criteria,
                             direct=self.side('direct', 100, 20), routed=self.side('routed', 50, 10))

    def artifact(self, name, raw):
        path = '/private/example/' + name
        self.files[path] = raw
        return dict(path=path, sha256=hashlib.sha256(raw).hexdigest())

    def record(self, name, input_tokens, output_tokens):
        record = copy.deepcopy(self.base); usage = record['dispatch_usage']
        usage.update(request_id=name, session_id='session_' + name, actor_id='actor_' + name,
                     dispatch_id='actor_' + name, measurement_key='codex.session:session_' + name + ':turn:turn_example')
        counters = dict(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens,
                        cached_input_tokens=min(30, input_tokens), reasoning_output_tokens=min(2, output_tokens))
        usage['measurements'] = counters
        usage['final']['values'] = copy.deepcopy(counters)
        usage['increments'][0]['values'] = copy.deepcopy(counters)
        return record

    def side(self, name, input_tokens, output_tokens):
        record = self.record(name, input_tokens, output_tokens)
        return dict(request_id=name, measurements=[self.artifact(name, json.dumps(record).encode())],
                    coverage=dict.fromkeys(comparison.PHASES, 'included'),
                    quality=dict(status='pass', reviewer='independent', evidence=self.evaluation))

    def compare(self):
        return comparison.compare(self.manifest, self.files.__getitem__)

    def test_valid_pair_compares_unique_measured_tokens_without_enabling_automation(self):
        before = copy.deepcopy(self.manifest)
        result = self.compare()
        self.assertEqual(result['routed_minus_direct_tokens'], -60)
        self.assertEqual(result['decision'], 'candidate_for_repeated_evaluation')
        self.assertFalse(result['automation_allowed'])
        self.assertIsNone(result['elapsed_time_comparison'])
        self.assertIsNone(result['monetary_cost_comparison'])
        self.assertEqual(self.manifest, before)

    def test_cached_and_reasoning_details_are_not_added_twice(self):
        result = self.compare()['sides']['direct']['measured_tokens']
        self.assertEqual(result['total_tokens'], 120)
        self.assertEqual(result['cached_input_tokens'], 30)
        self.assertEqual(result['reasoning_output_tokens'], 2)

    def test_unknown_subset_counters_remain_unknown_while_primary_total_is_measured(self):
        record = self.record('direct', 100, 20); usage = record['dispatch_usage']
        usage['measurements']['cached_input_tokens'] = None
        usage['final']['values']['cached_input_tokens'] = None
        usage['increments'][0]['values']['cached_input_tokens'] = None
        self.manifest['direct']['measurements'] = [self.artifact('unknown-cache', json.dumps(record).encode())]
        totals = self.compare()['sides']['direct']['measured_tokens']
        self.assertIsNone(totals['cached_input_tokens'])
        self.assertEqual(totals['total_tokens'], 120)

    def test_existing_coordinator_turn_format_is_supported_without_double_counting_worker(self):
        record = self.record('direct', 100, 20)
        usage = record.pop('dispatch_usage')
        usage.update(version=2, scope='native_turn', role='coordinator', dispatch_id=None,
                     request_binding='explicit_native_turn', message_sha256='0' * 64)
        usage['interval']['binding_line'] = usage['interval'].pop('assignment_line')
        record.update(kind='orca_codex_coordinator_turn', turn_usage=usage)
        self.manifest['direct']['measurements'] = [self.artifact('coordinator', json.dumps(record).encode())]
        self.assertEqual(self.compare()['routed_minus_direct_tokens'], -60)
        record['kind'] = 'orca_codex_metadata'
        self.manifest['direct']['measurements'] = [self.artifact('bad-kind', json.dumps(record).encode())]
        with self.assertRaisesRegex(ValueError, 'kind_mismatch'):self.compare()

    def test_aggregated_counter_overflow_is_rejected(self):
        record = self.record('large', 2 ** 53 - 1, 0)
        record['dispatch_usage']['request_id'] = 'direct'
        self.manifest['direct']['measurements'].append(self.artifact('large', json.dumps(record).encode()))
        with self.assertRaisesRegex(ValueError, 'integer'):self.compare()

    def test_exact_replay_references_are_counted_only_once(self):
        self.manifest['routed']['measurements'] *= 2
        result = self.compare()
        self.assertEqual(result['routed_minus_direct_tokens'], -60)
        self.assertEqual(result['sides']['routed']['duplicate_references_skipped'], 1)

    def test_cross_arm_same_physical_turn_cannot_be_two_runs(self):
        record = self.record('direct', 100, 20)
        record['dispatch_usage']['request_id'] = 'routed'
        self.manifest['routed']['measurements'] = [self.artifact('shared-turn', json.dumps(record).encode())]
        with self.assertRaisesRegex(ValueError, 'Physical usage key'):
            self.compare()

    def test_conflicting_replays_and_overlapping_intervals_are_rejected(self):
        for overlap in (False, True):
            record = self.record('direct', 50, 10)
            if overlap:
                record['dispatch_usage'].update(actor_id='other', dispatch_id='other',
                                                measurement_key='codex.session:session_direct:turn:other')
                record['dispatch_usage']['interval']['native_turn_id'] = 'other'
            self.manifest['direct']['measurements'] = [self.manifest['direct']['measurements'][0],
                                                       self.artifact('conflict', json.dumps(record).encode())]
            with self.assertRaises(ValueError):self.compare()

    def test_partial_coverage_never_claims_total_saving(self):
        self.manifest['routed']['coverage']['reporting'] = 'missing'
        result = self.compare()
        self.assertEqual(result['decision'], 'incomplete_measurement')
        self.assertIsNone(result['routed_minus_direct_tokens'])
        self.assertEqual(result['sides']['routed']['measured_tokens']['total_tokens'], 60)

    def test_no_records_remains_unmeasured_instead_of_zero(self):
        self.manifest['direct']['measurements'] = []
        result = self.compare()
        self.assertIsNone(result['sides']['direct']['measured_tokens'])
        self.assertIsNone(result['routed_minus_direct_tokens'])

    def test_quality_regression_unknown_or_self_review_never_becomes_candidate(self):
        for field, value, expected in [('status', 'fail', 'quality_not_met'),
                                       ('status', 'unverified', 'needs_quality_review'),
                                       ('reviewer', 'self', 'needs_quality_review')]:
            self.manifest['routed']['quality'] = dict(status='pass', reviewer='independent', evidence=self.evaluation)
            self.manifest['routed']['quality'][field] = value
            self.assertEqual(self.compare()['decision'], expected)

    def test_higher_or_equal_total_is_not_a_token_saving(self):
        for total in (120, 150):
            self.manifest['routed'] = self.side('routed', total - 20, 20)
            self.assertEqual(self.compare()['decision'], 'no_token_saving')

    def test_changed_criteria_or_measurement_or_quality_artifact_is_rejected(self):
        for ref in (self.criteria, self.evaluation, self.manifest['routed']['measurements'][0]):
            path = ref['path']; original = self.files[path]
            self.files[path] += b' changed'
            with self.assertRaisesRegex(ValueError, 'hash_changed'):self.compare()
            self.files[path] = original

    def test_empty_evidence_and_ambiguous_json_are_rejected(self):
        self.manifest['criteria'] = self.artifact('empty', b' ')
        with self.assertRaisesRegex(ValueError, 'empty'):self.compare()
        for raw in (b'{"version":1,"version":2}', b'{"version":NaN}'):
            with self.assertRaises(ValueError):comparison.parse(raw)

    def test_request_binding_partial_native_counters_and_overflow_are_rejected(self):
        for change in ('request', 'partial', 'counters', 'overflow'):
            record = self.record('routed', 50, 10); usage = record['dispatch_usage']
            if change == 'request':usage['request_id'] = 'other'
            elif change == 'partial':usage['status'] = 'unavailable'
            elif change == 'counters':usage['measurements']['total_tokens'] += 1
            else:usage['measurements']['input_tokens'] = 2 ** 53
            self.manifest['routed']['measurements'] = [self.artifact('invalid', json.dumps(record).encode())]
            with self.subTest(change=change), self.assertRaises(ValueError):self.compare()

    def test_all_phases_and_quality_fields_must_be_explicit(self):
        for change in ('missing', 'invalid', 'processing', 'routing'):
            side = copy.deepcopy(self.manifest['routed'])
            if change == 'missing':side['coverage'].pop('reporting')
            elif change == 'invalid':side['coverage']['reporting'] = True
            else:side['coverage'][change] = 'not_applicable'
            manifest = dict(self.manifest, routed=side)
            with self.subTest(change=change), self.assertRaises(ValueError):
                comparison.compare(manifest, self.files.__getitem__)

    def test_comparison_does_not_call_models_network_or_runtime(self):
        with patch('subprocess.run', side_effect=AssertionError('runtime')), patch('socket.socket', side_effect=AssertionError('network')):
            self.assertEqual(self.compare()['decision'], 'candidate_for_repeated_evaluation')

    def test_cli_reads_only_private_references_and_does_not_modify_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); project = root / 'project'; project.mkdir()
            private = root / 'private'; private.mkdir(mode=0o700)
            manifest = copy.deepcopy(self.manifest)
            translated = {}
            for index, (old, raw) in enumerate(self.files.items()):
                path = private / str(index); path.write_bytes(raw); path.chmod(0o600)
                translated[old] = str(path)
            def translate(value):
                if isinstance(value, dict):return {k: translated.get(v, v) if k == 'path' else translate(v) for k, v in value.items()}
                if isinstance(value, list):return [translate(v) for v in value]
                return value
            path = private / 'comparison.json'; save(path, translate(manifest), project)
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in private.iterdir()}
            command = [sys.executable, str(ROOT / 'scripts/compare_routing.py'), '--project', str(project), '--manifest', str(path)]
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['routed_minus_direct_tokens'], -60)
            self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in private.iterdir()}, before)
            path.chmod(0o644)
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn(str(private).encode(), result.stderr)


if __name__ == '__main__':
    unittest.main()
