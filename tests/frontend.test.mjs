import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ApiError,
  GatewayClient,
  conversationMessages,
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

test("model and effort choices accompany the selected thread", async () => {
  const client = new GatewayClient("/codex", async (url, options) => {
    assert.deepEqual(JSON.parse(options.body), {
      prompt: "Continue our plan", thread_id: "selected-thread", model: "chosen-model", reasoning_effort: "high",
    });
    return { ok: true, json: async () => ({}) };
  });
  await client.submit("Continue our plan", "selected-thread", "key", { model: "chosen-model", reasoning_effort: "high" });
});

test("chat reconciles a live reply with saved history without duplicating messages", () => {
  const stamp = Date.parse("2026-01-01T00:00:00Z") / 1000;
  const history = [
    { id: "u", turn_id: "turn", role: "user", text: "Hello", created_at: stamp },
    { id: "a", turn_id: "turn", role: "assistant", text: "Earlier text", created_at: stamp + 2 },
  ];
  const run = { run_id: "run", prompt: "Hello", status: "completed", created_at: "2026-01-01T00:00:00Z", finished_at: "2026-01-01T00:00:05Z", final_output: "Hello back", model: "chosen-model", reasoning_effort: "high" };
  const result = conversationMessages(history, run);
  assert.equal(result.length, 2);
  assert.equal(result[1].text, "Hello back");
  assert.equal(result[1].model, "chosen-model");
  assert.equal(history[1].text, "Earlier text");
  const repeated = conversationMessages(history, { ...run, created_at: "2026-01-01T00:01:00Z", finished_at: null, status: "running", final_output: null });
  assert.equal(repeated.length, 4);
  assert.equal(repeated[3].status, "running");
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
