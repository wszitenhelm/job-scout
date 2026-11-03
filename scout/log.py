"""Structured logging: one JSON object per line.

Why JSON lines: grep-able locally, and any log tool (Datadog, Loki,
GitLab CI job logs) can parse it. Every event has a name, so you can
count failures per board instead of reading stack traces.
"""

import json
import os
import sys
from datetime import datetime, timezone

LOG_FILE = os.environ.get("SCOUT_LOG", "data/scout.jsonl")


def log(event, **fields):
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "event": event, **fields}
    line = json.dumps(record, ensure_ascii=False)
    print(line, file=sys.stderr)
    try:
        os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass  # logging must never crash the run
