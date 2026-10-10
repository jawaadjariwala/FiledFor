"""Everything GitHub Pages serves: the site, jobs.json, companies.json,
health.json and RSS.

public/ is rebuilt on every run and never committed. The site itself is
static files in site/, copied as they are; the browser reads the JSON.
"""

import csv
import json
import os
import shutil
from collections.abc import Callable
from datetime import datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from filedfor.classify import FIELDS, places
from filedfor.notify import ROLE_NAMES, evidence_line

PUBLIC = Path("public")
SITE = Path("site")
DOMAINS = Path("data/domains.csv")  # company -> website, for logos in site/logos
NOTICES = Path("data/employer_notices.csv")  # hand-kept: DOL actions against employers
SITE_URL = os.environ.get("SITE_URL", "https://filedfor.com/")
MAX_AGE = timedelta(days=30)  # older postings are mostly evergreen or filled
RECENT = timedelta(days=7)  # jobs-recent.json, loaded first; the rest on demand
# What the site needs per job. Filing evidence lives once per company in
# companies.json, and blocked jobs are never published, so their flags aren't either
SITE_KEYS = (
    "system",
    "slug",
    "job_id",
    "company",
    "title",
    "url",
    "location",
    "is_remote",
    "role",
    "level",
    "min_years",
    "posted_at",
    "first_seen_at",
)
FEED_ITEMS = 50
FEEDS = {"all": None} | {f: f for f in FIELDS}


def fresh(job: dict, now: datetime) -> bool:
    return job["posted_at"] is None or now - job["posted_at"] <= MAX_AGE


def listed(job: dict) -> bool:
    """On the site: every level, in the US (or unknown), open to people who
    need sponsorship. Interns and senior roles alike; the site filters."""
    if job["is_us"] is False:  # Workday multi-location jobs, checked on the job page
        return False
    return not (job["no_sponsorship"] or job["citizens_only"] or job["clearance"])


def dedupe(jobs: list[dict]) -> list[dict]:
    """One listing per company, title and location. Companies often post the
    same opening several times (one per requisition or recruiter); the most
    recently posted one is kept."""
    best: dict[tuple, dict] = {}
    for j in jobs:
        k = (
            j["company"],
            j["title"].strip().lower(),
            (j["location"] or "").strip().lower(),
        )
        when = j["posted_at"] or j["first_seen_at"]
        if k not in best or when > (best[k]["posted_at"] or best[k]["first_seen_at"]):
            best[k] = j
    kept = {id(j) for j in best.values()}
    return [j for j in jobs if id(j) in kept]


def _plain(job: dict) -> dict:
    out = {
        k: (v.isoformat() if hasattr(v, "isoformat") else v)
        for k, v in job.items()
        if k in SITE_KEYS
    }
    out["states"], out["metros"] = places(job["location"])
    return out


def notices() -> dict[str, dict]:
    """company -> its notice (e.g. a PERM suspension), with date and source."""
    if not NOTICES.exists():
        return {}
    with NOTICES.open(newline="") as f:
        return {r.pop("company"): r for r in csv.DictReader(f)}


def logos() -> dict[str, str]:
    """company -> logo path on the site, for companies whose logo was saved
    (python -m filedfor.logos)."""
    if not DOMAINS.exists():
        return {}
    with DOMAINS.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return {
        r["company"]: f"logos/{r['domain']}.png"
        for r in rows
        if (SITE / "logos" / f"{r['domain']}.png").exists()
    }


def sitemap(now: datetime) -> str:
    """One page for now; company pages will add more. lastmod tells search
    engines the list changes every run."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{escape(SITE_URL)}</loc><lastmod>{now.date().isoformat()}</lastmod>"
        "<changefreq>hourly</changefreq></url></urlset>\n"
    )


def rss(jobs: list[dict], role: str | None, now: datetime) -> str:
    """Newest jobs first. The posting URL is the guid, so readers never repeat one."""
    picked = [j for j in jobs if role is None or j["role"] == role]
    picked.sort(key=lambda j: j["posted_at"] or j["first_seen_at"], reverse=True)
    label = "all fields" if role is None else ROLE_NAMES[role]
    items = []
    for j in picked[:FEED_ITEMS]:
        when = j["posted_at"] or j["first_seen_at"]
        where = j.get("location") or "Location not listed"
        items.append(
            "<item>"
            f"<title>{escape(j['title'])} at {escape(j['company'])}</title>"
            f"<link>{escape(j['url'])}</link>"
            f'<guid isPermaLink="true">{escape(j["url"])}</guid>'
            f"<pubDate>{format_datetime(when)}</pubDate>"
            f"<description>{escape(where)}. {escape(evidence_line(j))}.</description>"
            "</item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        f"<title>FiledFor: new-grad jobs, {escape(label)}</title>"
        f"<link>{escape(SITE_URL)}</link>"
        "<description>New-grad tech jobs at companies with H-1B filings for that "
        "kind of role, from Department of Labor data.</description>"
        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>"
        f"{''.join(items)}</channel></rss>\n"
    )


def publish(
    jobs: list[dict],
    health: dict,
    now: datetime,
    in_feeds: Callable[[dict], bool],
    company_info: Callable[[str], dict] = lambda name: {},
) -> None:
    """Write public/: site files, jobs-recent.json (last 7 days) and
    jobs-older.json (8 to 30 days), companies.json (`company_info` for each
    company with a listed job), health.json, and one RSS feed per field with
    the jobs `in_feeds` accepts."""
    if SITE.exists():
        shutil.copytree(SITE, PUBLIC, dirs_exist_ok=True)
    PUBLIC.mkdir(exist_ok=True)
    keep = dedupe([j for j in jobs if fresh(j, now) and listed(j)])
    recent = [j for j in keep if now - (j["posted_at"] or j["first_seen_at"]) <= RECENT]
    older = [j for j in keep if now - (j["posted_at"] or j["first_seen_at"]) > RECENT]
    totals = {"total": len(keep), "companies": len({j["company"] for j in keep})}
    for name, part in (("jobs-recent.json", recent), ("jobs-older.json", older)):
        (PUBLIC / name).write_text(
            json.dumps(
                {
                    "updated": health["finished_at"],
                    **totals,
                    "jobs": [_plain(j) for j in part],
                },
                separators=(",", ":"),
            )
        )
    (PUBLIC / "jobs.json").unlink(missing_ok=True)  # replaced by the two files above
    logo, notice = logos(), notices()
    companies = {
        name: company_info(name)
        | ({"logo": logo[name]} if name in logo else {})
        | ({"notice": notice[name]} if name in notice else {})
        for name in sorted({j["company"] for j in keep})
    }
    (PUBLIC / "companies.json").write_text(json.dumps(companies, separators=(",", ":")))
    (PUBLIC / "health.json").write_text(json.dumps(health, indent=1))
    (PUBLIC / "sitemap.xml").write_text(sitemap(now))
    feed_jobs = [j for j in keep if in_feeds(j)]
    (PUBLIC / "feeds").mkdir(exist_ok=True)
    for name, role in FEEDS.items():
        (PUBLIC / "feeds" / f"{name}.xml").write_text(rss(feed_jobs, role, now))
