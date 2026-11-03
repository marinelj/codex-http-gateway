"""Optional live test: submit, poll, resume, and verify conversation memory.

This invokes the real Codex model twice and consumes normal account usage.
Run: uv run python scripts/smoke.py --base-url http://127.0.0.1:8080/codex
"""

from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/codex")
    args = parser.parse_args()

    def call(path, body=None):
        url = urljoin(args.base_url.rstrip("/") + "/", path)
        req = Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=15) as res:
                return json.load(res)
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode()}") from None

    def wait(run):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            detail = call(run["status_url"])
            if detail["status"] == "completed":
                return detail
            if detail["status"] not in {"queued", "starting", "running"}:
                raise RuntimeError(f"Run failed: {detail['error']}")
            time.sleep(0.5)
        raise TimeoutError("Live Codex smoke test did not finish within three minutes")

    code = "gateway-" + uuid4().hex[:12]
    start = time.monotonic()
    first = call(
        "v1/runs",
        {
            "prompt": f"Remember this verification code for this conversation: {code}. "
            f"Reply with exactly {code}. Do not use any tools or change any files.",
            "thread_id": None,
        },
    )
    print(json.dumps({"accepted_in_seconds": round(time.monotonic() - start, 3), **first}))
    first_result = wait(first)
    assert first_result["final_output"].strip() == code, first_result["final_output"]
    second = call(
        "v1/runs",
        {
            "prompt": "What verification code did I ask you to remember in my previous message? "
            "Reply with exactly that code. Do not use tools or change any files.",
            "thread_id": first_result["thread_id"],
        },
    )
    second_result = wait(second)
    assert second_result["thread_id"] == first_result["thread_id"]
    assert second_result["final_output"].strip() == code, second_result["final_output"]
    thread = call(f"v1/threads/{first_result['thread_id']}")
    assert thread["status"] == "completed"
    assert {event["run_id"] for event in thread["events"]} == {first["run_id"], second["run_id"]}
    print(
        json.dumps(
            {
                "verified": True,
                "thread_id": first_result["thread_id"],
                "runs": [first["run_id"], second["run_id"]],
                "events": len(thread["events"]),
            }
        )
    )


if __name__ == "__main__":
    main()
