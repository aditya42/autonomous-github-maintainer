import base64
import os
import subprocess
import time
from pathlib import Path

import jwt
import requests

API = "https://api.github.com"


class GitHubApp:
    def __init__(self):
        self.app_id = os.environ["GH_APP_ID"]
        self.installation_id = os.environ["GH_APP_INSTALLATION_ID"]
        key = os.environ["GH_APP_PRIVATE_KEY"]
        self.key = key.replace("\\n", "\n")
        self._token = None
        self._expiry = 0

    def token(self, repo=None):
        if not repo and self._token and time.time() < self._expiry - 120:
            return self._token
        now = int(time.time())
        assertion = jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": self.app_id},
            self.key,
            algorithm="RS256",
        )
        body = {"repositories": [repo.split("/")[1]]} if repo else {}
        resp = requests.post(
            f"{API}/app/installations/{self.installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {assertion}",
                "Accept": "application/vnd.github+json",
            },
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        token = resp.json()["token"]
        if not repo:
            self._token, self._expiry = token, time.time() + 3300
        return token

    def request(self, method, endpoint, repo=None, **kwargs):
        tok = self.token(repo)
        resp = requests.request(
            method,
            API + endpoint,
            headers={
                "Authorization": f"Bearer {tok}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
            **kwargs,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"GitHub HTTP {resp.status_code}: {resp.text[:400]}")
        return resp.json() if resp.content else {}

    def repo_info(self, name):
        return self.request("GET", f"/repos/{name}", repo=name)

    def open_agent_pr_exists(self, name):
        owner = name.split("/")[0]
        prs = self.request(
            "GET",
            f"/repos/{name}/pulls",
            repo=name,
            params={"state": "open", "per_page": 100},
        )
        return any(
            p["head"]["ref"].startswith("agent/")
            and p["head"]["repo"]["owner"]["login"].lower() == owner.lower()
            for p in prs
        )

    def issues(self, name):
        items = self.request(
            "GET",
            f"/repos/{name}/issues",
            repo=name,
            params={"state": "open", "per_page": 15},
        )
        return [
            {
                "number": x["number"],
                "title": x["title"],
                "body": (x.get("body") or "")[:1000],
            }
            for x in items
            if "pull_request" not in x
        ]

    def pull_request(self, name, branch, base, title, body):
        return self.request(
            "POST",
            f"/repos/{name}/pulls",
            repo=name,
            json={
                "title": title,
                "head": branch,
                "base": base,
                "body": body,
                "draft": True,
            },
        )


def git(repo_dir, *args, token=None, check=True):
    # The temporary HTTP header is injected into git via environment, not persisted
    # in remotes or disk. Never log its value.
    env = os.environ.copy()
    if token:
        auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {auth}",
            }
        )
    p = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    if check and p.returncode:
        raise RuntimeError(
            f"git {args[0]} failed (exit {p.returncode}): {p.stderr[-900:]}"
        )
    return p.stdout.strip()


def clone_repository(name, target: Path, token: str, branch: str):
    target.parent.mkdir(parents=True, exist_ok=True)
    git(
        target.parent,
        "clone",
        "--depth",
        "1",
        "--branch",
        branch,
        f"https://github.com/{name}.git",
        str(target),
        token=token,
    )
    git(target, "config", "user.name", "repo-maintainer[bot]")
    git(target, "config", "user.email", "repo-maintainer[bot]@users.noreply.github.com")
