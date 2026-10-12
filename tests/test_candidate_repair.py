from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agent.main import candidate_score, select_best_plan
from agent.models import Plan


def plan(title, score, risk="low", paths=None):
    return Plan(action="IMPLEMENT", title=title, rationale="evidence",
                category="test", target_files=paths or ["tests/api.test.js"],
                meaningfulness=score, risk=risk, defect_risk=score,
                regression_prevention=score, impact=score, maintainability=score)


def test_rubric_weights():
    assert candidate_score(plan("a", 0.8)) == 0.8


def test_selects_highest_qualifying_candidate(tmp_path):
    (tmp_path / "tests").mkdir()
    ai = Mock()
    ai.plan.side_effect = [
        plan("low", 0.60, paths=["tests/low.test.js"]),
        plan("good", 0.81, paths=["tests/good.test.js"]),
        plan("best", 0.90, paths=["tests/best.test.js"]),
    ]
    selected = select_best_plan(ai, "{}", ["README.md"], tmp_path)
    assert selected.title == "best"
    assert ai.plan.call_count == 3


def test_all_low_scores_skip(tmp_path):
    ai = Mock()
    ai.plan.side_effect = [
        plan("one", 0.30, paths=["tests/one.test.js"]),
        plan("two", 0.40, paths=["tests/two.test.js"]),
        plan("three", 0.49, paths=["tests/three.test.js"]),
    ]
    assert select_best_plan(ai, "{}", [], tmp_path) is None


def test_high_risk_rejected_even_if_high_score(tmp_path):
    ai = Mock()
    ai.plan.side_effect = [
        plan("a", 0.99, risk="high", paths=["tests/a.test.js"]),
        plan("b", 0.45, paths=["tests/b.test.js"]),
        plan("c", 0.44, paths=["tests/c.test.js"]),
    ]
    assert select_best_plan(ai, "{}", [], tmp_path) is None


def test_duplicate_target_set_rejected(tmp_path):
    ai = Mock()
    ai.plan.side_effect = [
        plan("a", 0.8, paths=["tests/same.test.js"]),
        plan("duplicate", 0.95, paths=["tests/same.test.js"]),
        plan("c", 0.65, paths=["tests/c.test.js"]),
    ]
    assert select_best_plan(ai, "{}", [], tmp_path).title == "a"
