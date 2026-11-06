# Codex HTTP Gateway

**Your personal Codex subscription, available to your own tools over HTTP.**

## The goal

**Maximize the value of the personal subscription you already pay for by extending
its Codex access into your own applications, scripts, and workflows.**

You already have a subscription and an included usage allowance. This project helps
you put that allowance to work wherever you need it: a personal app, a project
utility, or a workflow you can call with an HTTP request.

The purpose is to get more useful work from your existing AI budget. API-style
access makes your subscription easier to integrate into everyday development;
model controls and usage visibility help you decide how to spend its allowance.
Normal subscription limits still apply.

## How it works

Codex HTTP Gateway wraps your authenticated local Codex CLI in a persistent HTTP
service. Sign in with your eligible ChatGPT account, then send prompts and retrieve
results through the gateway's JSON API using that existing CLI session.

The included **Codex Online** web console gives you a browser interface for the
same conversations, models, and runs.

```text
Your tools or browser → HTTP gateway → your authenticated Codex CLI
                            └──────→ persistent run history and events
```

## Why use it?

- **Put your subscription to work in more places.** Call Codex from your own tools
  through a small JSON API, using your existing CLI sign-in.
- **Make informed use of your budget.** See reported usage limits and reset times,
  and choose a model and reasoning effort for each request.
- **Keep context between requests.** Start a conversation once and continue it with
  its real Codex thread ID.
- **Handle longer tasks.** Submit a prompt, receive a run ID, and poll for progress
  and output. Cancel a run when you need to stop it.
- **Keep a useful record.** SQLite stores gateway prompts, output, and ordered
  events across service restarts.
- **Use a browser when convenient.** The responsive console includes saved local
  threads, chat history, model controls, and a usage notice board.

## Quickstart

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), Git, and an installed
[Codex CLI](https://learn.chatgpt.com/docs/cli). Supports macOS and Linux.

```sh
git clone https://github.com/marinelj/codex-http-gateway.git
cd codex-http-gateway
uv sync --frozen
codex login
uv run codex-gateway serve --workspace /absolute/path/to/your/git-project
```

Use ChatGPT sign-in in `codex login` to use your eligible subscription. Check your
active authentication method with `codex login status`.

Open [the console](http://127.0.0.1:8080/) or
[interactive API documentation](http://127.0.0.1:8080/docs).

**Keep the running service private.** It has no gateway authentication and defaults
to `danger-full-access`, with approval requests disabled. Anyone who can reach it
can read shared conversations and submit tasks with the host's permissions.
The default bind address is loopback. See [security guidance](SECURITY.md) before
connecting another device or exposing a proxy.

## Call it over HTTP

Submit a new conversation:

```sh
curl -sS http://127.0.0.1:8080/v1/runs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: architecture-review-1' \
  -d '{"prompt":"Review this project and summarize its architecture","thread_id":null}'
```

The gateway returns HTTP `202 Accepted` with a `run_id`, `status_url`, and
`thread_id` when available. Poll the returned `status_url` for status, events,
and `final_output`:

```sh
curl -sS http://127.0.0.1:8080/v1/runs/<run-id>
```

If the thread is still starting, its ID can initially be `null`. Read the real
thread ID from the run response, then send a follow-up:

```sh
curl -sS http://127.0.0.1:8080/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Which part should I improve first?","thread_id":"<thread-uuid>"}'
```

Optional `model` and `reasoning_effort` fields control each message. Discover
options at `/v1/models` and remaining reported allowance at `/v1/usage`.

This is the gateway's own asynchronous API. Integrate through these endpoints;
it does not implement the OpenAI Responses or Chat Completions API schemas.

## How subscription usage works

The gateway reuses your local Codex CLI authentication. ChatGPT sign-in uses your
eligible plan's Codex access; API-key sign-in uses API billing. No OpenAI API key
is required for the ChatGPT sign-in path.

Requests still consume normal account allowance. Model availability, usage limits,
and reset windows depend on your account. The gateway extends how you access Codex;
it does not increase your quota or guarantee savings. The usage board displays
reported allowance, rather than a dollar balance or an exact number of prompts.

See the official [authentication documentation](https://learn.chatgpt.com/docs/auth)
and [usage and pricing documentation](https://learn.chatgpt.com/docs/pricing).

## Configure your workspace

Copy [.env.example](.env.example) to a local `.env` when you need custom settings.

| Setting | Default | Purpose |
| --- | --- | --- |
| `GATEWAY_HOST` | `127.0.0.1` | Bind address; keep access private |
| `GATEWAY_PORT` | `8080` | HTTP port |
| `GATEWAY_WORKSPACE` | Current directory | Project for new conversations |
| `GATEWAY_SANDBOX` | `danger-full-access` | Also accepts `read-only` or `workspace-write` |
| `GATEWAY_ALLOW_OTHER_WORKSPACES` | `false` | Allow sending to threads in other project folders |
| `GATEWAY_MODEL` | CLI configuration | Default model |
| `GATEWAY_REASONING_EFFORT` | CLI configuration | Default reasoning effort |
| `GATEWAY_WORKERS` | `1` | Concurrent CLI workers |

Other-workspace sending is opt-in, but the catalog and saved history can include
conversations from other projects in the same Codex home. Treat all responses and
transcript downloads as private.

## Documentation and development

- [API and operations reference](docs/usage.md): endpoints, pagination, concurrency,
  full configuration, proxy setup, and macOS background service installation.
- [Security guidance](SECURITY.md): credentials, runtime data, and public releases.
- [Node HTTP adapter](integrations/node-http/README.md): mount under `/codex` on an
  existing server with access restricted to your own clients.
- [Design notes](DESIGN.md): console interaction and visual design.

```sh
uv sync --frozen --dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
node --test integrations/node-http/proxy.test.mjs tests/frontend.test.mjs
uv build
```

Tests use a fake CLI and do not consume model usage. The optional live smoke test
in `scripts/smoke.py` consumes normal account allowance.

Released under the [MIT License](LICENSE).
