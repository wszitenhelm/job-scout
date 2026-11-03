"""Rule-based filters. Cheap, deterministic, testable.

The LLM never sees the 3,000+ raw jobs. Rules cut them to a short list
first, then Claude judges fit on the few that pass. That keeps cost low
and the pipeline predictable.
"""

import re


def _any(terms, text):
    text = text.lower()
    return any(re.search(rf"\b{re.escape(t.lower())}\b", text) for t in terms)


def title_ok(title, rules):
    if _any(rules["title_exclude"], title):
        return False
    return _any(rules["title_include"], title)


def location_verdict(location, rules):
    """Return 'yes', 'maybe' or 'no' for whether she can work from here.

    'yes'   a place she can work from is named (Poland, EMEA, anywhere...)
    'maybe' just 'Remote' with no country, worth a look
    'no'    only places she can't work from (US-only, Brazil...)
    A mixed location like 'Bangalore, India; Remote, Poland' is 'yes':
    one allowed place is enough.
    """
    if _any(rules["location_include"], location):
        return "yes"
    if _any(rules["location_exclude"], location):
        return "no"
    if re.fullmatch(r"\s*remote\s*", location, flags=re.I):
        return "maybe"
    return "no"


def asia_flag(location, rules):
    """Could she do this job from Asia? Only a hint, the listing decides."""
    if _any(rules["asia_yes"], location):
        return "yes"
    return "ask"


def keep(job, rules):
    """Annotate and decide. Returns the job with 'where' and 'asia' set,
    or None if it should be dropped."""
    if not title_ok(job["title"], rules):
        return None
    where = location_verdict(job["location"], rules)
    if where == "no":
        return None
    return {**job, "where": where, "asia": asia_flag(job["location"], rules)}
