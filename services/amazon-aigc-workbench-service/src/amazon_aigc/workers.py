"""Bounded worker lanes over the durable PostgreSQL job queue."""

import json
from concurrent.futures import ThreadPoolExecutor


def serve(run_once, concurrency, stop, poll_seconds=1):
    if not 1 <= concurrency <= 8:
        raise ValueError("Worker concurrency must be between 1 and 8")

    def lane():
        while not stop.is_set():
            try:
                worked = run_once()
            except Exception as exc:  # noqa: BLE001 -- keep other lanes alive, without exposing credentials
                print(json.dumps({"worker_error": type(exc).__name__}), flush=True)
                worked = False
            if not worked:
                stop.wait(poll_seconds)

    # Stop taking new work on SIGTERM; finish the bounded in-flight requests.
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="aigc-job") as pool:
        futures = [pool.submit(lane) for _ in range(concurrency)]
        for future in futures:
            future.result()
