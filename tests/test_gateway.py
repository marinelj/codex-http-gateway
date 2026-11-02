from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from uuid import uuid4

import pytest

from codex_gateway.config import Settings
from codex_gateway.store import Store

from .conftest import client_for, submit, wait_terminal


async def test_auth_validation_and_body_limit(settings_factory):
    async with client_for(settings_factory()) as (client, _):
        assert (await client.get("/healthz", headers={"Authorization": ""})).status_code == 200
        for token in ("", "Bearer bad", "Basic bad"):
            response = await client.get("/v1/runs", headers={"Authorization": token})
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
        for body in (
            {"prompt": " "},
            {"prompt": "ok", "thread_id": "bad"},
            {"prompt": "ok", "cwd": "/"},
            {"prompt": "x" * 100001},
        ):
            assert (await client.post("/v1/runs", json=body)).status_code == 422

        async def chunks():
            for _ in range(10):
                yield b"x" * 60000

        response = await client.post("/v1/runs", content=chunks())
        assert response.status_code == 413
        assert response.headers["cache-control"] == "no-store"


async def test_submit_continue_and_thread_history(settings_factory):
    async with client_for(settings_factory()) as (client, _):
        first = await submit(client)
        assert first["thread_id"] is not None
        result = await wait_terminal(client, first["run_id"])
        assert result["status"] == "completed"
        assert result["final_output"] == "hello"
        assert result["usage"]["input_tokens"] == 5
        assert result["exit_code"] == 0
        second = await submit(client, "follow up", first["thread_id"])
        result2 = await wait_terminal(client, second["run_id"])
        assert result2["thread_id"] == first["thread_id"]
        assert result2["final_output"] == "follow up"
        thread = (await client.get(first["thread_url"])).json()
        assert thread["status"] == "completed"
        assert thread["latest_run"]["run_id"] == second["run_id"]
        assert thread["history_scope"] == "gateway"
        assert {event["run_id"] for event in thread["events"]} == {
            first["run_id"],
            second["run_id"],
        }
        page = (await client.get("/v1/runs", params={"limit": 1})).json()
        assert page["has_more"]
        next_page = (await client.get("/v1/runs", params={"after": page["next_after"]})).json()
        assert [run["run_id"] for run in next_page["runs"]] == [second["run_id"]]


async def test_output_events_pagination_and_incremental_poll(settings_factory):
    async with client_for(settings_factory()) as (client, _):
        run = await submit(client, "stderr")
        result = await wait_terminal(client, run["run_id"])
        all_events = result["events"]
        sources = {event["source"] for event in all_events}
        assert sources == {"gateway", "codex", "stdout", "stderr"}
        assert any(event["data"].get("type") == "future.event" for event in all_events)
        cursor, collected = 0, []
        while True:
            page = (
                await client.get(run["thread_url"], params={"after": cursor, "limit": 2})
            ).json()
            collected.extend(page["events"])
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        assert collected == all_events
        empty = (await client.get(run["thread_url"], params={"after": cursor})).json()
        assert empty["events"] == [] and empty["next_cursor"] == cursor


async def test_fast_response_cancel_and_busy_thread(settings_factory):
    async with client_for(settings_factory(startup_wait_seconds=0.1)) as (client, _):
        start = time.monotonic()
        run = await submit(client, "sleep:30")
        assert time.monotonic() - start < 1
        async with asyncio.timeout(5):
            while not run["thread_id"]:
                run = {**run, **(await client.get(run["status_url"])).json()}
                await asyncio.sleep(0.02)
        assert run["status"] == "running"
        busy = await client.post(
            "/v1/runs", json={"prompt": "second", "thread_id": run["thread_id"]}
        )
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "thread_busy"
        response = await client.post(f"{run['status_url']}/cancel")
        assert response.json()["status"] == "cancelled"
        assert (await client.post(f"{run['status_url']}/cancel")).json()["status"] == "cancelled"
        resumed = await submit(client, "after cancellation", run["thread_id"])
        assert (await wait_terminal(client, resumed["run_id"]))["status"] == "completed"


async def test_queue_capacity_and_idempotency(settings_factory):
    async with client_for(settings_factory(max_pending_runs=1)) as (client, _):
        first = await submit(client, "sleep:30", headers={"Idempotency-Key": "same"})
        again = await submit(client, "sleep:30", headers={"Idempotency-Key": "same"})
        assert again["run_id"] == first["run_id"]
        conflict = await client.post(
            "/v1/runs", json={"prompt": "different"}, headers={"Idempotency-Key": "same"}
        )
        assert conflict.status_code == 409
        full = await client.post("/v1/runs", json={"prompt": "another"})
        assert full.status_code == 429 and full.headers["retry-after"] == "2"


async def test_cancel_queued_and_worker_continues(settings_factory):
    async with client_for(settings_factory(startup_wait_seconds=0.1)) as (client, _):
        first = await submit(client, "sleep:30")
        second = await submit(client, "do not execute")
        assert second["status"] == "queued"
        assert (await client.post(second["status_url"] + "/cancel")).json()["status"] == "cancelled"
        await client.post(first["status_url"] + "/cancel")
        third = await submit(client)
        assert (await wait_terminal(client, third["run_id"]))["status"] == "completed"


@pytest.mark.parametrize(
    ("prompt", "status", "code"),
    [
        ("fail", "failed", "turn_failed"),
        ("exit", "failed", "cli_exit"),
        ("no-thread", "failed", "incomplete_stream"),
        ("no-complete", "failed", "incomplete_stream"),
        ("sleep:30", "timed_out", "timeout"),
        ("large", "failed", "output_limit"),
    ],
)
async def test_failures_are_terminal_and_worker_recovers(settings_factory, prompt, status, code):
    timeout = 1.5 if prompt.startswith("sleep:") else 10
    async with client_for(settings_factory(run_timeout_seconds=timeout, max_output_bytes=3000)) as (
        client,
        _,
    ):
        run = await submit(client, prompt)
        result = await wait_terminal(client, run["run_id"])
        assert result["status"] == status and result["error"]["code"] == code
        another = await submit(client)
        assert (await wait_terminal(client, another["run_id"]))["status"] == "completed"


async def test_session_validation_and_uuid_mismatch(settings_factory):
    settings = settings_factory()
    async with client_for(settings) as (client, _):
        unknown = await client.post("/v1/runs", json={"prompt": "x", "thread_id": str(uuid4())})
        assert unknown.status_code == 404
        first = await submit(client)
        await wait_terminal(client, first["run_id"])
        mismatch = await submit(client, "mismatch", first["thread_id"])
        result = await wait_terminal(client, mismatch["run_id"])
        assert result["error"]["code"] == "thread_mismatch"
        assert result["thread_id"] == first["thread_id"]
        path = next((settings.codex_home / "sessions").glob(f"*{first['thread_id']}.jsonl"))
        meta = json.loads(path.read_text())
        meta["payload"]["cwd"] = "/another-workspace"
        path.write_text(json.dumps(meta))
        wrong = await client.post("/v1/runs", json={"prompt": "x", "thread_id": first["thread_id"]})
        assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "workspace_mismatch"


async def test_literal_prompt_and_no_gateway_secrets_in_child(settings_factory, monkeypatch):
    monkeypatch.setenv("GATEWAY_SECRET_FOR_TEST", "must-not-reach-cli")
    async with client_for(settings_factory()) as (client, _):
        prompt = "--help $(touch /tmp/unwanted-gateway-file) `false`\n中文 ' quote"
        run = await submit(client, prompt)
        assert (await wait_terminal(client, run["run_id"]))["final_output"] == prompt
        run = await submit(client, "env")
        assert (await wait_terminal(client, run["run_id"]))["final_output"] == ""


async def test_persistence_and_idempotency_survive_restart(settings_factory):
    settings = settings_factory()
    async with client_for(settings) as (client, _):
        first = await submit(client, headers={"Idempotency-Key": "durable"})
        await wait_terminal(client, first["run_id"])
    async with client_for(settings) as (client, _):
        assert (await client.get(first["status_url"])).json()["status"] == "completed"
        replay = await submit(client, headers={"Idempotency-Key": "durable"})
        assert replay["run_id"] == first["run_id"]
        resumed = await submit(client, "after restart", first["thread_id"])
        assert (await wait_terminal(client, resumed["run_id"]))["status"] == "completed"


async def test_shutdown_and_stale_run_recovery(settings_factory):
    settings = settings_factory()
    async with client_for(settings) as (client, _):
        run = await submit(client, "sleep:30")
    settings.prepare()
    store = Store(settings)
    assert store.get(run["run_id"])["status"] == "interrupted"
    stale = store.create("saved but not finished", None, None)
    store.close()
    async with client_for(settings) as (client, _):
        recovered = (await client.get(f"/v1/runs/{stale['run_id']}")).json()
        assert recovered["status"] == "interrupted"
        assert recovered["error"]["code"] == "service_restarted"


async def test_single_instance_and_workspace_binding(settings_factory):
    settings = settings_factory()
    async with client_for(settings):
        with pytest.raises(RuntimeError, match="already owns"):
            Store(settings)
    other = settings.workspace.parent / "other"
    other.mkdir()
    settings.workspace = other
    with pytest.raises(RuntimeError, match="workspace"):
        Store(settings)


async def test_proxy_prefix_in_docs_and_poll_urls(settings_factory):
    async with client_for(settings_factory(root_path="/codex")) as (client, _):
        result = await submit(client)
        assert result["status_url"].startswith("/codex/v1/")
        assert result["thread_url"].startswith("/codex/v1/")
        docs = await client.get("/docs")
        assert "/codex/openapi.json" in docs.text


async def test_supervisor_stops_cli_when_gateway_pipe_closes(settings_factory):
    settings = settings_factory()
    read_fd, write_fd = os.pipe()
    from codex_gateway.runner import Runner

    # This pipe stands in for the gateway process: EOF also occurs on SIGKILL.
    command = Runner(settings, None).command(None)
    env = dict(os.environ, CODEX_HOME=str(settings.codex_home))
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "codex_gateway.supervisor",
        str(read_fd),
        *command,
        pass_fds=(read_fd,),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    os.close(read_fd)
    proc.stdin.write(b"sleep:30")
    await proc.stdin.drain()
    proc.stdin.close()
    assert b"thread.started" in await asyncio.wait_for(proc.stdout.readline(), 3)
    os.close(write_fd)
    assert await asyncio.wait_for(proc.wait(), 5) != 0


def test_generated_token_is_private_and_stable(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "private")
    assert settings.token() == settings.token()
    assert (settings.data_dir / "api-token").stat().st_mode & 0o777 == 0o600


def test_proxy_only_changes_child_environment(settings_factory, monkeypatch):
    from codex_gateway.runner import Runner

    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    settings = settings_factory(proxy_url="http://127.0.0.1:12345")
    env = Runner(settings, None).environment()
    assert env["HTTPS_PROXY"] == settings.proxy_url
    assert "127.0.0.1" in env["NO_PROXY"]
    assert "HTTPS_PROXY" not in os.environ


async def test_zero_wait_and_concurrent_retry(settings_factory):
    async with client_for(settings_factory(startup_wait_seconds=0)) as (client, _):
        requests = await asyncio.gather(
            *[submit(client, headers={"Idempotency-Key": "parallel"}) for _ in range(5)]
        )
        assert len({run["run_id"] for run in requests}) == 1
        assert requests[0]["thread_id"] is None
        run = await wait_terminal(client, requests[0]["run_id"])
        assert run["status"] == "completed" and run["thread_id"]


async def test_single_line_output_limit(settings_factory):
    async with client_for(settings_factory(max_line_bytes=1024)) as (client, _):
        run = await submit(client, "large")
        result = await wait_terminal(client, run["run_id"])
        assert result["status"] == "failed" and result["error"]["code"] == "output_limit"
