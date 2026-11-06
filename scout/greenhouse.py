"""Greenhouse Job Board API client.

Public, read-only API: https://developers.greenhouse.io/job-board.html
No auth needed. One request per company returns every open job.

Why the retry logic: a scheduled job that dies on one flaky response
is worse than useless, because nobody notices it stopped. So we retry
timeouts, 429s and 5xx with exponential backoff, and fail loudly
(raise + log) on anything else.
"""

import html
import re
import time
from urllib.parse import parse_qs, urlparse

import requests

from scout.log import log

BASE = "https://boards-api.greenhouse.io/v1/boards"
RETRY_STATUS = {429, 500, 502, 503, 504}


def get_with_retry(url, params=None, attempts=4, timeout=20, sleep=time.sleep):
    """GET with exponential backoff: 1s, 2s, 4s. Honors Retry-After on 429."""
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == attempts:
                raise
            log("http_retry", url=url, attempt=attempt, error=type(exc).__name__)
            sleep(2 ** (attempt - 1))
            continue

        if resp.status_code in RETRY_STATUS and attempt < attempts:
            wait = int(resp.headers.get("Retry-After", 2 ** (attempt - 1)))
            log("http_retry", url=url, attempt=attempt, status=resp.status_code)
            sleep(wait)
            continue

        resp.raise_for_status()
        return resp
    raise RuntimeError("unreachable")


def fetch_jobs(board_token, get=get_with_retry):
    """Return the open jobs for one company, normalized to plain dicts."""
    started = time.monotonic()
    resp = get(f"{BASE}/{board_token}/jobs", params={"content": "true"})
    jobs = [normalize(board_token, j) for j in resp.json().get("jobs", [])]
    log("fetch_ok", board=board_token, count=len(jobs),
        duration_ms=int((time.monotonic() - started) * 1000))
    return jobs


def parse_job_url(url, board=None):
    """Turn a job link into (board_token, job_id).

    Understands the three shapes a Slack digest link can have:
      job-boards.greenhouse.io/<board>/jobs/<id>      (also boards.greenhouse.io)
      boards.greenhouse.io/embed/job_app?for=<board>&token=<id>
      <company site>/careers/...?gh_jid=<id>          (board guessed from the
                                                       domain, or pass board=)
    Raises ValueError with a message that says what to do.
    """
    parts = urlparse(url.strip())
    host = (parts.hostname or "").lower()
    query = parse_qs(parts.query)

    if host.endswith("greenhouse.io"):
        m = re.match(r"^/([^/]+)/jobs/(\d+)", parts.path)
        if m:
            return board or m.group(1), m.group(2)
        if query.get("for") and query.get("token"):
            return board or query["for"][0], query["token"][0]

    jid = (query.get("gh_jid") or query.get("gh_job_id") or [None])[0]
    if jid and jid.isdigit():
        if board:
            return board, jid
        labels = [l for l in host.split(".") if l not in ("www", "careers", "jobs")]
        guess = labels[-2] if len(labels) >= 2 else labels[0] if labels else ""
        if guess:
            return guess, jid
    raise ValueError("No Greenhouse job id in that link. Pass --board <token> "
                     "or use a job-boards.greenhouse.io link.")


def fetch_job(board_token, job_id, get=get_with_retry):
    """Fetch one job by id. A 404 means the role closed, or the board token
    is wrong (company sites often differ from the Greenhouse token)."""
    resp = get(f"{BASE}/{board_token}/jobs/{job_id}")
    job = normalize(board_token, resp.json())
    log("fetch_job_ok", board=board_token, id=job["id"])
    return job


def normalize(board_token, raw):
    """Keep only the fields we use. The id is board-scoped so two companies
    can never collide."""
    return {
        "id": f"{board_token}:{raw['id']}",
        "board": board_token,
        "company": raw.get("company_name") or board_token,
        "title": raw.get("title", "").strip(),
        "location": (raw.get("location") or {}).get("name", ""),
        "url": raw.get("absolute_url", ""),
        "updated_at": raw.get("updated_at", ""),
        "description": to_text(raw.get("content", "")),
    }


def to_text(content):
    """Greenhouse returns HTML that is itself HTML-escaped. Unescape, strip
    tags, collapse whitespace. Good enough for a human or an LLM to read."""
    text = html.unescape(html.unescape(content or ""))
    text = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()
