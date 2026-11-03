# job-scout

A small job-search agent I use every day. It watches the Greenhouse boards of companies I care about, filters out roles I can't take, lets Claude score the rest against my profile, and posts a short digest to Slack. I decide what to apply for. It never applies for me.

I built it to solve my own problem: I was checking 30 career pages by hand and missing new roles. It's also a small, honest example of how I build integrations: a deterministic pipeline for the plumbing, an LLM only where judgment is needed, and a human making the final call.

## How it works

```
Greenhouse Job Board API          (28 companies, ~4,000 open roles)
        |  fetch, retries + backoff
        v
Rule filters                      title, seniority, location  ->  ~80 roles
        |
        v
SQLite store                      idempotent: knows what's new, what's posted, what I decided
        |
        v
Claude skill                      reads each new role, scores 0-100 vs my profile, one-line reason
        |
        v
Slack digest (Incoming Webhook)   top roles, with score, reason and id
        |
        v
Me                                shortlist / skip / applied, through the same CLI
```

## Design choices

- **LLM only for judgment.** Fetching, filtering, deduplication and posting are plain code: cheap, testable and the same every run. Claude only sees the ~80 roles that survive the rules, never the ~4,000 raw ones.
- **The CLI is the contract.** The Claude skill (`.claude/skills/job-scout/SKILL.md`) only calls `python -m scout ...` commands. It never touches the database directly, so every action it takes is visible, logged and replayable.
- **Idempotent by design.** Job ids are `board:job_id`. A rerun after a crash never double-posts, and a new fetch never overwrites my decisions (status, score).
- **Fail loud, not silent.** Retries with exponential backoff on timeouts, 429 (honoring `Retry-After`) and 5xx. 4xx is a bug on our side, so it fails right away. One broken board doesn't stop the others, but the run exits non-zero so CI shows it.
- **Slack only gets marked as posted after Slack says 200.** A failed post means the roles go out on the next run instead of disappearing.
- **Structured logs.** One JSON object per line (`data/scout.jsonl`): `fetch_ok`, `fetch_failed`, `http_retry`, `digest_posted`, with counts and durations.
- **Empty fetch is a glitch, not "everything closed".** A board returning zero jobs doesn't close existing roles.

## Run it

```bash
pip install -r requirements.txt
python -m scout fetch                       # pull and filter
python -m scout list                        # what's in the store
python -m scout show gitlab:8861748002      # full description
python -m scout score gitlab:8861748002 82 "Automation + webhooks match; gap: Workato"
python -m scout digest --min-score 60       # needs SLACK_WEBHOOK_URL, else prints the payload
python -m scout set gitlab:8861748002 applied
python -m scout stats
```

With Claude Code, open the repo and say "scout new jobs": the skill runs fetch, scores everything new and posts the digest.

Tests: `pip install -r requirements-dev.txt && python -m pytest` (retries, filters, idempotency, Slack block limits, end-to-end CLI flow).

## Config

- `config/companies.toml`: board tokens and filter rules. Adding a company is one line.
- `config/profile.md`: what the scoring compares against, including what I must never claim.

## CI

`.gitlab-ci.yml` runs the tests on every push. A pipeline schedule runs `fetch` + `digest` daily with `SLACK_WEBHOOK_URL` as a masked variable. The SQLite file lives in the CI cache. That's fine for a digest (worst case: one repeated post), but anything with side effects should use a real database.

## Where this goes next

**Slack buttons.** Shortlist / skip straight from the digest. Needs a Slack app with interactivity and a small endpoint to receive the callbacks. The store and the CLI already support every action.

**The same pattern, on the employer side.** The pieces here are the same pieces a People Systems integration needs. Example: when a candidate is hired in Greenhouse, create the pre-hire in Workday and welcome them in Slack.

```
Greenhouse Harvest API webhook: candidate_hired
        |  verify signature, store event id (idempotency)
        v
Map fields  ->  Workday Web Services: Put_Applicant / Hire_Employee
        |  retries, dead-letter on repeated failure
        v
Slack: post in #new-hires, DM the hiring manager, alert #people-systems on failure
        |
        v
Structured log + audit record per step (who, what, when) for change-management controls
```

What changes: OAuth/API keys instead of a public API, PII handling (log ids, never names), a review step before writes to the HR system of record, and an audit trail. What stays: the client with retries, the idempotency store, JSON logs and a human in the loop for anything that writes.

## Limits

- Greenhouse boards only. Lever and Ashby have similar public APIs and would be one more client module each.
- Location rules are keyword-based. "Remote" with no country is marked "maybe", not guessed.
- Scores are a second opinion, not a decision. I still read every listing before applying.

## How I built it

With Claude Code, in one evening. I made the calls: what's deterministic and what's LLM, idempotency keys, what counts as a retryable error, what the tests must prove. Claude wrote much of the code and I reviewed and ran every part against the live API. The repo's own Claude skill is how I use it day to day.
