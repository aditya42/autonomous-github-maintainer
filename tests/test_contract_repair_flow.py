"""Regression checks for sharing one repair attempt across contract and Docker failures."""
import inspect

from agent.main import run_one


def test_provider_contract_failure_enters_existing_repair_path():
    source = inspect.getsource(run_one)
    assert 'valid, result = False, f"Provider API contract violation: {exc}"' in source
    assert 'repaired = ai.repair(' in source
    assert source.index('valid, result = False, f"Provider API contract violation: {exc}"') < source.index('repaired = ai.repair(')
    assert 'validate_python_provider_calls(root, repaired_paths)' in source
    assert 'REPAIR ATTEMPT 1/1' in source
