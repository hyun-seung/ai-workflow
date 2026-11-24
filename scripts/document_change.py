#!/usr/bin/env python3
"""Bounded local document proposals. No approval minting, Git or service needed."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import uuid
from urllib.parse import unquote, urlsplit

from development_workflow import snapshot, paths
from lifecycle_io import digest
from native_record import unique_object, reject_constant
from skill_catalog import safe

LIMIT = 2 * 1024 * 1024


def parent_fd(root, name):
    safe(root, name)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in Path(name).parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def read(root, name):
    directory = parent_fd(root, name)
    try:
        fd = os.open(Path(name).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('regular file required')
            data = stream.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise ValueError('input too large')
            return data
    finally:
        os.close(directory)


def input_bytes(path):
    path = Path(path).absolute()
    return read(Path(path.anchor), str(path.relative_to(path.anchor)))


def load(path):
    return json.loads(input_bytes(path),
                      object_pairs_hook=unique_object, parse_constant=reject_constant)


def config(root, filename):
    c = load(filename)
    roots = c['document_roots']
    if not isinstance(roots, list) or not roots:
        raise ValueError('explicit document roots required')
    for item in roots:
        paths(root, [item], check_files=False)
        if not safe(root, item).is_dir():
            raise ValueError('document root directory required')
    files = paths(root, c['permitted_files'])
    for name in files:
        if not any(Path(name).is_relative_to(Path(r)) for r in roots):
            raise ValueError('file outside document roots')
    for field in ('reviewers', 'appliers', 'policy_approvers'):
        if not isinstance(c[field], list) or not c[field] or not all(isinstance(x, str) and x for x in c[field]):
            raise ValueError('explicit identities required')
    backup = safe(root, c['backup_directory'])
    if not backup.is_dir() or any(backup.is_relative_to(root / r) for r in roots):
        raise ValueError('pre-existing backup directory outside document roots required')
    return c


def prepare(root, cfg, version, author, kind, rationale, changes):
    c = config(root, cfg)
    names = paths(root, list(changes))
    if not set(names) <= set(c['permitted_files']):
        raise ValueError('unpermitted file')
    if type(version) is not int or version < 1 or kind not in ('minor', 'meaning') or not isinstance(author, str) or not author or not isinstance(rationale, str) or not rationale:
        raise ValueError('version, author, kind and rationale required')
    if not all(isinstance(v, str) for v in changes.values()) or sum(len(v.encode()) for v in changes.values()) > LIMIT:
        raise ValueError('bounded UTF-8 replacements required')
    before = snapshot(root, names)
    old = {n: read(root, n).decode('utf-8') for n in names}
    if snapshot(root, names) != before:
        raise ValueError('target changed during preparation')
    return dict(version=version, author=author, kind=kind, rationale=rationale,
                project=str(root), config=hashlib.sha256(input_bytes(cfg)).hexdigest(), target=before, changes=changes,
                diff={n: ''.join(difflib.unified_diff(old[n].splitlines(True), changes[n].splitlines(True), fromfile=n, tofile=n)) for n in names})


def lint(root, changes):
    """CommonMark tokens; local file existence only, never fetch or execute."""
    try:
        from markdown_it import MarkdownIt
    except ImportError as exc:
        raise ValueError('parser unavailable: install requirements-docs.txt in an isolated directory and set PYTHONPATH') from exc
    errors, skipped = [], []
    parser = MarkdownIt('commonmark')
    for name, text in changes.items():
        # Validate proposed file paths even when they have no links.
        safe(root, name)
        if not text.endswith('\n') or any(line.rstrip() != line for line in text.splitlines()):
            errors.append(name + ': final newline/trailing whitespace')
        tokens = parser.parse(text)
        def walk(items):
            for token in items:
                if token.type == 'fence' and token.info.strip().startswith('mermaid'):
                    skipped.append(name + ': wiki/Mermaid unsupported')
                if token.type in ('html_inline', 'html_block'):
                    skipped.append(name + ': HTML unsupported')
                if token.type == 'text' and '[[' in token.content:
                    skipped.append(name + ': wiki/Mermaid unsupported')
                if token.type in ('link_open', 'image'):
                    link = token.attrGet('href' if token.type == 'link_open' else 'src')
                    parts = urlsplit(link)
                    if parts.scheme or parts.netloc or parts.query or parts.fragment or link.startswith(('/', '#')):
                        skipped.append(name + ': unsupported link ' + link)
                    else:
                        target = os.path.normpath(str(Path(name).parent / unquote(parts.path)))
                        try:
                            safe(root, target)
                            read(root, target)  # targets must already exist, including proposed files
                        except (ValueError, OSError):
                            errors.append(name + ': missing/unsafe link ' + link)
                if token.children:
                    walk(token.children)
        walk(tokens)
    return dict(errors=errors, skipped=skipped, mode='markdown-it-py/commonmark/local-existence')


def validate(root, cfg, p, reviews, actor):
    c = config(root, cfg)
    rebuilt = prepare(root, cfg, p['version'], p['author'], p['kind'], p['rationale'], p['changes'])
    if rebuilt != p:
        raise ValueError('stale proposal/target/config/scope or altered diff')
    if actor not in c['appliers']:
        raise ValueError('unapproved applier')
    required = {'independent', 'policy'} if p['kind'] == 'meaning' else {'independent'}
    for role in required:
        allowed = c['policy_approvers'] if role == 'policy' else c['reviewers']
        matches = [r for r in reviews if r.get('role') == role and r.get('conclusion') == 'approved'
                   and r.get('proposal_sha256') == digest(p) and r.get('version') == p['version']
                   and r.get('reviewer') in allowed and r.get('evidence')
                   and (role != 'independent' or r['reviewer'] != p['author'])
                   and (r.get('synthetic') is False or (r.get('synthetic') is True and c.get('synthetic_trial') is True))]
        if not matches:
            raise ValueError('required explicit bound review missing: ' + role)
    result = lint(root, p['changes'])
    if result['errors']:
        raise ValueError('lint failed: ' + str(result))
    return c, result


def apply(root, cfg, p, reviews, actor):
    c, result = validate(root, cfg, p, reviews, actor)
    receipt = dict(applied=[], backups={}, incomplete_backups={}, validation=result, complete=False)
    try:
        originals = {n: read(root, n) for n in p['changes']}
        if snapshot(root, list(p['changes'])) != p['target']:
            raise ValueError('target changed before backups')
        # Backup all files before first mutation; exclusive names, never overwrite.
        for n, raw in originals.items():
            b = str(Path(c['backup_directory']) / (uuid.uuid4().hex + '.bak'))
            directory = parent_fd(root, b)
            try:
                fd = os.open(Path(b).name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                receipt['incomplete_backups'][n] = b
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                receipt['backups'][n] = b
                del receipt['incomplete_backups'][n]
            finally:
                os.close(directory)
        for n, text in p['changes'].items():
            if (config(root, cfg) != c or hashlib.sha256(input_bytes(cfg)).hexdigest() != p['config']
                    or read(root, n) != originals[n]):
                raise ValueError('target/config changed during apply')
            directory = parent_fd(root, n)
            temporary = '.document-' + uuid.uuid4().hex
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(text.encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                if read(root, n) != originals[n]:
                    raise ValueError('target changed before replacement')
                os.replace(temporary, Path(n).name, src_dir_fd=directory, dst_dir_fd=directory)
                receipt['applied'].append(n)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.close(directory)
        receipt['complete'] = True
    except (OSError, ValueError) as exc:
        receipt['error'] = str(exc)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'check', 'apply'])
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True, help='request JSON for prepare, proposal JSON otherwise')
    parser.add_argument('--reviews', type=Path)
    parser.add_argument('--actor')
    args = parser.parse_args()
    try:
        root = args.project.absolute()
        value = load(args.input)
        if args.command == 'prepare':
            result = prepare(root, args.config, **value)
            result = dict(proposal=result, proposal_sha256=digest(result))
        elif args.command == 'check':
            result = validate(root, args.config, value, load(args.reviews), args.actor)[1]
        else:
            result = apply(root, args.config, value, load(args.reviews), args.actor)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('complete', True) else 2
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
