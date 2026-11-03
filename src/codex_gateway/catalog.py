"""Read Codex's supported local thread catalog without starting model turns."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from uuid import UUID

from . import __version__
from .config import Settings
from .runner import Runner
from .store import GatewayError, Store

SOURCES = [
    "cli",
    "vscode",
    "exec",
    "appServer",
    "subAgent",
    "subAgentReview",
    "subAgentCompact",
    "subAgentThreadSpawn",
    "subAgentOther",
    "unknown",
]


class Catalog:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.slots = asyncio.Semaphore(2)

    async def rpc(self, method: str, params: dict) -> dict:
        """A short-lived, read-only stdio client. EOF closes it if the gateway dies."""
        proc = None
        try:
            async with asyncio.timeout(10), self.slots:
                proc = await asyncio.create_subprocess_exec(
                    self.settings.codex_bin,
                    "app-server",
                    "--stdio",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=Runner(self.settings, self.store).environment(),
                    cwd=self.settings.workspace,
                    limit=self.settings.max_line_bytes,
                    start_new_session=True,
                )

                async def exchange(request_id: int, name: str, arguments: dict) -> dict:
                    proc.stdin.write(
                        (
                            json.dumps({"id": request_id, "method": name, "params": arguments})
                            + "\n"
                        ).encode()
                    )
                    await proc.stdin.drain()
                    while line := await proc.stdout.readline():
                        try:
                            response = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(response, dict) or response.get("id") != request_id:
                            continue
                        if "error" in response:
                            error = response["error"]
                            raise GatewayError(
                                404 if name == "thread/read" else 400,
                                "catalog_error",
                                str(error.get("message", "Codex catalog request failed")),
                            )
                        result = response.get("result")
                        if not isinstance(result, dict):
                            raise ValueError("Invalid Codex catalog response")
                        return result
                    raise ValueError("Codex catalog closed before responding")

                await exchange(
                    1,
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "codex_http_gateway",
                            "version": __version__,
                        }
                    },
                )
                proc.stdin.write(b'{"method":"initialized","params":{}}\n')
                await proc.stdin.drain()
                return await exchange(2, method, params)
        except (TimeoutError, OSError, ValueError) as exc:
            raise GatewayError(
                503, "catalog_unavailable", "Could not read the local Codex thread catalog"
            ) from exc
        finally:
            if proc is not None:
                if proc.stdin:
                    proc.stdin.close()
                if proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 3)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()

    def normalize(self, thread: dict) -> dict:
        thread_id = str(UUID(thread["id"]))
        cwd = thread.get("cwd") or ""
        workspace = str(Path(cwd).expanduser().resolve()) if cwd else ""
        latest = self.store.maybe_latest(thread_id)
        status = (thread.get("status") or {}).get("type", "notLoaded")
        status = "available" if status in {"notLoaded", "idle"} else status
        if latest and latest["status"] in {"queued", "starting", "running"}:
            status = latest["status"]
        path = thread.get("path")
        saved = bool(path) and Path(path).resolve().is_relative_to(
            self.settings.codex_home / "sessions"
        )
        return {
            "thread_id": thread_id,
            "title": thread.get("name") or (thread.get("preview") or "Untitled conversation")[:100],
            "preview": (thread.get("preview") or "")[:240],
            "workspace": workspace,
            "updated_at": thread.get("updatedAt") or 0,
            "status": status,
            "can_send": bool(
                saved
                and workspace
                and Path(workspace).is_dir()
                and thread.get("canAcceptDirectInput") is not False
                and (
                    self.settings.allow_other_workspaces
                    or workspace == str(self.settings.workspace)
                )
            ),
            "latest_run": latest,
        }

    async def page(self, cursor: str | None, limit: int, search: str | None) -> dict:
        result = await self.rpc(
            "thread/list",
            {
                "cursor": cursor,
                "limit": limit,
                "searchTerm": search,
                "sortKey": "updated_at",
                "sortDirection": "desc",
                "archived": False,
                "sourceKinds": SOURCES,
                "modelProviders": [],
                "useStateDbOnly": True,
            },
        )
        threads = []
        for thread in result.get("data", []):
            try:
                threads.append(self.normalize(thread))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        return {
            "threads": threads,
            "next_cursor": result.get("nextCursor"),
            "has_more": bool(result.get("nextCursor")),
        }

    async def get(self, thread_id: str) -> dict:
        result = await self.rpc("thread/read", {"threadId": thread_id, "includeTurns": False})
        try:
            thread = self.normalize(result["thread"])
        except (ValueError, KeyError, TypeError) as exc:
            raise GatewayError(
                502, "invalid_catalog", "Codex returned invalid thread metadata"
            ) from exc
        if thread["thread_id"] != thread_id:
            raise GatewayError(502, "thread_mismatch", "Codex returned a different thread ID")
        return thread
