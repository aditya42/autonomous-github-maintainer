from unittest.mock import Mock
from agent.github_client import GitHubApp


def test_auto_merge_requires_repo_opt_in():
    gh=GitHubApp.__new__(GitHubApp)
    gh.repo_info=Mock(return_value={"allow_auto_merge":False})
    gh.request=Mock()
    assert gh.queue_native_auto_merge("owner/repo","main",{}) is False
    gh.request.assert_not_called()
