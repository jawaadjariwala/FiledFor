"""Download company logos into site/logos, once, so visitors' browsers never
call a third-party logo service.

    uv run python -m filedfor.logos            fetch logos not saved yet
    uv run python -m filedfor.logos --sheet    also write logos.html, a contact
                                               sheet for checking them by eye

data/domains.csv maps a company name (as on the site) to its website. The
icon comes from Google's favicon service, or failing that from the company's
own site (its apple-touch-icon or largest declared icon). JPEG and ICO files
are converted to PNG; Pillow is a development dependency, used only here. A
company without a row, or whose icon is missing or too small, keeps the
lettered badge the site draws.
"""

import asyncio
import csv
import html
import io
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import httpx

DOMAINS = Path("data/domains.csv")
OUT = Path("site/logos")
SOURCE = "https://www.google.com/s2/favicons?domain={domain}&sz=128"
MIN_SIZE = 32  # pixels; smaller icons look blurry at the site's 44px
MAX_SIZE = 128  # larger icons are scaled down to this
ICON_LINK = re.compile(r"<link\b[^>]*>", re.IGNORECASE)


def to_png(data: bytes) -> bytes | None:
    """Any raster icon as a PNG of at most MAX_SIZE, or None if it isn't one
    or is smaller than MIN_SIZE."""
    from PIL import Image  # development dependency, only needed here

    try:
        img = Image.open(io.BytesIO(data))
        if img.format == "ICO":  # pick the largest image inside the .ico
            img.size = max(img.info.get("sizes") or [img.size])
        img.load()
    except Exception:  # noqa: BLE001 (any unreadable file is just "missing")
        return None
    if min(img.size) < MIN_SIZE:
        return None
    img = img.convert("RGBA")
    img.thumbnail((MAX_SIZE, MAX_SIZE))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def site_icons(page: str, base: str) -> list[str]:
    """Icon URLs a homepage declares, largest first, then the usual path."""
    found = []
    for tag in ICON_LINK.findall(page):
        rel = re.search(r'rel=["\']?([^"\'>]+)', tag, re.IGNORECASE)
        href = re.search(r'href=["\']?([^"\' >]+)', tag, re.IGNORECASE)
        if (
            not rel
            or not href
            or "icon" not in rel.group(1).lower()
            or href.group(1).endswith(".svg")
        ):
            continue
        sizes = re.search(r'sizes=["\']?(\d+)', tag, re.IGNORECASE)
        size = (
            int(sizes.group(1))
            if sizes
            else (180 if "apple" in rel.group(1).lower() else 32)
        )
        found.append((size, urljoin(base, html.unescape(href.group(1)))))
    urls = [u for _, u in sorted(found, reverse=True)]
    return urls + [urljoin(base, "/apple-touch-icon.png")]


async def get(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        r = await client.get(url)
    except (httpx.HTTPError, ValueError):
        return None
    return r.content if r.status_code == 200 else None


async def fetch(client: httpx.AsyncClient, sem: asyncio.Semaphore, domain: str) -> str:
    path = OUT / f"{domain}.png"
    if path.exists():
        return "kept"
    async with sem:
        data = await get(client, SOURCE.format(domain=domain))
        png = to_png(data) if data else None
        if png is None:  # Google's icon is missing or too small: ask the site
            base = f"https://{domain}/"
            page = await get(client, base)
            text = page.decode("utf-8", "ignore")[:200_000] if page else ""
            for url in site_icons(text, base)[:4]:
                if (data := await get(client, url)) and (png := to_png(data)):
                    break
    if png is None:
        return "missing"
    path.write_bytes(png)
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
    ua = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
    }
    async with httpx.AsyncClient(
        timeout=15, follow_redirects=True, headers=ua
    ) as client:
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
