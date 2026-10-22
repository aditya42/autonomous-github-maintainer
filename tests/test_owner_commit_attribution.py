"""Prevent GitHub App squash merging from obscuring the source commit author."""
from pathlib import Path

from agent import github_client


def test_auto_merge_preserves_authored_commit():
    source = Path(github_client.__file__).read_text()
    assert "mergeMethod:REBASE" in source
    assert "mergeMethod:SQUASH" not in source


def test_clone_sets_owner_commit_identity(monkeypatch, tmp_path):
    calls = []
    monkeypatch.delenv("MAINTAINER_COMMIT_NAME", raising=False)
    monkeypatch.delenv("MAINTAINER_COMMIT_EMAIL", raising=False)
    monkeypatch.setattr(github_client, "git", lambda *args, **kwargs: calls.append(args))
    github_client.clone_repository(
        "aditya42/llm-evaluation-framework",
        tmp_path / "checkout", "fake-token", "main"
    )
    assert any(cmd[1:] == ("config", "user.name", "aditya42") for cmd in calls)
    assert any(cmd[1:] == ("config", "user.email", "dhendeaditya510@gmail.com") for cmd in calls)
