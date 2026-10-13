from unittest.mock import Mock

from agent.llm import AI
from agent.models import Plan


def plan():
    return Plan(action="IMPLEMENT", title="Test PII detection", rationale="Coverage",
                category="test", target_files=["packages/core/test/core.test.ts"],
                meaningfulness=0.7, risk="low")


def test_implementation_prompt_handles_overlapping_pii_patterns():
    ai = AI.__new__(AI)
    ai.ask = Mock(return_value="ok")
    assert ai.implement(plan(), "{}", reference_context="SCANNER SOURCE") == "ok"
    system, user = ai.ask.call_args.args[1:]
    assert "patterns overlap" in system
    assert "total findings.length === 1" in system
    assert "absence of the PROHIBITED kind" in system
    assert "SCANNER SOURCE" in user


def test_repair_prompt_preserves_detection_assertions_and_checks_allowlist():
    ai = AI.__new__(AI)
    ai.ask = Mock(return_value="ok")
    assert ai.repair(plan(), "{}", "failing patch", "expected 2 to be 1",
                     reference_context="PII IMPLEMENTATION") == "ok"
    system, user = ai.ask.call_args.args[1:]
    assert "patterns overlap" in system
    assert "Preserve the positive detection" in system
    assert "PII IMPLEMENTATION" in user
    assert "expected 2 to be 1" in user


def test_reviewer_rejects_invented_exclusivity():
    ai = AI.__new__(AI)
    ai.ask = Mock(return_value="ok")
    assert ai.review(plan(), "diff", "passed") == "ok"
    system = ai.ask.call_args.args[1]
    assert "invented exclusivity" in system
    assert "drop positive security assertions" in system
