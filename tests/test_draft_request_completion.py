"""Synthetic-only draft validation; native subprocess execution is forbidden."""
import copy
from contextlib import redirect_stdout, redirect_stderr
import io
import runpy
import sqlite3
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import draft_request_completion as draft
import complete_request as batch
import test_complete_request_integration as integration


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.fixture = integration.IntegratedBatchTests('test_n_retains_original_and_creates_no_cleanup_plan')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        Path(f.config['directory']).mkdir(mode=0o700)
        f.plan.update(action='start', model='example-model', effort='low')
        f.operation['receipt'] = dict(ok=True, result=dict(taskId=f.operation['task_id'], dispatchId=f.operation['dispatch_id'], runId=f.operation['run_id']))
        f.write_ledger()
        self.input = f.private / 'draft-input.json'; self.output = f.root / 'draft.json'
        self.data = dict(schema_version=1, id='draft-1', hook_config=str(f.config_path), request_id='sample-request',
                         expected_reviewer='auto_review', resource_directories=[str(f.registry)], acceptance_file=str(f.acceptance),
                         closeout_file=str(f.close_path), coordinator_turns=f.m['coordinator_turns'], retry_pending=False)
        self.write()

    def write(self): self.fixture.save(self.input, self.data)
    def build(self): return draft.build(self.input, self.output)
    def bad(self):
        with self.assertRaises((ValueError, OSError, KeyError, TypeError)): self.build()
        self.assertFalse(self.output.exists())

    def test_real_fixture_prepare_and_readonly_replay(self):
        f = self.fixture
        files = [self.input, f.config_path, Path(f.config['ledger']), f.db, f.source, f.coord]
        before = {p:p.read_bytes() for p in files}
        with patch.object(draft.turns, 'read_session', side_effect=AssertionError('source forbidden')):
            result = self.build()
        self.assertEqual(result['status'], 'draft'); self.assertFalse(result['execution_authorized'])
        self.assertEqual(set(json.loads(self.output.read_text())), batch.FIELDS)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        stamp = self.output.stat().st_mtime_ns
        self.assertTrue(self.build()['replay']); self.assertEqual(stamp,self.output.stat().st_mtime_ns)
        self.assertEqual(before, {p:p.read_bytes() for p in files})
        batch.prepare(self.output, f.private/'batch-output', None, None)

    def cli_entrypoint(self):
        """Run the actual __main__ path without permitting any subprocess."""
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = [str(Path(draft.__file__)), '--input', str(self.input), '--output', str(self.output)]
        with patch.object(sys, 'argv', argv), redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(draft.__file__, run_name='__main__')
        return stopped.exception.code, stdout.getvalue(), stderr.getvalue()

    def assert_cli_rejection_unchanged(self):
        f = self.fixture
        files = [self.input, f.config_path, Path(f.config['ledger']), f.db, f.source, f.coord,
                 f.close_path, f.acceptance, f.coord_binding, f.registry / 'output.resource.json']
        before = {p: p.read_bytes() for p in files}
        with patch.object(draft.turns, 'read_session', side_effect=AssertionError('source forbidden')):
            code, stdout, stderr = self.cli_entrypoint()
        self.assertEqual(code, 2)
        self.assertEqual(stderr, '')
        self.assertEqual(len(stdout.splitlines()), 1)
        self.assertEqual(json.loads(stdout), dict(status='error', error='draft_validation_failed'))
        self.assertLess(len(stdout), 100)
        self.assertFalse(self.output.exists())
        self.assertEqual(before, {p: p.read_bytes() for p in files})
        self.assertEqual(f.calls, [])

    def test_cli_rejects_nonobject_hook_config(self):
        self.fixture.save(self.fixture.config_path, [])
        with self.assertRaisesRegex(ValueError, 'draft_invalid_hook_config'): self.build()
        self.assert_cli_rejection_unchanged()

    def test_cli_rejects_nonobject_closeout(self):
        self.fixture.save(self.fixture.close_path, [])
        with self.assertRaisesRegex(ValueError, 'draft_invalid_closeout'): self.build()
        self.assert_cli_rejection_unchanged()

    def test_cli_rejects_nonobject_resource_registrations(self):
        filename = self.fixture.registry / 'output.resource.json'
        for value in ([], None, 1, 'invalid registration'):
            with self.subTest(value=value):
                self.fixture.save(filename, value)
                with self.assertRaisesRegex(ValueError, 'draft_invalid_resource_registration'):
                    self.build()
                self.assert_cli_rejection_unchanged()

    def test_cli_rejects_malformed_artifacts(self):
        f = self.fixture
        for artifacts in ([1], {'bad': 1}):
            with self.subTest(artifacts=artifacts):
                f.close['artifacts'] = artifacts; f.save(f.close_path, f.close)
                with self.assertRaisesRegex(ValueError, 'draft_invalid_artifacts'): self.build()
                self.assert_cli_rejection_unchanged()

    def test_cli_rejects_corrupt_sqlite_preserving_api_error(self):
        f = self.fixture
        f.store.close(); f.db.write_bytes(b'not sqlite')
        with self.assertRaises(sqlite3.DatabaseError): self.build()
        self.assert_cli_rejection_unchanged()

    def test_cli_normal_generation_replay_and_prepare(self):
        code, stdout, stderr = self.cli_entrypoint()
        self.assertEqual((code, stderr), (0, ''))
        result = json.loads(stdout)
        self.assertEqual(result['status'], 'draft')
        self.assertTrue(result['requires_review']); self.assertFalse(result['execution_authorized'])
        self.assertFalse(result['replay'])
        raw = self.output.read_bytes(); stamp = self.output.stat().st_mtime_ns
        code, stdout, stderr = self.cli_entrypoint()
        self.assertEqual((code, stderr), (0, '')); self.assertTrue(json.loads(stdout)['replay'])
        self.assertEqual(self.output.read_bytes(), raw); self.assertEqual(self.output.stat().st_mtime_ns, stamp)
        batch.prepare(self.output, self.fixture.private / 'cli-batch-output', None, None)
        self.assertEqual(self.fixture.calls, [])

    def followup(self):
        f=self.fixture; old=copy.deepcopy(f.operation);old['state']='released'
        f.ledger['operations']['origin']=old
        f.plan.update(action='followup', previous='origin', previous_task=old['task_id'],previous_dispatch=old['dispatch_id'],previous_run=old['run_id'])
        f.operation.update(task_id='task-new', dispatch_id='dispatch-new', run_id='run-new')
        f.operation['receipt']=dict(ok=True,result=dict(taskId='task-new',dispatchId='dispatch-new',runId='run-new'))
        del f.plan['model'];del f.plan['effort'];f.write_ledger()

    def test_followup_inherits_origin(self):
        self.followup();result=self.build()
        self.assertEqual(result['profile_origins']['example'],dict(model='origin',effort='origin'))
        self.assertEqual(json.loads(self.output.read_text())['operations'][0]['expected']['model'],'example-model')

    def test_followup_conflict(self):
        self.followup();self.fixture.plan['model']='conflict';self.fixture.write_ledger();self.bad()

    def test_missing_start_and_retry_profile(self):
        for action in ('start','retry'):
            self.fixture.plan['action']=action;self.fixture.plan.pop('model',None);self.fixture.write_ledger();self.bad()

    def test_lineage_binding_and_cycle(self):
        self.followup();f=self.fixture
        f.plan['previous_run']='wrong';f.write_ledger();self.bad()
        f.plan['previous_run']='run_example'
        f.ledger['operations']['origin']['plan'].update(action='followup',previous='example', previous_task='task-new',previous_dispatch='dispatch-new',previous_run='run-new')
        f.operation['state']='released';f.write_ledger();self.bad()

    def test_cross_request_predecessor(self):
        self.followup();self.fixture.ledger['operations']['origin']['request_id']='other';self.fixture.write_ledger();self.bad()

    def test_wrong_role_native_and_caller(self):
        f=self.fixture;f.plan['usage_collection']['role']='frontend';f.write_ledger();self.bad()
        f.plan['usage_collection']['role']='backend';f.operation['receipt']['result']['dispatchId']='wrong';f.write_ledger();self.bad()
        with patch.dict(os.environ,ORCA_AGENT_SESSION_ID='wrong'):self.bad()

    def test_json_boundaries(self):
        for raw in ('{"id":1,"id":2}', '{"x":NaN}', '['*66+'0'+']'*66):
            self.input.write_text(raw);self.bad()

    def test_conflict_preserves_output(self):
        self.build();raw=self.output.read_bytes();self.data['id']='changed';self.write()
        with self.assertRaises(ValueError):self.build()
        self.assertEqual(raw,self.output.read_bytes())

    def test_output_scope_and_hardlink(self):
        self.output=self.fixture.project/'bad.json';self.bad()
        self.output=self.fixture.root/'alias.json';os.link(self.input,self.output)
        with self.assertRaises(ValueError):self.build()

    def test_missing_directory_not_created(self):
        p=self.fixture.root/'missing';self.fixture.config['directory']=str(p);self.fixture.save(self.fixture.config_path,self.fixture.config)
        self.bad();self.assertFalse(p.exists())

    def test_resource_phases_and_malformed(self):
        p=self.fixture.registry/'output.resource.json';r=json.loads(p.read_text())
        r['phase']='archiving';self.fixture.save(p,r);self.bad()
        r['phase']='removed';self.fixture.save(p,r);self.build()

    def test_resource_duplicate_and_limit(self):
        self.data['resource_directories']*=2;self.write();self.bad()
        self.data['resource_directories']=[str(self.fixture.registry)];self.write()
        for i in range(100):self.fixture.save(self.fixture.registry/f'x{i}.resource.json',{})
        self.bad()

    def test_empty_resources_allowed(self):
        self.data['resource_directories']=[];self.write();self.build()
        self.assertEqual(json.loads(self.output.read_text())['resources'],[])

    def test_assignment_duplicates_and_turn_scope(self):
        f=self.fixture;f.close['assignments']*=2;f.save(f.close_path,f.close);self.bad()
        f.close['assignments']=f.close['assignments'][:1];f.save(f.close_path,f.close)
        binding=json.loads(f.coord_binding.read_text());binding['actor_id']='wrong';f.save(f.coord_binding,binding);self.bad()


if __name__ == '__main__':unittest.main()
