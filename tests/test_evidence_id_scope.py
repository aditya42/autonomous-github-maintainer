"""Regression tests for evidence IDs and intent-based scope classification."""
import json
from unittest.mock import Mock

from agent.main import (
    build_evidence_ids, capability_signature, evidence_id_error, select_best_plan,
)
from agent.models import Plan


def candidate(title, *, evidence_id="", file="src/route.ts", score=0.80):
    return Plan(
        action="IMPLEMENT", title=title, rationale="verified source pathway",
        category="test", target_files=["tests/route.test.mjs"],
        meaningfulness=score, risk="low", defect_risk=score,
        regression_prevention=score, impact=score, maintainability=score,
        evidence_id=evidence_id, evidence_file=file, evidence_quote="",
        missing_behavior="A documented negative case lacks focused regression coverage",
    )


def test_stable_source_evidence_id():
    c = {"src/route.ts": "export function run() { return true }"}
    assert build_evidence_ids(c) == build_evidence_ids(c)
    assert len(build_evidence_ids(c)) == 1


def test_valid_evidence_id_allows_no_quote():
    c = {"src/route.ts": "export function run() { return true }"}
    eid = next(iter(build_evidence_ids(c)))
    assert evidence_id_error(candidate("Test schema errors", evidence_id=eid), c,
                             build_evidence_ids(c)) is None


def test_fabricated_id_rejected():
    c = {"src/route.ts": "export function run() { return true }"}
    assert "not in inspected" in evidence_id_error(
        candidate("Test schema errors", evidence_id="ev-invented"), c, build_evidence_ids(c))


def test_mismatched_path_rejected():
    c = {"src/route.ts": "export function run() { return true }"}
    eid = next(iter(build_evidence_ids(c)))
    assert "does not match" in evidence_id_error(
        candidate("Test schema errors", evidence_id=eid, file="src/other.ts"),
        c, build_evidence_ids(c))


def test_related_words_do_not_create_overbroad_rejection():
    for title in (
        "Add deterministic generation tests for boundary string lengths",
        "Add schema validation failure and boundary case tests",
        "Add LLM PII screening error recovery test",
    ):
        assert capability_signature(candidate(title))[0] != "overbroad"


def test_explicit_multiple_domains_are_overbroad():
    assert capability_signature(candidate("Test schema and count and seed"))[0] == "overbroad"


def test_candidate_with_id_reaches_selection(tmp_path):
    c = {"src/route.ts": "export function run() { return true }"}
    ids = build_evidence_ids(c)
    eid = next(iter(ids))
    ai = Mock()
    ai.plan.side_effect = [
        candidate("Test schema errors", evidence_id=eid, score=.8),
        candidate("Test count handling", evidence_id="fake", score=.9),
        candidate("Test PII filtering", evidence_id=eid, score=.7),
    ]
    ctx = json.dumps({"evidence_catalog": c, "evidence_ids": ids})
    selected = select_best_plan(ai, ctx, [], tmp_path)
    assert selected.title == "Test schema errors"
    assert ai.plan.call_count == 3
