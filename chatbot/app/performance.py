from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

logger = logging.getLogger("chatbot.performance")
_current_timer: ContextVar["RequestTimer | None"] = ContextVar("current_request_timer", default=None)


class RequestTimer:
    def __init__(self, request_name: str) -> None:
        self.request_name = request_name
        self.started_at = time.perf_counter()
        self.measurements: list[dict[str, Any]] = []

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started_at) * 1000, 2)

    @contextmanager
    def time_block(self, name: str, **metadata: Any) -> Iterator[None]:
        start = time.perf_counter()
        status = "ok"
        error: str | None = None
        try:
            yield
        except Exception as exc:
            status = "error"
            error = repr(exc)
            raise
        finally:
            item: dict[str, Any] = {
                "name": name,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                "status": status,
            }
            if metadata:
                item["metadata"] = metadata
            if error:
                item["error"] = error
            self.measurements.append(item)

    def snapshot(self, *, status: str = "running") -> dict[str, Any]:
        return {
            "request": self.request_name,
            "status": status,
            "elapsed_ms": self.elapsed_ms(),
            "measurements": list(self.measurements),
        }


def start_request_timer(request_name: str):
    timer = RequestTimer(request_name)
    token = _current_timer.set(timer)
    return timer, token


def reset_request_timer(token) -> None:
    _current_timer.reset(token)


def get_timer() -> RequestTimer | None:
    return _current_timer.get()


@contextmanager
def timed_step(name: str, **metadata: Any) -> Iterator[None]:
    timer = get_timer()
    if timer is None:
        yield
    else:
        with timer.time_block(name, **metadata):
            yield


def log_timing(timing: dict[str, Any]) -> None:
    logger.info("request_timing=%s", timing)
