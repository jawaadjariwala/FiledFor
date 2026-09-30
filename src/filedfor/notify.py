"""Send job alerts. Discord now; web push for the FiledFor app in Phase 2.

A job is marked alerted only after Discord accepts the message, so a failed
send is retried on the next run (ADR-002, decision 4).
"""

import asyncio
from collections.abc import Sequence

import httpx

PER_MESSAGE = 10  # Discord's limit on embeds per message
ROLE_NAMES = {"ai": "AI/ML", "swe": "Software", "data": "Data"}


def _money(n: int | None) -> str:
    return f"${n / 1000:.0f}K" if n else "n/a"


def embed(job: dict) -> dict:
    """One job as a Discord card: title links to the posting, evidence below."""
    ev = job.get("evidence") or {}
    lines = [f"**{job['company']}** · {job.get('location') or 'Location not listed'}"]
    if ev:
        levels = ev["level_1"] + ev["level_2"] + ev["level_3"] + ev["level_4"]
        low = ev["level_1"] + ev["level_2"]
        share = f", {low * 100 // levels}% at wage level I-II" if levels else ""
        lines.append(
            f"{ev['filings']} {ROLE_NAMES[job['role']]} H-1B filings "
            f"({ev['new_hire_filings']} new hires{share}), median {_money(ev['median_wage'])}"
        )
    else:
        lines.append("No H-1B filings found for this role type")
    if job.get("min_years") is not None:
        lines.append(f"Asks for {job['min_years']}+ years")
    return {
        "title": job["title"][:250],
        "url": job["url"],
        "description": "\n".join(lines),
        "color": 0x2F7D5B if ev else 0x8A8F98,
    }


async def send_discord(
    client: httpx.AsyncClient, webhook: str, jobs: Sequence[dict]
) -> list[dict]:
    """Post jobs in batches of 10. Returns the jobs Discord accepted."""
    sent = []
    for i in range(0, len(jobs), PER_MESSAGE):
        batch = jobs[i : i + PER_MESSAGE]
        for _ in range(3):
            r = await client.post(webhook, json={"embeds": [embed(j) for j in batch]})
            if r.status_code == 429:  # rate limited: Discord says how long to wait
                await asyncio.sleep(float(r.json().get("retry_after", 1)))
                continue
            break
        if r.status_code >= 300:
            break  # stop here; the rest stay pending for the next run
        sent.extend(batch)
    return sent
