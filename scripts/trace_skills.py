"""Explicit skill-use records and a read-only managed-library overview."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

SOURCE = 'trace.skill-use.v1'
STAGES = {'selected', 'read', 'applied', 'executed'}
RESULTS = {'unverified', 'pass', 'fail', 'partial'}
ROLES = {'coordinator', 'architect', 'designer', 'frontend', 'backend', 'verifier'}


def text(value, maximum):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise ValueError('Invalid skill record text')


def ref(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}', value):
        raise ValueError('Skill source must have a pinned commit')


def uses_contract(row):
    source = row.get('source')
    return row.get('kind') == 'skill_use' or isinstance(source, str) and (
        source == 'trace.skill-use' or source.startswith('trace.skill-use.'))


def validate_event(row):
    from trace_store import ident, stamp
    allowed = {'id', 'request_id', 'actor_id', 'source', 'source_id', 'time',
               'completeness', 'kind', 'detail', 'detail_sha256'}
    d = row.get('detail')
    if (set(row) - allowed or row.get('source') != SOURCE or row.get('kind') != 'skill_use' or
            row.get('completeness') != 'partial' or row.get('source_id') != row.get('id') or
            not isinstance(d, dict) or set(d) != {'version', 'skill', 'role', 'stage', 'purpose', 'result', 'evidence_event_ids'} or
            type(d.get('version')) is not int or d['version'] != 1):
        raise ValueError('Invalid skill-use event')
    for key in ('id', 'request_id', 'actor_id'): ident(row.get(key))
    if stamp(row.get('time')) is None: raise ValueError('Skill-use time is required')
    skill = d['skill']
    if not isinstance(skill, dict) or set(skill) != {'id', 'source_url', 'source_ref', 'mode'}:
        raise ValueError('Invalid skill source')
    ident(skill['id']); ref(skill['source_ref']); text(skill['source_url'], 1000)
    url = urlsplit(skill['source_url'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Skill source must be an HTTPS URL without credentials/query/fragment')
    if (skill['mode'] not in ('skill', 'reference-only') or not isinstance(d['stage'], str) or d['stage'] not in STAGES or
            not isinstance(d['role'], str) or d['role'] not in ROLES):
        raise ValueError('Invalid skill mode/stage/role')
    if skill['mode'] == 'reference-only' and d['stage'] == 'executed':
        raise ValueError('Reference-only guidance is not tool execution')
    text(d['purpose'], 1000)
    result = d['result']
    if (not isinstance(result, dict) or set(result) != {'status', 'summary'} or
            not isinstance(result['status'], str) or result['status'] not in RESULTS):
        raise ValueError('Invalid skill result')
    text(result['summary'], 2000)
    ids = d['evidence_event_ids']
    if not isinstance(ids, list) or len(ids) > 30: raise ValueError('Invalid skill evidence list')
    for key in ids: ident(key)
    if len(set(ids)) != len(ids) or row['id'] in ids: raise ValueError('Duplicate/self skill evidence')
    if result['status'] != 'unverified' and (d['stage'] not in ('applied', 'executed') or not ids):
        raise ValueError('Skill results need application and explicit evidence')


def validate_store(store, row):
    for key in row['detail']['evidence_event_ids']:
        found = store.db.execute("SELECT body FROM records WHERE kind='event' AND id=?", (key,)).fetchone()
        event = json.loads(found[0]) if found else None
        if not event or event['request_id'] != row['request_id'] or event['kind'] not in ('tool_result', 'verification'):
            raise ValueError('Skill evidence must be a result/verification in the same request')


def validate_review(value):
    from trace_store import ident, stamp
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'run_id', 'reviewed_at', 'status', 'summary', 'candidates'} or
            type(value['schema_version']) is not int or value['schema_version'] != 1 or
            value['status'] not in ('complete', 'partial', 'failed')):
        raise ValueError('Invalid skill review summary')
    ident(value['run_id'])
    if stamp(value['reviewed_at']) is None: raise ValueError('Review time is required')
    text(value['summary'], 2000)
    if not isinstance(value['candidates'], list) or len(value['candidates']) > 20:
        raise ValueError('Invalid review candidates')
    for item in value['candidates']:
        if (not isinstance(item, dict) or set(item) != {'id', 'decision', 'source_ref', 'reason'} or
                item['decision'] not in ('add', 'update', 'hold', 'disabled', 'retired', 'error')):
            raise ValueError('Invalid candidate decision')
        ident(item['id']); text(item['reason'], 2000)
        if item['source_ref'] is not None: ref(item['source_ref'])
    return value


def library_snapshot(root):
    if root is None: return {'status': 'unconnected', 'skills': [], 'review': None, 'history': [], 'invalid_reviews': 0}
    from skill_catalog import load, safe, verify_entry
    root = Path(root).absolute()
    catalog, registry = load(root)
    skills = []
    specs = {s['id']: s for s in catalog['skills']}
    specs.update({key: entry['spec'] for key, entry in registry['skills'].items()})
    for key, spec in sorted(specs.items()):
        entry = registry['skills'].get(key)
        integrity = 'not_installed'
        if entry:
            try: verify_entry(root, entry); integrity = 'verified'
            except (OSError, ValueError): integrity = 'failed'
        skills.append({'id': key, 'status': entry['status'] if entry else 'not_installed',
                       'mode': spec['mode'], 'roles': spec['roles'], 'tags': spec['tags'],
                       'source_ref': spec['ref'], 'integrity': integrity})
    latest, latest_time, invalid = None, None, 0
    runs = safe(root, 'local/role-skills/runs')
    from trace_store import stamp
    for candidate in runs.glob('*/review-summary.json'):
        try:
            path = safe(root, candidate.relative_to(root).as_posix())
            if path.stat().st_size > 131072: raise ValueError('Review summary too large')
            value = validate_review(json.loads(path.read_text(encoding='utf-8')))
            if value['run_id'] != candidate.parent.name: raise ValueError('Review/run mismatch')
            at = stamp(value['reviewed_at'])
            if latest_time is None or at > latest_time:
                latest, latest_time = value, at
        except (OSError, ValueError, TypeError, KeyError): invalid += 1
    history = [{k: item.get(k) for k in ('id', 'action', 'at', 'reason')}
               for item in registry.get('history', [])[-30:]]
    return {'status': 'connected', 'skills': skills, 'review': latest, 'history': history, 'invalid_reviews': invalid}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('review_summary')
    args = parser.parse_args()
    try:
        path = Path(args.review_summary)
        if path.stat().st_size > 131072: raise ValueError('Review summary too large')
        validate_review(json.loads(path.read_text(encoding='utf-8')))
        print('Skill review summary: valid')
    except (ValueError, OSError, TypeError, KeyError) as exc: parser.exit(2, str(exc) + '\n')
