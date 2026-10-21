from unittest.mock import Mock
from agent.github_client import GitHubApp


def test_auto_merge_requires_repo_opt_in():
    gh=GitHubApp.__new__(GitHubApp)
    gh.repo_info=Mock(return_value={"allow_auto_merge":False})
    gh.request=Mock()
    assert gh.queue_native_auto_merge("owner/repo","main",{}) is False
    gh.request.assert_not_called()


def test_auto_merge_uses_github_native_rules_without_protection_rest_api():
    gh = GitHubApp.__new__(GitHubApp)
    gh.repo_info = Mock(return_value={"allow_auto_merge": True})
    pr = {
        "state": "open", "draft": True, "node_id": "PR_123",
        "base": {"ref": "main"}, "head": {"ref": "agent/maintenance"},
    }
    gh.request = Mock(side_effect=[
        {"data": {"markPullRequestReadyForReview": {"pullRequest": {
            "id": "PR_123", "isDraft": False
        }}}},
        {"data": {"enablePullRequestAutoMerge": {"pullRequest": {
            "id": "PR_123", "autoMergeRequest": {"enabledAt": "2026-10-21T00:00:00Z"}
        }}}},
    ])
    assert gh.queue_native_auto_merge("owner/repo", "main", pr) is True
    assert gh.request.call_count == 2
    assert all("/protection/" not in str(call) for call in gh.request.call_args_list)
