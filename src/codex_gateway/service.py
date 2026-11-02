from __future__ import annotations

import asyncio
import logging

from .config import Settings
from .models import ACTIVE, SubmitRun
from .runner import RunFailure, Runner, validate_session
from .store import GatewayError, Store, fingerprint, now

logger = logging.getLogger(__name__)


class Gateway:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.runner = Runner(settings, store)
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.lock = asyncio.Lock()
        self.ready: dict[str, asyncio.Event] = {}
        self.running: dict[str, asyncio.Task] = {}
        self.settled: dict[str, asyncio.Event] = {}
        self.active_workers: set[asyncio.Task] = set()
        self.workers: list[asyncio.Task] = []
        self.closing = False

    async def start(self) -> None:
        self.store.recover()
        self.workers = [asyncio.create_task(self.work()) for _ in range(self.settings.workers)]

    async def stop(self) -> None:
        self.closing = True
        for task in self.running.values():
            if not task.cancelling():
                task.cancel()
        for task in self.workers:
            if task not in self.active_workers:
                task.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        # Queued submissions are never automatically replayed after a restart.
        self.store.recover()

    async def submit(self, request: SubmitRun, key: str | None) -> dict:
        thread_id = str(request.thread_id) if request.thread_id else None
        digest = fingerprint(request.prompt, thread_id)
        async with self.lock:
            if self.closing:
                raise GatewayError(503, "shutting_down", "Service is shutting down")
            replay = self.store.replay(key, digest)
            if replay is not None:
                return replay
            if self.store.active_count() >= self.settings.max_pending_runs:
                raise GatewayError(429, "queue_full", "Too many pending runs; retry later")
            if thread_id:
                await asyncio.to_thread(validate_session, self.settings, thread_id)
            run = self.store.create(request.prompt, thread_id, key)
            signal = self.ready[run["run_id"]] = asyncio.Event()
            self.settled[run["run_id"]] = asyncio.Event()
            self.queue.put_nowait(run["run_id"])
        if self.settings.startup_wait_seconds:
            try:
                await asyncio.wait_for(signal.wait(), self.settings.startup_wait_seconds)
            except TimeoutError:
                pass
        return self.store.get(run["run_id"])

    async def work(self) -> None:
        while True:
            run_id = await self.queue.get()
            worker = asyncio.current_task()
            self.active_workers.add(worker)
            try:
                if self.store.get(run_id)["status"] != "queued":
                    continue
                self.store.update(run_id, status="starting", started_at=now())
                signal = self.ready[run_id]
                task = self.running[run_id] = asyncio.create_task(
                    self.runner.execute(run_id, signal)
                )
                try:
                    await task
                except asyncio.CancelledError:
                    self.store.finish(
                        run_id,
                        "interrupted" if self.closing else "cancelled",
                        {
                            "code": "service_stopped" if self.closing else "cancelled",
                            "message": "Service stopped"
                            if self.closing
                            else "Run cancelled by client",
                        },
                    )
                    if self.closing:
                        raise
                except (RunFailure, GatewayError) as exc:
                    self.store.finish(
                        run_id,
                        "timed_out" if exc.code == "timeout" else "failed",
                        {
                            "code": exc.code,
                            "message": exc.message,
                        },
                    )
                except Exception:
                    logger.exception("Worker failed for run %s", run_id)
                    self.store.finish(
                        run_id,
                        "failed",
                        {
                            "code": "worker_error",
                            "message": "Worker failed; check service logs",
                        },
                    )
                finally:
                    signal.set()
                    self.running.pop(run_id, None)
            finally:
                self.ready.pop(run_id, None)
                self.settled.pop(run_id).set()
                self.active_workers.discard(worker)
                self.queue.task_done()

    async def cancel(self, run_id: str) -> dict:
        run = self.store.get(run_id)
        if run["status"] not in ACTIVE:
            return run
        task = self.running.get(run_id)
        if task is None:
            self.store.finish(
                run_id,
                "cancelled",
                {
                    "code": "cancelled",
                    "message": "Run cancelled before execution",
                },
            )
            if signal := self.ready.get(run_id):
                signal.set()
        else:
            # The worker records terminal status after the runner stops the CLI.
            done = self.settled[run_id]
            if not task.cancelling():
                task.cancel()
            await done.wait()
        return self.store.get(run_id)
