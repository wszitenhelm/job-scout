"""Slack digest through an Incoming Webhook.

Why a webhook and not a bot: posting a digest needs no OAuth, no server
and no public URL. It's one secret (the webhook URL) in an env variable.
A bot with buttons (shortlist / skip from Slack) is the next step and
needs an endpoint to receive Slack's interaction callbacks.

Slack limits: max 50 blocks per message, so long digests are split.
"""

import json
import os
import time

import requests

from scout.log import log

MAX_BLOCKS = 50


def job_block(job):
    score = f"*{job['score']}*/100 · " if job.get("score") is not None else ""
    asia = " · Asia: yes" if job.get("asia") == "yes" else ""
    remote = " · remote, country unclear" if job.get("where_ok") == "maybe" else ""
    text = (f"{score}<{job['url']}|{job['title']}> · {job['company']}\n"
            f"{job['location']}{remote}{asia}")
    if job.get("reason"):
        text += f"\n_{job['reason']}_"
    text += f"\n`{job['id']}`"
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def build_messages(jobs, counts):
    """Return a list of Slack payloads (one per 50-block chunk)."""
    header = {"type": "header",
              "text": {"type": "plain_text", "text": f"job-scout: {len(jobs)} new roles"}}
    pipeline = " · ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    footer = {"type": "context",
              "elements": [{"type": "mrkdwn", "text": f"Pipeline: {pipeline}"}]}

    body = []
    for job in jobs:
        body += [job_block(job), {"type": "divider"}]

    room = MAX_BLOCKS - 2  # header + footer
    chunks = [body[i:i + room] for i in range(0, len(body), room)] or [[]]
    messages = []
    for n, chunk in enumerate(chunks):
        blocks = ([header] if n == 0 else []) + chunk
        if n == len(chunks) - 1:
            blocks.append(footer)
        messages.append({"text": f"job-scout: {len(jobs)} new roles", "blocks": blocks})
    return messages


def post(payload, webhook_url, attempts=4, sleep=time.sleep):
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=15)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == attempts:
                raise
            log("slack_retry", attempt=attempt, error=type(exc).__name__)
            sleep(2 ** (attempt - 1))
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == attempts:
                resp.raise_for_status()
            sleep(int(resp.headers.get("Retry-After", 2 ** (attempt - 1))))
            log("slack_retry", attempt=attempt, status=resp.status_code)
            continue
        if resp.status_code != 200:
            # 4xx is our bug (bad payload, revoked webhook): don't retry, fail loud
            log("slack_error", status=resp.status_code, body=resp.text[:200])
            resp.raise_for_status()
        return
    raise RuntimeError("unreachable")


def send_digest(jobs, counts, webhook_url=None, dry_run=False):
    """Post the digest. Returns True if it was posted (so the caller marks
    the jobs as posted only after Slack accepted them)."""
    webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
    messages = build_messages(jobs, counts)
    if dry_run or not webhook_url:
        print(json.dumps(messages, indent=2, ensure_ascii=False))
        log("digest_dry_run", jobs=len(jobs), messages=len(messages))
        return False
    for msg in messages:
        post(msg, webhook_url)
    log("digest_posted", jobs=len(jobs), messages=len(messages))
    return True
