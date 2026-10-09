import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

from filedfor import publish

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


def job(**kw) -> dict:
    base = {
        "system": "greenhouse",
        "slug": "acme",
        "job_id": "1",
        "company": "Acme & Sons",
        "title": "Software Engineer, New Grad <2026>",
        "url": "https://example.com/jobs/1?a=1&b=2",
        "location": "New York, NY",
        "is_us": True,
        "is_remote": False,
        "role": "swe",
        "level": "entry",
        "min_years": None,
        "no_sponsorship": False,
        "citizens_only": False,
        "clearance": False,
        "evidence": None,
        "posted_at": NOW - timedelta(hours=2),
        "first_seen_at": NOW - timedelta(hours=1),
        "alerted_at": None,
        "classifier_version": 3,
        "closed_at": None,
    }
    return base | kw


def test_listed_keeps_entry_intern_and_low_year_unclear():
    assert not publish.listed(job(citizens_only=True))
    assert not publish.listed(job(no_sponsorship=True))
    assert not publish.listed(job(clearance=True))
    assert publish.listed(job())
    assert publish.listed(job(level="intern"))
    assert publish.listed(job(level="unclear", min_years=None))
    assert publish.listed(job(level="unclear", min_years=2))
    assert not publish.listed(job(level="unclear", min_years=3))


def test_rss_is_valid_xml_and_escapes_text():
    xml = publish.rss([job()], None, NOW)
    item = ET.fromstring(xml).find("channel/item")
    assert item.findtext("title") == "Software Engineer, New Grad <2026> at Acme & Sons"
    assert item.findtext("link") == "https://example.com/jobs/1?a=1&b=2"
    assert "No H-1B filings found" in item.findtext("description")


def test_rss_filters_by_role_newest_first_and_caps_items():
    jobs = [
        job(job_id=str(i), url=f"https://x/{i}", posted_at=NOW - timedelta(hours=i))
        for i in range(publish.FEED_ITEMS + 5)
    ] + [job(job_id="ai", url="https://x/ai", role="ai")]
    swe = ET.fromstring(publish.rss(jobs, "swe", NOW)).findall("channel/item")
    assert len(swe) == publish.FEED_ITEMS
    assert swe[0].findtext("link") == "https://x/0"
    ai = ET.fromstring(publish.rss(jobs, "ai", NOW)).findall("channel/item")
    assert [i.findtext("link") for i in ai] == ["https://x/ai"]


def test_publish_writes_site_json_and_feeds(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<p>hi</p>")
    monkeypatch.setattr(publish, "SITE", site)
    monkeypatch.setattr(publish, "PUBLIC", tmp_path / "public")
    jobs = [
        job(),
        job(job_id="old", posted_at=NOW - timedelta(days=40)),
        job(job_id="blocked", url="https://x/blocked", citizens_only=True),
    ]
    monkeypatch.setattr(publish, "DOMAINS", tmp_path / "domains.csv")
    (tmp_path / "domains.csv").write_text("company,domain\nAcme & Sons,acme.com\n")
    (site / "logos").mkdir()
    (site / "logos" / "acme.com.png").write_bytes(b"png")
    publish.publish(
        jobs,
        {"finished_at": NOW.isoformat()},
        NOW,
        in_feeds=lambda j: not j["citizens_only"],
        company_info=lambda name: {"tech_filings": 7},
    )
    out = tmp_path / "public"
    assert (out / "index.html").read_text() == "<p>hi</p>"
    data = json.loads((out / "jobs.json").read_text())
    assert [j["job_id"] for j in data["jobs"]] == ["1"]  # old and blocked jobs left out
    assert "alerted_at" not in data["jobs"][0]
    assert data["jobs"][0]["states"] == ["NY"] and data["jobs"][0]["metros"] == [
        "New York"
    ]
    assert data["jobs"][0]["first_seen_at"]  # the site's "new since your last visit"
    companies = json.loads((out / "companies.json").read_text())
    assert companies == {
        "Acme & Sons": {"tech_filings": 7, "logo": "logos/acme.com.png"}
    }
    loc = ET.parse(out / "sitemap.xml").getroot()[0][0].text
    assert loc == publish.SITE_URL
    items = ET.parse(out / "feeds" / "all.xml").findall("channel/item")
    assert [i.findtext("guid") for i in items] == ["https://example.com/jobs/1?a=1&b=2"]


def test_dedupe_keeps_the_newest_of_identical_listings():
    a = job(job_id="a", posted_at=NOW - timedelta(days=2))
    b = job(job_id="b", posted_at=NOW - timedelta(hours=1), location="new york, ny ")
    other_city = job(job_id="c", location="Austin, TX")
    assert [j["job_id"] for j in publish.dedupe([a, b, other_city])] == ["b", "c"]
