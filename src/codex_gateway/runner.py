from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from .config import Settings
from .store import GatewayError, Store


def validate_session(settings: Settings, thread_id: str, workspace: Path | None = None) -> None:
    """Validate local session metadata, without importing private transcript history."""
    root = settings.codex_home / "sessions"
    for path in root.glob(f"**/*{thread_id}.jsonl"):
        if not path.resolve().is_relative_to(root.resolve()):
            continue
        try:
            with path.open("rb") as stream:
                line = stream.readline(1024 * 1024)
            header = json.loads(line)
            payload = header.get("payload", {})
            if header.get("type") != "session_meta" or payload.get("id") != thread_id:
                continue
            cwd = payload.get("cwd")
            if not isinstance(cwd, str) or Path(cwd).resolve() != (workspace or settings.workspace):
                raise GatewayError(409, "workspace_mismatch", "Thread belongs to another workspace")
            return
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    raise GatewayError(404, "thread_not_found", "No saved local Codex session with this thread ID")


class RunFailure(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class Runner:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store

    def command(self, thread_id: str | None, workspace: str | None = None) -> list[str]:
        args = [
            self.settings.codex_bin,
            "exec",
            "--json",
            "--color",
            "never",
            "-C",
            workspace or str(self.settings.workspace),
            "-c",
            f'sandbox_mode="{self.settings.sandbox}"',
            "-c",
            'approval_policy="never"',
        ]
        if self.settings.model:
            args.extend(["--model", self.settings.model])
        if self.settings.skip_git_repo_check:
            args.append("--skip-git-repo-check")
        if thread_id:
            args.extend(["resume", thread_id])
        args.append("-")
        return args

    def environment(self) -> dict[str, str]:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("GATEWAY_")
            and k not in {"CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_TOOL_CALL_ID"}
        }
        env["CODEX_HOME"] = str(self.settings.codex_home)
        if self.settings.proxy_url:
            env["HTTPS_PROXY"] = env["HTTP_PROXY"] = self.settings.proxy_url
            # Local MCP services and the HTTP provider must stay on loopback.
            env["NO_PROXY"] = ",".join(
                [env.get("NO_PROXY", env.get("no_proxy", "")), "localhost", "127.0.0.1", "::1"]
            ).lstrip(",")
        return env

    async def execute(self, run_id: str, ready: asyncio.Event) -> None:
        run = self.store.get(run_id)
        proc: asyncio.subprocess.Process | None = None
        lifetime_read, lifetime_write = os.pipe()
        tasks: list[asyncio.Task] = []
        bound = False
        completed = False
        failure: dict | None = None
        total_bytes = 0

        def consume_event(data: dict) -> None:
            nonlocal bound, completed, failure
            kind = data.get("type")
            if kind == "thread.started":
                try:
                    actual = str(UUID(data["thread_id"]))
                except (KeyError, ValueError, TypeError, AttributeError):
                    raise RunFailure(
                        "invalid_thread_id", "CLI emitted an invalid thread ID"
                    ) from None
                if run["requested_thread_id"] and actual != run["requested_thread_id"]:
                    raise RunFailure(
                        "thread_mismatch", "CLI resumed a different thread; execution stopped"
                    )
                if bound and actual != self.store.get(run_id)["thread_id"]:
                    raise RunFailure("thread_mismatch", "CLI changed thread IDs during execution")
                self.store.bind_thread(run_id, actual)
                self.store.update(run_id, status="running")
                bound = True
                ready.set()
            elif kind == "item.completed":
                item = data.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    if isinstance(item.get("text"), str):
                        self.store.update(run_id, final_output=item["text"])
            elif kind == "turn.completed":
                completed = True
                if isinstance(data.get("usage"), dict):
                    self.store.update(run_id, usage=data["usage"])
            elif kind == "turn.failed":
                detail = data.get("error", {})
                message = (
                    detail.get("message", "Codex turn failed")
                    if isinstance(detail, dict)
                    else str(detail)
                )
                failure = {"code": "turn_failed", "message": message}

        async def read_stream(stream: asyncio.StreamReader, source: str) -> None:
            nonlocal total_bytes
            while True:
                try:
                    line = await stream.readline()
                except ValueError:
                    raise RunFailure(
                        "output_limit", "A CLI output line exceeded the configured limit"
                    ) from None
                if not line:
                    return
                total_bytes += len(line)
                if total_bytes > self.settings.max_output_bytes:
                    raise RunFailure(
                        "output_limit", "CLI output exceeded the configured per-run limit"
                    )
                value = line.decode("utf-8", errors="replace")
                if source == "stdout":
                    try:
                        data = json.loads(value)
                    except ValueError:
                        data = None
                    if isinstance(data, dict):
                        self.store.event(run_id, "codex", data)
                        consume_event(data)
                        continue
                self.store.event(run_id, source, {"type": f"cli.{source}", "text": value})

        async def write_prompt() -> None:
            assert proc is not None and proc.stdin is not None
            try:
                proc.stdin.write(run["prompt"].encode())
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.stdin.close()

        try:
            if run["requested_thread_id"]:
                await asyncio.to_thread(
                    validate_session,
                    self.settings,
                    run["requested_thread_id"],
                    Path(run["workspace"]),
                )
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "codex_gateway.supervisor",
                str(lifetime_read),
                *self.command(run["requested_thread_id"], run["workspace"]),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.environment(),
                cwd=run["workspace"],
                pass_fds=(lifetime_read,),
                start_new_session=True,
                limit=self.settings.max_line_bytes,
            )
            os.close(lifetime_read)
            lifetime_read = -1
            assert proc.stdout is not None and proc.stderr is not None
            tasks = [
                asyncio.create_task(read_stream(proc.stdout, "stdout")),
                asyncio.create_task(read_stream(proc.stderr, "stderr")),
                asyncio.create_task(write_prompt()),
            ]
            async with asyncio.timeout(self.settings.run_timeout_seconds):
                await asyncio.gather(*tasks)
                code = await proc.wait()
            if failure:
                self.store.finish(run_id, "failed", failure, exit_code=code)
            elif code != 0:
                self.store.finish(
                    run_id,
                    "failed",
                    {
                        "code": "cli_exit",
                        "message": f"Codex exited with code {code}; inspect events",
                    },
                    exit_code=code,
                )
            elif not bound or not completed:
                self.store.finish(
                    run_id,
                    "failed",
                    {"code": "incomplete_stream", "message": "CLI exited without a complete turn"},
                    exit_code=code,
                )
            else:
                self.store.finish(run_id, "completed", exit_code=code)
        except TimeoutError:
            failure = {"code": "timeout", "message": "Run exceeded the configured time limit"}
            # Finish only after process cleanup below releases the thread.
            raise RunFailure("timeout", failure["message"]) from None
        finally:
            # Closing the pipe makes the supervisor stop the CLI group, even if
            # this coroutine is cancelled before it receives a subprocess handle.
            os.close(lifetime_write)
            if lifetime_read != -1:
                os.close(lifetime_read)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if proc is not None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=5)
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
