"""One poller run: fetch, classify, diff, enrich, store, alert, publish.

    uv run python -m filedfor.poll              full run
    uv run python -m filedfor.poll --no-alerts  store and publish, send nothing

Needs DATABASE_URL, and DISCORD_WEBHOOK for alerts (both from .env locally,
GitHub secrets in Actions). Design and trade-offs: ADR-002.
"""

import asyncio
import csv
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from filedfor import classify, feeds, notify, store
from filedfor.diff import diff, should_alert
from filedfor.evidence import COMPANIES, EvidenceIndex

PUBLIC = Path("public")  # published to GitHub Pages, not committed
CONCURRENCY = 8
FAILED_SHARE_WARNING = 0.10


@dataclass(frozen=True)
class AlertFilter:
    roles: frozenset[str] = frozenset({"ai", "swe", "data"})
    levels: frozenset[str] = frozenset({"entry"})
    max_years: int = 2  # 'unclear' titles pass when the description asks for <= this
    include_unknown_country: bool = True  # "Remote" with no country stated

    def matches(self, job: dict) -> bool:
        if job["role"] not in self.roles:
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


PERSONAL = AlertFilter()


@dataclass
class Candidate:
    posting: feeds.Posting
    role: str
    level: str
    is_us: bool | None
    is_remote: bool
    extra: dict = field(default_factory=dict)


def to_candidate(p: feeds.Posting) -> Candidate | None:
    """Tech role, not clearly senior, not clearly outside the US."""
    role = classify.role(p.title)
    if role is None:
        return None
    level = classify.level(p.title)
    if level == "experienced":
        return None
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


async def fetch_all(companies: list[dict]) -> list[feeds.BoardResult]:
    sem = asyncio.Semaphore(CONCURRENCY)
    async with feeds.client() as c:
        return await asyncio.gather(
            *(
                feeds.fetch_board(c, sem, r["system"], r["slug"], r["eu"] == "1")
                for r in companies
            )
        )


async def fill_descriptions(postings: list[feeds.Posting]) -> None:
    sem = asyncio.Semaphore(CONCURRENCY)
    async with feeds.client() as c:
        await asyncio.gather(*(feeds.fetch_description(c, sem, p) for p in postings))


def job_row(c: Candidate, company: str, evidence, now, alerted) -> dict:
    p = c.posting
    f = classify.flags(p.description or "")
    return {
        "system": p.system,
        "slug": p.slug,
        "job_id": p.job_id,
        "company": company,
        "title": p.title,
        "url": p.url,
        "location": p.location,
        "is_us": c.is_us,
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


def publish(jobs: list[dict], health: dict) -> None:
    """jobs.json for the site: open jobs a new grad could apply to."""
    PUBLIC.mkdir(exist_ok=True)
    keep = [
        {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in j.items()}
        for j in jobs
        if j["level"] in ("entry", "intern")
        or (j["level"] == "unclear" and (j["min_years"] is None or j["min_years"] <= 2))
    ]
    for j in keep:
        for k in ("alerted_at", "classifier_version", "first_seen_at"):
            j.pop(k, None)
    (PUBLIC / "jobs.json").write_text(
        json.dumps({"updated": health["finished_at"], "jobs": keep})
    )
    (PUBLIC / "health.json").write_text(json.dumps(health, indent=1))


async def run(send_alerts: bool = True) -> dict:
    started = store.utcnow()
    t0 = time.monotonic()
    companies = load_companies()
    names = {(r["system"], r["slug"]): r["company"] for r in companies}
    results = await fetch_all(companies)
    # Excluded boards count as fetched and empty, so their stored jobs close
    ok = {(r.system, r.slug) for r in results if r.ok} | load_excluded()
    failed = {(r.system, r.slug): r.error for r in results if not r.ok}
    postings = [p for r in results for p in r.postings]
    cands = {c.posting.key: c for p in postings if (c := to_candidate(p))}
    t_fetch = time.monotonic() - t0

    conn = store.connect()
    store.ensure_schema(conn)
    open_keys, closed_keys, known = store.load_state(conn)
    conn.close()  # let the database sleep while we fetch descriptions
    ch = diff(open_keys, set(cands), ok, known, closed_keys)

    new = [cands[k] for k in sorted(ch.new)]
    await fill_descriptions([c.posting for c in new])
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
    store.save_run(conn, rows, ch.closed, ok, failed, stats, now)

    sent = 0
    webhook = os.environ.get("DISCORD_WEBHOOK")
    if send_alerts and webhook:
        pending = store.pending_alerts(conn)
        wanted = [j for j in pending if PERSONAL.matches(j)]
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
        "failing_now": sorted(f"{s}/{g}: {e}" for (s, g), e in failed.items()),
        "failing_over_a_day": stale,
    }
    publish(jobs, health)
    return health


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    health = asyncio.run(run(send_alerts="--no-alerts" not in sys.argv))
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
