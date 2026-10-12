from pathlib import Path
from unittest.mock import Mock
import json

from agent.main import build_evidence_catalog, evidence_rejection, select_best_plan
from agent.models import Plan


SOURCE = "export function validateCount(value) { return value > 0; }\n"


def make_plan(quote=SOURCE[0:45], path="apps/api/src/route.ts", score=0.9):
    return Plan(action="IMPLEMENT", title="Test invalid schema handling",
                rationale="A focused regression gap", category="test",
                target_files=["tests/schema.test.mjs"], meaningfulness=score,
                risk="low", defect_risk=score, regression_prevention=score,
                impact=score, maintainability=score, evidence_file=path,
                evidence_quote=quote,
                missing_behavior="A malformed request is not covered by the current smoke tests")


def test_catalog_reads_source_and_test_files(tmp_path):
    (tmp_path / "apps/api/src").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "apps/api/src/route.ts").write_text(SOURCE)
    (tmp_path / "tests/schema.test.mjs").write_text("test('valid request', ()=>{});\n")
    c = build_evidence_catalog(tmp_path, ["apps/api/src/route.ts", "tests/schema.test.mjs"])
    assert "apps/api/src/route.ts" in c
    assert "tests/schema.test.mjs" in c


def test_exact_evidence_is_accepted():
    assert evidence_rejection(make_plan(), {"apps/api/src/route.ts": SOURCE}) is None


def test_invented_quote_rejected():
    assert evidence_rejection(make_plan(quote="invented response status is 422"),
                              {"apps/api/src/route.ts": SOURCE}) is not None


def test_uninspected_source_rejected():
    assert evidence_rejection(make_plan(path="secret.py"),
                              {"apps/api/src/route.ts": SOURCE}) is not None


def test_no_evidence_skips_before_implementation(tmp_path):
    ai = Mock()
    ai.plan.return_value = make_plan(quote="nonexistent exact quote content")
    ctx = json.dumps({"evidence_catalog": {"apps/api/src/route.ts": SOURCE}})
    assert select_best_plan(ai, ctx, ["apps/api/src/route.ts"], tmp_path) is None
    assert ai.plan.call_count == 3
