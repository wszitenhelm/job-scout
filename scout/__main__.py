"""job-scout CLI.

    python -m scout fetch                 pull boards, filter, store new roles
    python -m scout list [--status new] [--unscored] [--json]
    python -m scout add <url> [--board T] add one role from a link (any Greenhouse job)
    python -m scout show <id>             full description (for Claude to judge)
    python -m scout score <id> <0-100> "<reason>"
    python -m scout set <id> <status>     shortlist / skip / applied / interview ...
    python -m scout digest [--dry-run] [--min-score N] [--limit N]
    python -m scout stats

The CLI is the contract between the plumbing and the Claude skill: Claude
only ever calls these commands, it never writes to the database directly.
"""

import argparse
import json
import os
import sys
import tomllib

import requests

from scout import filters, greenhouse, slack, store
from scout.log import log

DB_PATH = os.environ.get("SCOUT_DB", "data/scout.db")
CONFIG = os.environ.get("SCOUT_CONFIG", "config/companies.toml")


def load_config(path=CONFIG):
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def cmd_fetch(db, cfg, args):
    new, failed = 0, []
    for token in cfg["boards"]:
        try:
            jobs = greenhouse.fetch_jobs(token)
        except Exception as exc:  # one broken board must not stop the others
            log("fetch_failed", board=token, error=str(exc)[:200])
            failed.append(token)
            continue
        for job in jobs:
            kept = filters.keep(job, cfg["rules"])
            if kept and store.upsert(db, kept):
                new += 1
        store.mark_closed(db, token, [j["id"] for j in jobs])
        db.commit()
    log("fetch_done", boards=len(cfg["boards"]), new=new, failed=failed)
    print(f"{new} new roles. Failed boards: {', '.join(failed) or 'none'}")
    # Partial failure is still a failure for CI, so someone looks at it.
    return 1 if failed else 0


def cmd_add(db, cfg, args):
    """Add one role by link, even if the rules would have dropped it or the
    company isn't in config. You picked it, so it goes in. The rule verdict
    is printed as information, never used to refuse."""
    try:
        board, job_id = greenhouse.parse_job_url(args.url, board=args.board)
        job = greenhouse.fetch_job(board, job_id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except requests.HTTPError as exc:
        print(f"Greenhouse says {exc}. The role may be closed, or the board "
              f"token is not '{board}'. Retry with --board <token>.", file=sys.stderr)
        return 2
    rules = cfg["rules"]
    job["company"] = cfg["boards"].get(board, job["company"])
    job["where"] = filters.location_verdict(job["location"], rules)
    job["asia"] = filters.asia_flag(job["location"], rules)
    is_new = store.upsert(db, job)
    db.commit()
    log("job_added", id=job["id"], new=is_new)
    notes = []
    if not filters.title_ok(job["title"], rules):
        notes.append("title would be filtered by the rules")
    if job["where"] == "no":
        notes.append("location is not workable from Poland")
    print(f"{'Added' if is_new else 'Already stored'}: {job['id']}  "
          f"{job['company']}: {job['title']} ({job['location']})")
    for n in notes:
        print(f"Note: {n}")
    return 0


def cmd_list(db, cfg, args):
    jobs = store.list_jobs(db, status=args.status, unscored=args.unscored)
    if args.json:
        print(json.dumps([{k: v for k, v in j.items() if k != "description"} for j in jobs],
                         indent=1, ensure_ascii=False))
        return 0
    for j in jobs:
        score = j["score"] if j["score"] is not None else "--"
        print(f"{score:>3}  {j['status']:<9} {j['id']:<28} {j['company']}: {j['title']} ({j['location']})")
    print(f"{len(jobs)} roles")
    return 0


def cmd_show(db, cfg, args):
    j = store.get(db, args.id)
    print(f"{j['title']} - {j['company']}\n{j['location']}\n{j['url']}\n\n{j['description']}")
    return 0


def cmd_score(db, cfg, args):
    store.set_score(db, args.id, args.score, args.reason)
    db.commit()
    log("scored", id=args.id, score=args.score)
    return 0


def cmd_set(db, cfg, args):
    store.set_status(db, args.id, args.status)
    db.commit()
    log("status_set", id=args.id, status=args.status)
    return 0


def cmd_digest(db, cfg, args):
    jobs = [j for j in store.list_jobs(db, unposted=True) if j["status"] in ("new", "shortlist")]
    if args.min_score is not None:
        jobs = [j for j in jobs if (j["score"] or 0) >= args.min_score]
    if args.limit:
        jobs = jobs[:args.limit]  # list_jobs is sorted best score first
    if not jobs:
        print("Nothing new to post.")
        return 0
    if slack.send_digest(jobs, store.counts(db), dry_run=args.dry_run):
        store.mark_posted(db, [j["id"] for j in jobs])
        db.commit()
    return 0


def cmd_stats(db, cfg, args):
    for status, n in sorted(store.counts(db).items()):
        print(f"{status:<10} {n}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="scout")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch")
    pl = sub.add_parser("list")
    pl.add_argument("--status")
    pl.add_argument("--unscored", action="store_true")
    pl.add_argument("--json", action="store_true")
    pa = sub.add_parser("add")
    pa.add_argument("url")
    pa.add_argument("--board", help="Greenhouse board token, if the link doesn't contain it")
    ps = sub.add_parser("show")
    ps.add_argument("id")
    pc = sub.add_parser("score")
    pc.add_argument("id")
    pc.add_argument("score", type=int)
    pc.add_argument("reason")
    pt = sub.add_parser("set")
    pt.add_argument("id")
    pt.add_argument("status", choices=store.STATUSES)
    pd = sub.add_parser("digest")
    pd.add_argument("--dry-run", action="store_true")
    pd.add_argument("--min-score", type=int)
    pd.add_argument("--limit", type=int, help="post at most N roles")
    sub.add_parser("stats")
    args = p.parse_args(argv)

    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    db = store.connect(DB_PATH)
    cfg = load_config()
    handler = globals()[f"cmd_{args.cmd}"]
    try:
        return handler(db, cfg, args)
    except KeyError as exc:
        print(f"Unknown job id: {exc}", file=sys.stderr)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
