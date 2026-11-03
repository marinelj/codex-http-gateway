"""Read Codex's supported local thread catalog without starting model turns."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from uuid import UUID

from . import __version__
from .config import Settings
from .runner import Runner
from .store import GatewayError, Store, now

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
        self.model_lock = asyncio.Lock()
        self.model_cache: dict | None = None
        self.models_at = 0.0
        self.usage_lock = asyncio.Lock()
        self.usage_cache: dict | None = None
        self.usage_at = 0.0

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
                                404 if name in {"thread/read", "thread/turns/list"} else 400,
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

    async def model_options(self) -> dict:
        async with self.model_lock:
            if self.model_cache is not None and time.monotonic() - self.models_at < 300:
                return self.model_cache
            choices = []
            cursor = None
            seen = set()
            default = None
            while True:
                page = await self.rpc(
                    "model/list", {"limit": 100, "includeHidden": False, "cursor": cursor}
                )
                for model in page.get("data", []):
                    if model.get("hidden") or "text" not in model.get("inputModalities", ["text"]):
                        continue
                    choices.append(
                        {
                            "id": model["model"],
                            "name": model["displayName"],
                            "description": model.get("description", ""),
                            "default_effort": model["defaultReasoningEffort"],
                            "efforts": [
                                {
                                    "id": effort["reasoningEffort"],
                                    "description": effort["description"],
                                }
                                for effort in model["supportedReasoningEfforts"]
                            ],
                        }
                    )
                    if model.get("isDefault"):
                        default = model["model"]
                cursor = page.get("nextCursor")
                if not cursor:
                    break
                if cursor in seen:
                    raise GatewayError(502, "invalid_catalog", "Model pagination did not advance")
                seen.add(cursor)
            if not choices:
                raise GatewayError(503, "models_unavailable", "Codex has no available text models")
            config = (await self.rpc("config/read", {"includeLayers": False})).get("config", {})
            preferred = self.settings.model or config.get("model") or default
            selected = next((item for item in choices if item["id"] == preferred), choices[0])
            effort = self.settings.reasoning_effort or config.get("model_reasoning_effort")
            if effort not in {item["id"] for item in selected["efforts"]}:
                effort = selected["default_effort"]
            self.model_cache = {
                "models": choices,
                "default_model": selected["id"],
                "default_effort": effort,
            }
            self.models_at = time.monotonic()
            return self.model_cache

    async def resolve_options(self, model: str | None, effort: str | None) -> tuple[str, str]:
        options = await self.model_options()
        model_id = model or options["default_model"]
        choice = next((item for item in options["models"] if item["id"] == model_id), None)
        if choice is None:
            raise GatewayError(422, "invalid_model", "Select a model available in Codex")
        selected_effort = effort or (
            options["default_effort"]
            if model_id == options["default_model"]
            else choice["default_effort"]
        )
        if selected_effort not in {item["id"] for item in choice["efforts"]}:
            raise GatewayError(
                422,
                "invalid_effort",
                "This reasoning effort is not supported by the selected model",
            )
        return model_id, selected_effort

    async def messages(self, thread_id: str, cursor: str | None, limit: int) -> dict:
        page = await self.rpc(
            "thread/turns/list",
            {
                "threadId": thread_id,
                "cursor": cursor,
                "limit": limit,
                "sortDirection": "desc",
                "itemsView": "full",
            },
        )
        messages = []
        for turn in reversed(page.get("data", [])):
            for index, item in enumerate(turn.get("items", [])):
                if item.get("type") == "userMessage":
                    role = "user"
                    text = "\n".join(
                        part.get("text", "")
                        if part.get("type") == "text"
                        else f"[{part.get('type', 'attachment')}]"
                        for part in item.get("content", [])
                    )
                elif item.get("type") == "agentMessage" and item.get("phase") != "commentary":
                    role, text = "assistant", item.get("text", "")
                else:
                    continue
                messages.append(
                    {
                        "id": item.get("id") or f"{turn['id']}-{index}",
                        "turn_id": turn["id"],
                        "role": role,
                        "text": text,
                        "created_at": turn.get("startedAt")
                        if role == "user"
                        else turn.get("completedAt"),
                        "status": turn.get("status", "completed"),
                    }
                )
        return {
            "thread_id": thread_id,
            "messages": messages,
            "next_cursor": page.get("nextCursor"),
            "has_more": bool(page.get("nextCursor")),
        }

    async def usage(self) -> dict:
        async with self.usage_lock:
            if self.usage_cache is not None and time.monotonic() - self.usage_at < 60:
                return self.usage_cache
            notice = {"available": False, "updated_at": now(), "windows": []}
            try:
                result = await self.rpc("account/rateLimits/read", {})
                snapshots = result.get("rateLimitsByLimitId") or {}
                if not snapshots and result.get("rateLimits"):
                    snapshots = {"codex": result["rateLimits"]}
                for key, snapshot in snapshots.items():
                    for slot in ("primary", "secondary"):
                        window = snapshot.get(slot)
                        if not window or window.get("usedPercent") is None:
                            continue
                        minutes = window.get("windowDurationMins")
                        if minutes == 10080:
                            name = "Weekly"
                        elif minutes and minutes % 60 == 0:
                            name = f"{minutes // 60}-hour"
                        elif minutes:
                            name = f"{minutes}-minute"
                        else:
                            name = slot.title()
                        notice["windows"].append(
                            {
                                "id": f"{key}:{slot}",
                                "name": name,
                                "scope": snapshot.get("limitName")
                                or ("Codex" if key == "codex" else key),
                                "remaining_percent": max(0, min(100, 100 - window["usedPercent"])),
                                "resets_at": window.get("resetsAt"),
                                "window_minutes": minutes,
                            }
                        )
                notice["available"] = bool(notice["windows"])
                notice["resets_available"] = (result.get("rateLimitResetCredits") or {}).get(
                    "availableCount"
                )
            except GatewayError:
                notice["message"] = "Codex usage limits are unavailable right now."
            if not notice["available"]:
                notice["message"] = (
                    notice.get("message") or "This Codex account does not report usage limits."
                )
            self.usage_cache = notice
            self.usage_at = time.monotonic()
            return notice
