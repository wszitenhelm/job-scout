---
name: job-scout
description: Find new remote roles from company Greenhouse boards, score each one against Wiktoria's profile, and post the best to Slack. Use when she says "scout", "new jobs", "find roles", "score jobs", or asks to update a job's status. Never applies to anything.
---

# job-scout

The pipeline (fetch, filter, store, Slack) is plain Python and runs without you. Your job is the part rules can't do: read a listing and judge if it's worth her time. You only touch the data through the CLI.

## 1. Fetch

```
python -m scout fetch
```
Prints how many new roles passed the filters. If a board failed, say which one and move on.

## 2. Score every unscored role

```
python -m scout list --unscored --json
```
For each role, read it with `python -m scout show <id>` and score it against `config/profile.md`:

| Part | Points | Question |
|---|---|---|
| Skills | 40 | How many must-haves does she really have? A missing must-have costs a lot. |
| Level | 25 | Early-career or intermediate = full points. Asks 5+ years or senior scope = near zero. |
| Goals | 20 | Growth, impact (health, wellbeing, education, widely used tools), AI/automation, pay. |
| Location | 15 | Remote from Poland = full. Worldwide or APAC too (Asia plan) = full plus mention it. |

Then save it:
```
python -m scout score <id> <0-100> "<one line: strongest match + biggest gap>"
```
Reason style: short, plain, honest. Example: "Automation + webhooks match, HR domain from WellKey; gap: Workato, Ruby."

When there are many roles from one company (Canonical posts 60+), score them all, but be strict: most will be under 50.

## 3. Post the digest

```
python -m scout digest --min-score 60 --limit 15
```
Show her the same list in chat: score, title, company, reason, id.

## 4. Her decisions

She answers in chat ("shortlist gitlab:123", "skip the Canonical ones", "applied to 8861748002"). Map that to:
```
python -m scout set <id> shortlist|skip|applied|interview|rejected|offer
```
`python -m scout stats` shows the pipeline.

## 5. Application pack (only when she asks for one)

If her `job_applications` repo is available, build the pack there following its `_master/pack-spec.md`: CV PDF, cover letter PDF when accepted, `apply.md`. Then `python -m scout set <id> shortlist`.

## Rules

- Never apply, submit a form, send an email or create an account. She does that.
- Never invent experience. `config/profile.md` lists what she has and what she must never claim.
- No em dashes, no corporate words in anything you write for her.
