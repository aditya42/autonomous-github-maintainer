from unittest.mock import Mock, patch
import pytest
from agent.main import push_persistent_maintenance_branch


def fixture_client(prs=None, open_pr=False):
    gh=Mock()
    gh.open_agent_pr_exists.return_value=open_pr
    gh.token.return_value="fake-token"
    gh.request.return_value=prs or []
    return gh


def merged_pr(sha):
    return {"merged_at":"2026-10-01T00:00:00Z",
            "base":{"ref":"main"},
            "head":{"ref":"agent/maintenance","sha":sha}}


def test_absent_branch_created_without_force():
    gh=fixture_client()
    with patch("agent.main.git",side_effect=["", ""]) as git:
        assert push_persistent_maintenance_branch("/tmp",gh,"owner/repo","main","agent/maintenance")=="created"
    assert not any("force" in str(call) for call in git.call_args_list)


def test_merged_branch_reused_with_exact_sha_lease():
    sha="a"*40
    gh=fixture_client([merged_pr(sha)])
    with patch("agent.main.git",side_effect=[f"{sha}\trefs/heads/agent/maintenance", ""]) as git:
        assert push_persistent_maintenance_branch("/tmp",gh,"owner/repo","main","agent/maintenance")=="reused"
    assert f"--force-with-lease=refs/heads/agent/maintenance:{sha}" in git.call_args_list[1].args


def test_unmerged_branch_cannot_be_overwritten():
    sha="b"*40
    gh=fixture_client([])
    with patch("agent.main.git",return_value=f"{sha}\trefs/heads/agent/maintenance") as git:
        with pytest.raises(ValueError,match="verified previously merged"):
            push_persistent_maintenance_branch("/tmp",gh,"owner/repo","main","agent/maintenance")
    assert git.call_count==1


def test_open_pr_blocks_branch_reuse():
    gh=fixture_client(open_pr=True)
    with patch("agent.main.git") as git:
        with pytest.raises(ValueError,match="Another agent PR"):
            push_persistent_maintenance_branch("/tmp",gh,"owner/repo","main","agent/maintenance")
    git.assert_not_called()


def test_closed_unmerged_pr_does_not_authorize_reuse():
    sha="c"*40
    item=merged_pr(sha);item["merged_at"]=None
    gh=fixture_client([item])
    with patch("agent.main.git",return_value=f"{sha}\trefs/heads/agent/maintenance"):
        with pytest.raises(ValueError):
            push_persistent_maintenance_branch("/tmp",gh,"owner/repo","main","agent/maintenance")
