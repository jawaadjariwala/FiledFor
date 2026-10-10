"""One poller run: fetch, classify, diff, enrich, store, alert, publish.

    uv run python -m filedfor.poll              full run
    uv run python -m filedfor.poll --no-alerts  store and publish, send nothing

    uv run python -m filedfor.poll --publish-only   rebuild public/ from the
                                                database, no fetching
    uv run python -m filedfor.poll --workday-full 2000
                                                read this many Workday boards in
                                                full (default 60), e.g. to
                                                bootstrap them all at once

Needs DATABASE_URL, and DISCORD_WEBHOOK for alerts (both from .env locally,
GitHub secrets in Actions). Design and trade-offs: ADR-002, Workday: ADR-003.
"""

import asyncio
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from filedfor import classify, feeds, notify, store
from filedfor.diff import diff, should_alert
from filedfor.evidence import COMPANIES, EvidenceIndex
from filedfor.publish import PUBLIC, publish

CONCURRENCY = 8
FAILED_SHARE_WARNING = 0.10
# Workday boards read in full per run, longest-waiting first. About 1,300
# boards at 48 runs a day means each one is read in full about twice a day
WORKDAY_FULL_PER_RUN = 60


@dataclass(frozen=True)
class AlertFilter:
    roles: frozenset[str] = frozenset({"ai", "swe", "data"})
    levels: frozenset[str] = frozenset({"entry"})
    max_years: int = 2  # 'unclear' titles pass when the description asks for <= this
    include_unknown_country: bool = True  # "Remote" with no country stated
    # Research titles to skip, but only on these boards: at startups "Research
    # Engineer" is often applied ML engineering, at frontier labs it's research
    skip_research_at: frozenset[str] = frozenset()

    def matches(self, job: dict) -> bool:
        if job["role"] not in self.roles:
            return False
        if job["slug"] in self.skip_research_at and RESEARCH.search(job["title"]):
            return False
        if job["no_sponsorship"] or job["citizens_only"] or job["clearance"]:
            return False
        if job["is_us"] is False or (
            job["is_us"] is None and not self.include_unknown_country
        ):
            return False
        if job["level"] in self.levels:
            return job["min_years"] is None or job["min_years"] <= self.max_years
        if job["level"] == "unclear":
            return job["min_years"] is not None and job["min_years"] <= self.max_years
        return False


RESEARCH = re.compile(
    r"research (engineer|scientist)|\bresearcher\b|^research\b", re.IGNORECASE
)
FRONTIER_LABS = frozenset({"anthropic", "openai", "xai", "mistral.ai", "cohere"})


def in_feeds(job: dict) -> bool:
    """Public RSS: new-grad jobs in any field, open to F-1 students, at
    companies with filings for that kind of role."""
    return bool(job["evidence"]) and AlertFilter(
        roles=frozenset(classify.FIELDS)
    ).matches(job)


# Discord alerts: applied AI roles, not frontier-lab research
DISCORD_ALERTS = AlertFilter(skip_research_at=FRONTIER_LABS)


@dataclass
class Candidate:
    posting: feeds.Posting
    role: str
    level: str
    is_us: bool | None
    is_remote: bool


def to_candidate(p: feeds.Posting) -> Candidate | None:
    """A tech role (any of the eight fields, any level) not clearly outside
    the US."""
    role = classify.role(p.title)
    if role is None:
        return None
    level = classify.level(p.title)
    us = classify.is_us(p.location, p.country)
    if us is False:
        return None
    return Candidate(p, role, level, us, classify.is_remote(p.location, p.workplace))


EXCLUDED = Path("data/excluded_boards.csv")


def load_excluded() -> set[tuple[str, str]]:
    with EXCLUDED.open(newline="") as f:
        return {(r["system"], r["slug"]) for r in csv.DictReader(f)}


def load_companies() -> list[dict]:
    """Live boards from the watchlist, minus ones that aren't employers."""
    excluded = load_excluded()
    with COMPANIES.open(newline="") as f:
        return [
            r for r in csv.DictReader(f) if (r["system"], r["slug"]) not in excluded
        ]


def plan_workday(
    slugs: list[str], last_full: dict[str, datetime], n: int
) -> dict[str, bool]:
    """Which Workday boards to read this run: True for a full read, False for
    a quick one. The n boards that have waited longest (never read in full
    first) get a full read. A board never read in full is skipped until its
    turn: its first full read records every job quietly, like any new board."""
    order = sorted(slugs, key=lambda s: (s in last_full, last_full.get(s), s))
    full = set(order[:n])
    return {s: s in full for s in slugs if s in full or s in last_full}


async def fetch_all(
    companies: list[dict], workday: dict[str, bool]
) -> list[feeds.BoardResult]:
    sem = asyncio.Semaphore(CONCURRENCY)
    now = store.utcnow()
    async with feeds.client() as c:
        return await asyncio.gather(
            *(
                feeds.fetch_workday(
                    c, sem, r["slug"], r.get("us_filter") or "", workday[r["slug"]], now
                )
                if r["system"] == "workday"
                else feeds.fetch_smartrecruiters(c, sem, r["slug"], now)
                if r["system"] == "smartrecruiters"
                else feeds.fetch_board(c, sem, r["system"], r["slug"], r["eu"] == "1")
                for r in companies
                if r["system"] != "workday" or r["slug"] in workday
            )
        )


async def fill_descriptions(postings: list[feeds.Posting]) -> None:
    sem = asyncio.Semaphore(CONCURRENCY)
    async with feeds.client() as c:
        await asyncio.gather(*(feeds.fetch_description(c, sem, p) for p in postings))


def job_row(c: Candidate, company: str, evidence, now, alerted) -> dict:
    p = c.posting
    f = classify.flags(p.description or "", p.title, company)
    return {
        "system": p.system,
        "slug": p.slug,
        "job_id": p.job_id,
        "company": company,
        "title": p.title,
        "url": p.url,
        "location": p.location,
        # Workday lists "3 Locations"; the job page gives the real country
        "is_us": c.is_us if c.is_us is not None else classify.is_us(None, p.country),
        "is_remote": c.is_remote,
        "role": c.role,
        "level": c.level,
        "min_years": classify.min_years(p.description or ""),
        "no_sponsorship": f.no_sponsorship,
        "citizens_only": f.citizens_only,
        "clearance": f.clearance,
        "evidence": evidence.as_dict() if evidence else None,
        "posted_at": p.posted_at,
        "first_seen_at": now,
        "alerted_at": now if alerted else None,
        "classifier_version": classify.VERSION,
    }


def company_info(companies: list[dict], evidence: EvidenceIndex):
    """name -> what the site's company panel shows: evidence for every role
    type across the company's boards, and links to its careers pages."""
    boards_by_company: dict[str, list[dict]] = {}
    for r in companies:
        boards_by_company.setdefault(r["company"], []).append(r)

    def info(name: str) -> dict:
        boards = boards_by_company.get(name, [])
        return {
            # Boards of one company share FEINs, so take the largest, not the sum
            "tech_filings": max(
                (int(b["tech_filings"] or 0) for b in boards), default=0
            ),
            "evidence": evidence.company([(b["system"], b["slug"]) for b in boards]),
            "careers": [
                feeds.careers_url(b["system"], b["slug"], b["eu"] == "1")
                for b in boards
            ],
        }

    return info


def publish_only() -> None:
    """Rebuild public/ from the database without fetching, e.g. after a
    change to the site or to what gets published."""
    conn = store.connect()
    jobs = store.open_jobs(conn)
    conn.close()
    health_file = PUBLIC / "health.json"
    health = (
        json.loads(health_file.read_text())
        if health_file.exists()
        else {"finished_at": store.utcnow().isoformat()}
    )
    publish(
        jobs,
        health,
        store.utcnow(),
        in_feeds=in_feeds,
        company_info=company_info(load_companies(), EvidenceIndex()),
    )


async def run(
    send_alerts: bool = True, workday_full: int = WORKDAY_FULL_PER_RUN
) -> dict:
    started = store.utcnow()
    t0 = time.monotonic()
    companies = load_companies()
    names = {(r["system"], r["slug"]): r["company"] for r in companies}
    conn = store.connect()
    store.ensure_schema(conn)
    plan = plan_workday(
        [r["slug"] for r in companies if r["system"] == "workday"],
        store.workday_last_full(conn),
        workday_full,
    )
    conn.close()
    results = await fetch_all(companies, plan)
    excluded = load_excluded()
    # Excluded boards count as fetched and empty, so their stored jobs close
    ok = {(r.system, r.slug) for r in results if r.ok} | excluded
    # Only a board read to the end can tell us a job is gone
    complete = {(r.system, r.slug) for r in results if r.ok and r.complete} | excluded
    failed = {(r.system, r.slug): r.error for r in results if not r.ok}
    postings = [p for r in results for p in r.postings]
    cands = {c.posting.key: c for p in postings if (c := to_candidate(p))}
    t_fetch = time.monotonic() - t0

    conn = store.connect()
    store.ensure_schema(conn)
    open_keys, closed_keys, known = store.load_state(conn)
    # Boards dropped from the watchlist are never fetched again, so close
    # their jobs as if the board were empty
    complete |= {k[:2] for k in open_keys} - names.keys()
    stale = store.stale_jobs(conn, classify.VERSION)
    conn.close()  # let the database sleep while we fetch descriptions
    ch = diff(open_keys, set(cands), complete, known, closed_keys)
    # New rules say the title isn't a tech role: close it now, even on a board
    # that was only partly read this run
    ch.closed |= {k for k, title in stale.items() if classify.role(title) is None}

    new = [cands[k] for k in sorted(ch.new)]
    # Still open and still a candidate, but classified by older rules
    refresh = [cands[k] for k in sorted(stale.keys() & set(cands))]
    await fill_descriptions([c.posting for c in new + refresh])
    evidence = EvidenceIndex()
    now = store.utcnow()
    rows = [
        job_row(
            c,
            names[c.posting.key[:2]],
            evidence.lookup(c.posting.system, c.posting.slug, c.role),
            now,
            alerted=c.posting.key in ch.silent
            or not should_alert(c.posting.posted_at, now),
        )
        for c in new
    ] + [
        # alerted_at None keeps whatever the job already had (coalesce in store)
        job_row(
            c,
            names[c.posting.key[:2]],
            evidence.lookup(c.posting.system, c.posting.slug, c.role),
            now,
            alerted=False,
        )
        for c in refresh
    ]

    stats = {
        "started_at": started,
        "finished_at": store.utcnow(),
        "boards_ok": len(ok),
        "boards_failed": len(failed),
        "postings": len(postings),
        "candidates": len(cands),
        "new_jobs": len(new),
        "closed_jobs": len(ch.closed),
    }
    conn = store.connect()
    full_workday = {k for k in complete if k[0] == "workday"}
    store.save_run(conn, rows, ch.closed, ok, failed, stats, now, full_workday)

    sent = 0
    webhook = os.environ.get("DISCORD_WEBHOOK")
    if send_alerts and webhook:
        pending = store.pending_alerts(conn)
        wanted = [j for j in pending if DISCORD_ALERTS.matches(j)]
        async with feeds.client() as c:
            delivered = await notify.send_discord(c, webhook, wanted)
            if len(failed) > FAILED_SHARE_WARNING * len(companies):
                await c.post(
                    webhook,
                    json={
                        "content": (
                            f"FiledFor warning: {len(failed)} of {len(companies)} boards failed this run."
                        )
                    },
                )
        wanted_keys = {(j["system"], j["slug"], j["job_id"]) for j in wanted}
        delivered_keys = {(j["system"], j["slug"], j["job_id"]) for j in delivered}
        # Jobs outside the filter are done; wanted jobs only once Discord took them
        done = [
            (j["system"], j["slug"], j["job_id"])
            for j in pending
            if (j["system"], j["slug"], j["job_id"]) not in wanted_keys
        ] + list(delivered_keys)
        store.mark_alerted(conn, done, store.utcnow())
        sent = len(delivered)
        store.record_alerts(conn, sent)

    pruned = store.prune(conn, now)
    jobs = store.open_jobs(conn)
    stale = [
        f"{r['system']}/{r['slug']}"
        for r in conn.execute(
            "select system, slug from boards where consecutive_failures >= 48 order by 1, 2"
        )
    ]
    conn.close()

    health = {
        "finished_at": store.utcnow().isoformat(),
        "seconds": round(time.monotonic() - t0, 1),
        "fetch_seconds": round(t_fetch, 1),
        **{k: v for k, v in stats.items() if k not in ("started_at", "finished_at")},
        "alerts_sent": sent,
        "pruned": pruned,
        "open_jobs": len(jobs),
        "workday_full": len(full_workday),
        "workday_quick": sum(1 for full in plan.values() if not full),
        "failing_now": sorted(f"{s}/{g}: {e}" for (s, g), e in failed.items()),
        "failing_over_a_day": stale,
    }
    publish(
        jobs,
        health,
        store.utcnow(),
        in_feeds=in_feeds,
        company_info=company_info(companies, evidence),
    )
    return health


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    if "--publish-only" in sys.argv:
        publish_only()
        return
    workday_full = WORKDAY_FULL_PER_RUN
    if "--workday-full" in sys.argv:
        workday_full = int(sys.argv[sys.argv.index("--workday-full") + 1])
    health = asyncio.run(
        run(send_alerts="--no-alerts" not in sys.argv, workday_full=workday_full)
    )
    for k in (
        "seconds",
        "fetch_seconds",
        "boards_ok",
        "boards_failed",
        "postings",
        "candidates",
        "new_jobs",
        "closed_jobs",
        "alerts_sent",
        "open_jobs",
    ):
        print(f"{k:<14} {health[k]:>8}")


if __name__ == "__main__":
    main()
