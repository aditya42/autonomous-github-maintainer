# Autonomous GitHub Maintainer

A **PR-only, test-gated, AI-assisted** maintenance agent for selected GitHub repositories. It runs daily, picks at most one meaningful low-risk change per repo, verifies the repo was green **before and after** changing it, asks a separate AI reviewer to inspect the diff, and creates a **draft PR**, never an automatic merge.

> No filler commits. No guarantee a code change is perfect. No direct pushes to main. Agent-authored commits are attributed to the GitHub App and reviewable as AI-produced changes.

## What you need

- Python 3.12+
- Docker Desktop / Docker Engine **running**
- Git installed
- An OpenAI API key (API usage is billed separately from ChatGPT plans)
- A GitHub App installed on explicitly selected repos
- A target repo with a deterministic validation command that can pass in Docker without private application credentials

## 1. Local install

```bash
unzip autonomous-github-maintainer.zip
cd autonomous-github-maintainer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
python -m agent.main --doctor
```

On Windows PowerShell: `.venv\Scripts\Activate.ps1` instead of `source ...`.

## 2. Create your GitHub App

Go to **GitHub > Settings > Developer settings > GitHub Apps > New GitHub App**.

- Give it a name such as `my-repo-maintainer`.
- Webhook: **Active = false** (this scheduler doesn't need inbound webhooks).
- Permissions: **Contents: read/write**, **Pull requests: read/write**, **Issues: read**, **Metadata: read** (automatic).
- Don't grant **Workflows**, **Administration**, or organization-wide permissions.
- Install **Only on select repositories**; start with a small, non-critical public repo.
- Generate a **private key** from the App settings and record the **App ID**.
- Record **installation ID** from the installation URL (`.../installations/1234567`).

A separate GitHub App installation may be required for repositories in different owners/orgs. This MVP assumes the enabled repositories are within **one installation**.

## 3. Configure targets

Edit `config/repositories.yaml`. Replace `YOUR_GITHUB_USERNAME/...` with **real owner/repo names** and set `enabled: true` for only **one** repo initially.

The `validation.commands` are trusted by the central agent (they do not come from target repo text). They run inside a Docker container against a checkout. Use commands that build/test **your actual project**, including service integration checks if needed. Pick a Docker image that contains your tools, or build/publish your own testing image.

**Important:** The Playwright example's image tag is illustrative. Match its version to the installed Playwright package; update the image tag if needed. Apps requiring PostgreSQL, Kubernetes, Xcode/macOS, proprietary packages, SMS, environment credentials or live third-party APIs **are not supported by the sample Docker validation as-is**. Configure a real isolated integration environment or do not enable those repos. `npm run lint --if-present` does not substitute for a required lint script.

## 4. Set credentials locally

The program reads environment variables directly. You can set them manually:

```bash
export OPENAI_API_KEY='sk-...'
export OPENAI_MODEL='gpt-4.1-mini'
export GH_APP_ID='your-app-id'
export GH_APP_INSTALLATION_ID='your-installation-id'
export GH_APP_PRIVATE_KEY="$(cat /secure/path/to/github-app-private-key.pem)"
```

Do **not** use `source .env` with an untrusted or incorrectly quoted key. `.env.example` documents expected variables but is not automatically loaded. Never commit the PEM or paste keys into issues.

## 5. Start with dry-run

```bash
# config and syntax only; no keys needed
python -m agent.main --doctor

# actual repo analysis + model calls + Docker baseline and post-change checks
# NO push, NO PR, NO remote modifications
python -m agent.main --dry-run --repo YOUR_GITHUB_USERNAME/YOUR_REPO
```

If the repo is healthy and the model found a task, expect `dry-run: validated proposal`. If no suitable change exists, **SKIP is expected**.

## 6. Create a draft PR

```bash
python -m agent.main --repo YOUR_GITHUB_USERNAME/YOUR_REPO
```

Result: short-lived `agent/YYYYMMDD-...` branch, a commit authored by the bot and a **draft pull request**. Your review and manual merge are required. No direct push to the default branch. Run a PR's full CI before merging.

## 7. Enable the daily scheduler

Push this project to a **private central GitHub repository**. In that repository's **Settings > Secrets and variables > Actions**, create secrets:

| Secret | Value |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key |
| `GH_APP_ID` | GitHub App ID |
| `GH_APP_INSTALLATION_ID` | App installation ID |
| `GH_APP_PRIVATE_KEY` | Complete PEM text (including BEGIN/END lines) |

Optional variable `OPENAI_MODEL` defaults to `gpt-4.1-mini`. The workflow is in `.github/workflows/daily-maintenance.yml` and schedules **07:37 Asia/Kolkata**. You can first use **Actions > Autonomous Repository Maintenance > Run workflow** with **dry_run=true**. On scheduled days, `dry_run` is not set, so the job creates draft PRs when all gates pass. If you want *scheduled dry-run only* at first, explicitly add `--dry-run` to the scheduled command before enabling the workflow.

## Implementation details

1. Retrieve a short-lived repository-scoped GitHub App installation token.
2. Clone the default branch using a temporary auth header; do not persist credentials in git config or remote URLs.
3. Skip repos with existing open agent PRs.
4. Read a small inventory, top-level docs, and open issue titles/descriptions.
5. Ask OpenAI for a **structured** low-risk task or SKIP.
6. Limit changes to **four existing text files**, under **160 changed lines**; block `.github`, secret files, symlink paths, binary artifacts and new files.
7. Verify baseline in resource-limited Docker, then modify and run exactly the same validation again.
8. Independently review the diff and test log; if approved, create a draft PR.

## Safety limitations

- Target repos and their dependencies can run arbitrary code in Docker. The agent provides **no secrets to those containers**, drops Linux capabilities, caps resources, and does not mount the Docker socket, but the validation container **has network access** for dependency downloads. For highly untrusted repos, disable network and use prebuilt images or isolated ephemeral runners. A Docker daemon is highly privileged: use a dedicated runner for production.
- GitHub Actions does not automatically provide external runtime prerequisites. For **Kubernetes, Xcode, live databases, or live integrations**, customize validation and isolate those repositories until checks are reliable.
- The model could miss faults; green tests are not proof of correctness. Draft PRs require human review, and protected default branches plus repository CI remain essential.
- Review LLM output and Docker logs; both can contain data from repo contents, so only connect repos whose content is permitted to be sent to the API.
- The selected files must already exist. This MVP intentionally does not allow adding new tests or editing workflow YAML. Expand scope only after hardening.
- No automatic remediation/retry if validation fails. It skips safely.
- The `max_repositories_per_run` first-N policy is static rather than round-robin; change the order manually or extend scheduling if you want rotation.
- API calls and repeated fresh dependency downloads can incur cost. Set budgets and limits.

## Suggested next steps

Add a test-environment adapter for service-backed projects, per-repo rotating selection, proper code coverage diff analysis, GitHub issue labeling, audit log artifacts, branch cleanup, PR check-result polling, and explicit owner-approved auto-merge policies. Keep protected branches mandatory.
