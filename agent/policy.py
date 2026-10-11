from pathlib import Path
import re
import yaml

DEFAULT_EXCLUDES = {'.git', 'node_modules', '.venv', 'venv', 'dist', 'build', 'coverage', '__pycache__', '.next', '.pytest_cache'}
FORBIDDEN = {'.env', '.npmrc', '.pypirc', 'id_rsa', 'id_ed25519', 'credentials.json'}
ALLOWED_SUFFIXES = {'.py', '.ts', '.tsx', '.js', '.jsx', '.json', '.yaml', '.yml', '.md', '.toml', '.xml', '.java', '.swift', '.sh', '.txt', '.properties'}
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
