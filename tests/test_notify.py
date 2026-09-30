import asyncio

import httpx

from filedfor.notify import embed, send_discord

REAL_SLEEP = asyncio.sleep


def job(n, evidence=True):
    return {
        "title": f"Software Engineer {n}",
        "url": f"https://x/{n}",
        "company": "Acme",
        "location": "New York, NY",
        "role": "swe",
        "min_years": 1,
        "evidence": {
            "filings": 40,
            "new_hire_filings": 10,
            "level_1": 4,
            "level_2": 16,
            "level_3": 15,
            "level_4": 5,
            "median_wage": 175000,
            "last_decision": "2026-03-01",
        }
        if evidence
        else None,
    }


def run(handler, jobs):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await send_discord(c, "https://discord/webhook", jobs)

    return asyncio.run(go())


def test_embed_shows_evidence():
    d = embed(job(1))["description"]
    assert (
        "40 Software H-1B filings" in d
        and "50% at wage level I-II" in d
        and "$175K" in d
    )
    assert "No H-1B filings" in embed(job(2, evidence=False))["description"]


def test_batches_of_ten():
    sizes = []

    def handler(req):
        import json

        sizes.append(len(json.loads(req.content)["embeds"]))
        return httpx.Response(204)

    assert len(run(handler, [job(i) for i in range(23)])) == 23
    assert sizes == [10, 10, 3]


def test_failure_leaves_rest_pending():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(204) if len(calls) == 1 else httpx.Response(500)

    sent = run(handler, [job(i) for i in range(15)])
    assert len(sent) == 10  # second batch failed, so it isn't marked alerted


def test_rate_limit_waits_and_retries(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda s: REAL_SLEEP(0))
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"retry_after": 0.5})
        return httpx.Response(204)

    assert len(run(handler, [job(1)])) == 1 and len(calls) == 2
