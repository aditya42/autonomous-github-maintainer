from agent.llm import AI


class DummyPlan:
    def model_dump_json(self):
        return '{"category":"test","target_files":["tests/test_openai_provider.py"]}'


def test_implementation_requires_sdk_patch_before_constructor():
    agent = AI.__new__(AI)
    captured = {}
    agent.ask = lambda schema, system, user: captured.update(system=system, user=user)
    agent.implement(DummyPlan(), "{}", reference_context="class OpenAIProvider: pass")
    prompt = captured["system"]
    assert "OFFLINE EXTERNAL-SDK TEST CONTRACT" in prompt
    assert "BEFORE constructing" in prompt
    assert "llm_eval_framework.providers.openai_provider.OpenAI" in prompt
    assert "Do not mock the provider itself" in prompt


def test_repair_preserves_offline_provider_isolation():
    agent = AI.__new__(AI)
    captured = {}
    agent.ask = lambda schema, system, user: captured.update(system=system, user=user)
    agent.repair(DummyPlan(), "{}", "diff", "OpenAIError", reference_context="client = OpenAI(...)")
    assert "OFFLINE EXTERNAL-SDK TEST CONTRACT" in captured["system"]
    assert "No real credentials or network traffic" in captured["system"]


def test_review_rejects_live_sdk_unit_tests():
    agent = AI.__new__(AI)
    captured = {}
    agent.ask = lambda schema, system, user: captured.update(system=system, user=user)
    agent.review(DummyPlan(), "diff", "passed")
    assert "network-dependent unit tests" in captured["system"]
