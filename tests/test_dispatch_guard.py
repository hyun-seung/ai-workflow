import copy
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import dispatch_guard as dg
import finish_worker as fw
import harness_policy as hp
import workspace_cleanup as wc
from test_lifecycle import snapshot


def plan(project,**changes):
    value=dict(action='start',project=str(project),worktree='current',agent='codex',model=None,effort=None,
               spec_digest='example',writes=[],depends_on=[],previous=None,subject='test')
    value.update(changes)
    return value


def receipt(number=1):
    return {'ok':True,'result':{'taskId':'task_'+str(number),'dispatchId':'ctx_'+str(number),'runId':'run_example','state':'ready'}}


def reserve_race(directory,project,number,gate,output):
    ledger=dg.Ledger(directory,project,'coordinator_example')
    gate.wait(5)
    try:
        ledger.reserve('request_example','op_'+str(number),plan(project),{'max_active_workers':1,'max_dispatches_per_request':4})
        output.put('reserved')
    except (ValueError,OSError):output.put('stopped')


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name).resolve()

    def test_default_cleanup_y_and_unset_limits(self):
        p=hp.load(self.project)
        self.assertEqual(p['auto_cleanup'],'Y')
        self.assertEqual(p['limits'],{'max_active_workers':None,'max_dispatches_per_request':None})

    def test_project_then_explicit_then_cli_precedence(self):
        (self.project/'.ai-workflow-harness.json').write_text(json.dumps({'auto_cleanup':'N','limits':{'max_active_workers':3}}))
        local=self.project/'explicit.json';local.write_text(json.dumps({'auto_cleanup':'Y','limits':{'max_dispatches_per_request':8}}))
        p=hp.load(self.project,local,'N',{'max_active_workers':4})
        self.assertEqual(p['auto_cleanup'],'N')
        self.assertEqual(p['limits'],{'max_active_workers':4,'max_dispatches_per_request':8})

    def test_invalid_policy_cannot_silently_enable_cleanup_or_unlimit(self):
        for value in ({'auto_cleanup':True},{'auto_cleanup':'yes'},{'unknown':1},{'limits':{'max_active_workers':True}},
                      {'limits':{'max_active_workers':0}},{'limits':{'max_disptaches':3}},{'version':True}):
            with self.subTest(value=value),self.assertRaises(ValueError):hp.validate(value)

    def test_missing_explicit_file_and_symlink_fail(self):
        with self.assertRaises(ValueError):hp.load(self.project,self.project/'missing')
        (self.project/'.ai-workflow-harness.json').symlink_to(self.project/'missing')
        with self.assertRaises(ValueError):hp.load(self.project)

    def test_missing_or_file_policy_project_never_defaults_to_y(self):
        (self.project/'.ai-workflow-harness.json').write_text('{"auto_cleanup":"N"}')
        file=self.project/'not-a-directory';file.write_text('fixture')
        for target in (self.project/'projet',file):
            with self.subTest(target=target),patch.object(wc,'apply') as apply:
                self.assertEqual(wc.main(['apply','--plan','preauthorized.json','--policy-project',str(target),'--orca-cli','unused']),2)
                apply.assert_not_called()
            with self.assertRaisesRegex(ValueError,'project_not_directory'):hp.load(target,auto_cleanup='Y')

    def test_finish_n_records_but_never_reads_or_applies_plan(self):
        args=['--project',str(self.project),'--session-file','example','--session-id','session_example',
              '--task-id','task_example','--dispatch-id','ctx_example','--directory',str(self.project/'records'),
              '--auto-cleanup','N','--cleanup-plan','nonexistent','--orca-cli','example']
        with patch.object(fw,'finish',return_value={'replay':False,'verification':'pass'}) as finish,patch.object(wc,'apply') as apply:
            self.assertEqual(fw.main(args),0)
            finish.assert_called_once();apply.assert_not_called()

    def test_cleanup_apply_n_does_not_touch_missing_plan(self):
        with patch.object(wc,'apply') as apply:
            self.assertEqual(wc.main(['apply','--plan','missing','--policy-project',str(self.project),'--auto-cleanup','N']),0)
            apply.assert_not_called()


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.project=self.root/'project';self.project.mkdir()
        self.directory=self.root/'private'
        self.ledger=dg.Ledger(self.directory,self.project,'coordinator_example')
        self.limits={'max_active_workers':2,'max_dispatches_per_request':4}

    def launch(self,operation='op_1',request='request_example',**changes):
        return dg.launch(self.ledger,request,operation,plan(self.project,**changes),'task',self.limits,'example')

    def release(self,operation='op_1',outcome='succeeded'):
        op=self.ledger.read()['operations'][operation]
        s=snapshot();s['result']['dispatch'].update(id=op['dispatch_id'],taskId=op['task_id'])
        s['result']['worker'].update(dispatchId=op['dispatch_id'],state=outcome)
        with patch.object(dg,'orca',return_value=s):return self.ledger.refresh(operation,'example')

    def test_caps_1_2_3_before_any_extra_native_call(self):
        for cap in (1,2,3):
            self.ledger=dg.Ledger(self.root/('private'+str(cap)),self.project,'coordinator_example')
            self.limits['max_active_workers']=cap
            with patch.object(dg,'invoke',side_effect=[(receipt(i),0) for i in range(cap)]) as invoke:
                for i in range(cap):self.launch('op_'+str(i))
                with self.assertRaisesRegex(dg.LimitReached,'max_active'):self.launch('extra')
                self.assertEqual(invoke.call_count,cap)

    def test_total_cap_survives_release_and_restart(self):
        self.limits['max_dispatches_per_request']=1
        with patch.object(dg,'invoke',return_value=(receipt(),0)):self.launch()
        self.release();self.ledger=dg.Ledger(self.directory,self.project,'coordinator_example')
        with patch.object(dg,'invoke') as invoke,self.assertRaisesRegex(dg.LimitReached,'max_dispatches'):
            self.launch('op_2')
        invoke.assert_not_called()

    def test_identical_operation_is_replay_binding_and_limits_changes_rejected(self):
        with patch.object(dg,'invoke',return_value=(receipt(),0)) as invoke:
            self.launch();self.assertTrue(self.launch()['replay']);self.assertEqual(invoke.call_count,1)
        with self.assertRaisesRegex(ValueError,'binding_conflict'):self.launch(spec_digest='changed')
        self.limits['max_active_workers']=3
        with self.assertRaisesRegex(ValueError,'limits_changed'):self.launch('op_2')

    def test_timeout_unknown_and_lost_receipt_do_not_retry_or_free(self):
        self.limits['max_active_workers']=1
        with patch.object(dg,'invoke',side_effect=subprocess.TimeoutExpired('example',1)) as invoke:
            self.assertEqual(self.launch()['state'],'unknown')
            self.assertTrue(self.launch()['replay']);self.assertEqual(invoke.call_count,1)
        with self.assertRaises(dg.LimitReached):self.launch('op_2')
        with self.assertRaisesRegex(ValueError,'receipt_inspection'):self.ledger.refresh('op_1','example')

    def test_crash_after_reservation_keeps_slot(self):
        self.ledger.reserve('request_example','op_1',plan(self.project),self.limits)
        with patch.object(dg,'invoke') as invoke:
            self.assertEqual(self.launch()['state'],'launching');invoke.assert_not_called()

    def test_exact_sandbox_denial_can_repeat_same_operation_without_double_count(self):
        with patch.object(dg,'invoke',side_effect=[({'ok':False,'error':{'code':'runtime_access_denied'}},1),(receipt(),0)]) as invoke:
            self.assertEqual(self.launch()['state'],'not_started')
            self.assertEqual(self.launch()['state'],'bound')
        self.assertEqual(invoke.call_count,2)
        self.assertEqual(len(self.ledger.read()['operations']),1)

    def test_conflicting_denial_receipts_never_allow_second_native_call(self):
        values=[(dict(receipt(),error={'code':'runtime_access_denied'}),0),
                ({'ok':False,'error':{'code':'runtime_access_denied'}},0),
                ({'ok':False,'result':receipt()['result'],'error':{'code':'runtime_access_denied'}},1),
                ({'ok':True,'error':{'code':'runtime_access_denied'}},1)]
        for i,(value,code) in enumerate(values):
            self.ledger=dg.Ledger(self.root/('case'+str(i)),self.project,'coordinator_example')
            with patch.object(dg,'invoke',return_value=(value,code)) as invoke:
                self.assertEqual(self.launch()['state'],'unknown')
                self.assertTrue(self.launch()['replay']);self.assertEqual(invoke.call_count,1)

    def test_legacy_inconsistent_not_started_is_not_retried(self):
        self.ledger.reserve('request_example','op_1',plan(self.project),self.limits)
        data=self.ledger.read();data['operations']['op_1'].update(state='not_started',receipt=receipt(),returncode=0)
        self.ledger.write(data)
        with patch.object(dg,'invoke') as invoke:
            self.assertEqual(self.launch()['state'],'unknown');invoke.assert_not_called()

    def test_waits_and_completed_live_remain_active(self):
        self.limits['max_active_workers']=1
        with patch.object(dg,'invoke',return_value=(receipt(),0)):self.launch()
        for observation in ({'exactWorker':True,'status':'running','agentWait':{'kind':'approval'}},{'exactWorker':True,'status':'idle'},{}):
            s=snapshot();s['result']['dispatch'].update(id='ctx_1',taskId='task_1');s['result']['worker']['dispatchId']='ctx_1';s['result']['observation']=observation
            with patch.object(dg,'orca',return_value=s):self.assertEqual(self.ledger.refresh('op_1','example')['state'],'bound')
            with self.assertRaises(dg.LimitReached):self.launch('op_2')

    def test_foreign_worker_cannot_release_slot(self):
        with patch.object(dg,'invoke',return_value=(receipt(),0)):self.launch()
        with patch.object(dg,'orca',return_value=snapshot()),self.assertRaisesRegex(ValueError,'identity_mismatch'):
            self.ledger.refresh('op_1','example')
        self.assertEqual(self.ledger.read()['operations']['op_1']['state'],'bound')

    def test_same_file_owner_then_dependency_release_allows_independent_work(self):
        path=str(self.project/'shared')
        with patch.object(dg,'invoke',return_value=(receipt(),0)):self.launch(writes=[path])
        with self.assertRaisesRegex(dg.LimitReached,'write_scope'):self.launch('op_2',writes=[path+'/child'])
        with self.assertRaisesRegex(dg.LimitReached,'dependency'):self.launch('op_2',depends_on=['op_1'])
        self.release()
        with patch.object(dg,'invoke',return_value=(receipt(2),0)):self.launch('op_2',depends_on=['op_1'],writes=[path])

    def test_failed_dependency_not_treated_as_ready(self):
        with patch.object(dg,'invoke',return_value=(receipt(),0)):self.launch()
        self.release(outcome='failed')
        with self.assertRaisesRegex(dg.LimitReached,'dependency'):self.launch('op_2',depends_on=['op_1'])

    def test_followup_and_retry_consume_new_budget_after_exit(self):
        with patch.object(dg,'invoke',return_value=(receipt(),0)):self.launch()
        self.release(outcome='failed')
        retried=receipt(2);retried['result']['taskId']='task_1'
        with patch.object(dg,'invoke',return_value=(retried,0)):
            self.launch('op_2',action='retry',previous='op_1',previous_dispatch='ctx_1',previous_task='task_1',previous_run='run_example')
        self.release('op_2')
        with patch.object(dg,'invoke',return_value=(receipt(3),0)):
            self.launch('op_3',action='followup',previous='op_2',previous_dispatch='ctx_2')
        self.assertEqual(len(self.ledger.read()['operations']),3)

    def test_cancelled_retry_requires_exact_exit_and_preserves_same_task(self):
        with patch.object(dg,'invoke',return_value=(receipt(),0)):self.launch()
        retry=dict(action='retry',previous='op_1',previous_dispatch='ctx_1',previous_task='task_1',previous_run='run_example')
        with patch.object(dg,'invoke') as invoke,self.assertRaisesRegex(dg.LimitReached,'predecessor_exit'):
            self.launch('op_2',**retry)
        invoke.assert_not_called()
        self.release(outcome='cancelled')
        value=receipt(2);value['result']['taskId']='task_1'
        with patch.object(dg,'invoke',return_value=(value,0)):
            result=self.launch('op_2',**retry)
        self.assertEqual(result['task_id'],'task_1');self.assertEqual(result['dispatch_id'],'ctx_2')
        self.assertEqual(len(self.ledger.read()['operations']),2)

    def test_retry_wrong_task_or_run_receipt_stays_unknown_and_cannot_relaunch(self):
        for field,value in [('taskId','another_task'),('runId','another_run')]:
            with self.subTest(field=field):
                self.ledger=dg.Ledger(self.root/field,self.project,'coordinator_example')
                with patch.object(dg,'invoke',return_value=(receipt(),0)):self.launch()
                self.release(outcome='cancelled')
                wrong=receipt(2);wrong['result']['taskId']='task_1';wrong['result'][field]=value
                retry=dict(action='retry',previous='op_1',previous_dispatch='ctx_1',previous_task='task_1',previous_run='run_example')
                with patch.object(dg,'invoke',return_value=(wrong,0)),self.assertRaisesRegex(ValueError,'retry_native_binding_mismatch'):
                    self.launch('op_2',**retry)
                old=self.ledger.read()['operations']['op_2'];self.assertEqual(old['state'],'unknown');self.assertIsNone(old['task_id'])
                with patch.object(dg,'invoke') as invoke:
                    result=self.launch('op_2',**retry)
                self.assertTrue(result['replay']);invoke.assert_not_called()

    def test_native_stopped_retry_releases_only_after_exact_exit(self):
        with patch.object(dg,'invoke',return_value=(receipt(),0)):self.launch()
        s=snapshot();s['result']['dispatch'].update(id='ctx_1',taskId='task_1',status='failed',lastFailure='stopped')
        s['result']['worker'].update(dispatchId='ctx_1',state='stopped',stage='process_stopped')
        pending=copy.deepcopy(s);pending['result']['observation']['status']='unverifiable'
        with patch.object(dg,'orca',return_value=pending):
            self.assertEqual(self.ledger.refresh('op_1','example')['state'],'bound')
        with patch.object(dg,'orca',return_value=s):
            released=self.ledger.refresh('op_1','example')
        self.assertEqual((released['state'],released['outcome']),('released','stopped'))
        value=receipt(2);value['result']['taskId']='task_1'
        with patch.object(dg,'invoke',return_value=(value,0)):
            retried=self.launch('op_2',action='retry',previous='op_1',previous_dispatch='ctx_1',previous_task='task_1',previous_run='run_example')
        self.assertEqual(retried['task_id'],'task_1')

    def test_successful_predecessor_cannot_use_retry(self):
        with patch.object(dg,'invoke',return_value=(receipt(),0)):self.launch()
        self.release()
        with patch.object(dg,'invoke') as invoke,self.assertRaisesRegex(ValueError,'failed_or_cancelled'):
            self.launch('op_2',action='retry',previous='op_1',previous_dispatch='ctx_1',previous_task='task_1',previous_run='run_example')
        invoke.assert_not_called()

    def test_retry_invokes_original_native_task_without_sending_a_new_spec(self):
        p=plan(self.project,action='retry',previous_task='task_1',previous_dispatch='ctx_1',previous_run='run_example')
        value=receipt(2);value['result']['taskId']='task_1'
        with patch.object(dg.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(value),'')) as run:
            dg.invoke('official-orca',p,'unused spec')
        args=run.call_args.args[0]
        self.assertEqual(args[args.index('--task')+1],'task_1');self.assertEqual(args[args.index('--retry-of')+1],'ctx_1')
        self.assertEqual(args[args.index('--run')+1],'run_example');self.assertNotIn('--spec-file',args)
        self.assertIsNone(run.call_args.kwargs['input'])

    def test_active_predecessor_cannot_be_reassigned(self):
        with patch.object(dg,'invoke',return_value=(receipt(),0)):self.launch()
        with self.assertRaisesRegex(dg.LimitReached,'predecessor_exit'):self.launch('op_2',action='followup',previous='op_1',previous_dispatch='ctx_1')

    def test_unknown_followup_owns_target_and_blocks_another_followup_or_retry(self):
        self.limits['max_active_workers']=3
        with patch.object(dg,'invoke',return_value=(receipt(),0)):self.launch()
        self.release(outcome='failed')
        with patch.object(dg,'invoke',side_effect=subprocess.TimeoutExpired('example',1)):
            self.launch('op_2',action='followup',previous='op_1',previous_dispatch='ctx_1')
        for action in ('followup','retry'):
            with patch.object(dg,'invoke') as invoke,self.assertRaisesRegex(dg.LimitReached,'lineage_already_owned'):
                self.launch('op_3',action=action,previous='op_1',previous_dispatch='ctx_1')
            invoke.assert_not_called()

    def test_older_ancestor_cannot_bypass_active_descendant(self):
        self.limits['max_active_workers']=3
        with patch.object(dg,'invoke',return_value=(receipt(),0)):self.launch()
        self.release()
        with patch.object(dg,'invoke',return_value=(receipt(2),0)):
            self.launch('op_2',action='followup',previous='op_1',previous_dispatch='ctx_1')
        self.release('op_2')
        with patch.object(dg,'invoke',return_value=(receipt(3),0)):
            self.launch('op_3',action='followup',previous='op_2',previous_dispatch='ctx_2')
        with self.assertRaisesRegex(dg.LimitReached,'lineage_already_owned'):
            self.launch('op_4',action='followup',previous='op_1',previous_dispatch='ctx_1')

    def test_new_request_cannot_raise_other_active_request_cap(self):
        self.limits['max_active_workers']=1
        with patch.object(dg,'invoke',return_value=(receipt(),0)):self.launch()
        self.limits['max_active_workers']=3
        with self.assertRaises(dg.LimitReached):self.launch('op_2',request='another_request')

    def test_coordinator_scope_change_rejected(self):
        self.ledger.reserve('request_example','op_1',plan(self.project),self.limits)
        with self.assertRaisesRegex(ValueError,'scope_mismatch'):
            dg.Ledger(self.directory,self.project,'other_coordinator').read()

    def test_missing_limits_never_launch(self):
        self.limits['max_active_workers']=None
        with patch.object(dg,'invoke') as invoke,self.assertRaisesRegex(ValueError,'explicit_positive'):
            self.launch()
        invoke.assert_not_called()

    def test_multiprocess_reservation_does_not_exceed_one_slot(self):
        ctx=multiprocessing.get_context('fork');gate=ctx.Event();output=ctx.Queue()
        procs=[ctx.Process(target=reserve_race,args=(str(self.directory),str(self.project),i,gate,output)) for i in range(4)]
        for p in procs:p.start()
        gate.set()
        for p in procs:p.join(10);self.assertEqual(p.exitcode,0)
        results=[output.get(timeout=2) for _ in procs]
        self.assertEqual(results.count('reserved'),1)
        self.assertEqual(len(self.ledger.read()['operations']),1)


if __name__=='__main__':unittest.main()
