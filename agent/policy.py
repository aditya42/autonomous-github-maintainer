from pathlib import Path
import re
import yaml

DEFAULT_EXCLUDES = {'.git', 'node_modules', '.venv', 'venv', 'dist', 'build', 'coverage', '__pycache__', '.next', '.pytest_cache'}
FORBIDDEN = {'.env', '.npmrc', '.pypirc', 'id_rsa', 'id_ed25519', 'credentials.json'}
ALLOWED_SUFFIXES = {'.py', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.json', '.yaml', '.yml', '.md', '.toml', '.xml', '.java', '.swift', '.sh', '.txt', '.properties'}
ALLOWED_NAMES = {'Dockerfile', 'Makefile', '.gitignore'}

def safe_path(root: Path, name: str, existing: bool = False) -> Path:
    if not name or '\\' in name or name.startswith('/') or '\x00' in name:
        raise ValueError(f'Unsafe path: {name!r}')
    rel = Path(name)
    if '..' in rel.parts or any(p in DEFAULT_EXCLUDES for p in rel.parts):
        raise ValueError(f'Unsafe path: {name!r}')
    if any(p.lower() in FORBIDDEN or p.endswith('.pem') or p.endswith('.key') for p in rel.parts):
        raise ValueError(f'Secrets file forbidden: {name!r}')
    if rel.parts[0] == '.github':
        raise ValueError('GitHub workflow / configuration edits require manual intervention')
    if rel.suffix not in ALLOWED_SUFFIXES and rel.name not in ALLOWED_NAMES:
        raise ValueError(f'File extension not allowed: {name!r}')
    target = root / rel
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Path escapes checkout')
    if existing and not target.is_file():
        raise ValueError(f'Expected existing file: {name!r}')
    if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != root.parent):
        raise ValueError('Symlink paths forbidden')
    return target

def safe_new_test_path(root: Path, name: str) -> Path:
    """Only allow creating ordinary test source files in test directories.

    safe_path checks traversal, prohibited directories, secret names, and symlinks.
    This additional policy deliberately excludes new runtime/source/config files.
    """
    path = safe_path(root, name, existing=False)
    rel = path.relative_to(root)
    if path.exists() or path.is_symlink():
        raise ValueError(f'New test path already exists: {name}')
    if not any(part.lower() in {'tests', 'test', '__tests__'} for part in rel.parts[:-1]):
        raise ValueError(f'New files only allowed under test directories: {name}')
    filename = rel.name
    if not (
        re.fullmatch(r'test_[\w.-]+\.py', filename)
        or re.fullmatch(r'[\w.-]+(?:\.test|\.spec)\.(?:mjs|js|jsx|ts|tsx)', filename)
        or re.fullmatch(r'[A-Z][\w]*Test\.java', filename)
        or re.fullmatch(r'[\w]+_test\.go', filename)
        or re.fullmatch(r'[\w.-]+Tests?\.swift', filename)
    ):
        raise ValueError(f'New file must use a recognized test filename: {name}')
    return path


def load_config(path: str):
    with open(path, encoding='utf-8') as fp:
        data = yaml.safe_load(fp)
    if not isinstance(data, dict) or not isinstance(data.get('repositories'), list):
        raise ValueError('config must contain repositories list')
    for repo in data['repositories']:
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo.get('repo', '')):
            raise ValueError(f"Invalid repository: {repo.get('repo')}")
        if not isinstance(repo.get('validation'), dict):
            raise ValueError('Repository needs trusted validation configuration')
        if not repo['validation'].get('commands'):
            raise ValueError('At least one validation command required')
        image = repo['validation'].get('image', '')
        if not re.fullmatch(r'[\w./:@-]+', image):
            raise ValueError('Invalid Docker image')
    return data
