from agent.llm import AI


class Plan:
    def model_dump_json(self):
        return '{"category":"test","target_files":["tests/test_openai_provider.py"]}'


def capture(agent, method, *args, **kwargs):
    holder = {}
    agent.ask = lambda schema, system, user: holder.update(system=system, user=user)
    getattr(agent, method)(*args, **kwargs)
    return holder["system"]


def test_generation_uses_actual_sdk_contract():
    ai = AI.__new__(AI)
    rules = capture(ai, "implement", Plan(), "{}", reference_context="OpenAIProvider")
    assert "OPENAI PROVIDER VERIFIED CONTRACT" in rules
    assert 'OpenAI(api_key=os.getenv("OPENAI_API_KEY"))' in rules
    assert "MagicMock" in rules
    assert "Never import openai.error" in rules
    assert "NO PII redaction" in rules


def test_repair_preserves_actual_provider_contract():
    ai = AI.__new__(AI)
    rules = capture(ai, "repair", Plan(), "{}", "diff", "ModuleNotFoundError")
    assert "OPENAI PROVIDER VERIFIED CONTRACT" in rules
    assert "return edits=[]" in rules
    assert "RuntimeError" in rules


def test_review_rejects_invented_provider_behavior():
    ai = AI.__new__(AI)
    rules = capture(ai, "review", Plan(), "diff", "passed")
    assert "legacy" in rules
    assert "absent from production source" in rules
