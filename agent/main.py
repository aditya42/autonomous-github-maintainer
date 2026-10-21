import argparse
import hashlib
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

# Candidate acceptance policy; other safety checks remain unchanged.
MIN_CANDIDATE_SCORE = 0.50


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


def is_test_file(name):
    """Classify supported test paths, never source modules with test-like substrings."""
    parts = Path(name).parts
    leaf = parts[-1] if parts else ""
    in_tests = any(p.lower() in {"test", "tests", "__tests__"} for p in parts[:-1])
    test_name = (leaf.startswith("test_") and leaf.endswith(".py")) or (
        ".test." in leaf or ".spec." in leaf or leaf.endswith("Test.java")
        or leaf.endswith("_test.go") or leaf.endswith("Tests.swift")
    )
    return in_tests and test_name


def read_only_contract_context(root, names, plan, max_bytes=22000):
    """Read independent production contracts without expanding the write allowlist.

    Include the cited production file and nearby source implementations, with
    scanner/PII, schema and route modules prioritized for relevant test tasks.
    """
    eligible = [n for n in names if not is_test_file(n) and
                n.endswith((".py", ".ts", ".tsx", ".js", ".mjs", ".java", ".swift"))]
    cited = getattr(plan, "evidence_file", "")
    title = (plan.title + " " + plan.rationale + " " + cited).lower()
    terms = (
        ("pii", "privacy", "secret", "redact", "safeguard") if
        any(x in title for x in ("pii", "privacy", "secret", "redact")) else
        ("schema", "generator", "generate", "route", "api")
    )
    ranked = sorted(eligible, key=lambda n: (
        0 if n == cited else 1,
        0 if any(term in n.lower() for term in terms) else 1,
        n,
    ))
    result = {}
    total = 0
    for name in ranked:
        if len(result) >= 4 or total >= max_bytes:
            break
        try:
            path = safe_path(root, name, existing=True)
            if path.stat().st_size > 20000:
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, ValueError, UnicodeError):
            continue
        data = content[:min(12000, max_bytes - total)]
        if not data:
            continue
        result[name] = data
        total += len(data.encode("utf-8"))
    return result


def validate_python_provider_calls(root, paths):
    """Reject invented methods/keywords on repository Python provider classes."""
    import ast
    for test_path in paths:
        if not is_test_file(test_path) or not test_path.endswith(".py"):
            continue
        test_file = safe_path(root, test_path, existing=True)
        try:
            tree = ast.parse(test_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue  # Docker/pytest will reject invalid Python.
        imports = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    imports[alias.asname or alias.name] = (node.module, alias.name)
        instances = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
                    continue
                cls = imports.get(value.func.id)
                if not cls or not cls[1].endswith("Provider"):
                    continue
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    if isinstance(target, ast.Name):
                        instances[target.id] = cls
        for variable, (module, class_name) in instances.items():
            source_rel = "src/" + module.replace(".", "/") + ".py"
            if source_rel not in inventory(root):
                continue
            production = ast.parse(safe_path(root, source_rel, existing=True).read_text(encoding="utf-8"))
            cls = next((x for x in production.body if isinstance(x, ast.ClassDef) and x.name == class_name), None)
            if cls is None:
                continue
            methods = {x.name: x for x in cls.body if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))}
            for call in (x for x in ast.walk(tree) if isinstance(x, ast.Call)):
                fn = call.func
                if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                        and fn.value.id == variable):
                    continue
                if fn.attr not in methods:
                    raise ValueError(f"Unsupported production method: {class_name}.{fn.attr}")
                method = methods[fn.attr]
                supported = {arg.arg for arg in method.args.args + method.args.kwonlyargs}
                if method.args.kwarg is None:
                    invalid = {kw.arg for kw in call.keywords if kw.arg is not None} - supported
                    if invalid:
                        raise ValueError(f"Unsupported arguments for {class_name}.{fn.attr}: {sorted(invalid)}")


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


def substantive_diff_check(plan, patch, changed_lines):
    """Reject cosmetic changes and test-only changes without real assertions.

    This is a conservative structural gate, not a proof of regression value.
    Independent model review and Docker validation remain mandatory.
    """
    if not patch.strip() or changed_lines < 4:
        return "change is too small to demonstrate a meaningful improvement"
    added = [line[1:].strip() for line in patch.splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    removed = [line[1:].strip() for line in patch.splitlines()
               if line.startswith("-") and not line.startswith("---")]
    meaningful = lambda s: s and not s.startswith(("#", "//", "/*", "*", "<!--"))
    added_code = [s for s in added if meaningful(s)]
    removed_code = [s for s in removed if meaningful(s)]
    if len(added_code) < 2:
        return "no substantive executable or assertion changes"
    if added_code == removed_code:
        return "reformatted or relocated content only"
    if plan.category == "test":
        # Tests must exercise production behavior, not implement a stand-in.
        # Reject newly introduced evaluator/provider/scanner implementations
        # even when those self-contained tests pass.
        if any(
            re.match(r"^(?:export\s+)?class\s+\w*(?:Evaluator|Provider|Scanner)\b", line)
            for line in added_code
        ):
            return "test implements its own evaluator/provider/scanner instead of exercising production code"
        assertions = ("expect(", "assert ", "assert(", "self.assert", "pytest.raises",
                      "assertthrows", "assert.rejects", "assert.equal", "assert.strict")
        if not any(token in s.lower() for s in added_code for token in assertions):
            return "test-only change does not add a meaningful assertion"
        if not any(token in s.lower() for s in added_code for token in
                   ("it(", "test(", "def test_", "@test", "describe(", "assert ", "expect(")):
            return "test-only change does not exercise an observable behavior"
    return None


def commit_description(plan, changed_lines, review):
    """Traceable commit body using validated facts, not invented impact claims."""
    subject = re.sub(r"\s+", " ", plan.title).strip()[:65]
    category = {"test": "test", "bugfix": "fix", "docs": "docs",
                "refactor": "refactor", "ci": "ci", "reliability": "fix"}[plan.category]
    heading = f"{category}: {subject}"[:72]
    gap = " ".join(plan.missing_behavior.split()) or "See planned improvement"
    body = (
        f"Observed gap: {gap[:450]}\n\n"
        f"Why: {' '.join(plan.rationale.split())[:450]}\n\n"
        f"Evidence: {plan.evidence_file or 'repository analysis'}\n"
        f"Change size: {changed_lines} added/deleted lines\n"
        f"Validation: configured Docker checks passed\n"
        f"Independent review: approved (score {review.score:.2f})\n"
        "Human review required; draft PR only."
    )
    return heading, body


def draft_pr_description(plan, changed_lines, review):
    return (
        f"## Quality gap\n{plan.missing_behavior or plan.rationale}\n\n"
        f"## Implementation rationale\n{plan.rationale}\n\n"
        f"## Evidence\n{plan.evidence_file or 'Repository analysis'}\n\n"
        f"## Verification\n- Docker validation: passed\n"
        f"- Change size: {changed_lines} added/deleted lines\n"
        f"- Independent reviewer: approved, score {review.score:.2f}\n\n"
        "**Draft PR only. Requires human review before merging.**"
    )


def candidate_score(plan):
    """Consistent ranking rubric; AI ratings are not independently verified."""
    return round(
        0.30 * plan.defect_risk
        + 0.35 * plan.regression_prevention
        + 0.20 * plan.impact
        + 0.15 * plan.maintainability,
        4,
    )


def capability_signature(plan):
    """Group related terminology by primary behavior, not keyword count.

    Explicit lists of independently named QE domains remain too broad for
    the small-change agent; related phrasing is a single scoped capability.
    """
    title = plan.title.casefold()
    domains = {
        "pii-safeguards": ("pii", "sensitive data", "redaction"),
        "llm-integration": ("llm", "enrichment", "model response"),
        "schema-validation": ("schema", "json schema"),
        "count-boundaries": ("count", "minimum", "maximum", "boundary"),
        "deterministic-seed": ("seed", "deterministic"),
        "api-errors": ("http error", "status code", "invalid input"),
        "ci-reliability": ("workflow", "pipeline", "ci reliability"),
    }
    matches = [key for key, terms in domains.items()
               if any(term in title for term in terms)]
    # Clearly enumerated, unrelated test areas require separate proposals.
    # Do not reject natural phrases such as "schema boundary cases" or
    # "deterministic generation at boundary lengths".
    if re.search(r"\b(?:schema|count|seed|pii|llm)\s*(?:,|and|&|/)\s*"
                 r"(?:schema|count|seed|pii|llm)\b", title):
        return "overbroad", matches
    return (matches[0] if matches else "general-" + plan.category), matches


def is_oversized_diff_error(exc):
    return str(exc).startswith("Diff exceeds 160 lines:")


def restore_initial_files(root, contents):
    """Discard staged generated changes and remove only approved new test files."""
    git(root, "reset", "--hard", "HEAD")
    for name, metadata in contents.items():
        if not metadata["exists"]:
            path = safe_path(root, name)
            if path.is_file():
                path.unlink()


def build_evidence_catalog(root, names):
    """Bounded source/test excerpts spread across three QE capability areas.

    All content is untrusted. Only allowlisted, readable checkout files enter
    the catalog; its literal excerpts are later used to verify LLM citations.
    """
    extensions = (".py", ".ts", ".tsx", ".js", ".mjs", ".java", ".swift")
    candidates = [n for n in names if n.endswith(extensions)]
    groups = (
        ("route", "api", "server", "handler"),
        ("generator", "generation", "core", "schema", "seed"),
        ("pii", "privacy", "guard", "redact", "safety"),
        ("tests/", "test/", "__tests__", ".test.", ".spec."),
    )
    chosen = []
    for group in groups:
        matches = [n for n in candidates if any(term in n.lower() for term in group)]
        chosen.extend(n for n in matches[:3] if n not in chosen)
    catalog = {}
    for name in chosen[:12]:
        try:
            path = safe_path(root, name, existing=True)
            if path.stat().st_size <= 20000:
                catalog[name] = path.read_text(encoding="utf-8")[:1050]
        except (UnicodeError, ValueError, OSError):
            continue
    return catalog



def build_evidence_ids(catalog):
    """Deterministic IDs for inspected source excerpts; no model-generated quotes."""
    index = {}
    for path, excerpt in sorted(catalog.items()):
        digest = hashlib.sha256((path + "\0" + excerpt).encode("utf-8")).hexdigest()[:16]
        index["ev-" + digest] = {"file": path, "excerpt": excerpt[:200]}
    return index


def evidence_id_error(plan, catalog, evidence_ids):
    """Fail closed on unknown IDs or mismatched cited file."""
    if plan.action != "IMPLEMENT":
        return None
    item = evidence_ids.get(plan.evidence_id)
    if item is None:
        return "evidence ID is absent or not in inspected catalog"
    if item["file"] != plan.evidence_file or item["file"] not in catalog:
        return "evidence ID does not match the cited source file"
    if not catalog[item["file"]].startswith(item["excerpt"]):
        return "evidence ID source excerpt mismatch"
    if len(plan.missing_behavior.strip()) < 20:
        return "missing-behavior justification is too vague"
    return None

def evidence_rejection(plan, catalog):
    """Verify evidence provenance, not the LLM's unverified impact claim."""
    if plan.action != "IMPLEMENT":
        return None
    if plan.evidence_file not in catalog:
        return "evidence file is missing from inspected source catalog"
    quote = plan.evidence_quote.strip()
    if not quote:
        return "evidence quote is empty"
    if len(quote) > 500:
        return "evidence quote exceeds 500 characters"
    if quote not in catalog[plan.evidence_file]:
        return "evidence quote does not exist in cited source"
    if len(plan.missing_behavior.strip()) < 20:
        return "missing-behavior justification is too vague"
    return None


PLANNING_FOCUS_AREAS = (
    "API contract, input validation, and narrowly scoped boundary behaviors",
    "Deterministic generation, schema constraints, and data correctness",
    "PII safeguards, error recovery, and operational reliability",
)


def select_best_plan(ai, context, names, root):
    """Evaluate three distinct proposals; only qualifying proposals reserve intent."""
    candidates = []
    excluded_titles = []
    seen_capabilities = set()
    seen_generic_titles = set()
    seen_generic_paths = set()
    try:
        parsed_context = json.loads(context)
        catalog = parsed_context.get("evidence_catalog")
        evidence_ids = parsed_context.get("evidence_ids")
    except (ValueError, TypeError):
        catalog = None
        evidence_ids = None

    for index, focus_area in enumerate(PLANNING_FOCUS_AREAS):
        log.info("PLANNING FOCUS %d: %s", index + 1, focus_area)
        plan = ai.plan(
            context,
            "\n".join(names),
            excluded_titles=excluded_titles,
            focus_area=focus_area,
        )
        excluded_titles.append(plan.title)
        score = candidate_score(plan)
        capability, matches = capability_signature(plan)
        log.info(
            "CANDIDATE %d title=%s action=%s risk=%s "
            "meaningfulness=%.2f rubric=%.2f capability=%s",
            index + 1, plan.title, plan.action, plan.risk,
            plan.meaningfulness, score, capability,
        )
        reasons = []
        if plan.action != "IMPLEMENT":
            reasons.append("not an IMPLEMENT task")
        if plan.risk != "low":
            reasons.append("risk is not low")
        if plan.meaningfulness < MIN_CANDIDATE_SCORE or score < MIN_CANDIDATE_SCORE:
            reasons.append(f"quality score below {MIN_CANDIDATE_SCORE:.2f}")
        if capability == "overbroad":
            reasons.append("overbroad: " + ", ".join(matches))
        if catalog is not None:
            evidence_error = (
                evidence_id_error(plan, catalog, evidence_ids)
                if evidence_ids is not None
                else evidence_rejection(plan, catalog)
            )
            if evidence_error:
                reasons.append(evidence_error)
            else:
                log.info("CANDIDATE %d EVIDENCE file=%s quote=%r gap=%s",
                         index + 1, plan.evidence_file,
                         plan.evidence_quote[:120], plan.missing_behavior[:220])

        specific = capability not in {"overbroad", "general-" + plan.category}
        normalized_title = " ".join(plan.title.casefold().split())
        if specific and capability in seen_capabilities:
            reasons.append("duplicate functional capability: " + capability)
        elif not specific and (normalized_title in seen_generic_titles
                               or tuple(sorted(plan.target_files)) in seen_generic_paths):
            reasons.append("duplicate generic task identity")

        paths = plan.target_files
        if plan.category == "test" and any(not is_test_file(p) for p in paths):
            reasons.append("test-only proposal includes production edit targets")
        if not 1 <= len(paths) <= 4 or len(set(paths)) != len(paths):
            reasons.append("invalid target file count or duplicate path")
        else:
            try:
                for name in paths:
                    p = safe_path(root, name)
                    if p.is_file():
                        safe_path(root, name, existing=True)
                    else:
                        safe_new_test_path(root, name)
            except ValueError as exc:
                reasons.append(f"unsafe target path: {exc}")

        # Paths are edit scope, NOT task identity: valid different tests can
        # target the very same smoke-test module.
        if reasons:
            log.info("CANDIDATE %d REJECTED: %s", index + 1, "; ".join(reasons))
            continue

        candidates.append((score, plan.meaningfulness, plan))
        if specific:
            seen_capabilities.add(capability)
        else:
            seen_generic_titles.add(normalized_title)
            seen_generic_paths.add(tuple(sorted(paths)))

    if not candidates:
        return None
    score, _, plan = max(candidates, key=lambda x: (x[0], x[1]))
    log.info("SELECTED PLAN title=%s rubric=%.2f", plan.title, score)
    return plan



def push_persistent_maintenance_branch(root, gh, name, base, branch):
    """Create/reuse one remote branch, refusing to overwrite unmerged work.

    A merged PR may be squash-merged, so ancestry alone is insufficient.
    Verify the exact remote tip is the head SHA of a merged PR, then use
    --force-with-lease anchored to that SHA. No unconditional force push.
    """
    if branch != "agent/maintenance":
        raise ValueError("Unexpected agent branch")
    if gh.open_agent_pr_exists(name):
        raise ValueError("Another agent PR is open; refusing maintenance branch reuse")
    ref = f"refs/heads/{branch}"
    lines = git(root, "ls-remote", "--heads", "origin", ref,
                token=gh.token(name)).splitlines()
    if not lines:
        git(root, "push", "-u", "origin", branch, token=gh.token(name))
        return "created"
    if len(lines) != 1:
        raise ValueError("Ambiguous remote maintenance branch")
    remote_sha = lines[0].split()[0]
    if len(remote_sha) != 40 or not all(c in "0123456789abcdef" for c in remote_sha):
        raise ValueError("Invalid remote branch SHA")
    owner = name.split("/", 1)[0]
    prs = gh.request(
        "GET", f"/repos/{name}/pulls", repo=name,
        params={"state": "closed", "head": f"{owner}:{branch}", "per_page": 100},
    )
    verified = any(
        p.get("merged_at") and p.get("base", {}).get("ref") == base
        and p.get("head", {}).get("ref") == branch
        and p.get("head", {}).get("sha") == remote_sha
        for p in prs
    )
    if not verified:
        raise ValueError("Persistent branch is not a verified previously merged PR tip")
    git(root, "push", "-u", f"--force-with-lease={ref}:{remote_sha}",
        "origin", f"HEAD:{ref}", token=gh.token(name))
    return "reused"


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

        # STEP 1: Validate repository before invoking the AI planner.
        log.info("BASELINE START %s", name)

        baseline_ok, baseline_log = validate_in_docker(root, repo_cfg)

        if not baseline_ok:
            log.error(
                "BASELINE FAILED %s:\n%s",
                name,
                baseline_log[-6000:],
            )
            return "skip: baseline failing"

        log.info("BASELINE PASSED %s", name)

        # Ensure baseline validation did not modify tracked files.
        if (
            git(root, "diff", "--name-only").strip()
            or git(root, "diff", "--cached", "--name-only").strip()
        ):
            log.warning(
                "SKIP %s: baseline checks modified tracked files",
                name,
            )
            return "skip: baseline modifies tracked files"

        # STEP 2: Start repository analysis and AI task planning.
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
        # Balance source sampling across functional areas. A purely alphabetical
        # first-ten sample can overrepresent the API layer and hide core logic.
        source_names = [
            n for n in names
            if n.endswith((".py", ".ts", ".tsx", ".js", ".mjs", ".java", ".swift"))
            and n not in intro
        ]
        sample_groups = (
            ("generation", "generator", "schema", "core", "seed"),
            ("pii", "privacy", "safety", "guard", "redact"),
            ("route", "api", "server", "handler", "validation"),
            ("tests/", "test/", "__tests__", ".test.", ".spec."),
        )
        candidates = []
        for terms in sample_groups:
            matched = [n for n in source_names if any(term in n.lower() for term in terms)]
            candidates.extend(n for n in matched[:4] if n not in candidates)
        candidates.extend(n for n in source_names if n not in candidates)
        samples = {}
        for filename in candidates[:16]:
            try:
                samples[filename] = safe_path(root, filename, existing=True).read_text(
                    encoding="utf-8"
                )[:1800]
            except (UnicodeDecodeError, ValueError):
                continue
        evidence_catalog = build_evidence_catalog(root, names)
        log.info("EVIDENCE CATALOG %s: %d inspected files: %s",
                 name, len(evidence_catalog), list(evidence_catalog))
        context = json.dumps(
            {
                "repo": name,
                "description": info.get("description"),
                "issues": str(issues)[:2000],
                "docs": {k: v[:1800] for k, v in intro.items()},
                "evidence_catalog": evidence_catalog,
                "evidence_ids": build_evidence_ids(evidence_catalog),
            },
            ensure_ascii=False,
        )
        plan = select_best_plan(ai, context, names, root)
        if plan is None:
            log.info(
                "NO_ACTIONABLE_WORK %s: no evidence-backed candidate passed the "
                "quality, scope, and safety requirements", name,
            )
            return "no_actionable_work: no qualifying candidate"

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

        if plan.meaningfulness < MIN_CANDIDATE_SCORE:
            rejection_reasons.append(
                f"Meaningfulness {plan.meaningfulness:.2f} is below threshold {MIN_CANDIDATE_SCORE:.2f}"
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
        # Test-only plans must not edit production code, even if the LLM requests it.
        if plan.category == "test" and any(not is_test_file(p) for p in plan.target_files):
            log.warning("SKIP %s: test-only proposal includes production edit targets", name)
            return "skip: test plan targets production files"
        contents = read_files(root, plan.target_files)
        contracts = read_only_contract_context(root, names, plan)
        log.info("READ-ONLY CONTRACTS %s: %s", name, list(contracts))
        contract_text = json.dumps(contracts, ensure_ascii=False)
        impl = ai.implement(
            plan, json.dumps(contents, ensure_ascii=False),
            reference_context=contract_text,
        )
        apply_edits(root, plan, impl)
        validate_python_provider_calls(root, [e.path for e in impl.edits])
        # Stage ONLY paths explicitly declared in the approved implementation.
        git(root, "add", "--", *[e.path for e in impl.edits])
        try:
            patch, count = diff_quality(root)
        except ValueError as exc:
            if not is_oversized_diff_error(exc):
                log.warning("SKIP %s: diff policy rejected: %s", name, exc)
                return "skip: generated change violates diff policy"
            # Exactly one reduction; no retries if the replacement is still oversized.
            log.info("SCOPE REDUCTION ATTEMPT 1/1 %s: %s", name, exc)
            oversized_patch = git(root, "diff", "--cached", "--no-ext-diff", "--unified=3")
            reduced = ai.reduce_scope(plan, json.dumps(contents, ensure_ascii=False),
                                      oversized_patch, str(exc))
            if not reduced.edits:
                log.warning("SKIP %s: scope reduction declined", name)
                return "skip: scope reduction declined"
            if (len(reduced.edits) > 4
                    or len({edit.path for edit in reduced.edits}) != len(reduced.edits)
                    or not {edit.path for edit in reduced.edits}.issubset(set(plan.target_files))):
                log.warning("SKIP %s: scope reduction returned invalid paths", name)
                return "skip: invalid scope reduction"
            restore_initial_files(root, contents)
            try:
                apply_edits(root, plan, reduced)
                git(root, "add", "--", *[edit.path for edit in reduced.edits])
                patch, count = diff_quality(root)
            except ValueError as reduced_exc:
                log.warning("SKIP %s: reduced change rejected: %s", name, reduced_exc)
                return "skip: reduced change violates diff policy"
            impl = reduced
            log.info("SCOPE REDUCTION ACCEPTED %s (%s changed lines)", name, count)
        if set(git(root, "diff", "--cached", "--name-only").splitlines()) != {
            e.path for e in impl.edits
        }:
            raise ValueError("Unexpected changed files")
        if git(root, "diff", "--name-only").strip():
            raise ValueError("Unstaged modifications before validation")
        valid, result = validate_in_docker(root, repo_cfg)
        if not valid:
            log.warning("VALIDATION FAILED %s:\n%s", name, result[-12000:])
            log.info("REPAIR ATTEMPT 1/1 %s", name)
            # Only one corrective attempt is allowed, followed by full validation.
            # The original contents and initial patch are evidence, not instructions.
            try:
                repaired = ai.repair(
                    plan, json.dumps(contents, ensure_ascii=False), patch, result,
                    reference_context=contract_text,
                )
                if not repaired.edits:
                    log.warning("SKIP %s: repair agent declined", name)
                    return "skip: repair declined"
                # Revalidate ALL paths and contents before resetting the temporary clone.
                expected = set(plan.target_files)
                repaired_paths = [edit.path for edit in repaired.edits]
                if len(repaired_paths) != len(set(repaired_paths)) or not set(repaired_paths).issubset(expected):
                    raise ValueError("Repair paths outside approved target files")
                for edit in repaired.edits:
                    p = safe_path(root, edit.path)
                    if contents[edit.path]["exists"]:
                        safe_path(root, edit.path, existing=True)
                    else:
                        # New test path was checked before the first attempt.
                        safe_new_test_staged(root, edit.path)
                    if not edit.content.strip() or len(edit.content.encode()) > 20000:
                        raise ValueError("Repair content invalid")
                # Reset tracked files and remove ONLY the new files from the approved plan.
                git(root, "reset", "--hard", "HEAD")
                for path_name in plan.target_files:
                    if not contents[path_name]["exists"]:
                        p = safe_path(root, path_name)
                        if p.is_file():
                            p.unlink()
                apply_edits(root, plan, repaired)
                validate_python_provider_calls(root, repaired_paths)
                git(root, "add", "--", *repaired_paths)
                patch, count = diff_quality(root)
                changed = set(git(root, "diff", "--cached", "--name-only").splitlines())
                if changed != set(repaired_paths):
                    raise ValueError("Repair changed unexpected files")
                if git(root, "diff", "--name-only").strip():
                    raise ValueError("Unstaged modifications after repair")
            except ValueError as exc:
                log.warning("SKIP %s: repair violates policy: %s", name, exc)
                return "skip: repair violates policy"
            log.info("REPAIR VALIDATION START %s", name)
            valid, result = validate_in_docker(root, repo_cfg)
            if not valid:
                log.warning("SKIP %s: repair validation failed:\n%s", name, result[-12000:])
                return "skip: repair tests failed"
            log.info("REPAIR VALIDATION PASSED %s", name)
        # Validation must not silently rewrite tracked source files.
        verified_patch, _ = diff_quality(root)
        if git(root, "diff", "--name-only").strip():
            raise ValueError("Validation modified staged source files")
        if verified_patch != patch:
            log.warning("SKIP %s: validation modified tracked files", name)
            return "skip: validation modified sources"
        review = ai.review(plan, patch, result)
        log.info(
            "REVIEW DECISION %s: approved=%s meaningful=%s score=%.2f",
            name, review.approved, review.meaningful, review.score,
        )
        if not review.approved or not review.meaningful or review.score < 0.8:
            log.info("SKIP %s: rejected by reviewer: %s", name, review.reason)
            return "skip: review declined"
        substance_issue = substantive_diff_check(plan, patch, count)
        if substance_issue:
            log.info("SKIP %s: non-substantive proposal: %s", name, substance_issue)
            return "skip: non-substantive change"
        commit_subject, commit_body = commit_description(plan, count, review)
        pr_body = draft_pr_description(plan, count, review)
        log.info("COMMIT READY %s: %s (%s changed lines)", name, commit_subject, count)
        if args.dry_run:
            log.info(
                "PR_READY_DRY_RUN %s: reviewed and Docker-validated; "
                "no push/PR because --dry-run is enabled", name,
            )
            log.info(
                "DRY RUN %s: %s (%s lines)\n%s", name, plan.title, count, patch[:6000]
            )
            return "dry-run: validated proposal"
        branch = "agent/maintenance"
        log.info(
            "DRAFT_PR_START %s: all validation and review gates passed; "
            "creating branch for human-reviewed draft PR", name,
        )
        git(root, "checkout", "-b", branch)
        git(
            root,
            "commit",
            "-m",
            commit_subject,
            "-m",
            commit_body,
        )
        try:
            branch_outcome = push_persistent_maintenance_branch(root, gh, name, base, branch)
            log.info("PERSISTENT_BRANCH %s: %s", name, branch_outcome)
        except ValueError as exc:
            log.warning("SKIP %s: persistent branch unsafe: %s", name, exc)
            return "skip: persistent branch unsafe"
        pr = gh.pull_request(
            name,
            branch,
            base,
            commit_subject,
            pr_body,
        )
        log.info("Created draft PR %s", pr["html_url"])
        if os.getenv("AUTO_MERGE_DRAFT_PRS", "").casefold() in {"1", "true", "yes"}:
            try:
                if gh.queue_native_auto_merge(name, base, pr):
                    log.info("AUTO_MERGE_QUEUED %s", pr["html_url"])
                else:
                    log.warning("AUTO_MERGE_NOT_ENABLED %s: leaving draft", pr["html_url"])
            except Exception as exc:
                log.warning("AUTO_MERGE_NOT_ENABLED %s: %s", pr["html_url"], exc)
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
