#!/usr/bin/env python3
"""Advisory, bounded local claim evidence drift report; never approval."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from document_change import read, load
from development_workflow import paths
from skill_catalog import safe

KINDS = {'code', 'design', 'verified', 'human', 'pending'}


def check(root, manifest):
    root = Path(root).absolute()
    if any(part.is_symlink() for part in (root, *root.parents)):
        raise ValueError('symlink in project ancestry')
    if manifest.get('version') != 1 or manifest.get('project') != str(root):
        raise ValueError('version/project binding required')
    roots = manifest['roots']
    if not isinstance(roots, list) or not roots:
        raise ValueError('explicit roots required')
    for item in roots:
        paths(root, [item], check_files=False)
        if not safe(root, item).is_dir():
            raise ValueError('existing root required')
    files = paths(root, manifest['files'])
    if any(not any(Path(n).is_relative_to(Path(r)) for r in roots) for n in files):
        raise ValueError('file outside explicit roots')
    claims = manifest['claims']
    if not isinstance(claims, list) or len(claims) > 128:
        raise ValueError('bounded claims required')
    seen, reports, candidates = set(), [], set()
    for claim in claims:
        cid, docs, sources = claim['id'], claim['documents'], claim['sources']
        if not isinstance(cid, str) or not cid or cid in seen:
            raise ValueError('unique claim ID required')
        seen.add(cid)
        if not isinstance(docs, list) or not docs or not set(docs) <= set(files):
            raise ValueError('explicit affected documents required')
        if not isinstance(sources, list) or not sources or len(sources) > 32:
            raise ValueError('bounded sources required')
        for source in sources:
            name, expected = source['file'], source['sha256']
            if name not in files or source['kind'] not in KINDS or not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
                raise ValueError('listed source, evidence kind and SHA256 required')
            try:
                actual = hashlib.sha256(read(root, name)).hexdigest()
                status = 'unchanged' if actual == expected else 'changed'
            except FileNotFoundError:
                actual, status = None, 'missing'
            # Unsafe paths and other IO failures are errors, never "unchanged".
            if status != 'unchanged':
                candidates.update(docs)
            reports.append(dict(claim_id=cid, kind=source['kind'], source=name,
                                recorded_sha256=expected, current_sha256=actual, status=status))
    return dict(advisory=True, evidence=reports, re_review_candidates=sorted(candidates),
                scope=files, exhaustive=False, approval=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = check(args.project, load(args.manifest))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result['re_review_candidates'] else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
