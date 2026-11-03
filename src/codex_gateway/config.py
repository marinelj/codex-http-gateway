from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    root_path: str = ""
    workspace: Path = Field(default_factory=Path.cwd)
    data_dir: Path = Field(default_factory=lambda: Path.home() / ".local/state/codex-http-gateway")
    codex_home: Path = Field(
        default_factory=lambda: Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    )
    codex_bin: str = "codex"
    sandbox: Literal["read-only", "workspace-write"] = "read-only"
    model: str | None = None
    proxy_url: str | None = None
    workers: int = Field(default=1, ge=1, le=16)
    max_pending_runs: int = Field(default=64, ge=1, le=10000)
    startup_wait_seconds: float = Field(default=1.0, ge=0, le=5)
    run_timeout_seconds: float = Field(default=1800, gt=0)
    max_output_bytes: int = Field(default=64 * 1024 * 1024, ge=1024)
    max_line_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)
    skip_git_repo_check: bool = False
    allow_other_workspaces: bool = False

    @field_validator("proxy_url")
    @classmethod
    def valid_proxy(cls, value: str | None) -> str | None:
        if value is not None:
            url = urlsplit(value)
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ValueError("proxy_url must be an HTTP or HTTPS proxy URL")
        return value

    @field_validator("root_path")
    @classmethod
    def path_prefix(cls, value: str) -> str:
        if value and not value.startswith("/"):
            raise ValueError("root_path must start with /")
        return value.rstrip("/")

    @field_validator("workspace", "data_dir", "codex_home")
    @classmethod
    def absolute_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    def prepare(self) -> None:
        if not self.workspace.is_dir():
            raise ValueError(f"Workspace does not exist: {self.workspace}")
        executable = shutil.which(self.codex_bin)
        if executable is None:
            raise ValueError(f"Codex executable not found: {self.codex_bin}")
        self.codex_bin = executable
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
