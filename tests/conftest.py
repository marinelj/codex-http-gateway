from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from codex_gateway.app import create_app
from codex_gateway.config import Settings

TOKEN = "test-token-" + "x" * 40


@pytest.fixture
def settings_factory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = tmp_path / "fake-codex"
    executable.write_text(
        f"#!{sys.executable}\n" + Path(__file__).with_name("fake_codex.py").read_text()
    )
    executable.chmod(0o700)

    def make(**overrides):
        return Settings(
            _env_file=None,
            **{
                "workspace": workspace,
                "data_dir": tmp_path / "data",
                "codex_home": tmp_path / "codex-home",
                "codex_bin": str(executable),
                "api_token": TOKEN,
                "startup_wait_seconds": 3,
                **overrides,
            },
        )

    return make


@asynccontextmanager
async def client_for(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, root_path=settings.root_path),
            base_url="http://test",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as client:
            yield client, app.state.gateway


async def wait_terminal(client, run_id):
    async with asyncio.timeout(8):
        while True:
            response = await client.get(f"/v1/runs/{run_id}")
            assert response.status_code == 200, response.text
            run = response.json()
            if run["status"] not in {"queued", "starting", "running"}:
                return run
            await asyncio.sleep(0.02)


async def submit(client, prompt="hello", thread_id=None, **kwargs):
    response = await client.post(
        "/v1/runs",
        json={
            "prompt": prompt,
            "thread_id": thread_id,
        },
        **kwargs,
    )
    assert response.status_code == 202, response.text
    return response.json()
