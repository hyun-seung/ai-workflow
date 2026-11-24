"""Fictional local trial; synthetic records are never actual approvals."""
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import document_change as dc
import document_evidence as de
import hashlib
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix='fictional-doc-trial-') as folder:
    root = Path(folder).resolve()
    (root / 'docs').mkdir()
    (root / 'backups').mkdir()
    for name, text in {'README.md': '# 가상 메모\n', 'guide.md': '# 사용법\n', 'adr.md': '# ADR: 미결\n'}.items():
        (root / 'docs' / name).write_text(text)
    c = json.loads((Path(__file__).resolve().parents[2] / 'templates/documentation-project.json').read_text())
    c.update(synthetic_trial=True, backup_directory='backups')
    cfg = root / 'settings.json'
    cfg.write_text(json.dumps(c))
    p = dc.prepare(root, cfg, 1, 'synthetic-writer', 'meaning', '가상 근거와 미결을 기록', {
        'docs/README.md': '# 가상 메모\n\n[사용법](guide.md) · [결정 기록](adr.md)\n',
        'docs/guide.md': '# 사용법\n\n코드 관측: 삭제 결과가 204. 설계: 200과 본문을 의도.\n가상 단위 시험: 204를 확인. 실제 앱은 미검증.\n정책 결정 전에는 계약을 확정하지 않는다.\n',
        'docs/adr.md': '# ADR: 삭제 응답\n\n상태: 보류. 코드 204와 설계 200이 불일치.\n선택지: 설계 갱신 또는 구현 수정. 사람의 선택 대기.\n이 시험은 불일치 기록만 반영하며 최종 설계는 바꾸지 않는다.\n'})
    reviews = [dict(role=role, reviewer=who, conclusion='approved', version=1,
                    proposal_sha256=dc.digest(p), synthetic=True,
                    evidence='SYNTHETIC fixture: only discrepancy recording, no actual human approval')
               for role, who in [('independent', 'document-reviewer'), ('policy', 'policy-owner')]]
    try:
        dc.apply(root, cfg, p, [], 'document-applier')
        raise AssertionError('unapproved apply accepted')
    except ValueError:
        pass
    checked = dc.validate(root, cfg, p, reviews, 'document-applier')[1]
    result = dc.apply(root, cfg, p, reviews, 'document-applier')
    assert result['complete'] and len(result['applied']) == 3
    assert '사람의 선택 대기' in (root / 'docs/adr.md').read_text()
    (root / 'sources').mkdir()
    source = root / 'sources/response.txt'
    source.write_text('fictional code: 204; design: 200; verification: synthetic 204; human: pending\n')
    manifest = dict(version=1, project=str(root), roots=['docs', 'sources'],
                    files=['docs/guide.md', 'docs/adr.md', 'sources/response.txt'],
                    claims=[dict(id='delete-response', documents=['docs/guide.md', 'docs/adr.md'],
                                 sources=[dict(kind='code', file='sources/response.txt',
                                               sha256=hashlib.sha256(source.read_bytes()).hexdigest())])])
    assert not de.check(root, manifest)['re_review_candidates']
    source.write_text('fictional changed code: 200; human decision still pending\n')
    drift = de.check(root, manifest)
    assert drift['re_review_candidates'] == ['docs/adr.md', 'docs/guide.md']
    try:
        dc.validate(root, cfg, p, reviews, 'document-applier')
        raise AssertionError('old proposal replayed')
    except ValueError:
        pass
    recovery_proposal = dc.prepare(root, cfg, 2, 'synthetic-writer', 'minor',
                                   'SYNTHETIC recovery trial: add newline under fictional existing authority',
                                   {'docs/README.md': p['changes']['docs/README.md'] + '\n'})
    recovery_review = [dict(reviews[0], version=2, proposal_sha256=dc.digest(recovery_proposal))]
    with patch.object(dc.os, 'fsync', side_effect=OSError('SYNTHETIC backup failure')):
        failure = dc.apply(root, cfg, recovery_proposal, recovery_review, 'document-applier')
    assert not failure['complete'] and failure['incomplete_backups'] and not failure['applied']
    assert (root / 'docs/README.md').read_text() == p['changes']['docs/README.md']
    # A new proposal/review is prepared from the observed current state; no rollback.
    recovery_proposal = dc.prepare(root, cfg, 3, 'synthetic-writer', 'minor',
                                   'SYNTHETIC reviewed current state after failed backup', recovery_proposal['changes'])
    recovery_review = [dict(reviews[0], version=3, proposal_sha256=dc.digest(recovery_proposal))]
    recovery = dc.apply(root, cfg, recovery_proposal, recovery_review, 'document-applier')
    assert recovery['complete']
    print(json.dumps(dict(synthetic_trial=True, unapproved_blocked=True, check=checked, result=result,
                         drift=drift, stale_replay_blocked=True, failed_backup=failure, recovery=recovery,
                         self_check='passed', automatic='CommonMark local existence passed',
                         independent_review='not performed', reader_test='not performed',
                         human_approval='not performed', remote_anchors_rendering='not performed',
                         pending='fictional response contract choice'), ensure_ascii=False, indent=2))
