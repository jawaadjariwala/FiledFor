"""Adapters against saved, trimmed responses from real boards (tests/fixtures)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from filedfor.feeds import (
    OLD,
    fetch_board,
    fetch_description,
    fetch_smartrecruiters,
    fetch_workday,
    html_to_text,
    parse_ashby,
    parse_greenhouse,
    parse_lever,
    parse_smartrecruiters,
    parse_workday,
    workday_days,
    workday_detail_url,
)

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_greenhouse_board_has_no_descriptions():
    p = parse_greenhouse("vercel", load("greenhouse_board.json"))[0]
    assert (p.system, p.slug) == ("greenhouse", "vercel")
    assert p.job_id.isdigit() and p.title and p.url.startswith("https://")
    assert p.location and p.country is None
    assert p.posted_at.tzinfo is not None
    assert p.description is None  # fetched later, only for new candidates


def test_lever_fields():
    p = parse_lever("voltus", load("lever_board.json"))[0]
    assert p.country == "US" and p.workplace == "remote"
    assert p.posted_at.year >= 2020  # createdAt is in milliseconds
    assert "Voltus" in p.description


def test_ashby_fields():
    p = parse_ashby("gimlet", load("ashby_board.json"))[0]
    assert p.country == "United States" and p.workplace is False
    assert p.description


def test_ashby_skips_unlisted_jobs():
    body = {"jobs": [{"id": "a", "title": "Hidden", "isListed": False}]}
    assert parse_ashby("x", body) == []


def test_missing_fields_do_not_crash():
    assert parse_greenhouse("x", {"jobs": [{"id": 1}]})[0].location is None
    p = parse_lever("x", [{"id": "a"}])[0]
    assert p.posted_at is None and p.description == ""
    assert parse_ashby("x", {"jobs": [{"id": "a"}]})[0].posted_at is None


def test_html_to_text_handles_double_escaping():
    assert html_to_text("&amp;lt;p&amp;gt;Hi&amp;lt;/p&amp;gt;") == "Hi"
    assert "- one" in html_to_text("<ul><li>one</li></ul>")


def run(handler, coro_factory):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await coro_factory(c, asyncio.Semaphore(2))

    return asyncio.run(go())


REAL_SLEEP = asyncio.sleep


def no_wait(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda s: REAL_SLEEP(0))


def test_failed_board_is_isolated(monkeypatch):
    no_wait(monkeypatch)
    res = run(
        lambda req: httpx.Response(404),
        lambda c, s: fetch_board(c, s, "lever", "gone", False),
    )
    assert not res.ok and res.postings == [] and "404" in res.error


def test_retries_on_server_errors(monkeypatch):
    no_wait(monkeypatch)
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, json=[])

    res = run(handler, lambda c, s: fetch_board(c, s, "lever", "flaky", False))
    assert res.ok and len(calls) == 3


def test_greenhouse_description_fetched_per_job():
    p = parse_greenhouse("vercel", load("greenhouse_board.json"))[0]
    run(
        lambda req: httpx.Response(200, json=load("greenhouse_job.json")),
        lambda c, s: fetch_description(c, s, p),
    )
    assert p.description and "Vercel" in p.description


# Workday (ADR-003)

WD = "nvidia.wd5/NVIDIAExternalCareerSite"
NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


def test_workday_days():
    assert workday_days("Posted Today") == 0
    assert workday_days("Posted Yesterday") == 1
    assert workday_days("Posted 7 Days Ago") == 7
    assert workday_days("Posted 30+ Days Ago") == OLD
    assert workday_days(None) is None


def test_workday_page_parses_and_drops_old_jobs():
    ps = parse_workday(WD, load("workday_page.json"), NOW)
    assert len(ps) == 4  # the "30+ Days Ago" posting is left out
    p = ps[0]
    assert (p.system, p.slug) == ("workday", WD)
    assert (
        p.url
        == f"https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite{load('workday_page.json')['jobPostings'][0]['externalPath']}"
    )
    assert p.job_id and "/" not in p.job_id
    assert p.posted_at == NOW and p.country is None and p.description is None
    assert (
        parse_workday(WD, load("workday_page.json"), NOW, us_only=True)[0].country
        == "US"
    )


def test_workday_detail_url_adds_api_prefix():
    p = parse_workday(WD, load("workday_page.json"), NOW)[0]
    assert workday_detail_url(p).startswith(
        "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/"
    )


def test_workday_description_and_country_from_job_page():
    p = parse_workday(WD, load("workday_page.json"), NOW)[0]
    run(
        lambda req: httpx.Response(200, json=load("workday_job.json")),
        lambda c, s: fetch_description(c, s, p),
    )
    assert p.description and "NVIDIA" in p.description and p.country == "US"


def pages(*posted, total=None):
    """A fake Workday board: one page of 20 jobs per postedOn string."""
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        i = body["offset"] // 20
        jobs = (
            [
                {
                    "title": f"Job {i}-{n}",
                    "externalPath": f"/job/x/J{i}_{n}",
                    "postedOn": posted[i],
                }
                for n in range(20)
            ]
            if i < len(posted)
            else []
        )
        return httpx.Response(
            200, json={"total": total or 20 * len(posted), "jobPostings": jobs}
        )

    return handler, seen


def test_workday_quick_read_stops_after_today_and_yesterday():
    handler, seen = pages(
        "Posted Today", "Posted Yesterday", "Posted 3 Days Ago", "Posted 9 Days Ago"
    )
    res = run(handler, lambda c, s: fetch_workday(c, s, WD, "", full=False, now=NOW))
    assert res.ok and not res.complete
    assert len(seen) == 3  # the third page has nothing newer than 2 days, so stop
    assert len(res.postings) == 60


def test_workday_full_read_stops_at_first_all_old_page():
    handler, seen = pages(
        "Posted Today",
        "Posted 12 Days Ago",
        "Posted 30+ Days Ago",
        "Posted 30+ Days Ago",
    )
    res = run(
        handler,
        lambda c, s: fetch_workday(c, s, WD, "locationCountry=us1", full=True, now=NOW),
    )
    assert res.ok and res.complete and len(seen) == 3
    assert seen[0]["appliedFacets"] == {"locationCountry": ["us1"]}
    assert len(res.postings) == 40 and all(p.country == "US" for p in res.postings)


def test_workday_stops_at_paging_limit():
    handler, seen = pages(*["Posted Today"] * 150, total=3000)
    res = run(handler, lambda c, s: fetch_workday(c, s, WD, "", full=True, now=NOW))
    assert res.complete and len(seen) == 100  # Workday won't page past 2,000


def test_workday_failure_is_isolated(monkeypatch):
    no_wait(monkeypatch)
    res = run(
        lambda req: httpx.Response(422), lambda c, s: fetch_workday(c, s, WD, "", True)
    )
    assert not res.ok and "422" in res.error


def test_smartrecruiters_fields():
    p = parse_smartrecruiters("servicenow", load("smartrecruiters_board.json"))[0]
    assert (p.system, p.slug) == ("smartrecruiters", "servicenow")
    assert p.url == f"https://jobs.smartrecruiters.com/servicenow/{p.job_id}"
    assert p.country == "us" and p.posted_at.tzinfo is not None
    assert p.description is None and isinstance(p.workplace, bool)


def test_smartrecruiters_description_joins_sections():
    p = parse_smartrecruiters("servicenow", load("smartrecruiters_board.json"))[0]
    run(
        lambda req: httpx.Response(200, json=load("smartrecruiters_job.json")),
        lambda c, s: fetch_description(c, s, p),
    )
    assert p.description and "ServiceNow" in p.description


def test_smartrecruiters_pages_until_jobs_are_a_month_old():
    seen = []

    def handler(req):
        offset = int(req.url.params["offset"])
        seen.append(offset)
        ages = {0: [1, 5], 2: [20, 40], 4: [50, 60]}[offset]
        content = [
            {
                "id": f"{offset}{a}",
                "name": "Engineer",
                "releasedDate": (NOW - timedelta(days=a)).isoformat(),
            }
            for a in ages
        ]
        return httpx.Response(200, json={"totalFound": 300, "content": content})

    res = run(handler, lambda c, s: fetch_smartrecruiters(c, s, "acme", NOW))
    assert res.ok and res.complete and seen == [0, 2]  # offset moves by jobs returned
    assert [p.job_id for p in res.postings] == ["01", "05", "220"]


def test_rate_limits_wait_longer_than_timeouts():
    from filedfor.feeds import retry_wait

    assert retry_wait(None, 1) == 2
    assert retry_wait(httpx.Response(429), 1) == 10
    assert retry_wait(httpx.Response(429, headers={"Retry-After": "7"}), 0) == 7
    assert retry_wait(httpx.Response(429, headers={"Retry-After": "600"}), 0) == 30
