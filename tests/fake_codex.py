"""Deterministic executable used to exercise the real subprocess boundary."""

import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4


def emit(data):
    print(json.dumps(data), flush=True)


def main():
    args = sys.argv[1:]
    prompt = sys.stdin.read()
    thread = args[args.index("resume") + 1] if "resume" in args else str(uuid4())
    cwd = args[args.index("-C") + 1]
    root = Path(os.environ["CODEX_HOME"]) / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"rollout-test-{thread}.jsonl"
    if not path.exists():
        path.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"id": thread, "cwd": cwd},
                }
            )
            + "\n"
        )
    if prompt == "mismatch":
        thread = str(uuid4())
    if prompt != "no-thread":
        emit({"type": "thread.started", "thread_id": thread})
    emit({"type": "turn.started"})
    if prompt.startswith("sleep:"):
        time.sleep(float(prompt.split(":")[1]))
    if prompt == "large":
        emit({"type": "item.completed", "item": {"type": "agent_message", "text": "x" * 10000}})
    if prompt == "stderr":
        print("diagnostic with newline", file=sys.stderr, flush=True)
        print("non-JSON output", flush=True)
        print("[1, 2, 3]", flush=True)
        emit({"type": "future.event", "value": "kept"})
    if prompt == "env":
        prompt = ",".join(sorted(k for k in os.environ if k.startswith("GATEWAY_")))
    emit(
        {
            "type": "item.completed",
            "item": {"id": "item_1", "type": "agent_message", "text": prompt},
        }
    )
    if prompt == "fail":
        emit({"type": "turn.failed", "error": {"message": "Deliberate failure"}})
        sys.exit(7)
    if prompt == "exit":
        sys.exit(3)
    if prompt != "no-complete":
        emit({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}})


if __name__ == "__main__":
    main()
