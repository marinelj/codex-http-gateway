import {
  ACTIVE,
  STATUS_LABELS,
  GatewayClient,
  duration,
  eventLabel,
  eventPreview,
  folderName,
  relativeTime,
} from "./client.mjs";

const $ = (id) => document.getElementById(id);
const base = document.body.dataset.base || "";
const state = {
  online: null,
  catalogLoaded: false,
  generation: 0,
  view: "workspace",
  overview: null,
  threads: [],
  runs: [],
  threadsVisible: 70,
  selectedThread: null,
  run: null,
  events: [],
  cursor: 0,
  output: "answer",
  requestVersion: 0,
  pollTimer: null,
  refreshing: false,
  historyAfter: null,
  historyHasMore: false,
  historyPages: 0,
  sending: false,
  retrySubmission: null,
  autoTargetRun: null,
  threadData: null,
  threadLoading: null,
};
const client = new GatewayClient(base);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function icon(name) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  node.setAttribute("class", "icon");
  node.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  node.append(use);
  return node;
}
function badge(status) {
  return el(
    "span",
    `status-badge ${Object.hasOwn(STATUS_LABELS, status) ? status : ""}`,
    STATUS_LABELS[status] || status,
  );
}
let toastTimer;
function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    $("toast").hidden = true;
  }, 3500);
}
function report(error) {
  if (error.name === "AbortError") return;
  $("page-error-text").textContent =
    error.message || "Something went wrong. Please try again.";
  $("page-error").hidden = false;
}
function closeSidebar() {
  $("sidebar").classList.remove("open");
  $("sidebar-backdrop").hidden = true;
}
function navigate(view, changeHash = true) {
  if (!["workspace", "runs", "connection"].includes(view)) view = "workspace";
  const changed = state.view !== view;
  state.view = view;
  for (const name of ["workspace", "runs", "connection"])
    $("view-" + name).hidden = name !== view;
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $("breadcrumb-title").textContent = {
    workspace: "Overview",
    runs: "Run history",
    connection: "Connection",
  }[view];
  if (changeHash && location.hash !== "#" + view)
    history.replaceState(null, "", "#" + view);
  closeSidebar();
  if (changed) window.scrollTo({ top: 0, behavior: "instant" });
}
function renderConnection() {
  $("connection-label").textContent =
    state.online === null
      ? "Checking gateway"
      : state.online
        ? "Gateway online"
        : "Gateway offline";
  $("connection-description").textContent = "No sign-in required";
  renderSettings();
}
function renderOverview() {
  const overview = state.overview;
  $("metric-total").textContent = overview?.total_runs ?? "—";
  $("metric-active").textContent = overview
    ? [...ACTIVE].reduce(
        (sum, status) => sum + (overview.statuses[status] || 0),
        0,
      )
    : "—";
  $("metric-completed").textContent =
    overview?.statuses.completed ?? (overview ? 0 : "—");
  $("runs-count").textContent = overview?.total_runs ?? "—";
  $("workspace-label").textContent = overview
    ? folderName(overview.workspace)
    : "Personal gateway";
  $("workspace-label").title = overview?.workspace || "";
  $("workspace-policy").textContent = overview
    ? `${overview.sandbox === "read-only" ? "Read only" : "Workspace write"} · ${folderName(overview.workspace)}`
    : "Connected to your local Codex CLI";
  renderSettings();
}
function renderSettings() {
  const info = state.overview;
  const rows = [
    [
      "Status",
      state.online === null ? "Checking…" : state.online ? "Online" : "Offline",
    ],
    ["API address", location.origin + base],
    ["New conversations", info?.workspace || "Loading…"],
    [
      "Access policy",
      info
        ? info.sandbox === "read-only"
          ? "Read only"
          : "Workspace write"
        : "—",
    ],
    [
      "Other project folders",
      info
        ? info.allow_other_workspaces
          ? "Enabled for saved threads"
          : "Current workspace only"
        : "—",
    ],
    ["Model", info ? info.model || "Codex CLI configuration" : "—"],
    ["Concurrent runs", info ? String(info.workers) : "—"],
    ["Run capacity", info ? String(info.max_pending_runs) : "—"],
    ["Version", info?.version || "—"],
  ];
  $("connection-settings").replaceChildren(
    ...rows.map(([key, value]) => {
      const row = el("div");
      row.append(el("dt", "", key), el("dd", "", value));
      return row;
    }),
  );
}
function renderThreads() {
  const query = $("thread-search").value.trim().toLowerCase();
  const matches = state.threads.filter((thread) =>
    [thread.title, thread.thread_id, thread.workspace].some((text) =>
      text.toLowerCase().includes(query),
    ),
  );
  $("thread-count").textContent = String(state.threads.length);
  const nodes = matches.slice(0, state.threadsVisible).map((thread) => {
    const row = el(
      "button",
      "thread-row" +
        (thread.thread_id === state.selectedThread?.thread_id
          ? " selected"
          : ""),
    );
    row.type = "button";
    row.title = `${thread.title}\n${thread.workspace}\n${thread.thread_id}`;
    row.setAttribute("aria-label", `Select thread: ${thread.title}`);
    row.setAttribute(
      "aria-pressed",
      String(thread.thread_id === state.selectedThread?.thread_id),
    );
    const content = el("span", "thread-row-content");
    content.append(
      el("strong", "", thread.title),
      el(
        "small",
        "",
        `${folderName(thread.workspace)} · ${relativeTime(thread.updated_at)}`,
      ),
    );
    row.append(icon("message"), content);
    if (ACTIVE.has(thread.status) || thread.status === "active")
      row.append(el("span", "status-dot pulse"));
    row.addEventListener("click", () => selectThread(thread));
    return row;
  });
  if (!nodes.length)
    nodes.push(
      el(
        "p",
        "sidebar-empty",
        !state.catalogLoaded
          ? "Loading conversations…"
          : query
            ? "No threads match your search."
            : "No saved conversations yet. Start one with a prompt.",
      ),
    );
  $("thread-list").replaceChildren(...nodes);
  $("show-more-threads").hidden = matches.length <= state.threadsVisible;
}
function applyTarget() {
  const thread = state.selectedThread;
  $("target-title").textContent = thread?.title || "New conversation";
  $("target-subtitle").textContent = thread
    ? `${folderName(thread.workspace)} · ${thread.thread_id}`
    : "A fresh start with Codex";
  $("target-subtitle").title = thread
    ? `${thread.workspace}\n${thread.thread_id}`
    : "";
  $("target-title").title = thread?.title || "";
  $("clear-target").hidden = !thread;
  if (document.activeElement !== $("manual-thread"))
    $("manual-thread").value = thread?.thread_id || "";
  document
    .querySelector(".target-icon")
    .replaceChildren(icon(thread ? "message" : "plus"));
  $("composer-description").textContent = thread
    ? "Your next prompt stays in this conversation."
    : "Start fresh, or pick a thread from the sidebar.";
  const busy = thread && ACTIVE.has(thread.status);
  $("submit-prompt").disabled =
    state.sending || Boolean(thread && (!thread.can_send || busy));
  $("submit-prompt").querySelector("span").textContent = state.sending
    ? "Sending…"
    : thread
      ? "Send to this thread"
      : "Send prompt";
  $("submission-note").textContent = busy
    ? "This thread has a run in progress. Wait for it to finish or stop the run."
    : thread && !thread.can_send
      ? "This thread is not available for a new prompt from this gateway."
      : "Your response runs in the background. You can keep an eye on it here.";
  renderThreads();
}
async function selectThread(thread) {
  state.selectedThread = thread;
  applyTarget();
  navigate("workspace");
  if (thread.latest_run) await openRun(thread.latest_run.run_id, false);
  else
    resetOutput(
      "Continue this conversation",
      thread.preview || "Send your next prompt to this thread.",
    );
  $("prompt").focus();
  renderOutputTab();
}
function resetOutput(title, description) {
  state.requestVersion++;
  state.autoTargetRun = null;
  state.threadData = null;
  state.threadLoading = null;
  clearTimeout(state.pollTimer);
  state.pollTimer = null;
  state.run = null;
  state.events = [];
  state.cursor = 0;
  $("response-empty").querySelector("h3").textContent =
    title || "A little prompt. A world of possibility.";
  $("response-empty").querySelector("p").textContent =
    description ||
    "Send something to Codex and watch the response come together.";
  $("response-empty").hidden = false;
  for (const id of [
    "output-answer",
    "output-activity",
    "output-thread",
    "output-json",
    "run-footer",
    "run-details",
    "run-status",
    "cancel-run",
  ])
    $(id).hidden = true;
  $("copy-output").disabled = true;
  $("export-output").disabled = true;
  $("activity-count").textContent = "0";
}
function newConversation() {
  state.selectedThread = null;
  state.retrySubmission = null;
  $("prompt").value = "";
  updatePromptCount();
  applyTarget();
  resetOutput();
  navigate("workspace");
  $("prompt").focus();
}
function renderRows(container, runs, historyView = false) {
  const nodes = runs.map((run) => {
    const row = el("tr");
    const titleCell = el("td");
    const title = el(
      "button",
      "run-title",
      run.prompt_preview || "Untitled prompt",
    );
    title.title = run.prompt_preview;
    title.addEventListener("click", () => openRun(run.run_id));
    titleCell.append(
      title,
      el(
        "span",
        "run-subtitle",
        historyView ? run.run_id : folderName(run.workspace),
      ),
    );
    const statusCell = el("td");
    statusCell.append(badge(run.status));
    const date = el("td", "", relativeTime(run.created_at));
    date.title = new Date(run.created_at).toLocaleString();
    const actionCell = el("td");
    const action = el("button", "icon-button");
    action.append(icon("arrow"));
    action.title = "Open run";
    action.setAttribute(
      "aria-label",
      `Open run: ${run.prompt_preview || run.run_id}`,
    );
    action.addEventListener("click", () => openRun(run.run_id));
    actionCell.append(action);
    row.append(titleCell, statusCell, date);
    if (historyView) row.append(el("td", "", duration(run)));
    row.append(actionCell);
    return row;
  });
  $(container).replaceChildren(...nodes);
}
function renderRuns() {
  renderRows("recent-runs", state.runs.slice(0, 4));
  $("recent-empty").hidden = state.runs.length > 0;
  const query = $("run-search").value.trim().toLowerCase();
  const status = $("status-filter").value;
  const filtered = state.runs.filter(
    (run) =>
      (!query ||
        [run.prompt_preview, run.run_id, run.thread_id || ""].some((text) =>
          text.toLowerCase().includes(query),
        )) &&
      (status === "all" ||
        (status === "active" ? ACTIVE.has(run.status) : run.status === status)),
  );
  renderRows("history-runs", filtered, true);
  $("history-empty").hidden = filtered.length > 0;
  $("history-count").textContent =
    `${filtered.length} ${filtered.length === 1 ? "run" : "runs"} shown${state.historyHasMore ? " · more available" : ""}`;
  $("load-more-runs").hidden = !state.historyHasMore;
}
async function refreshOverview() {
  const generation = state.generation;
  const overview = await client.request("/v1/overview");
  if (generation !== state.generation) return;
  state.overview = overview;
  renderOverview();
}
async function loadRuns(more = false) {
  const generation = state.generation;
  const params = new URLSearchParams({ order: "desc", limit: "50" });
  if (more && state.historyAfter) params.set("after", state.historyAfter);
  const page = await client.request("/v1/runs?" + params);
  if (generation !== state.generation) return;
  const items = [...state.runs, ...page.runs];
  state.runs = [
    ...new Map(items.map((run) => [run.run_id, run])).values(),
  ].sort((a, b) => b.created_at.localeCompare(a.created_at));
  if (more || state.historyPages <= 1) {
    state.historyAfter = page.next_after;
    state.historyHasMore = page.has_more;
  }
  state.historyPages = more
    ? state.historyPages + 1
    : Math.max(state.historyPages, 1);
  renderRuns();
}
async function loadThreads() {
  const generation = state.generation;
  const threads = new Map();
  const seen = new Set();
  let cursor;
  $("refresh-threads").classList.add("spinning");
  try {
    do {
      const params = new URLSearchParams({ limit: "100" });
      if (cursor) params.set("cursor", cursor);
      const page = await client.request("/v1/threads?" + params);
      if (generation !== state.generation) return;
      for (const thread of page.threads) threads.set(thread.thread_id, thread);
      cursor = page.next_cursor;
      if (cursor && seen.has(cursor))
        throw new Error("Thread pagination did not advance. Please refresh.");
      if (cursor) seen.add(cursor);
    } while (cursor);
    state.threads = [...threads.values()];
    state.catalogLoaded = true;
    if (state.selectedThread) {
      const fresh = threads.get(state.selectedThread.thread_id);
      if (fresh) state.selectedThread = fresh;
    }
    applyTarget();
  } finally {
    $("refresh-threads").classList.remove("spinning");
  }
}
async function refreshWorkspace() {
  if (state.refreshing) return;
  state.refreshing = true;
  $("refresh-workspace").classList.add("spinning");
  try {
    const results = await Promise.allSettled([
      refreshOverview(),
      loadRuns(),
      loadThreads(),
    ]);
    for (const result of results)
      if (result.status === "rejected") report(result.reason);
    if (state.output === "thread") await loadThreadActivity(true);
  } finally {
    state.refreshing = false;
    $("refresh-workspace").classList.remove("spinning");
  }
}
function renderAnswer() {
  const run = state.run;
  if (!run) return;
  const nodes = [];
  const prompt = el("div", "answer-prompt");
  prompt.append(
    el("strong", "", "YOU ASKED"),
    document.createTextNode(run.prompt),
  );
  nodes.push(prompt);
  if (run.error) nodes.push(el("div", "output-error", run.error.message));
  if (run.final_output !== null && run.final_output !== undefined) {
    const body = el("div", "answer-body");
    // Render code fences and paragraphs as text nodes. Model HTML is never executed.
    const parts = run.final_output.split(/```[^\n]*\n([\s\S]*?)```/g);
    parts.forEach((part, index) => {
      if (!part) return;
      if (index % 2) body.append(el("pre", "", part));
      else
        for (const paragraph of part.split(/\n\n+/))
          if (paragraph) body.append(el("p", "", paragraph));
    });
    if (!run.final_output)
      body.append(
        el(
          "p",
          "",
          "Codex returned an empty message. Check Activity for details.",
        ),
      );
    nodes.push(body);
  } else {
    const waiting = el("div", "waiting-message");
    waiting.append(
      el("span", "status-dot" + (ACTIVE.has(run.status) ? " pulse" : "")),
      document.createTextNode(
        ACTIVE.has(run.status)
          ? "Codex is working. New output appears here automatically."
          : "No answer was recorded. Check Activity for details.",
      ),
    );
    nodes.push(waiting);
  }
  $("output-answer").replaceChildren(...nodes);
}
function renderActivity(
  allEvents = state.events,
  destination = "output-activity",
) {
  // Keep large transcripts navigable. Export includes every captured event.
  const events = allEvents.slice(-500);
  const nodes = events.map((event) => {
    const row = el("div", "activity-item");
    const heading = el("div");
    heading.append(
      el("span", "", eventLabel(event)),
      el(
        "time",
        "",
        new Date(event.created_at).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        }),
      ),
    );
    const detail = el("details");
    const preview = String(eventPreview(event));
    detail.append(
      el("summary", "", preview.slice(0, 260) || "View event details"),
      el("pre", "", JSON.stringify(event.data, null, 2)),
    );
    row.append(heading, detail);
    return row;
  });
  if (allEvents.length > events.length)
    nodes.unshift(
      el(
        "p",
        "settings-note",
        `Showing the latest ${events.length} events. Download the full transcript for all ${allEvents.length}.`,
      ),
    );
  $(destination).replaceChildren(...nodes);
}

async function loadThreadActivity(force = false) {
  const id = state.run?.thread_id || state.selectedThread?.thread_id;
  const version = state.requestVersion;
  if (!id || state.threadLoading === version) return;
  const previous = state.threadData;
  if (
    !force &&
    previous?.thread_id === id &&
    previous.next_cursor >= state.cursor
  )
    return;
  state.threadLoading = version;
  try {
    const events = previous?.thread_id === id ? [...previous.events] : [];
    let cursor = previous?.thread_id === id ? previous.next_cursor : 0;
    let page;
    do {
      page = await client.request(
        `/v1/threads/${encodeURIComponent(id)}?after=${cursor}&limit=500`,
      );
      if (version !== state.requestVersion) return;
      events.push(...page.events);
      cursor = page.next_cursor;
    } while (page.has_more);
    state.threadData = { ...page, events };
    if (state.output === "thread") renderThreadActivity();
  } catch (error) {
    if (version !== state.requestVersion) return;
    if (error.status === 404) {
      state.threadData = { thread_id: id, events: [], next_cursor: 0 };
      if (state.output === "thread") renderThreadActivity();
    } else report(error);
  } finally {
    if (state.threadLoading === version) state.threadLoading = null;
  }
}

function renderThreadActivity() {
  const thread = state.threadData;
  if (!thread) {
    $("output-thread").replaceChildren(
      el("p", "waiting-message", "Loading conversation activity…"),
    );
    return;
  }
  renderActivity(thread.events, "output-thread");
  const intro = el(
    "p",
    "thread-activity-intro",
    thread.events.length
      ? `All ${thread.events.length} recorded events across this conversation. Latest status: ${STATUS_LABELS[thread.status] || thread.status}.`
      : "No gateway activity is recorded yet. Send a prompt to this thread to begin.",
  );
  $("output-thread").prepend(intro);
  $("copy-output").disabled = !thread.events.length;
  $("export-output").disabled = !thread.events.length;
}

function renderOutputTab() {
  document.querySelectorAll("[data-output]").forEach((button) => {
    const active = button.dataset.output === state.output;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  for (const name of ["answer", "activity", "thread", "json"])
    $("output-" + name).hidden =
      state.output !== name ||
      (name === "thread"
        ? !(state.run?.thread_id || state.selectedThread?.thread_id)
        : !state.run);
  if (
    state.output === "thread" &&
    (state.run?.thread_id || state.selectedThread?.thread_id)
  ) {
    $("response-empty").hidden = true;
    renderThreadActivity();
    loadThreadActivity();
    return;
  }
  $("response-empty").hidden = Boolean(state.run);
  if (!state.run) return;
  $("copy-output").disabled = !state.run.final_output && !state.events.length;
  $("export-output").disabled = false;
  if (state.output === "answer") renderAnswer();
  if (state.output === "activity") renderActivity();
  if (state.output === "json")
    $("output-json").textContent = JSON.stringify(
      { ...state.run, events: state.events },
      null,
      2,
    );
}
function renderRun() {
  const run = state.run;
  if (!run) return;
  $("response-empty").hidden = true;
  const status = badge(run.status);
  status.id = "run-status";
  $("run-status").replaceWith(status);
  $("activity-count").textContent = String(state.events.length);
  $("cancel-run").hidden = !ACTIVE.has(run.status);
  $("copy-output").disabled = !run.final_output && !state.events.length;
  $("export-output").disabled = false;
  $("run-footer").hidden = false;
  $("run-details").hidden = false;
  $("run-footer-text").textContent =
    `${STATUS_LABELS[run.status] || run.status} · ${duration(run)}`;
  $("continue-thread").hidden = !run.thread_id || ACTIVE.has(run.status);
  const metadata = [
    ["Run ID", run.run_id],
    ["Thread ID", run.thread_id || "Assigned when Codex starts"],
    ["Workspace", run.workspace],
    [
      "Started",
      run.started_at
        ? new Date(run.started_at).toLocaleString()
        : "Waiting for a worker",
    ],
    ["Duration", duration(run)],
    [
      "Tokens",
      run.usage
        ? `${(run.usage.input_tokens || 0).toLocaleString()} in · ${(run.usage.output_tokens || 0).toLocaleString()} out`
        : "—",
    ],
    ["Exit code", run.exit_code === null ? "—" : String(run.exit_code)],
  ];
  $("run-metadata").replaceChildren(
    ...metadata.flatMap(([key, value]) => {
      const term = el("dt", "", key);
      const def = el("dd");
      def.append(el("code", "", value));
      if (key.endsWith("ID") && value && !value.startsWith("Assigned")) {
        const copy = el("button", "copy-inline");
        copy.title = "Copy " + key;
        copy.setAttribute("aria-label", "Copy " + key);
        copy.append(icon("copy"));
        copy.addEventListener("click", () => copyText(value));
        def.append(copy);
      }
      return [term, def];
    }),
  );
  renderOutputTab();
}
function mergeRun(page) {
  const known = new Set(state.events.map((event) => event.cursor));
  for (const event of page.events)
    if (!known.has(event.cursor)) state.events.push(event);
  state.cursor = page.next_cursor;
  state.run = { ...page, events: [] };
  if (state.autoTargetRun === page.run_id && page.thread_id) {
    state.selectedThread = state.threads.find(
      (thread) => thread.thread_id === page.thread_id,
    ) || {
      thread_id: page.thread_id,
      title: page.prompt_preview || "Conversation",
      workspace: page.workspace,
      can_send: true,
    };
  }
  state.runs = [
    page,
    ...state.runs.filter((run) => run.run_id !== page.run_id),
  ].sort((a, b) => b.created_at.localeCompare(a.created_at));
  if (state.selectedThread?.thread_id === page.thread_id) {
    state.selectedThread = {
      ...state.selectedThread,
      status: ACTIVE.has(page.status) ? page.status : "available",
      latest_run: page,
    };
  }
  renderRun();
  renderRuns();
  applyTarget();
}
async function pollRun(runId, version) {
  clearTimeout(state.pollTimer);
  state.pollTimer = null;
  if (version !== state.requestVersion) return;
  try {
    let page;
    do {
      page = await client.request(
        `/v1/runs/${encodeURIComponent(runId)}?after=${state.cursor}&limit=200`,
      );
      if (version !== state.requestVersion) return;
      mergeRun(page);
    } while (page.has_more);
    if (ACTIVE.has(page.status))
      state.pollTimer = setTimeout(() => pollRun(runId, version), 1200);
    else {
      const results = await Promise.allSettled([
        refreshOverview(),
        loadThreads(),
      ]);
      for (const result of results)
        if (result.status === "rejected") report(result.reason);
    }
  } catch (error) {
    if (version !== state.requestVersion) return;
    report(error);
    if (error.status !== 404)
      state.pollTimer = setTimeout(() => pollRun(runId, version), 4000);
  }
}
async function openRun(runId, setTarget = true) {
  resetOutput(
    "Opening this run…",
    "Fetching the latest response and activity.",
  );
  const version = state.requestVersion;
  state.autoTargetRun = setTarget ? runId : null;
  navigate("workspace");
  try {
    const page = await client.request(
      `/v1/runs/${encodeURIComponent(runId)}?limit=200`,
    );
    if (version !== state.requestVersion) return;
    if (setTarget && page.thread_id) {
      state.selectedThread = state.threads.find(
        (thread) => thread.thread_id === page.thread_id,
      ) || {
        thread_id: page.thread_id,
        title: page.prompt_preview || "Conversation",
        workspace: page.workspace,
        status: ACTIVE.has(page.status) ? page.status : "available",
        can_send: true,
        latest_run: page,
      };
    }
    mergeRun(page);
    if (page.has_more || ACTIVE.has(page.status)) await pollRun(runId, version);
  } catch (error) {
    if (version === state.requestVersion) report(error);
  }
}
async function sendPrompt(event) {
  event.preventDefault();
  if (state.sending) return;
  const prompt = $("prompt").value;
  if (!prompt.trim()) {
    $("prompt").focus();
    return;
  }
  const thread = state.selectedThread;
  if (thread && (!thread.can_send || ACTIVE.has(thread.status))) return;
  const bodyKey = JSON.stringify([prompt, thread?.thread_id || null]);
  if (state.retrySubmission?.bodyKey !== bodyKey)
    state.retrySubmission = {
      bodyKey,
      key: Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) =>
        value.toString(16).padStart(2, "0"),
      ).join(""),
    };
  state.sending = true;
  applyTarget();
  $("page-error").hidden = true;
  const generation = state.generation;
  const viewVersion = state.requestVersion;
  try {
    const run = await client.submit(
      prompt,
      thread?.thread_id,
      state.retrySubmission.key,
    );
    if (generation !== state.generation) return;
    state.retrySubmission = null;
    toast("Prompt sent. Codex is on it.");
    if (viewVersion === state.requestVersion) await openRun(run.run_id);
    else await Promise.all([loadRuns(), loadThreads()]);
    await refreshOverview();
  } catch (error) {
    if (generation === state.generation) report(error);
  } finally {
    state.sending = false;
    applyTarget();
  }
}
function updatePromptCount() {
  $("prompt-count").textContent =
    `${$("prompt").value.length.toLocaleString()} / 100,000`;
}
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard");
  } catch {
    toast("Clipboard access is unavailable. Select the text to copy it.");
  }
}
async function exportOutput() {
  const threadId = state.run?.thread_id || state.selectedThread?.thread_id;
  const isThread = state.output === "thread" && threadId;
  if (!state.run && !isThread) return;
  const recordId = isThread ? threadId : state.run.run_id;
  const path = isThread ? "/v1/threads/" : "/v1/runs/";
  const generation = state.generation;
  $("export-output").disabled = true;
  try {
    const events = [];
    let cursor = 0;
    let page;
    do {
      page = await client.request(
        `${path}${encodeURIComponent(recordId)}?after=${cursor}&limit=1000`,
      );
      if (generation !== state.generation) return;
      events.push(...page.events);
      cursor = page.next_cursor;
    } while (page.has_more);
    const url = URL.createObjectURL(
      new Blob([JSON.stringify({ ...page, events }, null, 2)], {
        type: "application/json",
      }),
    );
    const link = el("a");
    link.href = url;
    link.download = `${isThread ? "thread-" : ""}${recordId}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("Transcript downloaded");
  } catch (error) {
    report(error);
  } finally {
    $("export-output").disabled =
      !state.run && !state.threadData?.events.length;
  }
}
async function checkHealth() {
  try {
    const response = await fetch(base + "/healthz", {
      cache: "no-store",
      credentials: "omit",
    });
    if (!response.ok) throw new Error();
    const status = await response.json();
    if (status.status !== "ok") throw new Error();
    state.online = true;
    $("health-status").className = "health-status online";
    $("health-status").replaceChildren(
      el("span", "status-dot"),
      document.createTextNode("Gateway online"),
    );
  } catch {
    state.online = false;
    $("health-status").className = "health-status offline";
    $("health-status").replaceChildren(
      el("span", "status-dot"),
      document.createTextNode("Gateway offline"),
    );
  }
  renderConnection();
}

for (const id of ["top-connect", "sidebar-connect"])
  $(id).addEventListener("click", () => navigate("connection"));
$("new-conversation").addEventListener("click", newConversation);
$("clear-target").addEventListener("click", () => {
  state.selectedThread = null;
  applyTarget();
  $("prompt").focus();
});
document
  .querySelectorAll("[data-view]")
  .forEach((button) =>
    button.addEventListener("click", () => navigate(button.dataset.view)),
  );
$("view-all-runs").addEventListener("click", () => navigate("runs"));
$("refresh-workspace").addEventListener("click", refreshWorkspace);
$("refresh-history").addEventListener("click", () => refreshWorkspace());
$("refresh-threads").addEventListener("click", () => loadThreads().catch(report));
$("check-connection").addEventListener("click", async () => {
  await checkHealth();
  await refreshWorkspace();
});
$("dismiss-error").addEventListener("click", () => {
  $("page-error").hidden = true;
});
$("thread-search").addEventListener("input", () => {
  state.threadsVisible = 70;
  renderThreads();
});
$("show-more-threads").addEventListener("click", () => {
  state.threadsVisible += 70;
  renderThreads();
});
$("run-search").addEventListener("input", renderRuns);
$("status-filter").addEventListener("change", renderRuns);
$("load-more-runs").addEventListener("click", async () => {
  $("load-more-runs").disabled = true;
  try {
    await loadRuns(true);
  } catch (error) {
    report(error);
  } finally {
    $("load-more-runs").disabled = false;
  }
});
$("prompt-form").addEventListener("submit", sendPrompt);
$("prompt").addEventListener("input", updatePromptCount);
$("prompt").addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    $("prompt-form").requestSubmit();
  }
});
$("use-thread-id").addEventListener("click", async () => {
  const id = $("manual-thread").value.trim();
  if (!id) return;
  $("use-thread-id").disabled = true;
  try {
    await selectThread(
      await client.request(`/v1/threads/${encodeURIComponent(id)}/info`),
    );
  } catch (error) {
    report(error);
  } finally {
    $("use-thread-id").disabled = false;
  }
});
const examples = {
  hello:
    "Say hello in one friendly sentence. Do not use tools or change any files.",
  review:
    "Review this project and give me a concise overview of its architecture and the most useful improvement to make next.",
  plan: "Help me plan a new feature for this project. First, ask me what I want to build.",
};
document.querySelectorAll("[data-example]").forEach((button) =>
  button.addEventListener("click", () => {
    $("prompt").value = examples[button.dataset.example];
    updatePromptCount();
    $("prompt").focus();
  }),
);
document.querySelectorAll("[data-output]").forEach((button) => {
  button.addEventListener("click", () => {
    state.output = button.dataset.output;
    renderOutputTab();
  });
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const names = ["answer", "activity", "thread", "json"];
    state.output =
      names[
        (names.indexOf(state.output) +
          (event.key === "ArrowRight" ? 1 : names.length - 1)) %
          names.length
      ];
    renderOutputTab();
    $("tab-" + state.output).focus();
  });
});
$("copy-output").addEventListener("click", () =>
  copyText(
    state.output === "answer"
      ? state.run?.final_output || ""
      : JSON.stringify(
          state.output === "thread"
            ? state.threadData
            : state.output === "activity"
              ? state.events
              : { ...state.run, events: state.events },
          null,
          2,
        ),
  ),
);
$("export-output").addEventListener("click", exportOutput);
$("cancel-run").addEventListener("click", async () => {
  if (!state.run || !ACTIVE.has(state.run.status)) return;
  const id = state.run.run_id;
  $("cancel-run").disabled = true;
  try {
    await client.request(`/v1/runs/${encodeURIComponent(id)}/cancel`, {
      body: {},
    });
    await openRun(id);
    toast("Run stopped");
    await refreshOverview();
    await loadThreads();
  } catch (error) {
    report(error);
  } finally {
    $("cancel-run").disabled = false;
  }
});
$("continue-thread").addEventListener("click", async () => {
  if (!state.run?.thread_id) return;
  try {
    const thread =
      state.threads.find((item) => item.thread_id === state.run.thread_id) ||
      (await client.request(
        `/v1/threads/${encodeURIComponent(state.run.thread_id)}/info`,
      ));
    state.selectedThread = thread;
    applyTarget();
    $("prompt").value = "";
    updatePromptCount();
    $("prompt").focus();
  } catch (error) {
    report(error);
  }
});
$("mobile-menu").addEventListener("click", () => {
  $("sidebar").classList.add("open");
  $("sidebar-backdrop").hidden = false;
});
$("sidebar-backdrop").addEventListener("click", closeSidebar);
window.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    newConversation();
  }
  if (event.key === "Escape") closeSidebar();
});
window.addEventListener("hashchange", () =>
  navigate(location.hash.slice(1), false),
);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  if (state.run && ACTIVE.has(state.run.status))
    pollRun(state.run.run_id, state.requestVersion);
  refreshWorkspace();
});
setInterval(() => {
  if (document.hidden) return;
  checkHealth();
  if (!state.refreshing) refreshWorkspace();
}, 30000);
window.addEventListener("pagehide", () => {
  state.generation++;
  state.requestVersion++;
  clearTimeout(state.pollTimer);
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    checkHealth();
    refreshWorkspace();
    if (state.run && ACTIVE.has(state.run.status))
      pollRun(state.run.run_id, state.requestVersion);
  }
});

navigate(location.hash.slice(1) || "workspace", false);
renderConnection();
renderOverview();
renderRuns();
renderThreads();
checkHealth();
refreshWorkspace();
