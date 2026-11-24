#!/usr/bin/env python3
"""Fictional optional checker trial; explicit config supplies trusted native identities."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import document_checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', help='JSON object with lychee and vale identities, as in template tools')
    args = parser.parse_args()
    template = Path(__file__).resolve().parents[2] / 'templates/document-checks.json'
    cfg = json.loads(template.read_text())
    if args.tools:
        cfg['tools'] = document_checks.load_json(document_checks.read_file(Path(args.tools)))
    with tempfile.TemporaryDirectory(prefix='fictional-checkers-') as temp:
        root = Path(temp).resolve()
        (root / 'docs').mkdir()
        (root / 'docs/guide.md').write_text('하늘단추\n', encoding='utf-8')
        cfg.update(project=str(root), files=['docs/README.md', 'docs/guide.md'], targets=['docs/guide.md', 'docs/missing.md'])
        config = root / 'checks.json'
        results = {}
        for name, content in {
            'good': '[good](guide.md)\n하늘단추\n',
            'broken_remote_korean': '[good](guide.md)\n[bad](missing.md)\n[remote](https://example.invalid)\n구름단추\n',
            'code_and_boundary': '```text\n구름단추\n```\n`구름단추`\n왕구름단추\n구름단추는\n하늘단추\n',
            'remote_only': '[remote](https://example.invalid)\n',
        }.items():
            (root / 'docs/README.md').write_text(content, encoding='utf-8')
            config.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
            results[name] = document_checks.run(str(root), str(config))
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
