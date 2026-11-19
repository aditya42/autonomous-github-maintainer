from unittest.mock import Mock

from agent.main import capability_signature, select_best_plan
from agent.models import Plan


def make_plan(title, score=0.9, path="tests/api-smoke.test.mjs", rationale=""):
    return Plan(
        action="IMPLEMENT", title=title, rationale=rationale,
        category="test", target_files=[path], meaningfulness=score,
        risk="low", defect_risk=score, regression_prevention=score,
        impact=score, maintainability=score,
    )


def test_distinct_capabilities_can_share_same_file(tmp_path):
    ai = Mock()
    ai.plan.side_effect = [
        make_plan("Test invalid JSON schema", .8),
        make_plan("Test count boundaries", .85),
        make_plan("Test PII blocking", .9),
    ]
    best = select_best_plan(ai, "{}", [], tmp_path)
    assert best.title == "Test PII blocking"
    assert ai.plan.call_count == 3


def test_rejected_candidate_does_not_reserve_capability(tmp_path):
    ai = Mock()
    ai.plan.side_effect = [
        make_plan("Test schema rejection", .40),
        make_plan("Test invalid schema errors", .90),
        make_plan("Test count boundaries", .80),
    ]
    best = select_best_plan(ai, "{}", [], tmp_path)
    assert best.title == "Test invalid schema errors"


def test_rationale_does_not_make_narrow_title_overbroad():
    plan = make_plan("Test invalid schema errors", rationale="count, seed, PII, LLM paths exist")
    assert capability_signature(plan)[0] == "schema-validation"


def test_broad_title_rejected(tmp_path):
    ai = Mock()
    ai.plan.side_effect = [
        make_plan("Test schema and count and seed", .99),
        make_plan("Test count and PII", .99),
        make_plan("Test schema and LLM", .99),
    ]
    assert select_best_plan(ai, "{}", [], tmp_path) is None


def test_same_capability_does_not_win_twice(tmp_path):
    ai = Mock()
    ai.plan.side_effect = [
        make_plan("Test invalid schema", .82),
        make_plan("Test malformed schema", .99),
        make_plan("Test count boundaries", .78),
    ]
    best = select_best_plan(ai, "{}", [], tmp_path)
    assert best.title == "Test invalid schema"


def test_oversized_candidate_falls_back_to_eligible_plan(tmp_path):
    large = tmp_path / "tests" / "test_openai_provider.py"
    large.parent.mkdir()
    large.write_text("x" * 18001)
    small = tmp_path / "tests" / "test_mock_provider.py"
    small.write_text("def test_existing(): assert True")

    ai = Mock()
    ai.plan.side_effect = [
        make_plan("Test PII redaction", .95, "tests/test_openai_provider.py"),
        make_plan("Test deterministic seed", .85, "tests/test_mock_provider.py"),
        make_plan("Test schema boundaries", .75, "tests/test_openai_provider.py"),
    ]

    best = select_best_plan(ai, "{}", [], tmp_path)

    assert best is not None
    assert best.target_files == ["tests/test_mock_provider.py"]
    assert ai.plan.call_count == 3


def test_all_oversized_candidates_return_no_actionable_work(tmp_path):
    large = tmp_path / "tests" / "test_openai_provider.py"
    large.parent.mkdir()
    large.write_text("x" * 18001)

    ai = Mock()
    ai.plan.side_effect = [
        make_plan("Test PII redaction", .95, "tests/test_openai_provider.py"),
        make_plan("Test deterministic seed", .85, "tests/test_openai_provider.py"),
        make_plan("Test schema boundaries", .75, "tests/test_openai_provider.py"),
    ]

    assert select_best_plan(ai, "{}", [], tmp_path) is None
    assert ai.plan.call_count == 3


def test_file_at_size_limit_remains_eligible(tmp_path):
    target = tmp_path / "tests" / "test_openai_provider.py"
    target.parent.mkdir()
    target.write_text("x" * 18000)

    ai = Mock()
    ai.plan.side_effect = [
        make_plan("Test PII redaction", .95, "tests/test_openai_provider.py"),
        make_plan("Test deterministic seed", .85, "tests/test_openai_provider.py"),
        make_plan("Test schema boundaries", .75, "tests/test_openai_provider.py"),
    ]

    assert select_best_plan(ai, "{}", [], tmp_path) is not None


def test_ranked_candidates_enable_fallback_after_empty_diff(tmp_path):
    ai = Mock()
    ai.plan.side_effect = [
        make_plan("Test deterministic schema", .90),
        make_plan("Test count boundaries", .80),
        make_plan("Test PII blocking", .70),
    ]
    plans = select_best_plan(ai, "{}", [], tmp_path, return_candidates=True)
    assert len(plans) == 3
    assert [p.title for p in plans] == [
        "Test deterministic schema", "Test count boundaries", "Test PII blocking"
    ]


def test_ranked_candidates_preserve_rejection_of_oversized_file(tmp_path):
    large = tmp_path / "tests" / "test_openai_provider.py"
    large.parent.mkdir()
    large.write_text("x" * 18001)
    ai = Mock()
    ai.plan.side_effect = [
        make_plan("Test PII redaction", .95, "tests/test_openai_provider.py"),
        make_plan("Test deterministic seed", .85, "tests/test_mock_provider.py"),
        make_plan("Test schema boundaries", .75, "tests/test_openai_provider.py"),
    ]
    plans = select_best_plan(ai, "{}", [], tmp_path, return_candidates=True)
    assert len(plans) == 1
    assert plans[0].target_files == ["tests/test_mock_provider.py"]


def test_approved_meaningful_review_below_threshold_retries():
    from agent.main import review_rejection_reason
    review = Mock(approved=True, meaningful=True, score=0.75)
    assert review_rejection_reason(review) == "review score 0.75 below 0.80"


def test_review_at_threshold_is_accepted():
    from agent.main import review_rejection_reason
    assert review_rejection_reason(Mock(approved=True, meaningful=True, score=0.80)) is None


def test_review_flags_remain_independent_of_score():
    from agent.main import review_rejection_reason
    assert review_rejection_reason(Mock(approved=False, meaningful=True, score=0.99)) == "reviewer did not approve"
    assert review_rejection_reason(Mock(approved=True, meaningful=False, score=0.99)) == "reviewer found the change non-meaningful"
