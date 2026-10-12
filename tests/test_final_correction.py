"""Regression checks for evidence citations and truthful no-work outcomes."""
import inspect
from agent.main import evidence_rejection, run_one
from agent.models import Plan


def make_plan(quote, file="src/feature.py", gap=None):
    return Plan(
        action="IMPLEMENT", title="Test meaningful behavior", rationale="actual gap",
        category="test", target_files=["tests/test_feature.py"],
        meaningfulness=0.85, risk="low", defect_risk=0.85,
        regression_prevention=0.85, impact=0.85, maintainability=0.85,
        evidence_file=file, evidence_quote=quote,
        missing_behavior=gap or "The current tests omit the documented invalid-input case",
    )


def test_exact_short_quote_is_valid():
    assert evidence_rejection(make_plan("return False"),
                              {"src/feature.py": "def valid():\n    return False\n"}) is None


def test_exact_quote_over_120_characters_is_valid():
    quote = "x" * 200
    assert evidence_rejection(make_plan(quote), {"src/feature.py": quote}) is None


def test_empty_quote_is_rejected():
    assert "empty" in evidence_rejection(make_plan("  "),
                                          {"src/feature.py": "return False"})


def test_oversized_quote_is_rejected():
    quote = "x" * 501
    assert "500" in evidence_rejection(make_plan(quote), {"src/feature.py": quote})


def test_fabricated_quote_is_rejected():
    assert "does not exist" in evidence_rejection(
        make_plan("invented"), {"src/feature.py": "return False"})


def test_uninspected_file_is_rejected():
    assert "missing" in evidence_rejection(
        make_plan("return False", file="src/unknown.py"),
        {"src/feature.py": "return False"})


def test_vague_missing_behavior_is_rejected():
    assert "vague" in evidence_rejection(
        make_plan("return False", gap="bad"),
        {"src/feature.py": "return False"})


def test_no_actionable_work_is_explicit_in_run_one():
    # Minimal contract test: orchestrator retains a distinct no-work outcome.
    source = inspect.getsource(run_one)
    assert "NO_ACTIONABLE_WORK" in source
    assert "no_actionable_work: no qualifying candidate" in source
