"""Regression coverage for meaningful autonomous draft PR commits."""
from types import SimpleNamespace
from agent.main import substantive_diff_check, commit_description, draft_pr_description


def p(category="test"):
    return SimpleNamespace(category=category, title="Add PII scanner regression coverage",
        missing_behavior="Scanner has no explicit assertion for unsafe addresses",
        rationale="Prevent accidental removal of PII safeguards",
        evidence_file="packages/core/src/pii.ts")


def patch(*lines):
    return "diff --git a/tests/a.py b/tests/a.py\n" + "\n".join(lines)


def test_rejects_comments_only():
    assert substantive_diff_check(p(), patch("+// comment", "+// another comment"), 4)


def test_rejects_no_assertions():
    assert substantive_diff_check(p(), patch("+it('something', () => {", "+ const x = 1", "+})"), 5)


def test_accepts_real_assertions():
    assert substantive_diff_check(p(), patch("+it('blocks PII', () => {", "+ expect(scan('unsafe')).toBe(true);", "+})"), 5) is None


def test_non_test_changes_require_code():
    assert substantive_diff_check(p('bugfix'), patch("+if (invalid) throw Error('invalid');", "+return result;"), 5) is None


def test_commit_documents_gap_evidence_and_validation():
    title, body = commit_description(p(), 82, SimpleNamespace(score=0.88))
    assert title.startswith("test:")
    assert "82 added/deleted" in body
    assert "packages/core/src/pii.ts" in body
    assert "0.88" in body
    assert "Docker checks passed" in body


def test_pr_remains_draft_and_human_reviewed():
    body = draft_pr_description(p(), 82, SimpleNamespace(score=0.9))
    assert "Draft PR only" in body
    assert "Human review" not in body or "human review" in body.lower()
