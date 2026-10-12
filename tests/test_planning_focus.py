from unittest.mock import Mock

from agent.main import PLANNING_FOCUS_AREAS, select_best_plan
from agent.models import Plan


def candidate(title, rating, path="tests/api.test.mjs"):
    return Plan(
        action="IMPLEMENT", title=title, rationale="Grounded example",
        category="test", target_files=[path], meaningfulness=rating,
        risk="low", defect_risk=rating, regression_prevention=rating,
        impact=rating, maintainability=rating,
    )


def test_three_distinct_focus_areas():
    assert len(PLANNING_FOCUS_AREAS) == 3
    assert len(set(PLANNING_FOCUS_AREAS)) == 3


def test_planner_receives_distinct_focus_areas(tmp_path):
    ai = Mock()
    ai.plan.side_effect = [
        candidate("Test invalid schema", 0.8),
        candidate("Test deterministic seed", 0.85),
        candidate("Test PII safeguards", 0.9),
    ]
    selected = select_best_plan(ai, "{}", [], tmp_path)
    assert selected.title == "Test PII safeguards"
    assert ai.plan.call_count == 3
    focus_values = [call.kwargs["focus_area"] for call in ai.plan.call_args_list]
    assert focus_values == list(PLANNING_FOCUS_AREAS)


def test_rejected_candidates_do_not_prevent_other_focuses(tmp_path):
    ai = Mock()
    ai.plan.side_effect = [
        candidate("Test invalid schema", 0.6),
        candidate("Test deterministic seed", 0.83),
        candidate("Test PII safeguards", 0.7),
    ]
    assert select_best_plan(ai, "{}", [], tmp_path).title == "Test deterministic seed"
