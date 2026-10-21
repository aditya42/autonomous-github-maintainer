from pathlib import Path
from types import SimpleNamespace
import pytest

from agent.main import validate_python_provider_calls


def repo_with_provider(tmp_path: Path):
    source = tmp_path / "src/pkg/providers"
    source.mkdir(parents=True)
    (source / "openai_provider.py").write_text(
        "class OpenAIProvider:\n"
        "    def __init__(self, model='test'): pass\n"
        "    def generate(self, prompt: str): return prompt\n"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    return tests / "test_provider.py"


def test_rejects_fabricated_completion_method(tmp_path):
    test_file = repo_with_provider(tmp_path)
    test_file.write_text(
        "from pkg.providers.openai_provider import OpenAIProvider\n"
        "provider = OpenAIProvider()\n"
        "def test_provider():\n"
        "    assert provider.completion(prompt='hello')\n"
    )
    with pytest.raises(ValueError, match="Unsupported production method"):
        validate_python_provider_calls(tmp_path, ["tests/test_provider.py"])


def test_rejects_fabricated_provider_keyword(tmp_path):
    test_file = repo_with_provider(tmp_path)
    test_file.write_text(
        "from pkg.providers.openai_provider import OpenAIProvider\n"
        "provider = OpenAIProvider()\n"
        "def test_provider():\n"
        "    assert provider.generate('hello', top_p=0.5)\n"
    )
    with pytest.raises(ValueError, match="Unsupported arguments"):
        validate_python_provider_calls(tmp_path, ["tests/test_provider.py"])


def test_accepts_real_generate_contract(tmp_path):
    test_file = repo_with_provider(tmp_path)
    test_file.write_text(
        "from pkg.providers.openai_provider import OpenAIProvider\n"
        "provider = OpenAIProvider()\n"
        "def test_provider():\n"
        "    assert provider.generate(prompt='hello') == 'hello'\n"
    )
    validate_python_provider_calls(tmp_path, ["tests/test_provider.py"])
