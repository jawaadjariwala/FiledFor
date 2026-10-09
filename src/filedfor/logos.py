"""Download company logos into site/logos, once, so visitors' browsers never
call a third-party logo service.

    uv run python -m filedfor.logos            fetch logos not saved yet
    uv run python -m filedfor.logos --sheet    also write logos.html, a contact
                                               sheet for checking them by eye

data/domains.csv maps a company name (as on the site) to its website. A
company without a row, or whose icon is missing or too small, keeps the
lettered badge the site draws.
"""

import asyncio
import csv
import html
import struct
import sys
from pathlib import Path

import httpx

DOMAINS = Path("data/domains.csv")
OUT = Path("site/logos")
SOURCE = "https://www.google.com/s2/favicons?domain={domain}&sz=128"
MIN_SIZE = 32  # pixels; smaller icons look blurry at the site's 44px


def png_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
        return None
    return struct.unpack(">II", data[16:24])


async def fetch(client: httpx.AsyncClient, sem: asyncio.Semaphore, domain: str) -> str:
    path = OUT / f"{domain}.png"
    if path.exists():
        return "kept"
    async with sem:
        try:
            r = await client.get(SOURCE.format(domain=domain))
        except httpx.TransportError:
            return "error"
    size = png_size(r.content)
    if r.status_code != 200 or not size:
        return "missing"
    if min(size) < MIN_SIZE:
        return "too small"
    path.write_bytes(r.content)
    return "saved"


def contact_sheet(rows: list[dict]) -> None:
    cells = "".join(
        f'<figure><img src="site/logos/{html.escape(r["domain"])}.png"><figcaption>'
        f"{html.escape(r['company'])}<br><small>{html.escape(r['domain'])}</small></figcaption></figure>"
        for r in rows
        if (OUT / f"{r['domain']}.png").exists()
    )
    Path("logos.html").write_text(
        "<!doctype html><meta charset=utf-8><style>body{font:11px system-ui;display:grid;"
        "grid-template-columns:repeat(8,1fr);gap:8px;margin:8px}figure{margin:0;text-align:center}"
        "img{width:44px;height:44px;object-fit:contain;border:1px solid #ddd;border-radius:8px;padding:4px}"
        "small{color:#888}</style>" + cells
    )


async def fetch_all(rows: list[dict]) -> list[str]:
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        return await asyncio.gather(*(fetch(client, sem, r["domain"]) for r in rows))


def main() -> None:
    with DOMAINS.open(newline="") as f:
        rows = list(csv.DictReader(f))
    OUT.mkdir(parents=True, exist_ok=True)
    results = asyncio.run(fetch_all(rows))
    for outcome in ("saved", "kept", "missing", "too small", "error"):
        names = [
            r["domain"] for r, o in zip(rows, results, strict=True) if o == outcome
        ]
        if names:
            listed = "" if outcome in ("saved", "kept") else ", ".join(names)
            print(f"{outcome:<10} {len(names):>4}  {listed}")
    if "--sheet" in sys.argv:
        contact_sheet(rows)
        print("wrote logos.html")


if __name__ == "__main__":
    main()
