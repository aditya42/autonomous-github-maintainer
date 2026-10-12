from types import SimpleNamespace
from agent.main import capability_signature, is_oversized_diff_error, select_best_plan
from agent.models import Plan


def make_plan(title, score=.9, path="tests/api.test.js", rationale="verified gap"):
    return Plan(action="IMPLEMENT", title=title, rationale=rationale,
                category="test", target_files=[path], meaningfulness=score,
                risk="low", defect_risk=score, regression_prevention=score,
                impact=score, maintainability=score)


def test_broad_candidate_is_detected():
    label, matches = capability_signature(make_plan(
        "Check schema count seed PII and LLM validation"))
    assert label == "overbroad"
    assert len(matches) >= 3


def test_distinct_candidate_capabilities():
    assert capability_signature(make_plan("PII redaction protection"))[0] == "pii-safeguards"
    assert capability_signature(make_plan("Deterministic seed consistency"))[0] == "deterministic-seed"


def test_duplicate_capability_rejected_even_with_different_path(tmp_path):
    class AI:
        def __init__(self):
            self.plans = iter([
                make_plan("PII redaction guard", .81, "tests/first.test.js"),
                make_plan("PII redaction regression", .96, "tests/second.test.js"),
                make_plan("Deterministic seed consistency", .83, "tests/third.test.js"),
            ])
        def plan(self, *_args, **_kwargs):
            return next(self.plans)
    selected = select_best_plan(AI(), "{}", [], tmp_path)
    assert selected.title == "Deterministic seed consistency"


def test_only_actual_size_error_is_reducible():
    assert is_oversized_diff_error(ValueError("Diff exceeds 160 lines: 226"))
    assert not is_oversized_diff_error(ValueError("Binary changes not allowed"))
