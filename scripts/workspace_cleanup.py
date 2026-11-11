#!/usr/bin/env python3
"""Plan then retire one authorized temporary folder after verifying its archive."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

from lifecycle_io import digest, exited, locked, now, orca, private_file, read_json, save
from native_record import identifier
from startup import executable_for
from harness_policy import load as load_policy

ROOT = Path(__file__).resolve().parents[1]
MAX_FILES = 100000


def exact_path(value):
    path = Path(value).expanduser().absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('cleanup_symlink_or_parent_traversal')
    return path.resolve()


def file_hash(path):
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        checksum = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
        value = checksum.hexdigest()
        after = os.fstat(stream.fileno())
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('cleanup_file_changed_while_reading')
    return value


def inventory(source):
    result = {}
    if not source.is_dir() or source.is_symlink():
        raise ValueError('cleanup_inventory_root_invalid')
    def scan_error(_):
        raise ValueError('cleanup_inventory_scan_failed')
    for parent, directories, files in os.walk(source, followlinks=False, onerror=scan_error):
        for name in sorted(directories + files):
            path = Path(parent) / name
            info = path.lstat()
            if name == '.git' or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError('cleanup_git_link_or_special_file')
            relative = path.relative_to(source).as_posix()
            result[relative] = ({'kind': 'dir', 'identity': [info.st_dev, info.st_ino]} if stat.S_ISDIR(info.st_mode) else
                                {'kind': 'file', 'identity': [info.st_dev, info.st_ino], 'mtime_ns': info.st_mtime_ns,
                                 'bytes': info.st_size, 'sha256': file_hash(path)})
            if len(result) > MAX_FILES:
                raise ValueError('cleanup_inventory_limit')
    return result


def discarded(name, roots):
    return any(name == r or name.startswith(r + '/') for r in roots)


def archive_matches(plan):
    archive = exact_path(plan['archive'])
    for name, entry in plan['inventory'].items():
        if discarded(name, plan['discard']):
            continue
        path = exact_path(archive / name)
        if not path.is_relative_to(archive):
            raise ValueError('cleanup_invalid_archive_member')
        if entry['kind'] == 'dir':
            if not path.is_dir():
                raise ValueError('cleanup_archive_directory_missing')
        elif not path.is_file() or path.stat().st_size != entry['bytes'] or file_hash(path) != entry['sha256']:
            raise ValueError('cleanup_archive_missing_or_changed')


def paths(plan):
    source, archive, allowed = (exact_path(plan[k]) for k in ('source', 'archive', 'allowed_root'))
    if (source == allowed or not source.is_relative_to(allowed) or allowed in (Path('/'), Path.home()) or
            source.is_relative_to(ROOT) or ROOT.is_relative_to(source) or Path.cwd().is_relative_to(source) or
            archive.is_relative_to(source) or source.is_relative_to(archive)):
        raise ValueError('cleanup_unsafe_or_overlapping_paths')
    return source, archive


def prepare(source, archive, allowed_root, output, authorization, acceptance, workers, setup_id=None, discard=()):
    source, archive, allowed_root = map(exact_path, (source, archive, allowed_root))
    if not authorization.strip() or not workers:
        raise ValueError('cleanup_requires_authorization_and_worker_evidence')
    for worker in workers:
        if set(worker) != {'task_id', 'dispatch_id'}:
            raise ValueError('cleanup_invalid_worker_binding')
        for value in worker.values():
            identifier(value)
    if len({w['dispatch_id'] for w in workers}) != len(workers):
        raise ValueError('cleanup_duplicate_worker')
    if setup_id is not None:
        identifier(setup_id)
    roots = sorted(set(discard))
    for name in roots:
        if not name or Path(name).is_absolute() or any(p in ('.', '..', '.git') for p in name.split('/')):
            raise ValueError('cleanup_invalid_discard_root')
    acceptance = exact_path(acceptance)
    plan = dict(version=1, kind='temporary', source=str(source), archive=str(archive),
                allowed_root=str(allowed_root), authorization=authorization,
                acceptance={'path': str(acceptance), 'sha256': file_hash(acceptance)},
                workers=workers, setup_id=setup_id, discard=roots, created_at=now())
    paths(plan)
    if not source.is_dir() or not archive.is_dir() or acceptance.is_relative_to(source):
        raise ValueError('cleanup_source_archive_or_acceptance_invalid')
    info = source.stat()
    plan['root_identity'] = [info.st_dev, info.st_ino]
    plan['inventory'] = inventory(source)
    archive_matches(plan)
    output = private_file(output, source)
    if output.is_relative_to(archive):
        raise ValueError('cleanup_plan_inside_archive')
    with locked(output.with_suffix('.lock'), source):
        if output.exists():
            raise ValueError('cleanup_plan_already_exists')
        save(output, plan, source)
    return plan


def no_open_files(source):
    executable = shutil.which('lsof')
    if not executable:
        raise ValueError('cleanup_process_check_unavailable')
    result = subprocess.run([executable, '-nP', '-t', '+D', str(source)], capture_output=True, text=True, timeout=30)
    if result.returncode != 1 or result.stdout.strip() or result.stderr.strip():
        raise ValueError('cleanup_open_files_or_unconfirmed_process_state')


def idle_registration(cli, plan):
    setups = orca(cli, 'project', 'setups', '--host', 'local')['result']['setups']
    matching = [s for s in setups if s.get('id') == plan['setup_id']]
    result = orca(cli, 'worktree', 'ps')['result']
    if result.get('truncated') is not False or result.get('hostScope', {}).get('omittedHostIds') != []:
        raise ValueError('cleanup_workspace_scan_incomplete')
    at_source = [r for r in result['worktrees'] if isinstance(r.get('path'), str) and
                 Path(r['path']).is_relative_to(Path(plan['source']))]
    if not matching:
        # A different registration at this path must not be silently left behind.
        if any(s.get('path') == plan['source'] for s in setups):
            raise ValueError('cleanup_registration_identity_changed')
        if at_source:
            raise ValueError('cleanup_residual_workspace_after_deregister')
        return False
    if len(matching) != 1 or matching[0].get('path') != plan['source'] or matching[0].get('kind') != 'folder' or matching[0].get('hostId') != 'local':
        raise ValueError('cleanup_registration_scope_mismatch')
    repo_id = matching[0].get('repoId')
    rows = [r for r in result['worktrees'] if r.get('repoId') == repo_id or r in at_source]
    if not rows:
        raise ValueError('cleanup_workspace_state_unknown')
    for row in rows:
        if (row.get('path') != plan['source'] or row.get('hostId') != 'local' or
                row.get('agents') != [] or row.get('liveTerminalCount') != 0 or
                row.get('unverifiableTerminalCount') != 0 or row.get('hasAttachedPty') is not False or
                row.get('childWorktreeIds') != []):
            raise ValueError('cleanup_workspace_not_idle_or_has_children')
    return True


def remove_manifest(source, entries, root_identity):
    """Unlink only planned entries using no-follow directory descriptors.

    New files cause rmdir to fail instead of being recursively swept away.
    A failure preserves the journal and remaining quarantine for inspection.
    """
    children = {}
    for name in entries:
        parent = str(Path(name).parent)
        children.setdefault(parent, []).append(name)
    def remove(fd, parent):
        for relative in sorted(children.get(parent, [])):
            name, entry = Path(relative).name, entries[relative]
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW |
                            (os.O_DIRECTORY if entry['kind']=='dir' else 0), dir_fd=fd)
            try:
                info = os.fstat(child)
                if [info.st_dev, info.st_ino] != entry['identity']:
                    raise ValueError('cleanup_entry_replaced')
                if entry['kind']=='dir':
                    remove(child, relative)
                else:
                    checksum = hashlib.sha256()
                    while True:
                        block = os.read(child, 1024*1024)
                        if not block: break
                        checksum.update(block)
                    after = os.fstat(child)
                    if (after.st_size != entry['bytes'] or after.st_mtime_ns != entry['mtime_ns'] or
                            checksum.hexdigest() != entry['sha256']):
                        raise ValueError('cleanup_entry_changed')
                linked = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError('cleanup_entry_replaced')
                if entry['kind']=='dir': os.rmdir(name, dir_fd=fd)
                else: os.unlink(name, dir_fd=fd)
            finally:
                os.close(child)
    fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if [info.st_dev, info.st_ino] != root_identity:
            raise ValueError('cleanup_root_replaced')
        remove(fd, '.')
        linked = source.lstat()
        if (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError('cleanup_root_replaced')
        source.rmdir()
    finally:
        os.close(fd)


def apply(plan_path, cli):
    plan_path = exact_path(plan_path)
    plan = read_json(plan_path)
    if plan.get('version') != 1 or plan.get('kind') != 'temporary' or not plan.get('authorization'):
        raise ValueError('cleanup_not_authorized_temporary_plan')
    source, archive = paths(plan)
    private_file(plan_path, source)
    state_path = plan_path.with_suffix('.state.json')
    with locked(plan_path.with_suffix('.lock'), source):
        state = read_json(state_path) if state_path.exists() else {'plan_digest': digest(plan), 'phase': 'planned'}
        if state.get('plan_digest') != digest(plan):
            raise ValueError('cleanup_plan_changed')
        if state['phase'] == 'removed':
            if source.exists():
                raise ValueError('cleanup_source_recreated_do_not_delete')
            archive_matches(plan)
            return state
        if state['phase'] == 'removing':
            raise ValueError('cleanup_interrupted_removal_requires_inspection')
        info = source.stat()
        if [info.st_dev, info.st_ino] != plan['root_identity'] or inventory(source) != plan['inventory']:
            raise ValueError('cleanup_source_changed')
        archive_matches(plan)
        if file_hash(exact_path(plan['acceptance']['path'])) != plan['acceptance']['sha256']:
            raise ValueError('cleanup_acceptance_changed')
        for worker in plan['workers']:
            exited(orca(cli, 'orchestration', 'worker-show', '--dispatch', worker['dispatch_id'], '--full'),
                   worker['task_id'], worker['dispatch_id'])
        no_open_files(source)
        if idle_registration(cli, plan):
            state.update(phase='deregistering', updated_at=now())
            save(state_path, state, source)
            # --force is limited to this proven idle folder setup's saved UI details.
            # Never run worktree removal or archive hooks; never remove a Git checkout.
            orca(cli, 'project', 'setup-delete', '--setup', plan['setup_id'], '--host', 'local', '--force')
            if idle_registration(cli, plan):
                raise ValueError('cleanup_registration_still_present')
        state.update(phase='deregistered', updated_at=now())
        save(state_path, state, source)
        # Recheck after the app mutation. Fresh files or changed archive halt deletion.
        if inventory(source) != plan['inventory']:
            raise ValueError('cleanup_source_changed')
        archive_matches(plan)
        no_open_files(source)
        quarantine = source.with_name('.' + source.name + '.cleanup-' + digest(plan)[:12])
        if quarantine.exists() or quarantine.is_symlink():
            raise ValueError('cleanup_quarantine_collision')
        state.update(phase='removing', quarantine=str(quarantine), updated_at=now())
        save(state_path, state, source)
        source.rename(quarantine)
        info = quarantine.stat()
        if [info.st_dev, info.st_ino] != plan['root_identity'] or inventory(quarantine) != plan['inventory']:
            raise ValueError('cleanup_quarantine_changed_requires_inspection')
        archive_matches(plan)
        no_open_files(quarantine)
        remove_manifest(quarantine, plan['inventory'], plan['root_identity'])
        state.update(phase='removed', removed_at=now(), archive_verified=True,
                     retained_files=sum(v['kind'] == 'file' and not discarded(k, plan['discard']) for k,v in plan['inventory'].items()))
        save(state_path, state, source)
        return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    for key in ('source', 'archive', 'allowed-root', 'output', 'authorization', 'acceptance'):
        p.add_argument('--' + key, required=True)
    p.add_argument('--worker', action='append', required=True, help='task_id/dispatch_id; list every relevant assignment')
    p.add_argument('--setup-id')
    p.add_argument('--discard', action='append', default=[])
    p = sub.add_parser('apply')
    p.add_argument('--plan', required=True)
    p.add_argument('--orca-cli')
    p.add_argument('--policy-project', type=Path, default=Path.cwd(), help='Project whose harness policy applies')
    p.add_argument('--config')
    p.add_argument('--auto-cleanup', choices=('Y','N'))
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            workers = [dict(zip(('task_id','dispatch_id'), w.split('/'))) for w in args.worker]
            value = prepare(args.source,args.archive,args.allowed_root,args.output,args.authorization,args.acceptance,
                            workers,args.setup_id,args.discard)
            print('prepared entries=' + str(len(value['inventory'])) + '; no files removed')
        else:
            policy = load_policy(args.policy_project, args.config, args.auto_cleanup)
            if policy['auto_cleanup'] == 'N':
                print('cleanup=skipped_disabled')
            else:
                print('cleanup=' + apply(args.plan, args.orca_cli or executable_for('orca', os.environ))['phase'])
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        print('Cleanup stopped: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
