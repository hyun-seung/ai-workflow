import copy
from decimal import localcontext
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
import test_compare_routing as samples
import test_routing_finish as completed_samples
from lifecycle_io import save


class MetricsTests(unittest.TestCase):
    def setUp(self):
        fixture = samples.ComparisonTests(); fixture.setUp()
        self.fixture = fixture
        self.manifest, self.files = fixture.manifest, fixture.files
        self.manifest['version'] = 2
        self.rate = dict(version=1, provider='example-provider', currency='USD', unit='per_million_tokens',
            billing_mode='fictional-flat-rates-not-an-invoice', valid_from='2025-01-01T00:00:00Z',
            valid_until='2027-01-01T00:00:00Z', source=dict(url='https://provider.example/pricing',
            retrieved_at='2026-01-01T00:00:00Z', evidence=self.artifact('rate-evidence', b'Fictional tariff evidence.')),
            models={'example-model': dict(uncached_input='2', cached_input='0.5', output='10')})

    def artifact(self, name, raw): return self.fixture.artifact(name, raw)
    def json_artifact(self, name, value): return self.artifact(name, json.dumps(value).encode())
    def compare(self): return comparison.compare(self.manifest, self.files.__getitem__)

    def timing(self, name, seconds, **changes):
        value = dict(version=1, request_id=name, measurement_id='timing_' + name, clock_id='clock_example',
            scope='request_wall_clock', start_event='request_received', end_event='final_response_delivered',
            started_at='2026-01-01T00:00:00Z', finished_at='2026-01-01T00:00:' + seconds + 'Z',
            coverage=dict.fromkeys(comparison.PHASES, 'included'), evidence=self.artifact('clock-' + name, b'Fictional clock boundary record.'))
        value.update(changes)
        self.manifest[name]['timing'] = self.json_artifact('timing-' + name, value)
        return value

    def price(self):
        self.manifest['rate_card'] = self.json_artifact('rate-card', self.rate)
        for name in ('direct', 'routed'):
            record = json.loads(self.files[self.manifest[name]['measurements'][0]['path']])
            usage = record['dispatch_usage']
            record.update(provider='example-provider', observed_contexts=[dict(line=3, values={'model': 'example-model'})],
                session=dict(id=usage['session_id'], source='vscode', originator='orca_desktop'),
                binding=dict(task_id=usage['task_id'], dispatch_id=usage['dispatch_id'], line=4, context_line=3))
            self.manifest[name]['measurements'] = [self.json_artifact(name + '-priced', record)]

    def edit_record(self, name, fn):
        ref = self.manifest[name]['measurements'][0]
        record = json.loads(self.files[ref['path']]); fn(record)
        self.manifest[name]['measurements'][0] = self.json_artifact(name + '-edited', record)

    def test_v2_without_optional_metrics_is_null_and_v1_rejects_new_fields(self):
        self.assertIsNone(self.compare()['elapsed_time_comparison'])
        self.assertIsNone(self.compare()['monetary_cost_comparison'])
        self.manifest['version'] = 1
        self.timing('direct', '10')
        with self.assertRaises(ValueError): self.compare()

    def test_request_elapsed_decimal_and_normalized_utc_not_worker_sum(self):
        self.timing('direct', '10.125', started_at='2026-01-01T00:00:00+00:00')
        self.timing('routed', '05.000001')
        result = self.compare()['elapsed_time_comparison']
        self.assertEqual(result['status'], 'declared_comparable')
        self.assertEqual(result['routed_minus_direct_seconds'], '-5.124999')
        self.assertEqual(result['sides']['routed']['elapsed_seconds'], '5.000001')

    def test_elapsed_independent_of_missing_token_measurements(self):
        self.timing('direct', '10'); self.timing('routed', '05')
        self.manifest['direct']['measurements'] = []
        result = self.compare()
        self.assertEqual(result['decision'], 'incomplete_measurement')
        self.assertEqual(result['elapsed_time_comparison']['routed_minus_direct_seconds'], '-5')

    def test_missing_timing_scope_or_matching_clock_preserves_partial_without_delta(self):
        for change in ('missing', 'coverage', 'clock'):
            self.timing('direct', '10')
            record = self.timing('routed', '05')
            if change == 'missing': self.manifest['routed'].pop('timing')
            elif change == 'coverage':
                record['coverage']['reporting'] = 'missing'
                self.manifest['routed']['timing'] = self.json_artifact('timing-routed', record)
            else: self.timing('routed', '05', clock_id='other_clock')
            result = self.compare()['elapsed_time_comparison']
            self.assertEqual(result['status'], 'unavailable')
            self.assertIsNone(result['routed_minus_direct_seconds'])
            self.assertEqual(result['sides']['direct']['elapsed_seconds'], '10')

    def test_timing_request_scope_id_and_timestamps_must_be_valid(self):
        self.timing('direct', '10')
        cases = [dict(request_id='other'), dict(version=True), dict(scope='worker'), dict(start_event='worker_started'),
                 dict(end_event='worker_done'), dict(finished_at='2025-01-01T00:00:00Z'),
                 dict(started_at='2026-01-01T00:00:00'), dict(finished_at='2026-02-30T00:00:00Z')]
        for change in cases:
            self.timing('routed', '05', **change)
            with self.subTest(change=change), self.assertRaises(ValueError): self.compare()

    def test_same_clock_measurement_cannot_be_reused_for_both_requests(self):
        self.timing('direct', '10'); self.timing('routed', '05', measurement_id='timing_direct')
        with self.assertRaisesRegex(ValueError, 'timing_reused'): self.compare()

    def test_timing_evidence_is_hash_checked_and_nonempty(self):
        self.timing('direct', '10'); record = self.timing('routed', '05')
        self.files[record['evidence']['path']] += b' changed'
        with self.assertRaisesRegex(ValueError, 'hash_changed'): self.compare()
        self.timing('routed', '05', evidence=self.artifact('empty-clock', b' '))
        with self.assertRaisesRegex(ValueError, 'empty_timing'): self.compare()

    def test_rate_estimates_charge_uncached_and_cached_input_once(self):
        self.price()
        result = self.compare()['monetary_cost_comparison']
        self.assertEqual(result['status'], 'declared_rate_estimate')
        self.assertEqual(result['sides']['direct']['measured_token_cost'], '0.000355')
        self.assertEqual(result['sides']['routed']['measured_token_cost'], '0.000155')
        self.assertEqual(result['routed_minus_direct_estimated_cost'], '-0.0002')
        self.assertFalse(result['billed_cost_verified'])
        self.assertFalse(self.compare()['automation_allowed'])

    def test_decimal_precision_does_not_depend_on_process_context(self):
        self.rate['models']['example-model'] = dict(uncached_input='0.123456789012', cached_input='0.000000000001', output='0.987654321098')
        self.price(); self.timing('direct', '10.125'); self.timing('routed', '05.000001')
        with localcontext() as context:
            context.prec = 5
            result = self.compare()
        self.assertEqual(result['monetary_cost_comparison']['sides']['direct']['measured_token_cost'], '0.00002839506165283')
        self.assertEqual(result['elapsed_time_comparison']['routed_minus_direct_seconds'], '-5.124999')

    def test_missing_model_does_not_use_requested_or_launch_profile(self):
        self.price()
        def change(record):
            record.pop('observed_contexts')
            record['requested'] = record['launch_effective'] = dict(status='observed', model='example-model')
        self.edit_record('direct', change)
        result = self.compare()['monetary_cost_comparison']
        self.assertIsNone(result['routed_minus_direct_estimated_cost'])
        self.assertIsNone(result['sides']['direct']['measured_token_cost'])
        self.assertEqual(result['sides']['direct']['unpriced_measurements'][0]['reason'], 'observed_model_unavailable')

    def test_mixed_out_of_range_null_and_wrong_provider_observations_remain_unpriced(self):
        for mode in ('mixed', 'outside', 'null', 'provider'):
            self.price()
            def change(record):
                if mode == 'mixed': record['observed_contexts'].append(dict(line=5, values={'model': 'other-model'}))
                elif mode == 'outside': record['observed_contexts'][0]['line'] = 99
                elif mode == 'null': record['observed_contexts'][0]['values']['model'] = None
                else: record['provider'] = 'other-provider'
            self.edit_record('direct', change)
            with self.subTest(mode=mode):
                result = self.compare()['monetary_cost_comparison']
                self.assertIsNone(result['routed_minus_direct_estimated_cost'])
                self.assertEqual(result['sides']['direct']['priced_measurements'], 0)

    def test_unknown_cache_and_absent_billing_time_are_not_zero_filled(self):
        for mode in ('cache', 'time'):
            self.price()
            def change(record):
                usage = record['dispatch_usage']
                if mode == 'time': usage['interval'].update(started_at=None, finished_at=None)
                else:
                    usage['measurements']['cached_input_tokens'] = None
                    usage['final']['values']['cached_input_tokens'] = None
                    usage['increments'][0]['values']['cached_input_tokens'] = None
            self.edit_record('direct', change)
            with self.subTest(mode=mode):
                self.assertIsNone(self.compare()['monetary_cost_comparison']['sides']['direct']['measured_token_cost'])

    def test_other_session_worker_or_origin_model_evidence_cannot_be_priced(self):
        for field in ('session_id', 'source', 'originator', 'task_id', 'dispatch_id'):
            self.price()
            def change(record):
                target = record['session'] if field in ('session_id', 'source', 'originator') else record['binding']
                target['id' if field == 'session_id' else field] = 'other'
            self.edit_record('direct', change)
            with self.subTest(field=field):
                result = self.compare()['monetary_cost_comparison']
                self.assertIsNone(result['routed_minus_direct_estimated_cost'])
                self.assertIsNone(result['sides']['direct']['measured_token_cost'])

    def test_boundary_or_after_usage_model_cannot_price_entire_interval(self):
        for mode in ('boundary', 'after_usage', 'assignment', 'wrong_assignment'):
            self.price()
            def change(record):
                usage = record['dispatch_usage']
                line = usage['interval']['start_line'] if mode == 'boundary' else 8 if mode == 'after_usage' else 4
                if mode == 'wrong_assignment': record['binding']['line'] = 5; return
                usage['interval']['end_line'] = 10
                record['binding']['context_line'] = line
                record['observed_contexts'][0]['line'] = line
            self.edit_record('direct', change)
            with self.subTest(mode=mode):
                result = self.compare()['monetary_cost_comparison']
                self.assertIsNone(result['routed_minus_direct_estimated_cost'])
                self.assertIsNone(result['sides']['direct']['measured_token_cost'])

    def test_malformed_or_unordered_observations_remain_unpriced(self):
        for rows in ([dict(line=3, values={'model': 'example-model'}), dict(line=3, values={'model': 'example-model'})],
                     [dict(line=5, values={'model': 'example-model'}), dict(line=3, values={'model': 'example-model'})], [None]):
            self.price(); self.edit_record('direct', lambda record: record.update(observed_contexts=rows))
            self.assertIsNone(self.compare()['monetary_cost_comparison']['routed_minus_direct_estimated_cost'])

    def test_rate_window_and_missing_models_suppress_full_cost_delta(self):
        for field, value in (('valid_from', '2026-02-01T00:00:00Z'), ('valid_until', '2025-12-31T00:00:00Z'),
                             ('models', {'other-model': self.rate['models']['example-model']})):
            old = self.rate[field]; self.rate[field] = value
            self.price()
            self.assertIsNone(self.compare()['monetary_cost_comparison']['routed_minus_direct_estimated_cost'])
            self.rate[field] = old

    def test_invalid_rates_currency_url_and_evidence_are_rejected(self):
        original = copy.deepcopy(self.rate)
        cases = [('price', '-1'), ('price', 'NaN'), ('price', '1e3'), ('price', 0.5), ('price', True),
                 ('currency', 'usd'), ('url', 'http://provider.example'), ('url', 'https://user:secret@provider.example')]
        for field, value in cases:
            self.rate = copy.deepcopy(original)
            if field == 'price': self.rate['models']['example-model']['output'] = value
            elif field == 'url': self.rate['source']['url'] = value
            else: self.rate[field] = value
            self.price()
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): self.compare()
        self.rate = original; self.price()
        self.files[self.rate['source']['evidence']['path']] += b' changed'
        with self.assertRaisesRegex(ValueError, 'hash_changed'): self.compare()

    def test_zero_rates_are_explicit_estimates_not_unknown_defaults(self):
        self.rate['models']['example-model'] = dict.fromkeys(('uncached_input', 'cached_input', 'output'), '0')
        self.price()
        result = self.compare()['monetary_cost_comparison']
        self.assertEqual(result['sides']['direct']['measured_token_cost'], '0')
        self.assertEqual(result['routed_minus_direct_estimated_cost'], '0')

    def test_partial_token_coverage_retains_known_estimate_but_no_full_delta(self):
        self.price(); self.manifest['routed']['coverage']['reporting'] = 'missing'
        result = self.compare()['monetary_cost_comparison']
        self.assertEqual(result['sides']['routed']['measured_token_cost'], '0.000155')
        self.assertIsNone(result['routed_minus_direct_estimated_cost'])

    def test_duplicate_costs_once_and_conflicting_model_observations_rejected(self):
        self.price(); self.manifest['direct']['measurements'] *= 2
        result = self.compare()['monetary_cost_comparison']
        self.assertEqual(result['sides']['direct']['measured_token_cost'], '0.000355')
        ref = self.manifest['direct']['measurements'][0]
        record = json.loads(self.files[ref['path']]); record['observed_contexts'][0]['values']['model'] = 'other-model'
        self.manifest['direct']['measurements'][1] = self.json_artifact('conflicting-model', record)
        with self.assertRaisesRegex(ValueError, 'conflicting_billing_model'): self.compare()

    def test_current_coordinator_records_remain_unpriced(self):
        self.price()
        def change(record):
            usage = record.pop('dispatch_usage')
            usage.update(version=2, scope='native_turn', role='coordinator', dispatch_id=None,
                         request_binding='explicit_native_turn', message_sha256='0' * 64)
            usage['interval']['binding_line'] = usage['interval'].pop('assignment_line')
            record.update(kind='orca_codex_coordinator_turn', turn_usage=usage)
        self.edit_record('direct', change)
        self.assertIsNone(self.compare()['monetary_cost_comparison']['sides']['direct']['measured_token_cost'])

    def test_actual_collector_shape_prices_only_bound_worker_observations(self):
        fixture = completed_samples.RoutingFinishTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.start(); completed = fixture.finish()
        self.price()
        self.rate['provider'] = 'codex'
        self.rate['models']['gpt-6.1-sol'] = copy.deepcopy(self.rate['models']['example-model'])
        self.manifest['rate_card'] = self.json_artifact('real-shape-card', self.rate)
        self.manifest['direct']['request_id'] = 'sample-request'
        self.manifest['direct']['measurements'] = [self.artifact('actual-collector', Path(completed['completion']['record_path']).read_bytes())]
        self.edit_record('routed', lambda record: record.update(provider='codex'))
        result = self.compare()['monetary_cost_comparison']
        self.assertEqual(result['status'], 'declared_rate_estimate')
        self.assertEqual(result['sides']['direct']['measured_token_cost'], '0.000295')

    def test_token_candidate_is_not_a_cost_or_automation_claim(self):
        self.rate['models']['expensive-model'] = dict(uncached_input='200', cached_input='50', output='1000')
        self.price()
        self.edit_record('routed', lambda r: r['observed_contexts'][0]['values'].update(model='expensive-model'))
        result = self.compare()
        self.assertEqual(result['decision'], 'candidate_for_repeated_evaluation')
        self.assertGreater(float(result['monetary_cost_comparison']['routed_minus_direct_estimated_cost']), 0)
        self.assertFalse(result['automation_allowed'])

    def test_metrics_do_not_call_runtime_network_or_modify_inputs(self):
        self.price(); self.timing('direct', '10'); self.timing('routed', '05')
        before = copy.deepcopy((self.manifest, self.files))
        with patch('subprocess.run', side_effect=AssertionError('runtime')), patch('socket.socket', side_effect=AssertionError('network')):
            self.assertEqual(self.compare()['monetary_cost_comparison']['status'], 'declared_rate_estimate')
        self.assertEqual((self.manifest, self.files), before)

    def test_v2_cli_private_artifacts_are_read_only(self):
        self.price(); self.timing('direct', '10'); self.timing('routed', '05')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(); project = root / 'project'; project.mkdir()
            private = root / 'private'; private.mkdir(mode=0o700)
            translated = {}
            for index, (old, raw) in enumerate(self.files.items()):
                path = private / str(index); path.write_bytes(raw); path.chmod(0o600); translated[old] = str(path)
            def rewrite(value):
                if isinstance(value, dict): return {k: translated.get(v, v) if k == 'path' else rewrite(v) for k, v in value.items()}
                if isinstance(value, list): return [rewrite(v) for v in value]
                return value
            # Nested timing/rate references need hashes of their rewritten bytes.
            for key in ('direct', 'routed'):
                timing = json.loads(self.files[self.manifest[key]['timing']['path']])
                path = Path(translated[self.manifest[key]['timing']['path']]); path.write_text(json.dumps(rewrite(timing)))
                self.manifest[key]['timing']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            path = Path(translated[self.manifest['rate_card']['path']]); path.write_text(json.dumps(rewrite(self.rate)))
            self.manifest['rate_card']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest = private / 'comparison.json'; save(manifest, rewrite(self.manifest), project)
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in private.iterdir()}
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/compare_routing.py'), '--project', str(project), '--manifest', str(manifest)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['elapsed_time_comparison']['routed_minus_direct_seconds'], '-5')
            self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in private.iterdir()}, before)


if __name__ == '__main__': unittest.main()
