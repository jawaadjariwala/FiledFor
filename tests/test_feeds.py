"""Adapters against saved, trimmed responses from real boards (tests/fixtures)."""

import asyncio
import json
from pathlib import Path

import httpx

from filedfor.feeds import (
    fetch_board,
    fetch_description,
    html_to_text,
    parse_ashby,
    parse_greenhouse,
    parse_lever,
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
