from pathlib import Path
import pytest

from agent.main import validate_python_provider_calls


def check(tmp_path: Path, snippet: str):
    tests = tmp_path / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_openai_provider.py").write_text(
        "from llm_eval_framework.providers.openai_provider import OpenAIProvider\n"
        + snippet
    )
    validate_python_provider_calls(tmp_path, ["tests/test_openai_provider.py"])


def test_unpatched_constructor_rejected(tmp_path):
    with pytest.raises(ValueError, match="requires patch"):
        check(tmp_path, "def test_normal():\n    provider = OpenAIProvider()\n")


def test_decorated_patch_accepted(tmp_path):
    check(tmp_path,
          "from unittest.mock import patch\n"
          "@patch('llm_eval_framework.providers.openai_provider.OpenAI')\n"
          "def test_normal(mock_openai):\n"
          "    provider = OpenAIProvider()\n")


def test_context_manager_patch_accepted(tmp_path):
    check(tmp_path,
          "from unittest.mock import patch\n"
          "def test_normal():\n"
          "    with patch('llm_eval_framework.providers.openai_provider.OpenAI'):\n"
          "        provider = OpenAIProvider()\n")


def test_unsupported_prompt_rejection_contract_rejected(tmp_path):
    with pytest.raises(ValueError, match="prompt-type rejection"):
        check(tmp_path,
              "from unittest.mock import patch\n"
              "@patch('llm_eval_framework.providers.openai_provider.OpenAI')\n"
              "def test_generate_raises_on_none_prompt(mock_openai):\n"
              "    provider = OpenAIProvider()\n")
