import pytest
from pathlib import Path
from agent.policy import safe_path, load_config
from agent.main import inventory, apply_edits, diff_quality
from agent.models import Plan, Edit, Implementation
from agent.github_client import git


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "tester@example.com")
    git(tmp_path, "config", "user.name", "Tester")
    (tmp_path / "module.py").write_text("VALUE = 1\n")
    (tmp_path / "README.md").write_text("# Project\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "baseline")
    return tmp_path


@pytest.mark.parametrize(
    "p",
    [
        "../x.py",
        "/etc/passwd",
        ".env",
        "config/private.pem",
        ".github/workflows/w.yml",
        "node_modules/x.py",
        "foo\\..\\x.py",
    ],
)
def test_reject_bad_paths(repo, p):
    with pytest.raises(ValueError):
        safe_path(repo, p)


def test_inventory(repo):
    assert "module.py" in inventory(repo)
    assert ".git/config" not in inventory(repo)


def test_apply_and_diff(repo):
    plan = Plan(
        action="IMPLEMENT",
        title="Change value",
        rationale="test",
        category="test",
        target_files=["module.py"],
        meaningfulness=0.9,
        risk="low",
    )
    impl = Implementation(
        explanation="test", edits=[Edit(path="module.py", content="VALUE = 2\n")]
    )
    apply_edits(repo, plan, impl)
    git(repo, "add", "--", "module.py")
    patch, count = diff_quality(repo)
    assert "+VALUE = 2" in patch
    assert count == 2


def test_out_of_scope_rejected(repo):
    plan = Plan(
        action="IMPLEMENT",
        title="Task",
        rationale="test",
        category="test",
        target_files=["module.py"],
        meaningfulness=0.9,
        risk="low",
    )
    impl = Implementation(
        explanation="test", edits=[Edit(path="README.md", content="different")]
    )
    with pytest.raises(ValueError):
        apply_edits(repo, plan, impl)


def test_config_loads():
    cfg = load_config("config/repositories.yaml")
    assert len(cfg["repositories"]) == 3
