import json
import os
import tomllib
from pathlib import Path

import pytest
import requests

from scout import filters, greenhouse, slack, store

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "tests/fixtures/gitlab_jobs.json").read_text())
RULES = tomllib.loads((ROOT / "config/companies.toml").read_text())["rules"]


class FakeResp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


@pytest.fixture(autouse=True)
def tmp_log(tmp_path, monkeypatch):
    monkeypatch.setattr("scout.log.LOG_FILE", str(tmp_path / "scout.jsonl"))


@pytest.fixture
def jobs():
    return [greenhouse.normalize("gitlab", j) for j in FIXTURE["jobs"]]


@pytest.fixture
def db(tmp_path):
    return store.connect(str(tmp_path / "t.db"))


# --- greenhouse -----------------------------------------------------------

def test_normalize_scopes_id_and_strips_html(jobs):
    pse = next(j for j in jobs if j["title"] == "People Systems Engineer")
    assert pse["id"] == "gitlab:8861748002"
    assert "<" not in pse["description"] and "&lt;" not in pse["description"]
    assert pse["description"].startswith("GitLab is")


def test_retry_on_503_then_success(monkeypatch):
    calls = iter([FakeResp(503), FakeResp(200, {"jobs": []})])
    monkeypatch.setattr(requests, "get", lambda *a, **k: next(calls))
    waits = []
    resp = greenhouse.get_with_retry("http://x", sleep=waits.append)
    assert resp.status_code == 200 and waits == [1]


def test_no_retry_on_404(monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(1) or FakeResp(404))
    with pytest.raises(requests.HTTPError):
        greenhouse.get_with_retry("http://x", sleep=lambda s: None)
    assert len(calls) == 1


# --- filters --------------------------------------------------------------

def test_mixed_location_with_poland_is_kept(jobs):
    pse = next(j for j in jobs if j["title"] == "People Systems Engineer")
    kept = filters.keep(pse, RULES)
    assert kept["where"] == "yes"


def test_us_only_and_senior_are_dropped(jobs):
    others = [j for j in jobs if j["title"] != "People Systems Engineer"]
    assert all(filters.keep(j, RULES) is None for j in others)


@pytest.mark.parametrize("loc,expected", [
    ("Remote", "maybe"),
    ("Remote, Poland", "yes"),
    ("Home based - Worldwide", "yes"),
    ("Remote, United States", "no"),
    ("Berlin", "no"),
])
def test_location_verdict(loc, expected):
    assert filters.location_verdict(loc, RULES) == expected


def test_title_rules():
    assert filters.title_ok("Junior Software Engineer", RULES)
    assert filters.title_ok("Forward Deployed Engineer", RULES)
    assert not filters.title_ok("Senior Software Engineer", RULES)
    assert not filters.title_ok("Account Executive", RULES)


# --- store ----------------------------------------------------------------

def _kept(jobs):
    return filters.keep(next(j for j in jobs if j["title"] == "People Systems Engineer"), RULES)


def test_upsert_is_idempotent_and_keeps_decisions(db, jobs):
    job = _kept(jobs)
    assert store.upsert(db, job) is True
    store.set_status(db, job["id"], "applied")
    store.set_score(db, job["id"], 80, "strong automation fit")
    assert store.upsert(db, job) is False          # second fetch: not new
    row = store.get(db, job["id"])
    assert row["status"] == "applied" and row["score"] == 80


def test_mark_closed(db, jobs):
    job = _kept(jobs)
    store.upsert(db, job)
    store.mark_closed(db, "gitlab", ["gitlab:other"])
    assert store.get(db, job["id"])["status"] == "closed"


def test_empty_fetch_closes_nothing(db, jobs):
    job = _kept(jobs)
    store.upsert(db, job)
    store.mark_closed(db, "gitlab", [])
    assert store.get(db, job["id"])["status"] == "new"


def test_bad_score_and_status_rejected(db, jobs):
    job = _kept(jobs)
    store.upsert(db, job)
    with pytest.raises(ValueError):
        store.set_score(db, job["id"], 120, "x")
    with pytest.raises(ValueError):
        store.set_status(db, job["id"], "maybe")


# --- slack ----------------------------------------------------------------

def _fake_job(i):
    return {"id": f"b:{i}", "title": f"Engineer {i}", "company": "Co", "url": "http://x",
            "location": "Remote, Poland", "score": None, "asia": "ask", "where_ok": "yes"}


def test_digest_splits_under_slack_block_limit():
    msgs = slack.build_messages([_fake_job(i) for i in range(60)], {"new": 60})
    assert len(msgs) > 1
    assert all(len(m["blocks"]) <= slack.MAX_BLOCKS for m in msgs)
    assert msgs[0]["blocks"][0]["type"] == "header"
    assert msgs[-1]["blocks"][-1]["type"] == "context"


def test_slack_retries_429(monkeypatch):
    calls = iter([FakeResp(429, headers={"Retry-After": "3"}), FakeResp(200)])
    monkeypatch.setattr(requests, "post", lambda *a, **k: next(calls))
    waits = []
    slack.post({"text": "hi"}, "http://hook", sleep=waits.append)
    assert waits == [3]


def test_slack_400_fails_without_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or FakeResp(400))
    with pytest.raises(requests.HTTPError):
        slack.post({"text": "hi"}, "http://hook", sleep=lambda s: None)
    assert len(calls) == 1


# --- end to end through the CLI -------------------------------------------

def test_cli_flow(tmp_path, monkeypatch, capsys, jobs):
    from scout import __main__ as cli
    monkeypatch.setattr(cli, "DB_PATH", str(tmp_path / "e2e.db"))
    monkeypatch.setattr(cli, "load_config",
                        lambda: {"boards": {"gitlab": "GitLab"}, "rules": RULES})
    monkeypatch.setattr(greenhouse, "fetch_jobs", lambda token: jobs)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    assert cli.main(["fetch"]) == 0
    assert "1 new roles" in capsys.readouterr().out
    assert cli.main(["fetch"]) == 0                       # rerun: nothing new
    assert "0 new roles" in capsys.readouterr().out

    assert cli.main(["score", "gitlab:8861748002", "82", "Automation + HR domain"]) == 0
    assert cli.main(["digest", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "People Systems Engineer" in out and "82" in out

    db = store.connect(str(tmp_path / "e2e.db"))
    assert store.get(db, "gitlab:8861748002")["posted_at"] is None   # dry run never marks posted
    assert cli.main(["show", "nope:1"]) == 2
