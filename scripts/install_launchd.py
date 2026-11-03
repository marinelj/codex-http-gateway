"""Install the gateway as a per-user macOS LaunchAgent (after uv sync).

Run from this repository: uv run python scripts/install_launchd.py
Uses .env and current environment settings.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

from codex_gateway.config import Settings

LABEL = "com.marinelj.codex-http-gateway"


def main():
    if sys.platform != "darwin":
        raise SystemExit("LaunchAgents require macOS; use your Linux service manager instead")
    repo = Path(__file__).resolve().parent.parent
    settings = Settings()
    settings.prepare()
    destination = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Environment overrides are explicit; the local .env remains available on restart.
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        **{
            f"GATEWAY_{key.upper()}": str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in settings.model_dump().items()
            if value is not None
        },
    }
    payload = {
        "Label": LABEL,
        "ProgramArguments": [str(repo / ".venv/bin/codex-gateway"), "serve"],
        "WorkingDirectory": str(repo),
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "StandardOutPath": str(settings.data_dir / "service.log"),
        "StandardErrorPath": str(settings.data_dir / "service-error.log"),
    }
    for key in ("StandardOutPath", "StandardErrorPath"):
        fd = os.open(payload[key], os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.close(fd)
    destination.write_bytes(plistlib.dumps(payload))
    destination.chmod(0o600)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], check=False, capture_output=True)
    # bootout can return before launchd has finished removing the prior job.
    for _ in range(50):
        result = subprocess.run(
            ["launchctl", "bootstrap", domain, str(destination)], capture_output=True, text=True
        )
        if result.returncode == 0:
            break
        time.sleep(0.1)
    else:
        raise SystemExit(f"Could not load LaunchAgent: {result.stderr.strip()}")
    print(f"Installed {destination}")
    print(f"Logs: {settings.data_dir}")


if __name__ == "__main__":
    main()
