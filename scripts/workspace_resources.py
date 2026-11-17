#!/usr/bin/env python3
"""Register new private temporary folders and finalize them through the existing cleanup guard."""

import argparse
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys

from harness_policy import load as load_policy
from lifecycle_io import digest, exited, locked, now, orca, private_file, read_json, save
from native_record import identifier
from prepare_handoff import private_destination
from startup import executable_for
import workspace_cleanup as cleanup

DEFAULT_MAX_BYTES = 64 * 1024 * 1024
PHASES = {'creating', 'created', 'archiving', 'archived', 'prepared', 'removed'}


@contextmanager
def directory_fd(path):
    """Open every directory component without following links."""
    path = Path(path)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in path.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def identity(info):
    return [info.st_dev, info.st_ino]


def record_path(project, directory, resource_id, create_parent=False):
    identifier(resource_id)
    path = cleanup.exact_path(Path(directory) / (resource_id + '.resource.json'))
    if not create_parent and not path.exists():
        raise ValueError('resource_not_registered')
    return private_file(path, project)


def scope(project, source, allowed_root, archive, record):
    source, allowed_root, archive = map(cleanup.exact_path, (source, allowed_root, archive))
    cleanup.paths(dict(source=str(source), allowed_root=str(allowed_root), archive=str(archive)))
    if not allowed_root.is_dir() or source.parent != allowed_root:
        raise ValueError('resource_requires_direct_child_of_existing_root')
    for a, b in ((record.parent, source), (record.parent, archive)):
        if a.is_relative_to(b) or b.is_relative_to(a):
            raise ValueError('resource_record_overlaps_source_or_archive')
    if archive.is_relative_to(project) or project.is_relative_to(archive):
        raise ValueError('resource_archive_overlaps_project')
    return source, allowed_root, archive


def load_record(path, project, resource_id):
    value = read_json(path)
    if (value.get('version') != 1 or value.get('kind') != 'owned_temporary_folder' or
            value.get('phase') not in PHASES or not isinstance(value.get('binding'), dict) or
            value['binding'].get('project') != str(project) or
            value['binding'].get('resource_id') != resource_id):
        raise ValueError('resource_record_scope_or_format_mismatch')
    return value


def create(project, directory, resource_id, request_id, owner, source, allowed_root, archive,
           purpose, authorization, max_bytes=DEFAULT_MAX_BYTES):
    project = Path(project).resolve()
    load_policy(project)  # A missing policy project must never become default Y.
    identifier(request_id); identifier(owner)
    if (not purpose.strip() or not authorization.strip() or len(purpose) > 2000 or
            len(authorization) > 2000 or type(max_bytes) is not int or max_bytes < 1):
        raise ValueError('resource_requires_purpose_authorization_and_positive_byte_limit')
    identifier(resource_id)
    path = cleanup.exact_path(Path(directory) / (resource_id + '.resource.json'))
    source, allowed_root, archive = scope(project, source, allowed_root, archive, path)
    if not path.exists() and (source.exists() or archive.exists()):
        raise ValueError('resource_existing_path_must_not_be_adopted')
    path = record_path(project, directory, resource_id, create_parent=True)
    # Only validated, disjoint paths may create private parents.
    private_destination(archive, project)
    binding = dict(project=str(project), resource_id=resource_id, request_id=request_id, owner=owner,
                   source=str(source), allowed_root=str(allowed_root), archive=str(archive),
                   purpose=purpose, authorization=authorization, max_bytes=max_bytes)
    with locked(path.with_suffix('.lock'), project):
        if path.exists():
            value = load_record(path, project, resource_id)
            if value['binding'] != binding:
                raise ValueError('resource_binding_conflict')
            if value['phase'] == 'creating':
                raise ValueError('resource_creation_incomplete_requires_inspection')
            if value['phase'] == 'removed':
                if source.exists():
                    raise ValueError('resource_source_recreated_do_not_adopt')
            else:
                verify_source(value)
            return dict(value, replay=True)
        if source.exists() or archive.exists():
            raise ValueError('resource_existing_path_must_not_be_adopted')
        value = dict(version=1, kind='owned_temporary_folder', binding=binding,
                     phase='creating', created_at=now())
        save(path, value, project)
        with directory_fd(allowed_root) as parent:
            os.mkdir(source.name, mode=0o700, dir_fd=parent)
            info = os.stat(source.name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError('resource_created_directory_changed')
        value.update(phase='created', root_identity=identity(info))
        save(path, value, project)
        return dict(value, replay=False)


def verify_source(value):
    source = cleanup.exact_path(value['binding']['source'])
    info = source.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) & 0o077 or identity(info) != value['root_identity']):
        raise ValueError('resource_source_identity_or_permissions_changed')
    return source


def copy_inventory(source_fd, archive_fd, entries, parent='.', children=None):
    if children is None:
        children = {}
        for name, entry in sorted(entries.items()):
            children.setdefault(str(Path(name).parent), []).append((name, entry))
    for name, entry in children.get(parent, []):
        leaf = Path(name).name
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW |
                     (os.O_DIRECTORY if entry['kind'] == 'dir' else 0), dir_fd=source_fd)
        try:
            info = os.fstat(fd)
            if identity(info) != entry['identity'] or info.st_uid != os.getuid():
                raise ValueError('resource_archive_source_replaced')
            if entry['kind'] == 'dir':
                os.mkdir(leaf, mode=0o700, dir_fd=archive_fd)
                child = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=archive_fd)
                try:
                    copy_inventory(fd, child, entries, name, children)
                finally:
                    os.close(child)
            else:
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('resource_archive_requires_unshared_regular_files')
                output = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=archive_fd)
                checksum = hashlib.sha256(); size = 0
                try:
                    with os.fdopen(output, 'wb') as stream:
                        while chunk := os.read(fd, 1024 * 1024):
                            size += len(chunk)
                            if size > entry['bytes']:
                                raise ValueError('resource_archive_source_grew')
                            checksum.update(chunk); stream.write(chunk)
                        stream.flush(); os.fsync(stream.fileno())
                finally:
                    after = os.fstat(fd)
                if (size != entry['bytes'] or checksum.hexdigest() != entry['sha256'] or
                        (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns)):
                    raise ValueError('resource_archive_source_changed')
        finally:
            os.close(fd)
    os.fsync(archive_fd)


def content_inventory(entries):
    return {name: {k: entry[k] for k in ('kind', 'bytes', 'sha256') if k in entry}
            for name, entry in entries.items()}


def verify_archive(value):
    archive = cleanup.exact_path(value['binding']['archive'])
    if identity(archive.stat()) != value['archive_identity']:
        raise ValueError('resource_archive_identity_changed')
    entries = cleanup.inventory(archive)
    if (content_inventory(entries) != content_inventory(value['inventory']) or
            digest(entries) != value['archive_inventory_digest']):
        raise ValueError('resource_archive_inventory_changed')


def finalize(project, directory, resource_id, acceptance, workers, cli, config=None, auto_cleanup=None):
    project = Path(project).resolve()
    policy = load_policy(project, config, auto_cleanup)
    path = record_path(project, directory, resource_id)
    with locked(path.with_suffix('.lock'), project):
        value = load_record(path, project, resource_id)
        if value['phase'] in {'creating', 'archiving'}:
            raise ValueError('resource_incomplete_phase_requires_inspection')
        source, _, archive = scope(project, *(value['binding'][k] for k in
                                  ('source', 'allowed_root', 'archive')), path)
        acceptance = cleanup.exact_path(acceptance)
        if acceptance.is_relative_to(source) or acceptance.is_relative_to(archive):
            raise ValueError('resource_acceptance_must_be_separate')
        if not workers or any(set(w) != {'task_id', 'dispatch_id'} for w in workers):
            raise ValueError('resource_requires_exact_worker_bindings')
        for worker in workers:
            identifier(worker['task_id']); identifier(worker['dispatch_id'])
        if len({w['dispatch_id'] for w in workers}) != len(workers):
            raise ValueError('resource_duplicate_worker')
        finalization = dict(acceptance=dict(path=str(acceptance), sha256=cleanup.file_hash(acceptance)),
                            workers=sorted(workers, key=lambda w: w['dispatch_id']))
        if value.get('finalization', finalization) != finalization:
            raise ValueError('resource_finalization_binding_changed')
        if value['phase'] == 'removed':
            if source.exists():
                raise ValueError('resource_source_recreated_do_not_delete')
            verify_archive(value)
            return dict(value, replay=True, cleanup='removed')
        verify_source(value)
        if value['phase'] == 'created':
            for worker in workers:
                exited(orca(cli, 'orchestration', 'worker-show', '--dispatch', worker['dispatch_id'], '--full'),
                       worker['task_id'], worker['dispatch_id'])
            cleanup.no_open_files(source)
            entries = cleanup.inventory(source)
            if sum(e.get('bytes', 0) for e in entries.values()) > value['binding']['max_bytes']:
                raise ValueError('resource_archive_byte_limit')
            if archive.exists():
                raise ValueError('resource_archive_destination_exists')
            value.update(phase='archiving', finalization=finalization, inventory=entries)
            save(path, value, project)
            with directory_fd(source) as source_fd, directory_fd(archive.parent) as parent:
                if identity(os.fstat(source_fd)) != value['root_identity']:
                    raise ValueError('resource_source_replaced_before_archive')
                os.mkdir(archive.name, mode=0o700, dir_fd=parent)
                destination = os.open(archive.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    copy_inventory(source_fd, destination, entries)
                    value['archive_identity'] = identity(os.fstat(destination))
                finally:
                    os.close(destination)
            verify_source(value)
            if cleanup.inventory(source) != entries:
                raise ValueError('resource_source_changed_during_archive')
            archived = cleanup.inventory(archive)
            if content_inventory(archived) != content_inventory(entries):
                raise ValueError('resource_archive_inventory_changed')
            value['archive_inventory_digest'] = digest(archived)
            value['phase'] = 'archived'
            save(path, value, project)
        if cleanup.inventory(source) != value['inventory']:
            raise ValueError('resource_source_or_archive_changed')
        verify_archive(value)
        if policy['auto_cleanup'] == 'N':
            return dict(value, cleanup='skipped_disabled')
        plan_path = path.with_name(resource_id + '.cleanup.json')
        if value['phase'] == 'archived':
            if plan_path.exists():
                raise ValueError('resource_unconfirmed_cleanup_plan')
            plan = cleanup.prepare(source, archive, value['binding']['allowed_root'], plan_path,
                                   value['binding']['authorization'], acceptance, workers)
            value.update(phase='prepared', cleanup_plan=str(plan_path), cleanup_plan_digest=digest(plan))
            save(path, value, project)
        if digest(read_json(plan_path)) != value['cleanup_plan_digest']:
            raise ValueError('resource_cleanup_plan_changed')
        verify_archive(value)
        result = cleanup.apply(plan_path, cli)
        value.update(phase=result['phase'], finalized_at=now())
        save(path, value, project)
        return dict(value, cleanup=result['phase'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--directory', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    create_parser = sub.add_parser('create')
    for name in ('resource-id', 'request-id', 'owner', 'source', 'allowed-root', 'archive', 'purpose', 'authorization'):
        create_parser.add_argument('--' + name, required=True)
    create_parser.add_argument('--max-bytes', type=int, default=DEFAULT_MAX_BYTES)
    finish = sub.add_parser('finalize')
    finish.add_argument('--resource-id', required=True); finish.add_argument('--acceptance', required=True)
    finish.add_argument('--worker', action='append', required=True, help='Exact task_id/dispatch_id; repeat for all writers')
    finish.add_argument('--orca-cli'); finish.add_argument('--config')
    finish.add_argument('--auto-cleanup', choices=('Y', 'N'))
    sub.add_parser('status').add_argument('--resource-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'create':
            result = create(args.project, args.directory, args.resource_id, args.request_id, args.owner,
                            args.source, args.allowed_root, args.archive, args.purpose, args.authorization, args.max_bytes)
        elif args.command == 'finalize':
            workers = []
            for binding in args.worker:
                if binding.count('/') != 1:
                    raise ValueError('resource_worker_must_be_task_id_slash_dispatch_id')
                task, dispatch = binding.split('/')
                workers.append(dict(task_id=task, dispatch_id=dispatch))
            result = finalize(args.project, args.directory, args.resource_id, args.acceptance, workers,
                              args.orca_cli or executable_for('orca', os.environ), args.config, args.auto_cleanup)
        else:
            project = Path(args.project).resolve(); load_policy(project)
            result = load_record(record_path(project, args.directory, args.resource_id), project, args.resource_id)
        import json
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError, subprocess.TimeoutExpired) as error:
        print('Resource operation stopped: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
