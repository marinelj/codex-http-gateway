import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ApiError,
  GatewayClient,
  duration,
  eventLabel,
  eventPreview,
  folderName,
  relativeTime,
} from "../src/codex_gateway/static/client.mjs";

test("the selected thread UUID and prompt are sent without modification", async () => {
  let request;
  const client = new GatewayClient(
    "/codex",
    async (url, options) => {
      request = { url, options };
      return { ok: true, json: async () => ({ run_id: "run_test" }) };
    },
  );
  await client.submit("hello\n<not HTML>", "selected-thread-uuid", "retry-key");
  assert.equal(request.url, "/codex/v1/runs");
  assert.deepEqual(JSON.parse(request.options.body), {
    prompt: "hello\n<not HTML>",
    thread_id: "selected-thread-uuid",
  });
  assert.equal(request.options.headers.Authorization, undefined);
  assert.equal(request.options.headers["Idempotency-Key"], "retry-key");
  assert.equal(request.options.credentials, "omit");
});

test("new conversations send a null ID without a credential", async () => {
  const client = new GatewayClient(
    "/codex/",
    async (url, options) => {
      assert.equal(url, "/codex/v1/runs");
      assert.equal(JSON.parse(options.body).thread_id, null);
      assert.equal(options.headers.Authorization, undefined);
      return { ok: true, json: async () => ({}) };
    },
  );
  await client.submit("hello");
});

test("API errors retain status and user-readable messages", async () => {
  const client = new GatewayClient(
    "",
    async () => ({
      ok: false,
      status: 409,
      json: async () => ({
        error: { code: "thread_busy", message: "Thread is busy" },
      }),
    }),
  );
  await assert.rejects(
    client.submit("hello", "one"),
    (error) =>
      error instanceof ApiError &&
      error.status === 409 &&
      error.code === "thread_busy",
  );
  await assert.rejects(
    client.request("//external.example/path"),
    /Invalid API path/,
  );
});

test("browser fetch receives the correct global receiver", async () => {
  const client = new GatewayClient(
    "",
    async function () {
      assert.equal(this, globalThis);
      return { ok: true, json: async () => ({}) };
    },
  );
  await client.request("/healthz");
});

test("display helpers handle queued runs, time ranges and tool output", () => {
  assert.equal(duration({ started_at: null }), "—");
  assert.equal(
    duration({
      started_at: "2026-01-01T00:00:00Z",
      finished_at: "2026-01-01T00:01:32Z",
    }),
    "1m 32s",
  );
  assert.equal(
    relativeTime("2026-01-01T00:00:00Z", Date.parse("2026-01-01T00:05:00Z")),
    "5m ago",
  );
  assert.equal(folderName("/some/path/project"), "project");
  const event = {
    source: "codex",
    data: {
      type: "item.completed",
      item: { type: "command_execution", command: "git status" },
    },
  };
  assert.equal(eventLabel(event), "Command");
  assert.equal(eventPreview(event), "git status");
});
