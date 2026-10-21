from agent.llm import AI
from agent.models import Implementation


class DummyPlan:
    def model_dump_json(self):
        return '{"action":"IMPLEMENT","category":"test","target_files":["tests/test_runner.py"]}'


def test_implement_checks_real_config_keys():
    agent = AI.__new__(AI)
    captured = {}
    def ask(schema, system, user):
        captured.update(system=system, user=user)
        return None
    agent.ask = ask
    agent.implement(DummyPlan(), "{}", reference_context="evaluation:\n  min_keyword_score: 0.7")
    assert "CONFIGURATION NEGATIVE TEST CONTRACT" in captured["system"]
    assert "Removing nonexistent" in captured["system"]
    assert "min_keyword_score" in captured["user"]


def test_repair_rejects_invented_config_fields():
    agent = AI.__new__(AI)
    captured = {}
    def ask(schema, system, user):
        captured.update(system=system, user=user)
        return None
    agent.ask = ask
    agent.repair(DummyPlan(), "{}", "bad fixture", "DID NOT RAISE", reference_context="evaluation:")
    assert "CONFIGURATION REPAIR CONTRACT" in captured["system"]
    assert "absent YAML section" in captured["system"]
    assert "DID NOT RAISE" in captured["user"]
