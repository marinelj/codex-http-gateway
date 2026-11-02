"""Stop the CLI process group if the gateway dies, even after SIGKILL.

The gateway alone owns the write end of a lifetime pipe. EOF on its read end
means the gateway has gone away. No platform-specific parent-death API needed.
"""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import sys


def main() -> int:
    lifetime_fd = int(sys.argv[1])
    command = sys.argv[2:]
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with selectors.DefaultSelector() as selector:
        selector.register(lifetime_fd, selectors.EVENT_READ)
        # The CLI gets a separate group so its tools can be terminated together.
        child = subprocess.Popen(command, start_new_session=True)
        try:
            while child.poll() is None:
                if stopping or selector.select(timeout=0.1):
                    break
        finally:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            # Also clean up any descendants that outlived their CLI parent.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            os.close(lifetime_fd)
    return child.returncode if child.returncode >= 0 else 128 - child.returncode


if __name__ == "__main__":
    sys.exit(main())
