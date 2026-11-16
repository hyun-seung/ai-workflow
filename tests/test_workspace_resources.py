import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import workspace_resources as resources
import workspace_cleanup as cleanup
from lifecycle_io import read_json
from test_lifecycle import snapshot


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root/'project'; self.project.mkdir()
        self.allowed = self.root/'scratch'; self.allowed.mkdir()
        self.source = self.allowed/'experiment'
        self.archive = self.root/'delivery'/'experiment'
        self.directory = self.root/'records'
        self.acceptance = self.root/'accepted.txt'; self.acceptance.write_text('accepted')
        self.workers = [dict(task_id='task_example', dispatch_id='ctx_example')]
        self.cli_calls = []

    def create(self, **changes):
        args = dict(project=self.project, directory=self.directory, resource_id='resource_example',
                    request_id='request_example', owner='backend_example', source=self.source,
                    allowed_root=self.allowed, archive=self.archive, purpose='isolated test',
                    authorization='User authorized creation, archive and retirement of this temporary test')
        args.update(changes)
        return resources.create(**args)

    def fake_cli(self, cli, *args):
        self.cli_calls.append(args)
        if args[:2] == ('orchestration', 'worker-show'): return snapshot()
        if args[:2] == ('project', 'setups'): return {'result':{'setups':[]}}
        if args[:2] == ('worktree', 'ps'):
            return {'result':{'truncated':False, 'hostScope':{'omittedHostIds':[]}, 'worktrees':[]}}
        raise AssertionError(args)

    def finalize(self, **changes):
        args = dict(project=self.project, directory=self.directory, resource_id='resource_example',
                    acceptance=self.acceptance, workers=self.workers, cli='official-example')
        args.update(changes)
        with patch.object(resources,'orca',self.fake_cli), patch.object(cleanup,'orca',self.fake_cli), \
                patch.object(cleanup,'no_open_files'):
            return resources.finalize(**args)

    def test_create_registers_private_new_directory_and_replays_without_overwrite(self):
        value = self.create()
        self.assertEqual(value['phase'],'created')
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode),0o700)
        path = self.directory/'resource_example.resource.json'
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
        (self.source/'mine').write_text('retain')
        before=path.read_bytes(); self.assertTrue(self.create()['replay'])
        self.assertEqual(path.read_bytes(),before); self.assertEqual((self.source/'mine').read_text(),'retain')

    def test_existing_empty_directory_file_and_archive_are_never_adopted(self):
        self.source.mkdir()
        with self.assertRaisesRegex(ValueError,'existing_path'):self.create()
        self.source.rmdir(); self.source.write_text('user input')
        with self.assertRaisesRegex(ValueError,'existing_path'):self.create()
        self.source.unlink(); self.archive.parent.mkdir(); self.archive.mkdir()
        with self.assertRaisesRegex(ValueError,'existing_path'):self.create()
        self.assertTrue(self.archive.is_dir()); self.assertFalse(self.source.exists())

    def test_scope_change_rejected_and_missing_policy_does_not_create(self):
        self.create()
        with self.assertRaisesRegex(ValueError,'binding_conflict'):self.create(request_id='other_request')
        with self.assertRaisesRegex(ValueError,'project_not_directory'):self.create(project=self.root/'missing')

    def test_source_must_be_direct_child_and_paths_must_be_separate(self):
        for changes in (dict(source=self.allowed),dict(source=self.allowed/'nested'/'experiment'),
                        dict(archive=self.source/'archive'),dict(archive=self.project/'archive'),
                        dict(directory=self.source/'records'),dict(archive=self.directory/'archive')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):self.create(**changes)
        self.assertFalse(self.source.exists())

    def test_symlink_parent_and_replaced_source_rejected(self):
        link=self.root/'link'; link.symlink_to(self.allowed,target_is_directory=True)
        with self.assertRaises(ValueError):self.create(source=link/'experiment')
        self.create(); self.source.rename(self.allowed/'old'); self.source.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError,'identity'):self.finalize()
        self.assertTrue((self.allowed/'old').exists()); self.assertTrue(self.source.exists())

    def test_y_archives_binary_empty_directories_and_removes_only_registered_source(self):
        self.create(); (self.source/'empty').mkdir(); (self.source/'payload').write_bytes(b'\x00\xff\r\n')
        sibling=self.allowed/'user'; sibling.mkdir(); (sibling/'data').write_text('keep')
        value=self.finalize()
        self.assertEqual(value['cleanup'],'removed'); self.assertFalse(self.source.exists())
        self.assertEqual((self.archive/'payload').read_bytes(),b'\x00\xff\r\n')
        self.assertTrue((self.archive/'empty').is_dir()); self.assertEqual((sibling/'data').read_text(),'keep')
        count=len(self.cli_calls); self.assertTrue(self.finalize()['replay'])
        self.assertEqual(len(self.cli_calls),count)
        self.assertTrue(self.create()['replay']); self.assertFalse(self.source.exists())

    def test_n_archives_without_preparing_or_applying_cleanup_then_explicit_y_can_finish(self):
        self.create(); (self.source/'data').write_text('retained')
        with patch.object(cleanup,'prepare') as prepare, patch.object(cleanup,'apply') as apply:
            value=self.finalize(auto_cleanup='N')
            prepare.assert_not_called(); apply.assert_not_called()
        self.assertEqual(value['cleanup'],'skipped_disabled'); self.assertTrue(self.source.exists())
        self.assertEqual((self.archive/'data').read_text(),'retained')
        self.assertEqual(self.finalize(auto_cleanup='Y')['cleanup'],'removed')

    def test_project_n_is_respected(self):
        (self.project/'.ai-workflow-harness.json').write_text('{"auto_cleanup":"N"}')
        self.create(); self.assertEqual(self.finalize()['cleanup'],'skipped_disabled')
        self.assertTrue(self.source.exists())

    def test_unknown_worker_or_identity_mismatch_stops_before_archive(self):
        self.create()
        for change in ({'observation':{'exactWorker':True,'status':'unverifiable'}},
                       {'dispatch':{'id':'other','taskId':'task_example'}}):
            value=snapshot(); value['result'].update(change)
            with patch.object(resources,'orca',return_value=value),self.assertRaises(ValueError):
                resources.finalize(self.project,self.directory,'resource_example',self.acceptance,self.workers,'example')
        self.assertFalse(self.archive.exists()); self.assertTrue(self.source.exists())

    def test_open_process_blocks_archive_and_source_is_preserved(self):
        self.create()
        with patch.object(resources,'orca',self.fake_cli),patch.object(cleanup,'no_open_files',side_effect=ValueError('busy')):
            with self.assertRaisesRegex(ValueError,'busy'):
                resources.finalize(self.project,self.directory,'resource_example',self.acceptance,self.workers,'example')
        self.assertFalse(self.archive.exists()); self.assertTrue(self.source.exists())

    def test_source_change_after_n_and_archive_corruption_block_y(self):
        self.create(); (self.source/'data').write_text('before'); self.finalize(auto_cleanup='N')
        (self.source/'data').write_text('changed')
        with self.assertRaises(ValueError):self.finalize()
        self.assertTrue(self.source.exists())

    def test_archive_corruption_with_unchanged_source_blocks_y(self):
        self.create(); (self.source/'data').write_text('before'); self.finalize(auto_cleanup='N')
        (self.archive/'data').write_text('changed')
        with self.assertRaises(ValueError):self.finalize()
        self.assertTrue(self.source.exists())

    def test_extra_archive_file_or_link_blocks_y_before_any_cleanup(self):
        self.create(); (self.source/'data').write_text('known'); self.finalize(auto_cleanup='N')
        extra=self.archive/'unexpected'
        for kind in ('file','link'):
            if kind=='file':extra.write_text('not in the registered archive')
            else:extra.symlink_to(self.acceptance)
            with patch.object(cleanup,'prepare') as prepare,patch.object(cleanup,'apply') as apply:
                with self.subTest(kind=kind),self.assertRaises(ValueError):self.finalize()
                prepare.assert_not_called();apply.assert_not_called()
            self.assertTrue(self.source.exists());extra.unlink()

    def test_removed_replay_checks_archive_root_even_when_empty(self):
        self.create(); self.finalize(); self.archive.rename(self.archive.with_name('original'))
        self.archive.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError,'archive_identity_changed'):self.finalize()
        self.assertFalse(self.source.exists())

    def test_removed_replay_checks_all_archive_members_and_link_types(self):
        self.create(); self.finalize(); extra=self.archive/'extra'
        for kind in ('file','link'):
            if kind=='file':extra.write_text('new')
            else:extra.symlink_to(self.acceptance)
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.finalize()
            extra.unlink()
        self.assertTrue(self.finalize()['replay'])

    def test_extra_archive_member_during_copy_is_not_adopted_as_baseline(self):
        self.create(); original=resources.copy_inventory
        def copy_and_inject(*args,**kwargs):
            original(*args,**kwargs);(self.archive/'unexpected').write_text('new')
        with patch.object(resources,'copy_inventory',copy_and_inject),self.assertRaisesRegex(ValueError,'archive_inventory_changed'):
            self.finalize()
        self.assertTrue(self.source.exists())
        self.assertEqual(read_json(self.directory/'resource_example.resource.json')['phase'],'archiving')

    def test_archive_changed_during_preparation_cannot_reach_apply(self):
        self.create(); original=cleanup.prepare
        def prepare_and_inject(*args,**kwargs):
            result=original(*args,**kwargs);(self.archive/'unexpected').write_text('new');return result
        with patch.object(cleanup,'prepare',prepare_and_inject),patch.object(cleanup,'apply') as apply:
            with self.assertRaisesRegex(ValueError,'archive_inventory_changed'):self.finalize()
            apply.assert_not_called()
        self.assertTrue(self.source.exists())

    def test_acceptance_worker_binding_and_cleanup_plan_changes_are_rejected(self):
        self.create(); self.finalize(auto_cleanup='N'); self.acceptance.write_text('different acceptance')
        with self.assertRaisesRegex(ValueError,'finalization_binding'):self.finalize()
        self.acceptance.write_text('accepted')
        with self.assertRaisesRegex(ValueError,'finalization_binding'):
            self.finalize(workers=[dict(task_id='task_other',dispatch_id='ctx_other')])
        (self.directory/'resource_example.cleanup.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'unconfirmed_cleanup_plan'):self.finalize()

    def test_duplicate_workers_and_acceptance_inside_source_are_rejected(self):
        self.create(); inner=self.source/'accepted'; inner.write_text('accepted')
        with self.assertRaisesRegex(ValueError,'acceptance_must_be_separate'):self.finalize(acceptance=inner)
        with self.assertRaisesRegex(ValueError,'duplicate_worker'):self.finalize(workers=self.workers*2)

    def test_incomplete_creation_preserves_source_and_does_not_adopt_on_replay(self):
        original=resources.save; calls=[]
        def interrupt(path,value,project):
            calls.append(value['phase'])
            if len(calls)==2:raise OSError('simulated crash before created receipt')
            original(path,value,project)
        with patch.object(resources,'save',interrupt),self.assertRaises(OSError):self.create()
        self.assertTrue(self.source.exists())
        with self.assertRaisesRegex(ValueError,'creation_incomplete'):self.create()

    def test_incomplete_archive_is_preserved_and_not_automatically_retried_or_deleted(self):
        self.create()
        with patch.object(resources,'copy_inventory',side_effect=OSError('interrupted')), \
                self.assertRaises(OSError):self.finalize()
        self.assertTrue(self.source.exists()); self.assertTrue(self.archive.exists())
        with self.assertRaisesRegex(ValueError,'incomplete_phase'):self.finalize()

    def test_byte_limit_symlink_git_and_hardlink_stop_before_deletion(self):
        self.create(max_bytes=1); (self.source/'data').write_text('too large')
        with self.assertRaisesRegex(ValueError,'byte_limit'):self.finalize()
        (self.source/'data').unlink(); link=self.source/'link'; link.symlink_to(self.acceptance)
        with self.assertRaises(ValueError):self.finalize()
        link.unlink(); (self.source/'.git').mkdir()
        with self.assertRaises(ValueError):self.finalize()
        (self.source/'.git').rmdir(); os.link(self.acceptance,self.source/'hardlink')
        # Raise the byte ceiling only in this isolated test's original registration.
        path=self.directory/'resource_example.resource.json'; value=read_json(path)
        value['binding']['max_bytes']=1000; resources.save(path,value,self.project)
        with self.assertRaisesRegex(ValueError,'unshared_regular'):self.finalize()
        self.assertTrue(self.source.exists()); self.assertEqual(self.acceptance.read_text(),'accepted')

    def test_removed_replay_never_deletes_a_recreated_source(self):
        self.create(); self.finalize(); self.source.mkdir(mode=0o700)
        (self.source/'new').write_text('user file')
        with self.assertRaisesRegex(ValueError,'recreated'):self.finalize()
        with self.assertRaisesRegex(ValueError,'recreated'):self.create()
        self.assertEqual((self.source/'new').read_text(),'user file')

    def test_saved_plan_tampering_stops_before_apply_and_preserves_source(self):
        self.create()
        with patch.object(cleanup,'apply',side_effect=ValueError('simulated apply failure')):
            with self.assertRaisesRegex(ValueError,'simulated'):self.finalize()
        path=self.directory/'resource_example.cleanup.json'
        value=read_json(path); value['authorization']='changed after preparation'
        resources.save(path,value,self.project)
        with patch.object(cleanup,'apply') as apply:
            with self.assertRaisesRegex(ValueError,'cleanup_plan_changed'):self.finalize()
            apply.assert_not_called()
        self.assertTrue(self.source.exists()); self.assertTrue(self.archive.exists())

    def test_status_of_unregistered_id_creates_no_record_directory(self):
        self.assertEqual(resources.main(['--project',str(self.project),'--directory',str(self.directory),
                                        'status','--resource-id','unknown']),2)
        self.assertFalse(self.directory.exists())


if __name__ == '__main__':unittest.main()
