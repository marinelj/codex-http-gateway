import json
import sqlite3
from uuid import uuid4

from codex_gateway.store import Store

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


async def test_console_is_public_but_private_workspace_details_are_protected(settings_factory):
    async with client_for(settings_factory(root_path="/codex")) as (client, _):
        page = await client.get("/", headers={"Authorization": ""})
        assert page.status_code == 200
        assert 'data-base="/codex"' in page.text
        assert 'src="/codex/assets/app.mjs"' in page.text
        assert "Active threads" in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert "no-referrer" == page.headers["referrer-policy"]
        for path in ["/v1/overview", "/v1/threads"]:
            assert (await client.get(path, headers={"Authorization": ""})).status_code == 401
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
