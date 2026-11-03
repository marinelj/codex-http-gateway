# Codex Online

A small, persistent HTTP service for submitting prompts to the **Codex CLI** and polling
thread status and output. New conversations and follow-up prompts use the same API.

Includes a responsive messaging console with conversation bubbles, saved Codex history,
model and effort selectors, remaining usage limits, activity, cancellation, and run history.

```text
Client → HTTP :8080 → gateway → codex exec --json
                        └────→ SQLite run history + ordered events
```

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), Git, and an installed,
authenticated [Codex CLI](https://learn.chatgpt.com/docs/cli). Supports macOS and Linux.

## Start

```sh
uv sync --frozen
codex login
uv run codex-gateway serve --workspace /absolute/path/to/your/git-project
```

The default address is `http://127.0.0.1:8080`. Interactive API documentation is at
`/docs`; health checks are at `/healthz`. The console and `/v1` APIs open directly,
without a gateway token or sign-in.

```sh
export BASE=http://127.0.0.1:8080
```

The service reuses local Codex CLI authentication. No model or API key is hardcoded.

## Web console

Open the gateway's root URL (`http://127.0.0.1:8080/` standalone, or
`http://127.0.0.1:8080/codex/` through the shared-port provider). Threads and history
load automatically. On the same LAN, use `http://<server-ip>:8080/codex/` when the
provider is configured to listen on its LAN interface.

The **Active threads** sidebar lists saved, non-archived local Codex threads across
all sources and providers, including conversations created outside the gateway.
It loads every catalog page and supports search and scrolling. Here, "active"
means non-archived; it does not mean every listed thread is currently generating.
Runtime activity in a different Codex process is not necessarily visible to this client.

Click a thread to select its ID and project folder. Enter a prompt, then click
the round **Send message** button (or press Enter). Shift+Enter adds a new line.
Selecting a thread does not submit the draft by itself. **New conversation** starts
in the configured default workspace.

The conversation shows your messages on the right and Codex replies on the left,
with timestamps, a live thinking indicator, and a bottom composer. Saved messages
from Codex CLI and app conversations load through the supported history API;
**Load earlier messages** pages back through older turns. A **Stop** button cancels
an active message. Selecting a different thread preserves the current draft.

Choose a **Model** and **Effort** before sending. The lists come from your installed
Codex account, and effort options update for each model. Choices apply to the next
message, including resumed conversations. Each run records its model, effort, project
folder and permissions. The API also accepts optional `model` and `reasoning_effort`;
the original `{prompt, thread_id}` request remains valid.

The **Usage left** notice board shows remaining percentages and reset times for
Codex's reported limits, including 5-hour and weekly windows when available. It
refreshes automatically and reports unavailable data explicitly. This is remaining
account allowance, not a count of messages or context tokens. No account identifiers
or authentication credentials are returned by this endpoint.

**Activity & run details** expands the original Answer, Activity, Thread and JSON
views. Copy output, download a transcript, or inspect individual runs. Raw event
history covers gateway submissions; saved chat history also includes earlier Codex
messages. History provides status filters and pagination, and the connection screen
shows the workspace, permissions, model defaults and service limits.

Prompts and model output are rendered as text, without executing model-supplied
HTML. All scripts, styles and icons are served locally; no third-party assets
receive your conversations.

To send prompts to saved threads from other projects, set
`GATEWAY_ALLOW_OTHER_WORKSPACES=true`. Each accepted run persists that thread's
original folder, and the CLI uses it when resuming. This remains opt-in; the
configured Codex sandbox applies to every run. Non-resumable or unavailable threads
remain visible with sending disabled.

## Submit and poll

```sh
curl -sS "$BASE/v1/runs" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: architecture-review-1' \
  -d '{"prompt":"Review this project and summarize its architecture","thread_id":null}'
```

Returns HTTP **202 Accepted** quickly, without waiting for the answer:

```json
{
  "run_id": "run_...",
  "thread_id": "<Codex UUID, or null while starting>",
  "status": "running",
  "status_url": "/v1/runs/run_...",
  "thread_url": "/v1/threads/<uuid>"
}
```

For a new thread the gateway waits at most one second for `thread.started`. If the
queue or CLI startup takes longer, poll `status_url` to obtain the real thread UUID.
A thread ID is never invented. Supply that UUID in a later POST to continue:

```sh
curl -sS "$BASE/v1/runs" \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Which part should I improve first?","thread_id":"<thread-uuid>"}'

curl -sS "$BASE/v1/threads/<thread-uuid>"
curl -sS "$BASE/v1/runs/<run-id>"
```

The thread response includes `status`, `latest_run` (with `final_output`, usage,
timestamps and any error), and the ordered events across **all gateway submissions**
on that thread. The run response also includes its original prompt.

Event records preserve CLI JSON objects, stderr, and non-JSON stdout. Agent messages,
tool results, file-change events and errors exposed by the CLI are available here;
this does not provide hidden model reasoning or file downloads. The latest completed
agent message is available as `final_output`, including partial output on a failed run.

### Read every event

Responses contain at most `limit` events (default 100, maximum 1000). Pass
`?after=<next_cursor>&limit=1000` until `has_more` is false. Continue polling with
the same last cursor for new events while the run is active. Cursors are durable
and strictly increasing; the latest status is returned even with an empty event page.

### Endpoints

| Method | Path | Result |
| --- | --- | --- |
| POST | `/v1/runs` | Accept `{prompt, thread_id, model?, reasoning_effort?}`; null or omitted thread ID starts a thread |
| GET | `/v1/threads` | Non-archived Codex catalog; optional `cursor`, `limit`, `search` |
| GET | `/v1/threads/{thread_id}/info` | Saved title, project folder and availability |
| GET | `/v1/threads/{thread_id}/messages` | Saved Codex messages; optional `cursor`, `limit` |
| GET | `/v1/threads/{thread_id}` | Latest status and paginated events across the thread |
| GET | `/v1/runs/{run_id}` | One prompt, status, output and paginated events |
| GET | `/v1/runs` | Paginated run summaries; optional `thread_id`, `after`, `limit`, `order=asc\|desc` |
| GET | `/v1/overview` | Workspace settings and aggregate run counts |
| GET | `/v1/models` | Available account models, supported efforts, and current defaults |
| GET | `/v1/usage` | Remaining account limits and reset times, cached for up to one minute |
| POST | `/v1/runs/{run_id}/cancel` | Stop a queued or active run; safe to repeat |
| GET | `/healthz` | Service liveness; does not verify model access or authentication |

Run-list pagination uses `next_after` (a run ID), rather than an event cursor.
`Idempotency-Key` is optional; reuse with the same body returns the original run,
even after restart. Reuse with another body returns `409`.

Statuses: `queued → starting → running → completed`. Terminal failures are
`failed`, `timed_out`, `cancelled`, and `interrupted`. HTTP 202 means the request
was recorded, not that Codex finished successfully. Check the eventual status.

## Thread continuity and concurrency

- Existing local CLI threads can be continued in the configured workspace, or in
  their original folders when other workspaces are enabled. Raw event inquiry declares
  `history_scope: "gateway"`; the separate messages endpoint reads saved Codex chat
  history with `history_scope: "codex"`.
- Missing/archived local sessions return `404` or `409`; another workspace returns
  `409` unless cross-workspace continuation is enabled.
  Cloud task IDs and arbitrary numbers are not local Codex thread UUIDs.
- The returned CLI thread UUID must match the requested UUID or execution is stopped.
- One active gateway run per thread; overlapping submissions return `409 thread_busy`.
  Coordinate independent CLI/app usage of the same thread separately.
- Default one CLI worker also serializes different threads in a shared workspace.
  Raising the worker count permits simultaneous filesystem access; use with care.
- A bounded queue returns `429` with `Retry-After` when full.
- A supervisor stops the CLI process group on cancellation, timeout, shutdown, or
  loss of the gateway's lifetime pipe. Restart marks unfinished runs `interrupted`;
  prompts are never automatically replayed because their actions may already have run.
- One gateway process owns a data directory. Do not use Uvicorn `--workers` or
  `--reload`; a process lock prevents accidental duplicate workers.

## Configuration

Settings use environment variables or a local `.env` (see [.env.example](.env.example)).
CLI `--host`, `--port`, `--workspace`, and `--data-dir` override these values.

| Variable | Default | Purpose |
| --- | --- | --- |
| `GATEWAY_HOST` | `127.0.0.1` | Listen address |
| `GATEWAY_PORT` | `8080` | Listen port |
| `GATEWAY_ROOT_PATH` | empty | Public proxy prefix, e.g. `/codex` |
| `GATEWAY_WORKSPACE` | current directory | Default folder for new conversations; normally a Git repo |
| `GATEWAY_ALLOW_OTHER_WORKSPACES` | `false` | Permit saved threads to resume in their original project folders |
| `GATEWAY_DATA_DIR` | `~/.local/state/codex-http-gateway` | SQLite database and instance lock |
| `GATEWAY_CODEX_HOME` | `CODEX_HOME` or `~/.codex` | Saved CLI auth, configuration and sessions |
| `GATEWAY_CODEX_BIN` | `codex` | CLI executable |
| `GATEWAY_SANDBOX` | `danger-full-access` | Full access by default for new and resumed conversations; also accepts `read-only` or `workspace-write` |
| `GATEWAY_MODEL` | CLI configuration | Optional default model override |
| `GATEWAY_REASONING_EFFORT` | CLI configuration | Optional default effort override |
| `GATEWAY_PROXY_URL` | inherited proxy environment | Optional HTTP(S) proxy for CLI network requests |
| `GATEWAY_WORKERS` | `1` | Background CLI workers |
| `GATEWAY_MAX_PENDING_RUNS` | `64` | Active plus queued run limit |
| `GATEWAY_STARTUP_WAIT_SECONDS` | `1` | Maximum POST wait for a thread ID (0–5 seconds) |
| `GATEWAY_RUN_TIMEOUT_SECONDS` | `1800` | Execution timeout after a worker starts |
| `GATEWAY_MAX_OUTPUT_BYTES` | `67108864` | Per-run captured CLI output limit (64 MiB) |
| `GATEWAY_MAX_LINE_BYTES` | `8388608` | Maximum single CLI output line (8 MiB) |
| `GATEWAY_SKIP_GIT_REPO_CHECK` | `false` | Explicitly permit a non-Git workspace |

The HTTP request body limit is 512 KiB and the prompt limit is 100,000 characters.
On networks requiring a desktop system proxy, set `GATEWAY_PROXY_URL` explicitly;
headless CLI processes may not inherit the operating system's proxy settings.
An output limit stops the run with an explicit error; captured output remains available.
History is retained until the operator removes or archives the data directory.

The database is bound to one workspace and one Codex home. Use another data directory
when changing either default. Individual runs can record other folders when enabled.
Upgrades migrate the existing database automatically and preserve prior output.
Preserve the database **and** Codex session directory for continuity.

## Publish through an existing port 8080 provider

Keep the gateway on a loopback-only upstream and proxy `/codex/*` from the existing
HTTP server. The independent gateway and its repository remain separate.

```sh
GATEWAY_PORT=18080 GATEWAY_ROOT_PATH=/codex \
  uv run codex-gateway serve --workspace /absolute/path/to/your/git-project
```

Configure the provider to strip `/codex` and forward to `http://127.0.0.1:18080`,
preserving the client `Content-Type` and `Idempotency-Key` headers.
For a Node HTTP provider, use the [included adapter](integrations/node-http/README.md).
Then use `BASE=http://your-provider:8080/codex`. `/codex/docs` and returned polling
URLs include the public prefix.

This is a single-operator service with no gateway authentication. Anyone who can
reach the API can submit prompts and read its shared thread history. Codex uses the
host's local account and the configured sandbox. Approval requests are disabled for
headless execution. The default permission is **Full access**: the CLI runs with
`sandbox_mode="danger-full-access"` and `approval_policy="never"`. This applies to
both new and resumed conversations. Configure a narrower sandbox when desired.

## Development

```sh
uv sync --frozen --dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
node --test integrations/node-http/proxy.test.mjs tests/frontend.test.mjs
uv build
```

Automated tests use a fake CLI to exercise real subprocesses, HTTP requests,
persistence, continuation, failures and lifecycle behavior without model charges.
The optional real CLI smoke test is documented in `scripts/smoke.py`.

Codex interface: [official noninteractive execution documentation](https://learn.chatgpt.com/docs/non-interactive-mode).
Thread discovery uses the supported [Codex app-server catalog](https://learn.chatgpt.com/docs/app-server).
Design research is recorded in [DESIGN.md](DESIGN.md).

## Run continuously on macOS

After configuring `.env` and running `uv sync --frozen`, install a user LaunchAgent:

```sh
uv run python scripts/install_launchd.py
```

This starts the gateway now and restarts it on failure and subsequent user login.
Logs are written to `GATEWAY_DATA_DIR/service*.log`.

Rerun the installer after changing `.env` so the LaunchAgent's settings are refreshed.

To stop and remove the agent:

```sh
launchctl bootout "gui/$(id -u)/com.marinelj.codex-http-gateway"
rm ~/Library/LaunchAgents/com.marinelj.codex-http-gateway.plist
```
