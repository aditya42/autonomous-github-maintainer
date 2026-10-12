import subprocess
from types import SimpleNamespace

import pytest

from agent.main import apply_edits, diff_quality, read_files
from agent.policy import safe_new_test_path


def git(repo, *args):
    return subprocess.run(
        ['git', *args], cwd=repo, check=True,
        capture_output=True, text=True
    ).stdout.strip()


def init_repo(repo):
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.email', 'test@example.test')
    git(repo, 'config', 'user.name', 'Test')
    (repo / 'README.md').write_text('baseline\n', encoding='utf-8')
    git(repo, 'add', 'README.md')
    git(repo, 'commit', '-qm', 'baseline')


def test_can_create_new_mjs_smoke_test(tmp_path):
    name = 'tests/smoke/generate.ping.test.mjs'
    assert read_files(tmp_path, [name])[name]['exists'] is False
    plan = SimpleNamespace(target_files=[name])
    content = 'import assert from "node:assert/strict";\nassert.equal(1, 1);\n'
    implementation = SimpleNamespace(edits=[SimpleNamespace(path=name, content=content)])
    apply_edits(tmp_path, plan, implementation)
    assert (tmp_path / name).read_text(encoding='utf-8') == content


@pytest.mark.parametrize('name', [
    '../outside/test_api.test.mjs',
    '.github/tests/api.test.mjs',
    'src/main.py',
    'tests/smoke/config.yaml',
    'tests/smoke/.env',
])
def test_forbidden_new_files(tmp_path, name):
    with pytest.raises(ValueError):
        safe_new_test_path(tmp_path, name)


def test_staged_diff_includes_new_test(tmp_path):
    init_repo(tmp_path)
    path = tmp_path / 'tests/smoke/api.test.mjs'
    path.parent.mkdir(parents=True)
    path.write_text('assert.ok(true);\n', encoding='utf-8')
    git(tmp_path, 'add', 'tests/smoke/api.test.mjs')
    patch, count = diff_quality(tmp_path)
    assert 'api.test.mjs' in patch
    assert count == 1


def test_staged_diff_rejects_new_non_test(tmp_path):
    init_repo(tmp_path)
    (tmp_path / 'unapproved.py').write_text('print("hello")\n', encoding='utf-8')
    git(tmp_path, 'add', 'unapproved.py')
    with pytest.raises(ValueError):
        diff_quality(tmp_path)
