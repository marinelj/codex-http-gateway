from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

RunStatus = Literal[
    "queued", "starting", "running", "completed", "failed", "interrupted", "cancelled", "timed_out"
]
ACTIVE = ("queued", "starting", "running")


class SubmitRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=100000)
    thread_id: UUID | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


class RunError(BaseModel):
    code: str
    message: str


class RunSummary(BaseModel):
    run_id: str
    prompt_preview: str
    workspace: str
    model: str | None = None
    reasoning_effort: str | None = None
    sandbox: str | None = None
    thread_id: str | None
    status: RunStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    final_output: str | None
    exit_code: int | None
    usage: dict[str, Any] | None
    error: RunError | None


class AcceptedRun(BaseModel):
    run_id: str
    thread_id: str | None
    status: RunStatus
    status_url: str
    thread_url: str | None


class Event(BaseModel):
    cursor: int
    run_id: str
    created_at: str
    source: Literal["codex", "stderr", "stdout", "gateway"]
    data: dict[str, Any]


class EventPage(BaseModel):
    events: list[Event]
    next_cursor: int
    has_more: bool


class RunDetail(RunSummary, EventPage):
    prompt: str
    requested_thread_id: str | None


class ThreadDetail(EventPage):
    thread_id: str
    status: RunStatus
    latest_run: RunSummary
    history_scope: Literal["gateway"] = "gateway"


class RunPage(BaseModel):
    runs: list[RunSummary]
    next_after: str | None
    has_more: bool


class CatalogThread(BaseModel):
    thread_id: str
    title: str
    preview: str
    workspace: str
    status: str
    updated_at: int
    can_send: bool
    latest_run: RunSummary | None = None


class ThreadPage(BaseModel):
    threads: list[CatalogThread]
    next_cursor: str | None
    has_more: bool


class Overview(BaseModel):
    version: str
    workspace: str
    sandbox: str
    model: str | None
    reasoning_effort: str | None = None
    workers: int
    max_pending_runs: int
    allow_other_workspaces: bool
    total_runs: int
    total_threads: int
    statuses: dict[str, int]


class EffortOption(BaseModel):
    id: str
    description: str


class ModelOption(BaseModel):
    id: str
    name: str
    description: str
    default_effort: str
    efforts: list[EffortOption]


class ModelOptions(BaseModel):
    models: list[ModelOption]
    default_model: str
    default_effort: str


class ChatMessage(BaseModel):
    id: str
    turn_id: str
    role: Literal["user", "assistant"]
    text: str
    created_at: int | None
    status: str


class ChatPage(BaseModel):
    thread_id: str
    messages: list[ChatMessage]
    next_cursor: str | None
    has_more: bool
    history_scope: Literal["codex"] = "codex"


class UsageWindow(BaseModel):
    id: str
    name: str
    scope: str
    remaining_percent: float
    resets_at: int | None
    window_minutes: int | None


class UsageNotice(BaseModel):
    available: bool
    updated_at: str
    windows: list[UsageWindow]
    resets_available: int | None = None
    message: str | None = None
