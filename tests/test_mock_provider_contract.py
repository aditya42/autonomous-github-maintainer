from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.main import validate_python_provider_calls
from agent.llm import AI


def test_reject_module_level_generate(tmp_path):
    p = tmp_path / "tests"
    p.mkdir()
    (p / "test_mock.py").write_text(
        "from llm_eval_framework.providers import mock_provider\n"
        "def test_mock():\n"
        "    assert mock_provider.generate('hello')\n"
    )
    with pytest.raises(ValueError, match="instantiate its provider class"):
        validate_python_provider_calls(tmp_path, ["tests/test_mock.py"])


def test_accept_real_instance_call(tmp_path):
    p = tmp_path / "tests"
    p.mkdir()
    source = tmp_path / "src/llm_eval_framework/providers"
    source.mkdir(parents=True)
    (source / "mock_provider.py").write_text(
        "class MockProvider:\n"
        "    def generate(self, prompt): return prompt\n"
    )
    (p / "test_mock.py").write_text(
        "from llm_eval_framework.providers.mock_provider import MockProvider\n"
        "def test_mock():\n"
        "    provider = MockProvider()\n"
        "    assert provider.generate('hello') == 'hello'\n"
    )
    validate_python_provider_calls(tmp_path, ["tests/test_mock.py"])


def test_agent_prompts_include_mock_contract():
    agent = AI.__new__(AI)
    seen = []
    agent.ask = lambda schema, system, user: seen.append(system)
    plan = SimpleNamespace(model_dump_json=lambda: '{"category":"test"}')
    agent.implement(plan, "{}")
    agent.repair(plan, "{}", "diff", "AttributeError")
    agent.review(plan, "diff", "passed")
    assert all("MockProvider" in system for system in seen)
    assert "MOCK PROVIDER VERIFIED CONTRACT" in seen[0]
    assert "latency_ms" in seen[1]
