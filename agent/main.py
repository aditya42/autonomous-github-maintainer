import argparse
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .github_client import GitHubApp, clone_repository, git
from .policy import DEFAULT_EXCLUDES, load_config, safe_path, safe_new_test_path
from .validation import validate_in_docker

log = logging.getLogger("maintainer")


def inventory(root: Path) -> list[str]:
    files = []
    for p in root.rglob("*"):
        if any(part in DEFAULT_EXCLUDES for part in p.relative_to(root).parts):
            continue
        if p.is_file() and not p.is_symlink():
            try:
                safe_path(root, p.relative_to(root).as_posix(), existing=True)
                if p.stat().st_size <= 25000:
                    files.append(p.relative_to(root).as_posix())
            except ValueError:
                continue
    return sorted(files)[:350]


def read_files(root, paths, max_bytes=40000):
    result = {}
    for name in paths:
        path = safe_path(root, name)
        if path.is_file():
            safe_path(root, name, existing=True)
            if path.stat().st_size > 18000:
                raise ValueError(f"Target file too large: {name}")
            result[name] = {"exists": True, "content": path.read_text(encoding="utf-8")}
        else:
            safe_new_test_path(root, name)
            result[name] = {"exists": False, "content": ""}
    if sum(len(v["content"].encode()) for v in result.values()) > max_bytes:
        raise ValueError("Selected files exceed context limit")
    return result


def apply_edits(root, plan, impl):
    if not impl.edits or len(impl.edits) > 4:
        raise ValueError("No edits or too many edits")
    allowed = set(plan.target_files)
    seen = set()
    # Validate the complete edit set BEFORE writing any file.
    prepared = []
    for edit in impl.edits:
        if edit.path not in allowed or edit.path in seen:
            raise ValueError("Edit outside task scope or duplicate")
        seen.add(edit.path)
        path = safe_path(root, edit.path)
        if path.is_file():
            safe_path(root, edit.path, existing=True)
        else:
            safe_new_test_path(root, edit.path)
        if not edit.content.strip() or len(edit.content.encode()) > 20000:
            raise ValueError("Edited file empty or too large")
        prepared.append((path, edit.content))
    for path, content in prepared:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def diff_quality(repo):
    # Diff the staged changes, including newly created (formerly untracked) files.
    changed = git(repo, "diff", "--cached", "--name-only").splitlines()
    if not changed or len(changed) > 4:
        raise ValueError("Empty diff or excessive file count")
    for name in changed:
        safe_path(repo, name, existing=True)
    statuses = git(repo, "diff", "--cached", "--name-status").splitlines()
    for entry in statuses:
        status, _, name = entry.partition("\t")
        if status not in {"A", "M"}:
            raise ValueError(f"Prohibited staged change: {entry}")
        if status == "A":
            safe_new_test_staged(repo, name)
    numstat = git(repo, "diff", "--cached", "--numstat")
    changed_lines = 0
    for line in numstat.splitlines():
        a, b, _ = line.split("\t", 2)
        if not a.isdigit() or not b.isdigit():
            raise ValueError("Binary changes not allowed")
        changed_lines += int(a) + int(b)
    if changed_lines > 160:
        raise ValueError(f"Diff exceeds 160 lines: {changed_lines}")
    patch = git(repo, "diff", "--cached", "--no-ext-diff", "--unified=3")
    if not patch.strip():
        raise ValueError("No actual change")
    return patch, changed_lines


def safe_new_test_staged(repo, name):
    # Existing file in the working tree, but newly added to git index.
    # Temporarily validate naming/directory restrictions without requiring absence.
    path = safe_path(repo, name, existing=True)
    rel = path.relative_to(repo)
    if not any(
        part.lower() in {"tests", "test", "__tests__"} for part in rel.parts[:-1]
    ):
        raise ValueError(f"New staged file outside test directories: {name}")
    import re as _re

    if not (
        _re.fullmatch(r"test_[\w.-]+\.py", rel.name)
        or _re.fullmatch(r"[\w.-]+(?:\.test|\.spec)\.(?:mjs|js|jsx|ts|tsx)", rel.name)
        or _re.fullmatch(r"[A-Z][\w]*Test\.java", rel.name)
        or _re.fullmatch(r"[\w]+_test\.go", rel.name)
        or _re.fullmatch(r"[\w.-]+Tests?\.swift", rel.name)
    ):
        raise ValueError(f"New staged file name not an approved test: {name}")


def run_one(repo_cfg, gh, ai, args):
    name = repo_cfg["repo"]
    log.info("Examining %s", name)
    info = gh.repo_info(name)
    base = info["default_branch"]
    if gh.open_agent_pr_exists(name):
        log.info("SKIP %s: existing open agent PR", name)
        return "skip: open agent PR"
    with tempfile.TemporaryDirectory(prefix="repo-maintainer-") as tmp:
        root = Path(tmp) / "checkout"
        token = gh.token(name)
        clone_repository(name, root, token, base)
        names = inventory(root)
        issues = gh.issues(name)
        intro = {}
        for doc in (
            "README.md",
            "MAINTENANCE.md",
            "pyproject.toml",
            "package.json",
            "pom.xml",
        ):
            if doc in names:
                intro[doc] = (root / doc).read_text(encoding="utf-8")[:5500]
        # Provide a small representative source sample: planning from filenames alone
        # frequently produces speculative or meaningless tasks.
        candidates = [
            n
            for n in names
            if n.startswith(("src/", "agent/", "tests/", "lib/")) and n not in intro
        ]
        candidates += [
            n
            for n in names
            if n.endswith((".py", ".ts", ".java", ".swift")) and n not in candidates
        ]
        samples = {}
        for filename in candidates[:10]:
            try:
                samples[filename] = safe_path(root, filename, existing=True).read_text(
                    encoding="utf-8"
                )[:1800]
            except (UnicodeDecodeError, ValueError):
                continue
        context = json.dumps(
            {
                "repo": name,
                "description": info.get("description"),
                "issues": issues,
                "docs": intro,
                "source_samples": samples,
            },
            ensure_ascii=False,
        )[:22000]
        plan = ai.plan(context, "\n".join(names))

        # Log the AI planner's complete decision.
        log.info(
            "PLAN %s | action=%s | title=%s | "
            "category=%s | risk=%s | meaningfulness=%.2f | "
            "target_files=%s",
            name,
            plan.action,
            plan.title,
            plan.category,
            plan.risk,
            plan.meaningfulness,
            plan.target_files,
        )

        log.info("PLAN RATIONALE: %s", plan.rationale)

        # Evaluate each rejection rule independently.
        rejection_reasons = []

        if plan.action != "IMPLEMENT":
            rejection_reasons.append(f"Action is {plan.action}, expected IMPLEMENT")

        if plan.risk != "low":
            rejection_reasons.append(f"Risk is {plan.risk}, only low is allowed")

        if plan.meaningfulness < 0.75:
            rejection_reasons.append(
                f"Meaningfulness {plan.meaningfulness:.2f} is below threshold 0.75"
            )

        if rejection_reasons:
            for reason in rejection_reasons:
                log.warning("PLAN REJECTED %s: %s", name, reason)
            return "skip: " + "; ".join(rejection_reasons)

        log.info("PLAN ACCEPTED %s: %s", name, plan.title)
        if not 1 <= len(plan.target_files) <= 4 or len(set(plan.target_files)) != len(
            plan.target_files
        ):
            raise ValueError(
                f"Plan file count invalid: "
                f"files={plan.target_files}, "
                f"count={len(plan.target_files)}"
            )
        contents = read_files(root, plan.target_files)
        # Baseline validation: never work from a broken project.
        baseline_ok, baseline_log = validate_in_docker(root, repo_cfg)
        if not baseline_ok:
            log.warning(
                "SKIP %s: baseline validation failed: %s", name, baseline_log[-900:]
            )
            return "skip: baseline failing"
        # Reject repositories whose own baseline commands rewrite tracked files.
        if (
            git(root, "diff", "--name-only").strip()
            or git(root, "diff", "--cached", "--name-only").strip()
        ):
            log.warning("SKIP %s: baseline checks modified tracked files", name)
            return "skip: baseline modifies tracked files"
        impl = ai.implement(plan, json.dumps(contents, ensure_ascii=False))
        apply_edits(root, plan, impl)
        # Stage ONLY paths explicitly declared in the approved implementation.
        git(root, "add", "--", *[e.path for e in impl.edits])
        patch, count = diff_quality(root)
        if set(git(root, "diff", "--cached", "--name-only").splitlines()) != {
            e.path for e in impl.edits
        }:
            raise ValueError("Unexpected changed files")
        if git(root, "diff", "--name-only").strip():
            raise ValueError("Unstaged modifications before validation")
        valid, result = validate_in_docker(root, repo_cfg)
        if not valid:
            log.warning(
                "SKIP %s: post-change validation failed: %s", name, result[-900:]
            )
            return "skip: tests failed"
        # Validation must not silently rewrite tracked source files.
        verified_patch, _ = diff_quality(root)
        if git(root, "diff", "--name-only").strip():
            raise ValueError("Validation modified staged source files")
        if verified_patch != patch:
            log.warning("SKIP %s: validation modified tracked files", name)
            return "skip: validation modified sources"
        review = ai.review(plan, patch, result)
        if not review.approved or not review.meaningful or review.score < 0.8:
            log.info("SKIP %s: rejected by reviewer: %s", name, review.reason)
            return "skip: review declined"
        if args.dry_run:
            log.info(
                "DRY RUN %s: %s (%s lines)\n%s", name, plan.title, count, patch[:6000]
            )
            return "dry-run: validated proposal"
        slug = (
            re.sub("[^a-z0-9-]+", "-", plan.title.lower()).strip("-")[:40] or "update"
        )
        branch = (
            "agent/" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + slug
        )
        git(root, "checkout", "-b", branch)
        git(
            root,
            "commit",
            "-m",
            f"agent: {plan.title[:60]}",
            "-m",
            f"{plan.rationale[:500]}\n\nValidation: configured checks passed in Docker.",
        )
        git(root, "push", "-u", "origin", branch, token=gh.token(name))
        pr = gh.pull_request(
            name,
            branch,
            base,
            "agent: " + plan.title[:100],
            f"## Improvement\n{plan.rationale}\n\n## Validation\nAll configured Docker checks passed.\n\n## Review\n{review.reason}\n\n**Draft PR: requires human review.**",
        )
        log.info("Created draft PR %s", pr["html_url"])
        return pr["html_url"]


def main():
    parser = argparse.ArgumentParser(
        description="PR-only autonomous repository maintenance"
    )
    parser.add_argument("--config", default="config/repositories.yaml")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run full checks, do not push branches or PRs",
    )
    parser.add_argument("--repo", help="Only selected allowlisted repository")
    parser.add_argument(
        "--doctor", action="store_true", help="Validate local config without API keys"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config(args.config)
    repos = [
        x
        for x in config["repositories"]
        if x.get("enabled") and (not args.repo or args.repo == x["repo"])
    ]
    if args.repo and not repos:
        parser.error("Requested repo is not enabled / allowlisted")
    if args.doctor:
        print(
            f"Config OK: {len(repos)} enabled repo(s). Docker, API credentials and builds are not tested."
        )
        return
    if not repos:
        print("No enabled repositories. Configure config/repositories.yaml first.")
        return
    from .llm import AI

    gh, ai = GitHubApp(), AI()
    errors = 0
    for cfg in repos[: int(config.get("max_repositories_per_run", 3))]:
        try:
            outcome = run_one(cfg, gh, ai, args)
            log.info("%s → %s", cfg["repo"], outcome)
        except Exception as exc:
            errors += 1
            log.error("%s failed: %s", cfg["repo"], exc)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
