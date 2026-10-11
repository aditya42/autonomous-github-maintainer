import subprocess
from pathlib import Path

def validate_in_docker(repo: Path, cfg: dict) -> tuple[bool, str]:
    validation = cfg['validation']
    commands = validation['commands']
    image = validation['image']
    if not all(isinstance(x, str) and x.strip() for x in commands):
        raise ValueError('Invalid validation commands')
    # No agent credentials or host secrets passed into container.
    # Networking allowed for package installation; use pinned dependencies and trusted image.
    script = 'set -eu\n' + '\n'.join(commands)
    cmd = ['docker', 'run', '--rm', '--init', '--cpus', '2', '--memory', '3g',
           '--pids-limit', '256', '--security-opt', 'no-new-privileges',
           '--cap-drop', 'ALL', '-v', f'{repo.resolve()}:/workspace',
           '-w', '/workspace', image, 'sh', '-lc', script]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=int(validation.get('timeout_seconds', 900)))
        log = ((p.stdout or '') + '\n' + (p.stderr or ''))[-12000:]
        return p.returncode == 0, log
    except subprocess.TimeoutExpired:
        return False, 'Validation timed out'
    except FileNotFoundError:
        return False, 'Docker executable not found'
