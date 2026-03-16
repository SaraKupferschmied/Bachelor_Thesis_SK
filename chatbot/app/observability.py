# Metrics + logs

# app/observability.py
import time
from typing import Any

def log_event(event: str, data: Any):
    print({
        "event": event,
        "timestamp": time.time(),
        "details": data,
    })
