import json
import sqlite3
from uuid import uuid4

from codex_gateway.store import GatewayError, Store

from .conftest import client_for, submit, wait_terminal


def saved_thread(settings, workspace=None, name="Design a feature", archived=False, **metadata):
    thread_id = str(uuid4())
    root = settings.codex_home / ("archived_sessions" if archived else "sessions")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"rollout-{thread_id}.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": thread_id,
                    "cwd": str(workspace or settings.workspace),
                    "name": name,
                    **metadata,
                },
            }
        )
        + "\n"
    )
    return thread_id


async def test_console_and_workspace_load_without_sign_in(settings_factory):
    async with client_for(settings_factory(root_path="/codex")) as (client, _):
        page = await client.get("/")
        assert page.status_code == 200
        assert 'data-base="/codex"' in page.text
        assert 'src="/codex/assets/app.mjs"' in page.text
        assert "Active threads" in page.text
        assert "connect-dialog" not in page.text
        assert "access-token" not in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert "no-referrer" == page.headers["referrer-policy"]
        for path in ["/v1/overview", "/v1/threads"]:
            assert (await client.get(path)).status_code == 200
        for path in ["styles.css", "app.mjs", "client.mjs", "favicon.svg"]:
            assert (await client.get("/codex/assets/" + path)).status_code == 200
        assert (await client.get("/ui")).headers["location"] == "/codex/"


async def test_catalog_includes_existing_threads_and_supports_pagination(settings_factory):
    settings = settings_factory()
    ids = {saved_thread(settings, name=f"Feature {index}") for index in range(3)}
    archived = saved_thread(settings, archived=True)
    async with client_for(settings) as (client, _):
        first = (await client.get("/v1/threads?limit=2")).json()
        assert first["has_more"] and len(first["threads"]) == 2
        second = (
            await client.get("/v1/threads", params={"limit": 2, "cursor": first["next_cursor"]})
        ).json()
        found = {item["thread_id"] for item in first["threads"] + second["threads"]}
        assert found == ids and archived not in found
        assert all(item["can_send"] and item["latest_run"] is None for item in first["threads"])
        match = (await client.get("/v1/threads?search=Feature+1")).json()
        assert len(match["threads"]) == 1
        info = (await client.get(f"/v1/threads/{next(iter(ids))}/info")).json()
        assert info["workspace"] == str(settings.workspace)


async def test_selected_thread_routes_prompt_to_its_original_workspace(settings_factory):
    settings = settings_factory(allow_other_workspaces=True)
    other = settings.workspace.parent / "second-project"
    other.mkdir()
    thread_id = saved_thread(settings, other)
    async with client_for(settings) as (client, _):
        threads = (await client.get("/v1/threads")).json()["threads"]
        assert threads[0]["can_send"]
        run = await submit(client, "Prompt for the selected thread", thread_id)
        result = await wait_terminal(client, run["run_id"])
        assert result["status"] == "completed"
        assert result["thread_id"] == thread_id
        assert result["workspace"] == str(other)
        assert result["requested_thread_id"] == thread_id
        assert result["final_output"] == "Prompt for the selected thread"
        meta = json.loads(next((settings.codex_home / "sessions").glob("*.jsonl")).read_text())
        assert meta["payload"]["cwd"] == str(other)


async def test_other_workspaces_remain_opt_in_and_unavailable_threads_are_disabled(
    settings_factory,
):
    settings = settings_factory()
    other = settings.workspace.parent / "another-project"
    other.mkdir()
    foreign = saved_thread(settings, other)
    unavailable = saved_thread(settings, can_send=False)
    async with client_for(settings) as (client, _):
        threads = (await client.get("/v1/threads")).json()["threads"]
        assert not any(thread["can_send"] for thread in threads)
        response = await client.post("/v1/runs", json={"prompt": "hello", "thread_id": foreign})
        assert (
            response.status_code == 409 and response.json()["error"]["code"] == "workspace_mismatch"
        )
        response = await client.post("/v1/runs", json={"prompt": "hello", "thread_id": unavailable})
        assert (
            response.status_code == 409 and response.json()["error"]["code"] == "thread_unavailable"
        )


async def test_overview_preview_and_newest_first_history(settings_factory):
    async with client_for(settings_factory()) as (client, _):
        first = await submit(client, "First prompt")
        await wait_terminal(client, first["run_id"])
        second = await submit(client, "Second prompt")
        await wait_terminal(client, second["run_id"])
        page = (await client.get("/v1/runs?order=desc&limit=1")).json()
        assert page["runs"][0]["run_id"] == second["run_id"]
        assert page["runs"][0]["prompt_preview"] == "Second prompt"
        next_page = (
            await client.get("/v1/runs", params={"order": "desc", "after": page["next_after"]})
        ).json()
        assert next_page["runs"][0]["run_id"] == first["run_id"]
        overview = (await client.get("/v1/overview")).json()
        assert overview["total_runs"] == 2 and overview["statuses"]["completed"] == 2
        assert overview["total_threads"] == 2


def test_existing_database_migrates_without_losing_output(settings_factory):
    settings = settings_factory()
    settings.prepare()
    store = Store(settings)
    run = store.create("Keep this prompt", None, "migration")
    store.finish(run["run_id"], "completed", final_output="Keep this answer")
    store.close()
    db = sqlite3.connect(settings.data_dir / "gateway.sqlite3")
    db.execute("ALTER TABLE runs DROP COLUMN workspace")
    db.execute("UPDATE metadata SET value='1' WHERE key='schema_version'")
    db.commit()
    db.close()
    store = Store(settings)
    migrated = store.get(run["run_id"])
    assert migrated["workspace"] == str(settings.workspace)
    assert migrated["final_output"] == "Keep this answer"
    assert migrated["prompt_preview"] == "Keep this prompt"
    store.close()


async def test_models_efforts_and_full_access_reach_new_and_resumed_cli(settings_factory):
    async with client_for(settings_factory()) as (client, _):
        options = (await client.get("/v1/models")).json()
        assert options["default_model"] == "codex-test"
        fast = next(model for model in options["models"] if model["id"] == "codex-fast")
        assert [effort["id"] for effort in fast["efforts"]] == ["low", "medium"]
        first = await submit(client, "execution-options")
        result = await wait_terminal(client, first["run_id"])
        assert json.loads(result["final_output"]) == {
            "model": "codex-test",
            "reasoning_effort": "medium",
            "sandbox": "danger-full-access",
            "approval_policy": "never",
        }
        response = await client.post(
            "/v1/runs",
            json={
                "prompt": "execution-options",
                "thread_id": result["thread_id"],
                "model": "codex-fast",
                "reasoning_effort": "low",
            },
            headers={"Idempotency-Key": "model-selection"},
        )
        assert response.status_code == 202
        resumed = await wait_terminal(client, response.json()["run_id"])
        assert resumed["thread_id"] == result["thread_id"]
        assert resumed["model"] == "codex-fast" and resumed["reasoning_effort"] == "low"
        assert json.loads(resumed["final_output"]) == {
            "model": "codex-fast",
            "reasoning_effort": "low",
            "sandbox": "danger-full-access",
            "approval_policy": "never",
        }
        conflict = await client.post(
            "/v1/runs",
            json={
                "prompt": "execution-options",
                "thread_id": result["thread_id"],
                "model": "codex-fast",
                "reasoning_effort": "medium",
            },
            headers={"Idempotency-Key": "model-selection"},
        )
        assert conflict.status_code == 409
        invalid = await client.post(
            "/v1/runs",
            json={
                "prompt": "hello",
                "model": "codex-fast",
                "reasoning_effort": "high",
            },
        )
        assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "invalid_effort"
        invalid = await client.post("/v1/runs", json={"prompt": "hello", "model": "missing-model"})
        assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "invalid_model"


async def test_chat_pages_saved_messages_in_conversation_order(settings_factory):
    settings = settings_factory()
    async with client_for(settings) as (client, _):
        first = await submit(client, "First message")
        result = await wait_terminal(client, first["run_id"])
        second = await submit(client, "Second message", result["thread_id"])
        await wait_terminal(client, second["run_id"])
        page = (await client.get(f"/v1/threads/{result['thread_id']}/messages?limit=1")).json()
        assert page["history_scope"] == "codex" and page["has_more"]
        assert [(message["role"], message["text"]) for message in page["messages"]] == [
            ("user", "Second message"),
            ("assistant", "Second message"),
        ]
        earlier = (
            await client.get(
                f"/v1/threads/{result['thread_id']}/messages",
                params={
                    "limit": 1,
                    "cursor": page["next_cursor"],
                },
            )
        ).json()
        assert [message["text"] for message in earlier["messages"]] == [
            "First message",
            "First message",
        ]
        assert not earlier["has_more"]
    async with client_for(settings) as (client, _):
        restored = (await client.get(f"/v1/runs/{second['run_id']}")).json()
        assert restored["model"] == "codex-test"
        assert restored["reasoning_effort"] == "medium"
        assert restored["sandbox"] == "danger-full-access"


def test_version_two_history_keeps_unknown_execution_settings(settings_factory):
    settings = settings_factory()
    settings.prepare()
    store = Store(settings)
    run = store.create("Older prompt", None, None)
    store.finish(run["run_id"], "completed", final_output="Older answer")
    store.close()

    with sqlite3.connect(settings.data_dir / "gateway.sqlite3") as db:
        for column in ["model", "reasoning_effort", "sandbox"]:
            db.execute(f"ALTER TABLE runs DROP COLUMN {column}")
        db.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
    store = Store(settings)
    result = store.get(run["run_id"])
    assert result["final_output"] == "Older answer"
    assert (
        result["model"] is None and result["reasoning_effort"] is None and result["sandbox"] is None
    )
    store.close()


async def test_usage_notice_shows_remaining_limits_without_account_identifiers(settings_factory):
    async with client_for(settings_factory()) as (client, gateway):
        response = await client.get("/v1/usage")
        notice = response.json()
        assert notice["available"]
        assert [(window["name"], window["remaining_percent"]) for window in notice["windows"]] == [
            ("5-hour", 75),
            ("Weekly", 40),
        ]
        assert notice["resets_available"] == 2
        assert all(window["resets_at"] > 0 for window in notice["windows"])
        assert "not-for-the-console" not in response.text and "accountId" not in response.text

        async def unavailable(*_args):
            raise GatewayError(503, "offline", "Offline")

        gateway.catalog.usage_cache = None
        gateway.catalog.rpc = unavailable
        notice = (await client.get("/v1/usage")).json()
        assert not notice["available"] and notice["windows"] == []
        assert "unavailable" in notice["message"]
