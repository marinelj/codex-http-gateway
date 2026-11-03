export const ACTIVE = new Set(["queued", "starting", "running"]);
export const STATUS_LABELS = {
  queued: "Queued",
  starting: "Starting",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  interrupted: "Interrupted",
  cancelled: "Cancelled",
  timed_out: "Timed out",
  available: "Available",
  active: "Active in Codex",
  systemError: "Needs attention",
};

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export class GatewayClient {
  constructor(base, transport = fetch) {
    this.base = base.replace(/\/$/, "");
    this.transport = transport.bind(globalThis);
  }

  async request(path, { body, key, signal } = {}) {
    if (!path.startsWith("/") || path.startsWith("//"))
      throw new Error("Invalid API path");
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (key) headers["Idempotency-Key"] = key;
    let response;
    try {
      response = await this.transport(`${this.base}${path}`, {
        method: body === undefined ? "GET" : "POST",
        headers,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        ...(signal ? { signal } : {}),
        cache: "no-store",
        credentials: "omit",
      });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      throw new ApiError(
        "Could not reach Codex Online. Check that the service is running.",
        0,
        "network",
      );
    }
    let data;
    try {
      data = await response.json();
    } catch {
      throw new ApiError(
        "Codex Online returned an unexpected response.",
        response.status,
        "invalid_response",
      );
    }
    if (!response.ok) {
      const message =
        data.error?.message ||
        (typeof data.detail === "string" ? data.detail : null) ||
        data.detail?.[0]?.msg ||
        `Request failed (${response.status})`;
      throw new ApiError(
        message,
        response.status,
        data.error?.code || "request_failed",
      );
    }
    return data;
  }

  submit(prompt, threadId, key, { model, reasoning_effort } = {}) {
    return this.request("/v1/runs", {
      body: {
        prompt,
        thread_id: threadId || null,
        ...(model ? { model } : {}),
        ...(reasoning_effort ? { reasoning_effort } : {}),
      },
      key,
    });
  }
}

export function conversationMessages(messages, run) {
  if (!run) return messages;
  const result = messages.map((message) => ({ ...message }));
  const start = Date.parse(run.created_at) - 1000;
  const end = run.finished_at ? Date.parse(run.finished_at) + 1000 : Infinity;
  const match = result.findLast((message) =>
    message.role === "user" && message.text.trim() === run.prompt.trim() &&
    (!message.created_at || (message.created_at * 1000 >= start && message.created_at * 1000 <= end)),
  );
  const assistant = {
    id: run.run_id + "-assistant",
    turn_id: match?.turn_id || run.run_id,
    role: "assistant",
    text: run.final_output || "",
    created_at: run.finished_at ? Date.parse(run.finished_at) / 1000 : null,
    status: run.status,
    model: run.model,
    reasoning_effort: run.reasoning_effort,
    error: run.error?.message,
  };
  if (match) {
    const index = result.findLastIndex((message) => message.turn_id === match.turn_id);
    const answer = result.findLastIndex((message) => message.turn_id === match.turn_id && message.role === "assistant");
    if (answer >= 0) {
      result[answer] = { ...result[answer], ...assistant, text: run.final_output || result[answer].text };
    } else result.splice(index + 1, 0, assistant);
    match.status = run.status;
    return result;
  }
  const latest = result.findLast((message) => message.role === "user" && message.created_at);
  if (!ACTIVE.has(run.status) && latest && latest.created_at * 1000 > end) return result;
  result.push({
    id: run.run_id + "-user", turn_id: run.run_id, role: "user", text: run.prompt,
    created_at: Date.parse(run.created_at) / 1000, status: run.status,
  }, assistant);
  return result;
}

export function duration(run, clock = Date.now()) {
  if (!run?.started_at) return "—";
  const start = Date.parse(run.started_at);
  const end = run.finished_at ? Date.parse(run.finished_at) : clock;
  const seconds = Math.max(0, Math.floor((end - start) / 1000));
  if (!Number.isFinite(seconds)) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

export function relativeTime(value, clock = Date.now()) {
  const stamp = typeof value === "number" ? value * 1000 : Date.parse(value);
  if (!stamp || !Number.isFinite(stamp)) return "—";
  const seconds = Math.max(0, Math.floor((clock - stamp) / 1000));
  if (seconds < 60) return "Just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return new Date(stamp).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

export function folderName(path) {
  return (path || "").replace(/\/$/, "").split(/[\\/]/).pop() || "Workspace";
}

export function eventLabel(event) {
  const kind = event.data?.type || event.source;
  if (event.source === "stderr") return "CLI diagnostic";
  if (event.source === "stdout") return "CLI output";
  if (
    kind === "item.completed" ||
    kind === "item.started" ||
    kind === "item.updated"
  ) {
    const types = {
      agent_message: "Codex message",
      reasoning: "Progress update",
      command_execution: "Command",
      file_change: "File changes",
      mcp_tool_call: "Tool call",
      web_search: "Web search",
      todo_list: "Plan update",
      error: "Notice",
    };
    return types[event.data.item?.type] || "Activity";
  }
  return (
    {
      "thread.started": "Conversation connected",
      "turn.started": "Codex started working",
      "turn.completed": "Turn completed",
      "turn.failed": "Turn failed",
      error: "Codex notice",
      "gateway.queued": "Prompt accepted",
      "gateway.completed": "Run completed",
      "gateway.cancelled": "Run stopped",
      "gateway.timed_out": "Run timed out",
      "gateway.failed": "Run failed",
      "gateway.interrupted": "Run interrupted",
    }[kind] || kind
  );
}

export function eventPreview(event) {
  const data = event.data || {};
  const item = data.item || {};
  return (
    item.command ||
    item.text ||
    item.message ||
    data.message ||
    data.text ||
    data.error?.message ||
    (data.thread_id ? `Thread ${data.thread_id}` : "")
  );
}
