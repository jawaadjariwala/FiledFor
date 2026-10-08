"""Everything GitHub Pages serves: the site, jobs.json, health.json and RSS.

public/ is rebuilt on every run and never committed. The site itself is
static files in site/, copied as they are; the browser reads jobs.json.
"""

import json
import os
import shutil
from collections.abc import Callable
from datetime import datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from filedfor.notify import ROLE_NAMES, evidence_line

PUBLIC = Path("public")
SITE = Path("site")
SITE_URL = os.environ.get("SITE_URL", "https://jawaadjariwala.github.io/FiledFor/")
MAX_AGE = timedelta(days=30)  # older postings are mostly evergreen or filled
FEED_ITEMS = 50
FEEDS = {"all": None, "swe": "swe", "ai": "ai", "data": "data"}


def fresh(job: dict, now: datetime) -> bool:
    return job["posted_at"] is None or now - job["posted_at"] <= MAX_AGE


def listed(job: dict) -> bool:
    """On the site: US (or unknown) jobs open to people who need sponsorship,
    that are entry level, internships, or level unclear but asking for 2 years
    or less (or not saying)."""
    if job["is_us"] is False:  # Workday multi-location jobs, checked on the job page
        return False
    if job["no_sponsorship"] or job["citizens_only"] or job["clearance"]:
        return False
    return job["level"] in ("entry", "intern") or (
        job["level"] == "unclear"
        and (job["min_years"] is None or job["min_years"] <= 2)
    )


def _plain(job: dict) -> dict:
    out = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in job.items()}
    for k in ("alerted_at", "classifier_version", "first_seen_at"):
        out.pop(k, None)
    return out


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
    jobs: list[dict], health: dict, now: datetime, in_feeds: Callable[[dict], bool]
) -> None:
    """Write public/: site files, jobs.json for the site, health.json, and one
    RSS feed per field with the jobs `in_feeds` accepts."""
    if SITE.exists():
        shutil.copytree(SITE, PUBLIC, dirs_exist_ok=True)
    PUBLIC.mkdir(exist_ok=True)
    keep = [j for j in jobs if fresh(j, now) and listed(j)]
    (PUBLIC / "jobs.json").write_text(
        json.dumps(
            {"updated": health["finished_at"], "jobs": [_plain(j) for j in keep]}
        )
    )
    (PUBLIC / "health.json").write_text(json.dumps(health, indent=1))
    feed_jobs = [j for j in keep if in_feeds(j)]
    (PUBLIC / "feeds").mkdir(exist_ok=True)
    for name, role in FEEDS.items():
        (PUBLIC / "feeds" / f"{name}.xml").write_text(rss(feed_jobs, role, now))
