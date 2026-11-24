#!/usr/bin/env python3
"""Optional advisory adapters. Trusted executables are an execution boundary, not a sandbox."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from urllib.parse import unquote, urlsplit

MAX_BYTES = 2 * 1024 * 1024
VERSIONS = {'lychee': 'lychee 0.24.2', 'vale': 'vale version 3.27.0'}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            raise ValueError('duplicate_key')
        out[key] = value
    return out


def exact(obj, keys):
    if not isinstance(obj, dict) or set(obj) != set(keys.split()):
        raise ValueError('unexpected_settings')


def canonical(path):
    p = Path(path)
    if not p.is_absolute() or str(p) != str(path) or '..' in p.parts:
        raise ValueError('canonical_absolute_path_required')
    for q in [p, *p.parents]:
        if q.is_symlink():
            raise ValueError('symlink_rejected')
    if p.resolve() != p:
        raise ValueError('canonical_absolute_path_required')
    return p


def relative(name):
    if not isinstance(name, str) or not name or '\\' in name or any(ord(c) < 32 or ord(c) == 127 or c in '*?[]' for c in name):
        raise ValueError('invalid_relative_path')
    p = Path(name)
    if p.is_absolute() or name == '.' or p.as_posix() != name or any(x in ('.', '..') or x.startswith('.') for x in p.parts):
        raise ValueError('invalid_relative_path')
    return p


def read_file(path):
    canonical(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        before = os.fstat(f.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            raise ValueError('input_not_regular_or_too_large')
        raw = f.read(MAX_BYTES + 1)
        after = os.fstat(f.fileno())
    if len(raw) > MAX_BYTES or (before.st_mtime_ns, before.st_ctime_ns, before.st_size) != (after.st_mtime_ns, after.st_ctime_ns, after.st_size):
        raise ValueError('input_changed_or_too_large')
    return raw


def load_json(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('json_too_large')
    return json.loads(raw, object_pairs_hook=pairs)


def settings(project, config):
    root = canonical(project)
    if not root.is_dir():
        raise ValueError('project_not_directory')
    raw = read_file(config)
    cfg = load_json(raw)
    exact(cfg, 'version project roots files targets tools')
    if type(cfg['version']) is not int or cfg['version'] != 1 or cfg['project'] != str(root):
        raise ValueError('project_or_schema_mismatch')
    for key in ('roots', 'files', 'targets'):
        if not isinstance(cfg[key], list) or not 1 <= len(cfg[key]) <= 32 or any(not isinstance(x, str) for x in cfg[key]) or len(set(cfg[key])) != len(cfg[key]):
            raise ValueError('explicit_roots_and_files_required')
        for name in cfg[key]:
            relative(name)
    for name in cfg['roots']:
        if not canonical(root / name).is_dir():
            raise ValueError('root_not_directory')
    inputs = {}
    for name in cfg['files']:
        p = relative(name)
        if not any(p.is_relative_to(Path(r)) for r in cfg['roots']) or p.suffix != '.md':
            raise ValueError('only_owned_markdown_inputs')
        inputs[name] = read_file(root / p)
        inputs[name].decode('utf-8')
    if sum(map(len, inputs.values())) > MAX_BYTES:
        raise ValueError('inputs_too_large')
    exact(cfg['tools'], 'lychee vale')
    for name, tool in cfg['tools'].items():
        exact(tool, 'enabled executable sha256 version' + (' terms' if name == 'vale' else ''))
        if any(not isinstance(tool[key], str) for key in ('executable', 'sha256', 'version')):
            raise ValueError('identity_fields_must_be_strings')
        if type(tool['enabled']) is not bool:
            raise ValueError('enabled_must_be_boolean')
        if tool['enabled']:
            if tool['version'] != VERSIONS[name] or not isinstance(tool['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', tool['sha256']):
                raise ValueError('pinned_identity_required')
            canonical(tool['executable'])
            if name == 'vale':
                terms = tool['terms']
                if not isinstance(terms, list) or not 1 <= len(terms) <= 32:
                    raise ValueError('project_terms_required')
                for term in terms:
                    exact(term, 'disallowed preferred')
                    for word in term.values():
                        if not isinstance(word, str) or not re.fullmatch('[가-힣]{2,32}', word):
                            raise ValueError('pilot_requires_literal_hangul_terms')
                    if term['disallowed'] == term['preferred']:
                        raise ValueError('terms_must_differ')
    targets = {}
    for name in cfg['targets']:
        target = relative(name)
        if not any(target.is_relative_to(Path(r)) for r in cfg['roots']):
            raise ValueError('target_outside_roots')
        path = canonical(root / target)
        targets[name] = read_file(path) if path.exists() else None
    if sum(len(v) for v in targets.values() if v is not None) > MAX_BYTES:
        raise ValueError('targets_too_large')
    return root, cfg, raw, inputs, targets


def process(argv, cwd, timeout=10):
    """No shell, inherited credentials/config or PATH lookup; bounded combined output."""
    env = {'HOME': str(cwd), 'XDG_CONFIG_HOME': str(cwd), 'XDG_CACHE_HOME': str(cwd),
           'PATH': '', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'TMPDIR': str(cwd)}
    p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    output = {p.stdout: bytearray(), p.stderr: bytearray()}
    selector = selectors.DefaultSelector()
    for stream in output:
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise ValueError('process_timeout')
            for key, _ in selector.select(min(.05, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if chunk:
                    output[key.fileobj].extend(chunk)
                    if sum(map(len, output.values())) > MAX_BYTES:
                        raise ValueError('process_output_limit')
                else:
                    selector.unregister(key.fileobj)
        p.wait(timeout=max(.001, deadline - time.monotonic()))
        return p.returncode, bytes(output[p.stdout]), bytes(output[p.stderr])
    finally:
        # The parent may have exited while descendants still hold output pipes.
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait()
        selector.close()
        p.stdout.close()
        p.stderr.close()


def binary(tool):
    path = canonical(tool['executable'])
    # Executables can exceed document limits. Stream with an explicit 256 MiB ceiling.
    if not path.is_file() or path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError('invalid_executable')
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            h.update(chunk)
    if h.hexdigest() != tool['sha256']:
        raise ValueError('executable_hash_mismatch')
    return str(path)


def links(root, cfg, inputs, targets, work):
    from markdown_it import MarkdownIt
    parser = MarkdownIt('commonmark')
    skipped, manifest, count = [], [], 0
    for i, (name, content) in enumerate(targets.items()):
        if content is None:
            continue
        (work / ('target%d.txt' % i)).write_text('')
    for name, raw in inputs.items():
        text = raw.decode('utf-8')
        # These syntaxes are outside this adapter's CommonMark link-only contract.
        if '[[' in text or '<' in text or '```mermaid' in text:
            skipped.append({'file': name, 'reason': 'html_wiki_or_mermaid_not_checked'})
        tokens = list(parser.parse(text))
        while tokens:
            token = tokens.pop(0)
            tokens.extend(token.children or [])
            if token.type not in ('link_open', 'image'):
                continue
            dest = token.attrGet('href' if token.type == 'link_open' else 'src')
            url = urlsplit(dest)
            if url.scheme or url.netloc or url.query or url.fragment or not url.path or url.path.startswith('/'):
                skipped.append({'file': name, 'reason': 'remote_absolute_anchor_query_or_unsupported'})
                continue
            try:
                target = relative((Path(name).parent / unquote(url.path)).as_posix())
                if not any(target.is_relative_to(Path(r)) for r in cfg['roots']):
                    raise ValueError('outside_roots')
                canonical(root / target)
                target_name = target.as_posix()
                if target_name not in targets:
                    # Never inspect or stage unallowlisted targets, even if they exist.
                    skipped.append({'file': name, 'reason': 'target_not_allowlisted'})
                    continue
            except ValueError:
                skipped.append({'file': name, 'reason': 'unsafe_target'})
                continue
            idx = list(targets).index(target_name)
            entry = '[link](target%d.txt)' % idx
            if entry not in manifest:
                manifest.append(entry)
                count += 1
    (work / 'links.md').write_text('\n'.join(manifest), encoding='utf-8')
    return skipped, count


def normalize_lychee(code, raw, expected):
    obj = load_json(raw)
    if not isinstance(obj, dict):
        raise ValueError('malformed_lychee_output')
    for key in ('total', 'successful', 'errors', 'unknown', 'unsupported', 'timeouts', 'excludes', 'cached'):
        if type(obj.get(key)) is not int or obj[key] < 0:
            raise ValueError('malformed_lychee_output')
    error_map = obj.get('error_map')
    if not isinstance(error_map, dict) or any(not isinstance(v, list) for v in error_map.values()) or sum(map(len, error_map.values())) != obj['errors']:
        raise ValueError('malformed_lychee_error_map')
    for rows in error_map.values():
        if any(not isinstance(row, dict) or not isinstance(row.get('url'), str) or not isinstance(row.get('status'), dict) for row in rows):
            raise ValueError('malformed_lychee_error')
    if obj['total'] != expected or obj['unknown'] or obj['unsupported'] or obj['timeouts'] or obj['excludes'] or obj['cached'] or obj['successful'] + obj['errors'] != expected:
        raise ValueError('incomplete_lychee_scope')
    if code != (2 if obj['errors'] else 0):
        raise ValueError('unexpected_lychee_exit')
    return 'findings' if obj['errors'] else 'pass', {'errors': obj['errors'], 'checked': expected}


def normalize_vale(code, raw, filenames, rule_count=32):
    obj = load_json(raw)
    if code != 0 or not isinstance(obj, dict) or not set(obj).issubset(filenames):
        raise ValueError('unexpected_vale_output_or_exit')
    findings = []
    for filename, alerts in obj.items():
        if not isinstance(alerts, list):
            raise ValueError('malformed_vale_alerts')
        for alert in alerts:
            if (not isinstance(alert, dict) or not isinstance(alert.get('Check'), str)
                    or not re.fullmatch(r'Project.Term\d+', alert['Check'])
                    or type(alert.get('Line')) is not int or alert['Line'] < 1
                    or alert.get('Severity') != 'warning'):
                raise ValueError('malformed_vale_alert')
            if int(alert['Check'].removeprefix('Project.Term')) >= rule_count:
                raise ValueError('unknown_vale_rule')
            findings.append({'file': filename, 'line': alert['Line'], 'rule': alert['Check']})
    return 'findings' if findings else 'pass', {'alerts': findings}


def run(project, config, timeout=10):
    root, cfg, config_raw, inputs, targets = settings(project, config)
    report = {'version': 1, 'advisory': True, 'config_sha256': digest(config_raw),
              'inputs': {name: digest(raw) for name, raw in inputs.items()}, 'targets': {name: digest(raw) if raw is not None else None for name, raw in targets.items()}, 'tools': {}}
    for name, tool in cfg['tools'].items():
        result = {'status': 'disabled', 'scope': 'allowlisted_commonmark_local_file_existence' if name == 'lychee' else 'project_literal_hangul_terms'}
        report['tools'][name] = result
        if not tool['enabled']:
            continue
        result['status'] = 'not_run'
        try:
            if not Path(tool['executable']).exists():
                result.update(status='unavailable', reason='explicit_executable_missing')
                continue
            exe = binary(tool)
            with tempfile.TemporaryDirectory(prefix='document-checks-') as temp:
                work = Path(temp).resolve()
                code, raw, _ = process([exe, '--version'], work, timeout)
                if code != 0 or raw.decode().strip() != tool['version']:
                    raise ValueError('executable_version_mismatch')
                binary(tool)
                if name == 'lychee':
                    try:
                        skipped, count = links(root, cfg, inputs, targets, work)
                    except ImportError:
                        result.update(status='unavailable', reason='markdown_parser_missing')
                        continue
                    result['skipped'] = skipped
                    result['coverage'] = 'partial' if skipped else 'supported_scope_only'
                    if not count:
                        result.update(status='not_run', reason='no_supported_local_links')
                        continue
                    (work / 'restrictive.toml').write_text('offline = true\ncache = false\ninclude_mail = false\ninclude_verbatim = false\n')
                    argv = [exe, '--offline', '--config', str(work / 'restrictive.toml'), '--cache=false', '--format', 'json', '--no-progress', '--', 'links.md']
                else:
                    (work / 'styles/Project').mkdir(parents=True)
                    for i, term in enumerate(tool['terms']):
                        pattern = r'(?<![\p{L}\p{N}_])' + re.escape(term['disallowed']) + r'(?![\p{L}\p{N}_])'
                        rule = "extends: existence\nmessage: 'Use project preferred term.'\nlevel: warning\nscope: text\nignorecase: false\nnonword: true\ntokens:\n  - '" + pattern.replace("'", "''") + "'\n"
                        (work / ('styles/Project/Term%d.yml' % i)).write_text(rule, encoding='utf-8')
                    (work / '.vale.ini').write_text('StylesPath = styles\nMinAlertLevel = suggestion\n[*.md]\nBasedOnStyles = Project\n')
                    for i, raw in enumerate(inputs.values()):
                        (work / ('input%d.md' % i)).write_bytes(raw)
                    filenames = ['input%d.md' % i for i in range(len(inputs))]
                    argv = [exe, '--no-global', '--config=' + str(work / '.vale.ini'), '--output=JSON', *filenames]
                code, raw, _ = process(argv, work, timeout)
                binary(tool)
                status, details = normalize_lychee(code, raw, count) if name == 'lychee' else normalize_vale(code, raw, filenames, len(tool['terms']))
                result.update(status=status, details=details, version=tool['version'], executable_sha256=tool['sha256'])
                if name == 'vale':
                    for alert in result['details']['alerts']:
                        alert['file'] = list(inputs)[filenames.index(alert['file'])]
        except (ValueError, OSError, UnicodeError, subprocess.SubprocessError) as error:
            result.update(status='error', reason=type(error).__name__ + ':' + (str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else 'process_or_protocol_failure'))
    try:
        if read_file(config) != config_raw or any(read_file(root / name) != raw for name, raw in inputs.items()) or any((read_file(canonical(root / name)) if canonical(root / name).exists() else None) != raw for name, raw in targets.items()):
            raise ValueError('inputs_changed')
    except (OSError, ValueError):
        for value in report['tools'].values():
            if value['status'] != 'disabled':
                value.update(status='error', reason='inputs_changed')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    try:
        report = run(args.project, args.config)
    except (ValueError, OSError, TypeError, UnicodeError):
        report = {'version': 1, 'error': 'invalid_settings_or_inputs', 'tools': {name: {'status': 'not_run'} for name in VERSIONS}}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    statuses = [v['status'] for v in report['tools'].values()]
    return 2 if 'error' in report or any(s in ('error', 'unavailable') for s in statuses) else 1 if 'findings' in statuses else 0


if __name__ == '__main__':
    raise SystemExit(main())
