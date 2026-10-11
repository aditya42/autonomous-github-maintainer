import argparse
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .github_client import GitHubApp, clone_repository, git
from .policy import DEFAULT_EXCLUDES, load_config, safe_path
from .validation import validate_in_docker

log = logging.getLogger('maintainer')


def inventory(root: Path) -> list[str]:
    files = []
    for p in root.rglob('*'):
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
        path = safe_path(root, name, existing=True)
        if path.stat().st_size > 18000:
            raise ValueError(f'Target file too large: {name}')
        result[name] = path.read_text(encoding='utf-8')
    if sum(len(v.encode()) for v in result.values()) > max_bytes:
        raise ValueError('Selected files exceed context limit')
    return result


def apply_edits(root, plan, impl):
    if not impl.edits or len(impl.edits) > 4:
        raise ValueError('No edits or too many edits')
    allowed = set(plan.target_files)
    seen = set()
    for edit in impl.edits:
        if edit.path not in allowed or edit.path in seen:
            raise ValueError('Edit outside task scope or duplicate')
        seen.add(edit.path)
        path = safe_path(root, edit.path, existing=True)
        if len(edit.content.encode()) > 20000:
            raise ValueError('Edited file too large')
        path.write_text(edit.content, encoding='utf-8')


def diff_quality(repo):
    changed = git(repo, 'diff', '--name-only').splitlines()
    if not changed or len(changed) > 4:
        raise ValueError('Empty diff or excessive file count')
    for path in changed:
        safe_path(repo, path, existing=True)
    numstat = git(repo, 'diff', '--numstat')
    changed_lines = 0
    for line in numstat.splitlines():
        a, b, _ = line.split('\t', 2)
        if not a.isdigit() or not b.isdigit():
            raise ValueError('Binary changes not allowed')
        changed_lines += int(a) + int(b)
    if changed_lines > 160:
        raise ValueError(f'Diff exceeds 160 lines: {changed_lines}')
    patch = git(repo, 'diff', '--no-ext-diff', '--unified=3')
    if not patch.strip():
        raise ValueError('No actual change')
    return patch, changed_lines


def run_one(repo_cfg, gh, ai, args):
    name = repo_cfg['repo']
    log.info('Examining %s', name)
    info = gh.repo_info(name)
    base = info['default_branch']
    if gh.open_agent_pr_exists(name):
        log.info('SKIP %s: existing open agent PR', name)
        return 'skip: open agent PR'
    with tempfile.TemporaryDirectory(prefix='repo-maintainer-') as tmp:
        root = Path(tmp)/'checkout'
        token = gh.token(name)
        clone_repository(name, root, token, base)
        names = inventory(root)
        issues = gh.issues(name)
        intro = {}
        for doc in ('README.md','MAINTENANCE.md','pyproject.toml','package.json','pom.xml'):
            if doc in names:
                intro[doc] = (root/doc).read_text(encoding='utf-8')[:5500]
        # Provide a small representative source sample: planning from filenames alone
        # frequently produces speculative or meaningless tasks.
        candidates = [n for n in names if n.startswith(('src/', 'agent/', 'tests/', 'lib/')) and n not in intro]
        candidates += [n for n in names if n.endswith(('.py', '.ts', '.java', '.swift')) and n not in candidates]
        samples = {}
        for filename in candidates[:10]:
            try:
                samples[filename] = safe_path(root, filename, existing=True).read_text(encoding='utf-8')[:1800]
            except (UnicodeDecodeError, ValueError):
                continue
        context = json.dumps({'repo':name,'description':info.get('description'), 'issues':issues,
                              'docs':intro,'source_samples':samples}, ensure_ascii=False)[:22000]
        plan = ai.plan(context, '\n'.join(names))
        if plan.action != 'IMPLEMENT' or plan.risk != 'low' or plan.meaningfulness < .75:
            log.info('SKIP %s: plan rejected %s', name, plan.rationale)
            return 'skip: no suitable task'
        if not 1 <= len(plan.target_files) <= 4 or len(set(plan.target_files)) != len(plan.target_files):
            raise ValueError('Plan file count invalid')
        contents = read_files(root, plan.target_files)
        # Baseline validation: never work from a broken project.
        baseline_ok, baseline_log = validate_in_docker(root, repo_cfg)
        if not baseline_ok:
            log.warning('SKIP %s: baseline validation failed: %s', name, baseline_log[-900:])
            return 'skip: baseline failing'
        # Reject repositories whose own baseline commands rewrite tracked files.
        if git(root, 'diff', '--name-only').strip():
            log.warning('SKIP %s: baseline checks modified tracked files', name)
            return 'skip: baseline modifies tracked files'
        impl = ai.implement(plan, json.dumps(contents, ensure_ascii=False))
        apply_edits(root, plan, impl)
        patch, count = diff_quality(root)
        if set(git(root, 'diff', '--name-only').splitlines()) != {e.path for e in impl.edits}:
            raise ValueError('Unexpected changed files')
        valid, result = validate_in_docker(root, repo_cfg)
        if not valid:
            log.warning('SKIP %s: post-change validation failed: %s', name, result[-900:])
            return 'skip: tests failed'
        # Validation must not silently rewrite tracked source files.
        verified_patch, _ = diff_quality(root)
        if verified_patch != patch:
            log.warning('SKIP %s: validation modified tracked files', name)
            return 'skip: validation modified sources'
        review = ai.review(plan, patch, result)
        if not review.approved or not review.meaningful or review.score < .8:
            log.info('SKIP %s: rejected by reviewer: %s', name, review.reason)
            return 'skip: review declined'
        if args.dry_run:
            log.info('DRY RUN %s: %s (%s lines)\n%s', name, plan.title, count, patch[:6000])
            return 'dry-run: validated proposal'
        slug = re.sub('[^a-z0-9-]+','-',plan.title.lower()).strip('-')[:40] or 'update'
        branch = 'agent/' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + slug
        git(root, 'checkout', '-b', branch)
        git(root, 'add', '--', *[e.path for e in impl.edits])
        git(root, 'commit', '-m', f'agent: {plan.title[:60]}', '-m', f'{plan.rationale[:500]}\n\nValidation: configured checks passed in Docker.')
        git(root, 'push', '-u', 'origin', branch, token=gh.token(name))
        pr = gh.pull_request(name, branch, base, 'agent: '+plan.title[:100],
           f'## Improvement\n{plan.rationale}\n\n## Validation\nAll configured Docker checks passed.\n\n## Review\n{review.reason}\n\n**Draft PR: requires human review.**')
        log.info('Created draft PR %s', pr['html_url'])
        return pr['html_url']


def main():
    parser = argparse.ArgumentParser(description='PR-only autonomous repository maintenance')
    parser.add_argument('--config', default='config/repositories.yaml')
    parser.add_argument('--dry-run', action='store_true', help='Run full checks, do not push branches or PRs')
    parser.add_argument('--repo', help='Only selected allowlisted repository')
    parser.add_argument('--doctor', action='store_true', help='Validate local config without API keys')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    config = load_config(args.config)
    repos = [x for x in config['repositories'] if x.get('enabled') and (not args.repo or args.repo == x['repo'])]
    if args.repo and not repos:
        parser.error('Requested repo is not enabled / allowlisted')
    if args.doctor:
        print(f'Config OK: {len(repos)} enabled repo(s). Docker, API credentials and builds are not tested.')
        return
    if not repos:
        print('No enabled repositories. Configure config/repositories.yaml first.')
        return
    from .llm import AI
    gh, ai = GitHubApp(), AI()
    errors = 0
    for cfg in repos[:int(config.get('max_repositories_per_run', 3))]:
        try:
            outcome = run_one(cfg, gh, ai, args)
            log.info('%s → %s', cfg['repo'], outcome)
        except Exception as exc:
            errors += 1
            log.error('%s failed: %s', cfg['repo'], exc)
    if errors:
        raise SystemExit(1)

if __name__ == '__main__':
    main()
