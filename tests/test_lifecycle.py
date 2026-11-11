import copy
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import finish_worker as fw
import lifecycle_io as io
import workspace_cleanup as wc
from test_native_record import event, context, assigned, usage


def snapshot():
    return {'ok': True, 'result': {
        'dispatch': {'id': 'ctx_example', 'taskId': 'task_example', 'status': 'completed',
                     'hostScope': {'kind': 'local'}, 'processIncarnation': 'structured:example'},
        'worker': {'dispatchId': 'ctx_example', 'state': 'succeeded',
                   'startOptions': {'launch': {'requested': {'agent': 'codex', 'model': None, 'effort': None}}}},
        'observation': {'exactWorker': True, 'status': 'exited'}}}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.allowed = self.root / 'scratch'
        self.allowed.mkdir()
        self.source = self.allowed / 'experiment'
        self.source.mkdir()
        (self.source / 'result.txt').write_text('measured result')
        self.archive = self.root / 'archive'
        shutil.copytree(self.source, self.archive)
        self.acceptance = self.root / 'acceptance.txt'
        self.acceptance.write_text('Coordinator accepted the result and archive.')
        self.plan = self.root / 'private' / 'cleanup.json'
        self.workers = [{'task_id': 'task_example', 'dispatch_id': 'ctx_example'}]
        self.registered = True
        self.calls = []
        self.setup = {'id': 'setup_example', 'path': str(self.source), 'kind': 'folder', 'hostId': 'local', 'repoId': 'repo_example'}
        self.row = {'repoId': 'repo_example', 'path': str(self.source), 'hostId': 'local', 'agents': [],
                    'liveTerminalCount': 0, 'unverifiableTerminalCount': 0, 'hasAttachedPty': False, 'childWorktreeIds': []}

    def prepare(self, **changes):
        args = dict(source=self.source, archive=self.archive, allowed_root=self.allowed, output=self.plan,
                    authorization='User requested retirement of this temporary folder after delivery.',
                    acceptance=self.acceptance, workers=self.workers, setup_id='setup_example')
        args.update(changes)
        return wc.prepare(**args)

    def orca(self, cli, *args):
        self.calls.append(args)
        if args[:2] == ('orchestration', 'worker-show'):
            return snapshot()
        if args[:2] == ('project', 'setups'):
            return {'result': {'setups': [self.setup] if self.registered else []}}
        if args[:2] == ('worktree', 'ps'):
            return {'result': {'truncated': False, 'hostScope': {'omittedHostIds': []}, 'worktrees': [self.row] if self.registered else []}}
        if args[:2] == ('project', 'setup-delete'):
            self.assertEqual(args[2:], ('--setup', 'setup_example', '--host', 'local', '--force'))
            self.registered = False
            return {'result': {}}
        raise AssertionError(args)

    def apply(self):
        with patch.object(wc, 'orca', self.orca), patch.object(wc, 'no_open_files'):
            return wc.apply(self.plan, 'orca-example')

    def test_prepare_never_removes_and_private_files(self):
        self.prepare()
        self.assertTrue(self.source.exists())
        self.assertEqual(stat.S_IMODE(self.plan.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.plan.parent.stat().st_mode), 0o700)

    def test_archive_and_idle_checks_before_removal_replay_is_safe(self):
        self.prepare()
        self.assertEqual(self.apply()['phase'], 'removed')
        self.assertFalse(self.source.exists())
        self.assertEqual((self.archive/'result.txt').read_text(), 'measured result')
        count = len(self.calls)
        self.assertEqual(self.apply()['phase'], 'removed')
        self.assertEqual(len(self.calls), count)

    def test_missing_archive_blocks_prepare(self):
        (self.archive/'result.txt').unlink()
        with self.assertRaisesRegex(ValueError, 'archive_missing'):
            self.prepare()
        self.assertFalse(self.plan.exists())

    def test_explicit_cache_discard_never_skips_source_change_check(self):
        (self.source/'cache').mkdir()
        (self.source/'cache'/'unused').write_text('download')
        self.prepare(discard=['cache'])
        (self.source/'cache'/'new').write_text('new file')
        with self.assertRaisesRegex(ValueError, 'source_changed'):
            self.apply()
        self.assertTrue(self.source.exists())

    def test_new_source_or_archive_edits_halt_before_deregister(self):
        for target in ('source', 'archive', 'acceptance'):
            with self.subTest(target=target):
                self.plan.unlink(missing_ok=True)
                (self.source/'result.txt').write_text('measured result')
                (self.archive/'result.txt').write_text('measured result')
                self.prepare()
                path = self.acceptance if target=='acceptance' else getattr(self,target)/'result.txt'
                path.write_text('edited')
                with self.assertRaises(ValueError):
                    self.apply()
                self.assertFalse(self.calls)
                self.assertTrue(self.source.exists())

    def test_symlinks_git_and_overlaps_rejected(self):
        for name in ('link', '.git'):
            p=self.source/name
            if name=='link': p.symlink_to(self.acceptance)
            else: p.mkdir()
            with self.assertRaises(ValueError): self.prepare()
            if p.is_symlink(): p.unlink()
            else: p.rmdir()
        with self.assertRaisesRegex(ValueError, 'overlapping'): self.prepare(archive=self.source)
        with self.assertRaisesRegex(ValueError, 'overlapping'): self.prepare(allowed_root=self.source)

    def test_running_or_unknown_worker_is_not_completion(self):
        self.prepare()
        for observation in ({}, {'status':'running','exactWorker':True}, {'status':'exited','exactWorker':False}):
            s=snapshot();s['result']['observation']=observation
            with patch.object(wc,'orca',return_value=s), self.assertRaisesRegex(ValueError,'exit_unconfirmed'):
                wc.apply(self.plan,'orca-example')
        self.assertTrue(self.source.exists())

    def test_active_workspace_or_changed_registration_stops(self):
        self.prepare()
        for field,value in (('agents',[{'state':'working'}]),('unverifiableTerminalCount',1),('childWorktreeIds',['child'])):
            old=self.row[field];self.row[field]=value
            with self.assertRaisesRegex(ValueError,'not_idle'): self.apply()
            self.row[field]=old
        self.setup['path']=str(self.archive)
        with self.assertRaisesRegex(ValueError,'scope_mismatch'): self.apply()
        self.assertTrue(self.source.exists())

    def test_unknown_registration_not_ignored(self):
        self.prepare(setup_id=None)
        with self.assertRaisesRegex(ValueError,'identity_changed'): self.apply()

    def test_partial_deregister_failure_preserves_source_and_resumes(self):
        self.prepare()
        def fail(cli,*args):
            if args[:2]==('project','setup-delete'):
                self.registered=False
                raise ValueError('transport_failed_after_unregister')
            return self.orca(cli,*args)
        with patch.object(wc,'orca',fail),patch.object(wc,'no_open_files'),self.assertRaises(ValueError):
            wc.apply(self.plan,'orca-example')
        self.assertTrue(self.source.exists())
        self.assertEqual(self.apply()['phase'],'removed')

    def test_recreated_path_never_deleted_on_replay(self):
        self.prepare();self.apply();self.source.mkdir()
        with self.assertRaisesRegex(ValueError,'recreated'):self.apply()
        self.assertTrue(self.source.exists())

    def test_interrupted_delete_is_manual_recovery(self):
        self.prepare()
        with patch.object(wc,'remove_manifest',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):self.apply()
        with self.assertRaisesRegex(ValueError,'interrupted_removal'):self.apply()
        state=io.read_json(self.plan.with_suffix('.state.json'))
        self.assertTrue(Path(state['quarantine']).exists())

    def test_plan_edit_after_partial_action_rejected(self):
        self.prepare()
        with patch.object(wc,'remove_manifest',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):self.apply()
        p=io.read_json(self.plan);p['authorization']='changed';io.save(self.plan,p,self.source)
        with self.assertRaisesRegex(ValueError,'plan_changed'):self.apply()

    def test_lsof_busy_unavailable_and_error_fail_closed(self):
        with patch.object(wc.shutil,'which',return_value=None),self.assertRaises(ValueError):wc.no_open_files(self.source)
        for code,stdout,stderr in ((0,'123',''),(1,'','warning'),(2,'','')):
            with patch.object(wc.subprocess,'run') as run:
                run.return_value.returncode=code;run.return_value.stdout=stdout;run.return_value.stderr=stderr
                with self.assertRaises(ValueError):wc.no_open_files(self.source)

    def test_missing_setup_with_residual_active_workspace_blocks(self):
        self.prepare();self.registered=False
        def orphan(cli,*args):
            if args[:2]==('worktree','ps'):
                return {'result':{'truncated':False,'hostScope':{'omittedHostIds':[]},'worktrees':[dict(self.row,agents=[{'state':'working'}])]}}
            return self.orca(cli,*args)
        with patch.object(wc,'orca',orphan),patch.object(wc,'no_open_files'),self.assertRaisesRegex(ValueError,'residual_workspace'):
            wc.apply(self.plan,'orca-example')
        self.assertTrue(self.source.exists())

    def test_unreadable_subtree_fails_inventory(self):
        child=self.source/'inaccessible';child.mkdir();(child/'unarchived').write_text('retain')
        original=wc.os.scandir
        def unreadable(path):
            if str(path)==str(child):raise PermissionError('fixture')
            return original(path)
        with patch.object(wc.os,'scandir',side_effect=unreadable),self.assertRaisesRegex(ValueError,'scan_failed'):
            self.prepare()
        self.assertFalse(self.plan.exists())
        self.assertTrue((child/'unarchived').exists())

    def test_late_new_file_is_not_deleted(self):
        self.prepare()
        original=wc.remove_manifest
        def add_late(path,entries,identity):
            (path/'new-user-file').write_text('retain')
            original(path,entries,identity)
        with patch.object(wc,'remove_manifest',side_effect=add_late),self.assertRaises(OSError):self.apply()
        state=io.read_json(self.plan.with_suffix('.state.json'))
        self.assertEqual((Path(state['quarantine'])/'new-user-file').read_text(),'retain')

    def test_late_symlink_cannot_delete_external_file(self):
        self.prepare()
        original=wc.remove_manifest
        def replace(path,entries,identity):
            (path/'result.txt').unlink();(path/'result.txt').symlink_to(self.acceptance)
            original(path,entries,identity)
        with patch.object(wc,'remove_manifest',side_effect=replace),self.assertRaises(OSError):self.apply()
        self.assertTrue(self.acceptance.exists())

    def test_lock_blocks_concurrent_operation_and_releases(self):
        path=self.root/'private'/'lock'
        with io.locked(path,self.source):
            with self.assertRaisesRegex(ValueError,'already_running'):
                with io.locked(path,self.source):pass
        with io.locked(path,self.source):pass

    def completion(self, **changes):
        self.session=self.root/'session.jsonl'
        if not self.session.exists():
            rows=[event('session_meta',{'id':'session_example','cwd':str(self.source),'source':'vscode','originator':'orca_desktop'}),context(),assigned(),usage()]
            self.session.write_text(''.join(json.dumps(x)+'\n' for x in rows))
        args=dict(cli='orca-example',project=self.source,session_file=self.session,session_id='session_example',
                  task_id='task_example',dispatch_id='ctx_example',directory=self.root/'private',expected={'reviewer':'auto_review'})
        args.update(changes)
        return fw.finish(**args)

    def test_completion_once_with_exact_native_state(self):
        with patch.object(fw,'orca',return_value=snapshot()) as call:
            one=self.completion();two=self.completion()
            self.assertEqual(call.call_count,1)
            self.assertFalse(one['replay']);self.assertTrue(two['replay'])
            self.assertEqual(one['verification'],'pass')
            shutil.rmtree(self.source)
            self.assertTrue(self.completion()['replay'])

    def test_completion_binding_change_or_metadata_tamper_not_replayed(self):
        with patch.object(fw,'orca',return_value=snapshot()):
            one=self.completion()
            with self.assertRaisesRegex(ValueError,'binding_conflict'):self.completion(session_id='different')
            p=Path(one['record_path']);p.write_text('{}')
            with self.assertRaisesRegex(ValueError,'record_changed'):self.completion()

    def test_completion_unknown_exit_does_not_create_success_receipt(self):
        s=snapshot();s['result']['observation']={}
        with patch.object(fw,'orca',return_value=s),self.assertRaises(ValueError):self.completion()
        self.assertFalse((self.root/'private'/'ctx_example.completion.json').exists())

    def test_completion_mismatch_preserved_without_cleanup(self):
        with patch.object(fw,'orca',return_value=snapshot()):
            self.assertEqual(self.completion(expected={'reviewer':'user'})['verification'],'fail')
        self.assertTrue(self.source.exists())

    def test_completion_cli_links_cleanup_and_replays_after_source_removed(self):
        self.prepare()
        with patch.object(fw,'orca',return_value=snapshot()):self.completion()
        args=['--project',str(self.source),'--session-file',str(self.session),'--session-id','session_example',
              '--task-id','task_example','--dispatch-id','ctx_example','--directory',str(self.root/'private'),
              '--expect-reviewer','auto_review','--cleanup-plan',str(self.plan),'--orca-cli','orca-example',
              '--policy-project',str(self.root)]
        with patch.object(wc,'orca',self.orca),patch.object(wc,'no_open_files'):
            self.assertEqual(fw.main(args),0)
            self.assertFalse(self.source.exists())
            self.assertEqual(fw.main(args),0)
            self.assertTrue((self.archive/'result.txt').exists())

    def test_completion_cli_mismatch_never_calls_cleanup(self):
        self.prepare()
        with patch.object(fw,'orca',return_value=snapshot()):self.completion(expected={'reviewer':'user'})
        args=['--project',str(self.source),'--session-file',str(self.session),'--session-id','session_example',
              '--task-id','task_example','--dispatch-id','ctx_example','--directory',str(self.root/'private'),
              '--expect-reviewer','user','--cleanup-plan',str(self.plan),'--orca-cli','orca-example']
        with patch.object(wc,'apply') as apply:
            self.assertEqual(fw.main(args),1)
            apply.assert_not_called()
        self.assertTrue(self.source.exists())


if __name__=='__main__':
    unittest.main()
