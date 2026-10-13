from pathlib import Path
from unittest.mock import Mock
import json

from agent.main import is_test_file, read_only_contract_context, select_best_plan
from agent.llm import AI
from agent.models import Plan


def plan(paths, score=0.8):
    return Plan(action="IMPLEMENT", title="Test PII scanner handling",
                rationale="PII validation", category="test", target_files=paths,
                risk="low", meaningfulness=score, defect_risk=score,
                regression_prevention=score, impact=score, maintainability=score,
                evidence_file="packages/core/src/pii.ts")


def test_test_paths_distinguish_production():
    assert is_test_file("packages/core/test/core.test.ts")
    assert is_test_file("tests/test_contract.py")
    assert not is_test_file("packages/core/src/pii.ts")
    assert not is_test_file("src/test_helpers.ts")


def test_contracts_include_scanner_even_when_only_tests_are_editable(tmp_path):
    src = tmp_path / "packages/core/src"
    src.mkdir(parents=True)
    (src / "pii.ts").write_text("const SAFE_TEST_CARDS = new Set(['4111111111111111']);")
    result = read_only_contract_context(tmp_path, ["packages/core/src/pii.ts"],
                                        plan(["packages/core/test/core.test.ts"]))
    assert "SAFE_TEST_CARDS" in result["packages/core/src/pii.ts"]


def test_contract_reference_not_in_edit_targets(tmp_path):
    src = tmp_path / "packages/core/src"
    src.mkdir(parents=True)
    (src / "pii.ts").write_text("export const x = 1;")
    p = plan(["packages/core/test/core.test.ts"])
    assert set(read_only_contract_context(tmp_path, ["packages/core/src/pii.ts"], p)).isdisjoint(p.target_files)


def test_candidate_rejects_test_plan_editing_production(tmp_path):
    ai = Mock()
    ai.plan.return_value = plan(["packages/core/test/core.test.ts", "packages/core/src/pii.ts"])
    assert select_best_plan(ai, "{}", [], tmp_path) is None


def test_implement_receives_read_only_contracts():
    client = AI.__new__(AI)
    client.ask = Mock(return_value="ok")
    p = plan(["packages/core/test/core.test.ts"])
    assert client.implement(p, "{}", reference_context="SAFE_TEST_CARDS") == "ok"
    assert "SAFE_TEST_CARDS" in client.ask.call_args.args[2]


def test_repair_receives_contracts():
    client = AI.__new__(AI)
    client.ask = Mock(return_value="ok")
    p = plan(["packages/core/test/core.test.ts"])
    assert client.repair(p, "{}", "patch", "error", reference_context="SAFE_TEST_CARDS") == "ok"
    assert "SAFE_TEST_CARDS" in client.ask.call_args.args[2]
