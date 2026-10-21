from types import SimpleNamespace
from agent.main import substantive_diff_check
from agent.llm import AI


def test_rejects_self_contained_fake_pii_evaluator():
    plan = SimpleNamespace(category="test")
    patch = """diff --git a/tests/test_pii.py b/tests/test_pii.py
+++ b/tests/test_pii.py
@@ -0,0 +1,8 @@
+class PIILeakageEvaluator:
+    def evaluate(self, text):
+        return "ssn" in text
+
+def test_pii():
+    evaluator = PIILeakageEvaluator()
+    assert evaluator.evaluate("ssn")
"""
    reason = substantive_diff_check(plan, patch, 8)
    assert reason is not None
    assert "instead of exercising production" in reason


def test_accepts_test_importing_real_evaluator():
    plan = SimpleNamespace(category="test")
    patch = """diff --git a/tests/test_keyword.py b/tests/test_keyword.py
+++ b/tests/test_keyword.py
@@ -0,0 +1,5 @@
+from llm_eval_framework.evaluators.keyword_coverage import KeywordCoverageEvaluator
+
+def test_keyword_coverage():
+    evaluator = KeywordCoverageEvaluator()
+    assert evaluator is not None
"""
    assert substantive_diff_check(plan, patch, 5) is None


def test_implementation_prompt_requires_production_binding():
    ai = AI.__new__(AI)
    captured = {}
    ai.ask = lambda schema, system, user: captured.update(system=system)
    plan = SimpleNamespace(model_dump_json=lambda: '{"category":"test"}')
    ai.implement(plan, "{}", reference_context="existing production code")
    assert "TEST-TO-PRODUCTION BINDING" in captured["system"]
    assert "Do not define a replacement" in captured["system"]
