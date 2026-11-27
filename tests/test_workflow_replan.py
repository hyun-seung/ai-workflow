import copy, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

common=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(common/'scripts'))
import dispatch_guard as dg
import development_workflow as dw

class ReplanTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name).resolve(); self.project=root/'project';self.project.mkdir()
        self.ledger=dg.Ledger(root/'private',self.project,'coordinator_test')
        self.before={'version':1,'coordinator':'coordinator_test',
          'requests':{'request_test':{'max_active_workers':1,'max_dispatches_per_request':8}},
          'operations':{f'op_{i}':{'request_id':'request_test','state':'released','outcome':'succeeded','task_id':f'task_{i}'} for i in range(6)}}
        self.ledger.write(self.before)
    def replan(self,**kwargs):
        args=dict(request_id='request_test',revision_id='revision_1',expected=8,proposed=9,approval_ref='explicit user continuation; local decision record')
        args.update(kwargs);return self.ledger.replan(**args)
    def test_preserves_operations_counts_and_old_limits(self):
        r=self.replan();after=self.ledger.read()
        self.assertEqual(after['operations'],self.before['operations'])
        self.assertEqual(r['spent_dispatches'],6)
        self.assertEqual(r['previous_limits'],self.before['requests']['request_test'])
        self.assertEqual(after['requests']['request_test'],{'max_active_workers':1,'max_dispatches_per_request':9})
        self.assertEqual(r['operations_digest'],dg.digest(self.before['operations']))
    def test_replay_is_identical_and_conflict_does_not_write(self):
        self.replan();before=self.ledger.path.read_bytes()
        self.assertTrue(self.replan()['replay']);self.assertEqual(before,self.ledger.path.read_bytes())
        with self.assertRaisesRegex(ValueError,'revision_conflict'):self.replan(approval_ref='changed')
        self.assertEqual(before,self.ledger.path.read_bytes())
    def test_stale_concurrent_revision_rejected(self):
        self.replan()
        with self.assertRaisesRegex(ValueError,'stale'):self.replan(revision_id='revision_2')
        self.assertEqual(len(self.ledger.read()['limit_revisions']),1)
    def test_inflight_unknown_and_nonterminal_never_expand(self):
        for state in ['bound','unknown','launching','not_started','unexpected']:
            data=copy.deepcopy(self.before);data['operations']['op_0']['state']=state;self.ledger.write(data)
            before=self.ledger.path.read_bytes()
            with self.subTest(state=state),self.assertRaisesRegex(ValueError,'quiescence'):self.replan()
            self.assertEqual(before,self.ledger.path.read_bytes())
    def test_invalid_limits_or_missing_approval_fail_without_write(self):
        for args in [dict(proposed=8),dict(proposed=7),dict(proposed=True),dict(expected=True),dict(approval_ref=''),dict(approval_ref='x'*4097),dict(request_id='unknown')]:
            before=self.ledger.path.read_bytes()
            with self.subTest(args=args),self.assertRaises(ValueError):self.replan(**args)
            self.assertEqual(before,self.ledger.path.read_bytes())
    def test_spent_count_still_stops_at_new_cap(self):
        self.replan();data=self.ledger.read()
        for i in range(6,9):data['operations'][f'op_{i}']={'request_id':'request_test','state':'released'}
        self.ledger.write(data)
        with self.assertRaisesRegex(dg.LimitReached,'max_dispatches'):
            self.ledger.reserve('request_test','extra',{},data['requests']['request_test'])
        self.assertEqual(len(self.ledger.read()['operations']),9)
    def test_policy_boundary_is_nine_not_unbounded(self):
        base=dict(version=1,enabled=True,max_dispatches=8,max_fix_rounds=1)
        self.assertEqual(dw.policy(base),base)
        self.assertEqual(dw.policy(dict(base,max_dispatches=9))['max_dispatches'],9)
        for value in [10,100,True,1]:
            with self.assertRaisesRegex(ValueError,'invalid_policy'):dw.policy(dict(base,max_dispatches=value))

if __name__=='__main__': unittest.main()
