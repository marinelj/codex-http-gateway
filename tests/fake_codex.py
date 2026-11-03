"""Deterministic executable used to exercise the real subprocess boundary."""

import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4


def emit(data):
    print(json.dumps(data), flush=True)


def catalog():
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        root = Path(os.environ["CODEX_HOME"]) / "sessions"
        threads = []
        for path in sorted(root.glob("**/*.jsonl")):
            try:
                header = json.loads(path.read_text().splitlines()[0])["payload"]
                threads.append(
                    {
                        "id": header["id"],
                        "cwd": header["cwd"],
                        "path": str(path),
                        "name": header.get("name"),
                        "preview": header.get("preview", "Saved conversation"),
                        "updatedAt": header.get("updated_at", 100),
                        "status": {"type": "notLoaded"},
                        "canAcceptDirectInput": header.get("can_send", True),
                    }
                )
            except (ValueError, KeyError, IndexError):
                continue
        params = request.get("params", {})
        if request["method"] == "initialize":
            result = {}
        elif request["method"] == "thread/list":
            query = params.get("searchTerm")
            if query:
                threads = [
                    thread for thread in threads if query in (thread["name"] or thread["preview"])
                ]
            offset = int(params.get("cursor") or 0)
            limit = params.get("limit", 50)
            result = {
                "data": threads[offset : offset + limit],
                "nextCursor": (str(offset + limit) if offset + limit < len(threads) else None),
            }
        elif request["method"] == "thread/read":
            thread = next((item for item in threads if item["id"] == params["threadId"]), None)
            if thread is None:
                emit(
                    {"id": request["id"], "error": {"code": -32602, "message": "Thread not found"}}
                )
                continue
            result = {"thread": thread}
        else:
            result = {}
        emit({"id": request["id"], "result": result})


def main():
    args = sys.argv[1:]
    if args and args[0] == "app-server":
        catalog()
        return
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
