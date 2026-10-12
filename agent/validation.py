import subprocess
from pathlib import Path


def validate_in_docker(repo: Path, cfg: dict) -> tuple[bool, str]:
    validation = cfg["validation"]
    commands = validation["commands"]
    image = validation["image"]

    if not all(isinstance(command, str) and command.strip() for command in commands):
        raise ValueError("Invalid validation commands")

    # Copy the read-only repository into an isolated,
    # writable workspace inside the container.
    # The original checkout is never modified by validation.
    script = "\n".join(
        [
            "set -eu",
            'WORKSPACE="$(mktemp -d /tmp/maintainer.XXXXXX)"',
            'cp -R /source/. "$WORKSPACE/"',
            'cd "$WORKSPACE"',
            *commands,
        ]
    )

    cmd = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--cpus",
        "2",
        "--memory",
        "3g",
        "--pids-limit",
        "256",
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        # Execute as an unprivileged user.
        "--user",
        "10001:10001",
        # Give npm and other tools a writable location.
        "-e",
        "HOME=/tmp",
        "-e",
        "NPM_CONFIG_CACHE=/tmp/.npm",
        # Mount original source read-only.
        "--mount",
        f"type=bind,source={repo.resolve()},target=/source,readonly",
        "-w",
        "/tmp",
        image,
        "sh",
        "-lc",
        script,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=int(validation.get("timeout_seconds", 900)),
        )

        output = ((result.stdout or "") + "\n" + (result.stderr or ""))[-12000:]

        return result.returncode == 0, output

    except subprocess.TimeoutExpired:
        return False, "Validation timed out"

    except FileNotFoundError:
        return False, "Docker executable not found"
